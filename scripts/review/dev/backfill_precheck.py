#!/usr/bin/env python3
"""과거 merge PR에 PR 검수의 스크립트 검사 단계(collect, classify, precheck)를 소급 실행해 PR별 결과를 CSV로 남긴다.

- 지표: P5 merge 시점 품질, P2의 Python 변경 줄. 지표 정의는 docs/pr-review-agent/metrics.md에 있다.
- 입력은 pr_metrics.py의 prs.csv다. PR마다 head SHA의 git worktree를 <work-root>/wt-<번호>에 만들고,
  그 worktree에서 scripts/review의 collect.py, classify.py, precheck.py를 subprocess로 실행한다.
- 파이프라인 스크립트는 실행만 하고 고치지 않는다. 결과는 <work-root>/work-<번호>/ctx/review-input.json에서 읽는다.
- 기본은 precheck.py --skip-tests다. pytest는 대상 repo의 코드를 실행하기 때문이다. --with-tests면 검사 5번도 실행한다.
- 하위 스크립트의 PATH 앞에 현재 Python의 bin 폴더를 붙인다. venv를 켜지 않은 셸에서도 venv의 ruff, vulture를 찾게 하기 위함이다.
- 한 PR이 실패하거나 검사 도구가 실행되지 않으면 그 PR의 error 열에 이유를 적고 다음 PR로 넘어간다.
- worktree는 이 스크립트가 만든 것만 PR마다 지운다. 다른 worktree 항목은 건드리지 않는다.

    python scripts/review/dev/backfill_precheck.py --repo ~/work/pose-griffin --prs reports/agent-impact/pr/prs.csv --out reports/agent-impact/backfill-pose-griffin.csv --work-root /tmp/backfill
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

REVIEW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REVIEW_DIR))
from common import git, load_json, log, run  # noqa: E402

OUT_COLUMNS = [
    "repo",
    "number",
    "head_sha",
    "py_lines",
    "targets",
    "targets_without_candidates",
    "targets_without_candidates_names",
    "lint_violations",
    "format_violations",
    "precheck_verdict",
    "tier",
    "dead_code_candidates",
    "dead_code_names",
    "too_large",
    "error",
]
# ruff 미사용 진단의 message에서 이름을 뽑는다. 예: "`os` imported but unused"
RUFF_NAME_RE = re.compile(r"`([^`]+)`")


def python_lines(files: list[dict]) -> int:
    """테스트 외 Python 파일의 추가·삭제 줄 합. precheck.py의 compute_tier가 등급을 정할 때 세는 줄 수와 같은 정의다."""
    return sum(f["additions"] + f["deletions"] for f in files if f["is_python"] and not f["is_test"])


def dead_code_items(candidates: list[dict]) -> list[tuple[str, int, str]]:
    """미사용 후보를 (파일, 줄, 이름)으로 바꾸고 중복을 뺀다.

    ruff 항목의 name은 규칙 코드(F401 등)이므로 message의 첫 `이름`을 쓰고, 뽑지 못하면 규칙 코드를 쓴다.
    vulture와 ruff가 같은 줄의 같은 이름(예: 미사용 import)을 함께 내면 한 번만 센다.
    한 줄에 이름이 여럿인 import(예: `import os, sys`)를 하나로 합치지 않도록 이름까지 키에 넣는다.

    Args:
        candidates: review-input.json의 dead_code_candidates.candidates.

    Returns:
        처음 나온 순서의 (파일, 줄, 이름) 목록.
    """
    items: dict[tuple[str, int, str], None] = {}
    for d in candidates:
        name = str(d.get("name"))
        if d.get("source") == "ruff":
            m = RUFF_NAME_RE.search(d.get("message") or "")
            name = m.group(1) if m else name
        items.setdefault((str(d.get("file")), int(d.get("line") or 0), name), None)
    return list(items)


def tool_problems(ri: dict) -> list[str]:
    """검사 도구가 실행되지 않아 위반 수나 미사용 후보 수가 빠졌을 수 있는 이유를 모은다.

    - lint, format: note가 있으면 ruff가 없거나 실행 오류다. ran과 pass가 모두 거짓인 경우도 같다.
      ran이 거짓이고 pass가 참이면 검사할 Python 파일이 없는 것이라 문제로 보지 않는다 (precheck.py의 check_lint).
    - dead_code_candidates: skipped가 참이면 vulture가 없고, note가 있으면 vulture 실행 오류다 (precheck.py의 find_dead_code).

    Args:
        ri: precheck.py가 쓴 review-input.json 내용.

    Returns:
        이유 목록. 문제가 없으면 빈 목록.
    """
    problems: list[str] = []
    for key in ("lint", "format"):
        d = ri.get(key) or {}
        if d.get("note") or (not d.get("ran") and not d.get("pass")):
            problems.append(f"{key} 검사 미실행: {d.get('note') or 'ruff가 실행되지 않음'}")
    dead = ri.get("dead_code_candidates") or {}
    if dead.get("skipped") or dead.get("note"):
        problems.append(f"미사용 후보 검사 미실행: {dead.get('note') or 'vulture가 실행되지 않음'}")
    return problems


def summarize(ri: dict) -> dict:
    """review-input.json에서 소급 판정 CSV 한 줄에 들어갈 값을 뽑는다.

    테스트 후보가 없는 판단 대상 심볼은 test_candidates의 name_hits(테스트 파일의 이름 참조)가 빈 심볼이다.
    import_hits는 같은 모듈의 다른 심볼을 import해도 생기므로 세지 않는다.
    검사 도구가 실행되지 않았으면 error에 이유를 적어 P5 집계에서 빠지게 한다 (tool_problems).

    Args:
        ri: precheck.py가 쓴 review-input.json 내용.

    Returns:
        OUT_COLUMNS 중 repo, number, head_sha를 뺀 키의 dict. 이름 목록은 ';'로 잇는다.
    """
    pre = ri.get("precheck") or {}
    targets = pre.get("targets") or []
    candidates = ri.get("test_candidates") or {}
    without = [t for t in targets if not (candidates.get(t) or {}).get("name_hits")]
    dead = dead_code_items((ri.get("dead_code_candidates") or {}).get("candidates") or [])
    files = ri.get("files") or []
    return {
        "py_lines": python_lines(files),
        "targets": len(targets),
        "targets_without_candidates": len(without),
        "targets_without_candidates_names": ";".join(without),
        "lint_violations": len((ri.get("lint") or {}).get("violations") or []),
        "format_violations": len((ri.get("format") or {}).get("violations") or []),
        "precheck_verdict": pre.get("verdict"),
        "tier": pre.get("tier"),
        "dead_code_candidates": len(dead),
        "dead_code_names": ";".join(sorted({name for _, _, name in dead})),
        "too_large": sum(1 for f in files if f.get("too_large")),
        "error": "; ".join(tool_problems(ri)),
    }


def read_prs(path: Path, repo_name: str) -> list[dict]:
    """prs.csv에서 repo 열이 repo_name인 행을 읽는다."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("repo") == repo_name]


def has_commit(repo: Path, sha: str) -> bool:
    """clone에 그 SHA의 commit이 있으면 True."""
    return git(repo, "cat-file", "-e", f"{sha}^{{commit}}").ok


def ensure_commit(repo: Path, sha: str, refspec: str, label: str) -> None:
    """SHA가 clone에 없으면 `git fetch origin <refspec>`로 받는다. 그래도 없으면 RuntimeError."""
    if has_commit(repo, sha):
        return
    r = git(repo, "fetch", "origin", refspec)
    if not has_commit(repo, sha):
        raise RuntimeError(
            f"{label} {sha[:10]}가 clone에 없고 `git fetch origin {refspec}`로도 받지 못함: {r.err.strip()[:300]}"
        )


def run_script(name: str, *args: str) -> None:
    """scripts/review의 스크립트를 현재 Python으로 실행한다. 종료 코드가 0이 아니면 RuntimeError.

    PATH 앞에 현재 Python의 bin 폴더를 붙인다. precheck.py가 ruff, vulture를 PATH에서 찾기 때문이다.
    """
    env = {**os.environ, "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])}
    r = run([sys.executable, str(REVIEW_DIR / name), *args], env=env)
    if not r.ok:
        raise RuntimeError(f"{name} 실패(exit {r.code}): {(r.err or r.out).strip()[-500:]}")


def backfill_one(repo: Path, pr: dict, work_root: Path, with_tests: bool) -> dict:
    """PR 하나에 collect, classify, precheck를 실행하고 CSV 한 줄을 돌려준다. 실패하면 error 열에 내용을 적는다.

    Args:
        repo: 대상 repo의 로컬 clone.
        pr: prs.csv 한 줄(repo, number, base_sha, head_sha).
        work_root: worktree(wt-<번호>)와 작업 디렉터리(work-<번호>)를 만들 폴더.
        with_tests: True면 precheck.py에 --skip-tests를 넘기지 않는다.

    Returns:
        OUT_COLUMNS를 키로 가진 dict.
    """
    number = int(pr["number"])
    head, base = (pr.get("head_sha") or "").strip(), (pr.get("base_sha") or "").strip()
    row: dict = {"repo": pr.get("repo"), "number": number, "head_sha": head, "error": ""}
    wt = work_root / f"wt-{number}"
    work = work_root / f"work-{number}"
    created = False
    try:
        if not head or not base:
            raise RuntimeError("prs.csv에 head_sha 또는 base_sha가 없음")
        ensure_commit(repo, head, f"pull/{number}/head", "head SHA")
        ensure_commit(repo, base, base, "base SHA")
        if wt.exists():
            raise RuntimeError(f"{wt}가 이미 있음. 이전 실행이 남긴 폴더면 지우고 다시 실행할 것")
        r = git(repo, "worktree", "add", "--detach", str(wt), head)
        if not r.ok:
            raise RuntimeError(f"git worktree add 실패: {r.err.strip()[:300]}")
        created = True
        run_script("collect.py", "--repo", str(wt), "--work", str(work), "--base", base, "--head", head)
        run_script("classify.py", "--repo", str(wt), "--work", str(work))
        run_script("precheck.py", "--repo", str(wt), "--work", str(work), *([] if with_tests else ["--skip-tests"]))
        ri = load_json(work / "ctx" / "review-input.json")
        if not isinstance(ri, dict):
            raise RuntimeError(f"{work / 'ctx' / 'review-input.json'}을 읽지 못함")
        row.update(summarize(ri))
    except Exception as e:  # 한 PR의 실패로 전체를 멈추지 않는다
        row["error"] = " ".join(f"{type(e).__name__}: {e}".split())
    finally:
        if created:
            r = git(repo, "worktree", "remove", "--force", str(wt))
            if not r.ok:
                log("backfill", f"경고: worktree {wt}를 지우지 못함: {r.err.strip()[:300]}")
    return row


def write_csv(path: Path, rows: list[dict]) -> None:
    """행 목록을 OUT_COLUMNS 순서의 CSV로 쓴다. 없는 값은 빈 칸이다. 상위 디렉터리는 만든다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else r.get(k) for k in OUT_COLUMNS})


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="대상 repo의 로컬 clone")
    ap.add_argument("--prs", required=True, help="pr_metrics.py의 prs.csv")
    ap.add_argument("--repo-name", default=None, help="prs.csv에서 고를 repo 이름 (기본: --repo 경로의 폴더 이름)")
    ap.add_argument("--out", required=True, help="출력 CSV")
    ap.add_argument("--work-root", required=True, help="git worktree와 작업 디렉터리를 만들 폴더")
    ap.add_argument("--with-tests", action="store_true", help="검사 5번(pytest)도 실행한다")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    if git(repo, "rev-parse", "--is-inside-work-tree").out.strip() != "true":
        raise SystemExit(f"[backfill] git repo가 아님: {repo}")
    repo_name = a.repo_name or repo.name
    work_root = Path(a.work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    prs = read_prs(Path(a.prs), repo_name)
    if not prs:
        log("backfill", f"{a.prs}에 repo {repo_name}의 PR이 없음")

    rows: list[dict] = []
    for i, pr in enumerate(prs, 1):
        row = backfill_one(repo, pr, work_root, a.with_tests)
        status = (
            f"오류: {row['error'][:200]}" if row["error"] else f"판정 {row['precheck_verdict']}, 등급 {row['tier']}"
        )
        log("backfill", f"[{i}/{len(prs)}] {repo_name}#{row['number']} {status}")
        rows.append(row)
    write_csv(Path(a.out), rows)
    failed = sum(1 for r in rows if r["error"])
    log("backfill", f"PR {len(rows)}개 (오류 {failed}개) -> {a.out}")


if __name__ == "__main__":
    main()
