#!/usr/bin/env python3
"""3단계: 결정론적 검사와 agent용 후보를 만들어 <work>/ctx/review-input.json 에 모은다.

결정론적 검사는 3-1(형식/타입 힌트/docstring)과 5(테스트 실행), 후보는 3-3/4-2(테스트 후보)와 6(미사용 후보)이다.
Claude 단계는 이 파일만 읽는다.

- 3-1: ruff check(변경 줄에 있는 위반만) + ruff format --diff(변경 줄과 겹치는 hunk만). 기존 코드의 위반으로 반려하지 않는다.
  F401/F841(미사용 import/변수)은 3-1이 아니라 6번 후보로 보낸다. 팀 규칙상 미사용은 comment이기 때문이다.
- 5: 대상 repo의 pytest 전체. failed/error는 반려, none(테스트 없음)은 반려가 아니다 (필요 여부는 agent가 3-2/4-1로 판단).
- 판정: 1(수집), 2(파싱), 3-1, 5 중 하나라도 실패면 precheck.verdict = reject. 그 경우 Claude 단계를 생략한다.
- 종료 코드는 항상 0. 결과는 GITHUB_OUTPUT의 precheck=reject|continue 로 알린다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    WORK_DIR_NAME,
    Result,
    git,
    github_output,
    in_ranges,
    is_test_path,
    load_json,
    log,
    ranges_overlap,
    resolve_work,
    run,
    tree_state,
    write_json,
)

DEFAULT_RUFF_CONFIG = Path(__file__).parent / "ruff-default.toml"
VERDICT_SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "review-verdict.json"
DEAD_CODE_RUFF_CODES = {"F401", "F841", "F811"}
MAX_DIFF_BYTES = 64 * 1024
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
VULTURE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): unused (?P<kind>\w+) '(?P<name>[^']+)' \((?P<conf>\d+)% confidence"
)


# ---------- ruff ----------


def target_has_ruff_config(repo: Path) -> bool:
    """대상 repo에 ruff 설정(ruff.toml, .ruff.toml, pyproject [tool.ruff])이 있는가."""
    if (repo / "ruff.toml").exists() or (repo / ".ruff.toml").exists():
        return True
    pp = repo / "pyproject.toml"
    if not pp.exists():
        return False
    try:
        import tomllib

        return "ruff" in tomllib.loads(pp.read_text(encoding="utf-8")).get("tool", {})
    except Exception:  # noqa: BLE001
        return "[tool.ruff" in pp.read_text(encoding="utf-8", errors="replace")


def ruff_base_args(repo: Path) -> tuple[list[str], str]:
    """ruff 공통 인자와 설정 출처('target' 또는 'default')를 돌려준다."""
    if target_has_ruff_config(repo):
        return [], "target"
    return ["--config", str(DEFAULT_RUFF_CONFIG)], "default"


def parse_ruff_json(text: str, repo: Path) -> list[dict]:
    """ruff check --output-format json 출력을 {file, line, code, message, url} 목록으로 바꾼다. 키가 없어도 견딘다."""
    try:
        items = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for it in items:
        fn = it.get("filename") or ""
        try:
            fn = str(Path(fn).resolve().relative_to(repo.resolve()))
        except ValueError:
            pass
        loc = it.get("location") or {}
        out.append(
            {
                "file": fn.replace(os.sep, "/"),
                "line": int(loc.get("row") or 0),
                "code": it.get("code"),
                "message": it.get("message") or "",
                "url": it.get("url"),
            }
        )
    return out


def filter_changed(diags: list[dict], lines_by_file: dict[str, list[list[int]]]) -> list[dict]:
    """변경 줄에 있는 진단만 남긴다."""
    return [d for d in diags if d["file"] in lines_by_file and in_ranges(d["line"], lines_by_file[d["file"]])]


def parse_format_diff(text: str, repo: Path) -> dict[str, list[dict]]:
    """ruff format --diff 출력을 파일별 hunk 목록으로 바꾼다. '---' 쪽(현재 파일)의 줄 범위를 lines로 둔다."""
    hunks: dict[str, list[dict]] = {}
    current: str | None = None
    buf: list[str] = []
    rng: tuple[int, int] | None = None

    def flush() -> None:
        if current and rng:
            hunks.setdefault(current, []).append({"lines": [rng[0], rng[1]], "diff": "\n".join(buf)})

    for line in text.splitlines():
        if line.startswith("--- "):
            flush()
            buf, rng = [], None
            name = line[4:].split("\t")[0].strip()
            try:
                name = str(Path(name).resolve().relative_to(repo.resolve()))
            except ValueError:
                pass
            current = name.replace(os.sep, "/")
            continue
        if line.startswith("+++ "):
            continue
        m = HUNK_RE.match(line)
        if m:
            flush()
            a, b = int(m.group(1)), 1 if m.group(2) is None else int(m.group(2))
            rng = (a, a + max(b, 1) - 1)
            buf = [line]
            continue
        if rng is not None:
            buf.append(line)
    flush()
    return hunks


def check_lint(repo: Path, py_files: list[dict]) -> tuple[dict, dict, list[dict]]:
    """ruff check + ruff format --diff. (lint.json, format.json, 6번 후보로 넘길 미사용 진단) 을 돌려준다."""
    paths = [f["path"] for f in py_files if f.get("after_path")]
    lines_by_file = {f["path"]: f["changed_lines"] for f in py_files}
    cfg_args, cfg = ruff_base_args(repo)
    if not paths:
        return (
            {"config": cfg, "violations": [], "pass": True, "ran": False},
            {"config": cfg, "violations": [], "pass": True, "ran": False},
            [],
        )
    if shutil.which("ruff") is None:
        note = "ruff가 설치되어 있지 않음"
        return (
            {"config": cfg, "violations": [], "pass": False, "ran": False, "note": note},
            {"config": cfg, "violations": [], "pass": False, "ran": False, "note": note},
            [],
        )

    r = run(
        ["ruff", "check", "--output-format", "json", "--no-cache", "--exit-zero", *cfg_args, "--", *paths], cwd=repo
    )
    diags = filter_changed(parse_ruff_json(r.out, repo), lines_by_file)
    dead = [d for d in diags if d["code"] in DEAD_CODE_RUFF_CODES]
    violations = [d for d in diags if d["code"] not in DEAD_CODE_RUFF_CODES and d["code"] is not None]
    lint = {"config": cfg, "ran": True, "violations": violations, "pass": not violations}
    if r.code not in (0, 1) and not r.out.strip():
        lint["note"] = f"ruff check 실행 오류: {r.err.strip()[:300]}"
        lint["pass"] = False

    r2 = run(["ruff", "format", "--diff", "--no-cache", *cfg_args, "--", *paths], cwd=repo)
    fmt_v: list[dict] = []
    for file, hs in parse_format_diff(r2.out, repo).items():
        for h in hs:
            if file in lines_by_file and ranges_overlap((h["lines"][0], h["lines"][1]), lines_by_file[file]):
                fmt_v.append({"file": file, "lines": h["lines"], "diff": h["diff"][:2000]})
    fmt = {"config": cfg, "ran": True, "violations": fmt_v, "pass": not fmt_v}
    if r2.code not in (0, 1):
        fmt["note"] = f"ruff format 실행 오류: {r2.err.strip()[:300]}"
        fmt["pass"] = False
    return lint, fmt, dead


# ---------- pytest ----------


def parse_junit(path: Path) -> dict:
    """pytest --junitxml 결과를 {tests, passed, failed, errors, skipped, failures:[{id, message}]} 로 바꾼다."""
    empty = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failures": []}
    if not path.exists():
        return empty
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return empty
    res = dict(empty)
    failures: list[dict] = []
    for case in root.iter("testcase"):
        res["tests"] += 1
        tid = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        f = case.find("failure")
        e = case.find("error")
        if f is not None:
            res["failed"] += 1
            failures.append(
                {"id": tid, "type": "failure", "message": (f.get("message") or (f.text or "").strip())[:300]}
            )
        elif e is not None:
            res["errors"] += 1
            failures.append({"id": tid, "type": "error", "message": (e.get("message") or (e.text or "").strip())[:300]})
        elif case.find("skipped") is not None:
            res["skipped"] += 1
        else:
            res["passed"] += 1
    res["failures"] = failures
    return res


def outcome_from(code: int, junit: dict, r: Result) -> str:
    """pytest 종료 코드와 junit 결과로 passed/failed/error/none 을 정한다."""
    if r.timed_out:
        return "error"
    if "No module named pytest" in (r.err or ""):
        return "error"
    if junit["errors"] > 0:
        return "error"
    return {0: "passed", 1: "failed", 5: "none"}.get(code, "error")


def check_tests(repo: Path, work: Path, files: list[dict], skip: bool, extra: str, timeout: int) -> dict:
    """대상 repo에서 pytest 전체를 돌리고 결과를 요약한다."""
    ctx = work / "ctx"
    changed_tests = [f["path"] for f in files if f["is_python"] and f["is_test"] and f["status"] != "D"]
    if skip:
        return {"outcome": "skipped", "changed_test_files": changed_tests, "note": "--skip-tests"}
    junit_path = ctx / "pytest.xml"
    junit_path.unlink(missing_ok=True)
    py = shutil.which("python") or sys.executable
    args = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit_path}", f"--ignore={work}"]
    if extra:
        args.extend(extra.split())
    r = run(args, cwd=repo, timeout=timeout)
    junit = parse_junit(junit_path)
    outcome = outcome_from(r.code, junit, r)
    tail = (r.out or "").strip().splitlines()[-30:]
    res = {
        "outcome": outcome,
        "exit_code": r.code,
        "changed_test_files": changed_tests,
        "output_tail": "\n".join(tail)[:4000],
        **junit,
    }
    if r.timed_out:
        res["note"] = f"pytest가 {timeout}초 안에 끝나지 않음"
    elif outcome == "error" and not junit["errors"]:
        res["note"] = (r.err or r.out or "").strip()[-500:]
    return res


# ---------- 후보 (3-3 / 4-2, 6) ----------


def module_name(path: str) -> str:
    """파일 경로를 import 경로로 바꾼다 (src/, lib/ 접두어와 __init__ 은 뺀다)."""
    p = PurePosixPath(path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[0] in ("src", "lib"):
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def list_test_files(repo: Path) -> list[str]:
    """git이 추적하는 python 파일 중 테스트 파일 목록."""
    r = git(repo, "ls-files", "--", "*.py")
    return [p for p in r.out.splitlines() if p and is_test_path(p)]


def find_test_candidates(repo: Path, symbols: list[dict], changed_tests: list[str], test_files: list[str]) -> dict:
    """추가/수정된 비테스트 심볼마다 테스트 파일에서 이름 참조와 모듈 import를 찾는다. agent가 실제 검증 여부를 판단한다."""
    contents: dict[str, list[str]] = {}
    for tf in test_files:
        try:
            contents[tf] = (repo / tf).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    changed_stems = {PurePosixPath(t).stem for t in changed_tests}
    out: dict[str, dict] = {}
    for s in symbols:
        if s["is_test"] or s["change"] == "removed":
            continue
        bare = s["name"].split("#")[0].split(".")[-1]
        owner = s["name"].split("#")[0].split(".")[0] if "." in s["name"] else None
        mod = module_name(s["file"])
        stem = PurePosixPath(s["file"]).stem
        name_re = re.compile(rf"\b{re.escape(bare)}\b")
        import_re = (
            re.compile(
                rf"(\b{re.escape(mod)}\b)|(from\s+{re.escape(mod.rsplit('.', 1)[0])}\s+import\s+[^\n]*\b{re.escape(mod.rsplit('.', 1)[-1])}\b)"
            )
            if mod
            else None
        )
        hits: list[dict] = []
        imports: list[dict] = []
        for tf, lines in contents.items():
            for i, line in enumerate(lines, 1):
                if name_re.search(line) and not line.lstrip().startswith("#"):
                    hits.append(
                        {
                            "file": tf,
                            "line": i,
                            "snippet": line.strip()[:160],
                            "mentions_class": bool(owner and owner in line),
                        }
                    )
                if import_re and import_re.search(line) and "import" in line:
                    imports.append({"file": tf, "line": i, "snippet": line.strip()[:160]})
        out[f"{s['file']}::{s['name']}"] = {
            "symbol": s["name"],
            "file": s["file"],
            "change": s["change"],
            "direct": f"test_{stem}" in changed_stems or f"{stem}_test" in changed_stems,
            "weak": len(bare) < 4,
            "name_hits": hits[:20],
            "import_hits": imports[:10],
        }
    return out


def parse_vulture(text: str) -> list[dict]:
    """vulture 출력 줄을 후보 목록으로 바꾼다."""
    out: list[dict] = []
    for line in text.splitlines():
        m = VULTURE_RE.match(line.strip())
        if m:
            out.append(
                {
                    "file": m["path"].replace(os.sep, "/"),
                    "line": int(m["line"]),
                    "kind": m["kind"],
                    "name": m["name"],
                    "confidence": int(m["conf"]),
                    "source": "vulture",
                }
            )
    return out


def find_dead_code(repo: Path, work: Path, py_files: list[dict], symbols: list[dict], ruff_dead: list[dict]) -> dict:
    """vulture 와 ruff(F401, F841, F811) 결과에서 PR 변경 범위에 해당하는 미사용 후보를 모은다."""
    lines_by_file = {f["path"]: f["changed_lines"] for f in py_files}
    names = {s["name"].split("#")[0].split(".")[-1] for s in symbols if s["change"] != "removed"}
    items = [
        {
            "file": d["file"],
            "line": d["line"],
            "kind": "ruff",
            "name": d["code"],
            "confidence": 100,
            "source": "ruff",
            "message": d["message"],
        }
        for d in ruff_dead
    ]
    res: dict = {"skipped": False, "candidates": items}
    if shutil.which("vulture") is None:
        res["skipped"] = True
        res["note"] = "vulture가 설치되어 있지 않아 ruff 결과만 사용"
        return res
    try:
        rel_work = str(work.relative_to(repo))
    except ValueError:
        rel_work = WORK_DIR_NAME
    exclude = ",".join(f"*/{d}/*" for d in (rel_work, ".venv", "venv", "build", "dist", "node_modules"))
    r = run(["vulture", ".", "--min-confidence", "60", "--exclude", exclude], cwd=repo, timeout=300)
    if r.code not in (0, 3):
        res["note"] = f"vulture 실행 오류(exit {r.code}): {(r.err or r.out).strip()[:300]}"
        return res
    for v in parse_vulture(r.out):
        if v["file"] not in lines_by_file:
            continue
        if in_ranges(v["line"], lines_by_file[v["file"]]) or v["name"] in names:
            items.append(v)
    res["candidates"] = items
    return res


# ---------- 작업 트리 확인 ----------


def ensure_head(repo: Path, pr: dict, allow: bool) -> None:
    """ruff와 pytest는 작업 트리를 검사하므로 작업 트리가 PR head 커밋이어야 한다. CI의 checkout이 그렇다."""
    head = pr.get("head_sha")
    cur = git(repo, "rev-parse", "HEAD").out.strip()
    if head and cur != head:
        msg = f"작업 트리 HEAD({cur[:10]})가 PR head({head[:10]})와 다름. `git checkout {head[:10]}` 후 다시 실행할 것"
        if allow:
            log("precheck", "경고: " + msg)
        else:
            raise SystemExit("[precheck] " + msg)
    dirty = git(repo, "status", "--porcelain", "--untracked-files=no").out.strip()
    if dirty:
        log("precheck", "경고: 작업 트리에 커밋되지 않은 변경이 있음. 검사 결과가 PR과 다를 수 있음")


# ---------- 조립 ----------


def build_review_input(
    work: Path,
    pr: dict,
    files: list[dict],
    symbols: dict,
    lint: dict,
    fmt: dict,
    tests: dict,
    candidates: dict,
    dead: dict,
) -> dict:
    """지금까지의 결과를 review-input.json 한 덩어리로 합치고 precheck 판정을 계산한다."""
    reasons: list[dict] = []
    checks: dict[str, str] = {"1": "pass", "2": "pass", "3-1": "pass", "5": "n/a"}
    notes: list[str] = list(symbols.get("notes", []))
    escalate = False

    if not files:
        notes.append("변경 파일이 없음")
    big = [f["path"] for f in files if f["too_large"]]
    if big:
        escalate = True
        notes.append("크기 초과로 내용을 검사하지 못한 파일: " + ", ".join(big))

    for e in symbols.get("parse_errors", []):
        checks["2"] = "fail"
        reasons.append({"check": "2", "message": f"{e['file']}:{e['line']} 파싱 오류: {e['message']}"})

    for v in lint["violations"]:
        checks["3-1"] = "fail"
        reasons.append({"check": "3-1", "message": f"{v['file']}:{v['line']} {v['code']} {v['message']}"})
    for v in fmt["violations"]:
        checks["3-1"] = "fail"
        reasons.append({"check": "3-1", "message": f"{v['file']}:{v['lines'][0]}-{v['lines'][1]} ruff format 필요"})
    for key in ("lint", "format"):
        d = lint if key == "lint" else fmt
        if d.get("note"):
            checks["3-1"] = "fail"
            reasons.append({"check": "3-1", "message": d["note"]})

    if tests["outcome"] == "passed":
        checks["5"] = "pass"
    elif tests["outcome"] in ("failed", "error"):
        checks["5"] = "fail"
        for f in tests.get("failures", [])[:20]:
            reasons.append({"check": "5", "message": f"{f['id']}: {f['message']}"})
        if not tests.get("failures"):
            reasons.append(
                {
                    "check": "5",
                    "message": f"pytest {tests['outcome']}: {tests.get('note') or tests.get('output_tail', '')[-300:]}",
                }
            )
    elif tests["outcome"] == "none":
        checks["5"] = "n/a"
        notes.append("수집된 테스트가 없음. 테스트 필요 여부는 agent가 3-2/4-1로 판단")
    elif tests["outcome"] == "skipped":
        checks["5"] = "n/a"
        notes.append("--skip-tests 로 테스트를 건너뜀")

    verdict = "reject" if any(v == "fail" for v in checks.values()) else "continue"

    slim_files = []
    for f in files:
        g = dict(f)
        dp = work / f["diff_path"] if f.get("diff_path") else None
        if dp and dp.exists():
            text = dp.read_text(encoding="utf-8", errors="replace")
            if len(text.encode("utf-8")) > MAX_DIFF_BYTES:
                g["diff"] = None
                g["diff_omitted"] = True
                escalate = True
                notes.append(
                    f"{f['path']}: diff가 {MAX_DIFF_BYTES // 1024}KB를 넘어 review-input에 넣지 않음 (파일은 {f['diff_path']})"
                )
            else:
                g["diff"] = text
                g["diff_omitted"] = False
        else:
            g["diff"] = None
            g["diff_omitted"] = False
        slim_files.append(g)

    return {
        "pr": pr,
        "files": slim_files,
        "symbols": symbols.get("symbols", []),
        "symbol_summary": symbols.get("summary", {}),
        "lint": lint,
        "format": fmt,
        "tests": tests,
        "test_candidates": candidates,
        "dead_code_candidates": dead,
        "precheck": {"verdict": verdict, "checks": checks, "reasons": reasons, "escalate": escalate, "notes": notes},
    }


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="대상 repo 경로")
    ap.add_argument("--work", default=None, help=f"작업 디렉터리 (기본 <repo>/{WORK_DIR_NAME})")
    ap.add_argument("--skip-tests", action="store_true", help="pytest를 돌리지 않는다 (5번은 n/a)")
    ap.add_argument("--pytest-args", default="", help="pytest에 덧붙일 인자")
    ap.add_argument("--test-timeout", type=int, default=900, help="pytest 제한 시간(초)")
    ap.add_argument("--allow-mismatch", action="store_true", help="작업 트리가 PR head와 달라도 계속한다 (로컬 실험용)")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    work = resolve_work(repo, a.work)
    ctx = work / "ctx"
    pr = load_json(ctx / "pr.json")
    files = load_json(ctx / "files.json")
    symbols = load_json(ctx / "symbols.json")
    if not isinstance(files, list) or not isinstance(symbols, dict):
        raise SystemExit(
            "[precheck] ctx/files.json 또는 ctx/symbols.json 이 없음. collect.py, classify.py를 먼저 실행할 것"
        )

    ensure_head(repo, pr or {}, a.allow_mismatch)
    py_files = [f for f in files if f["is_python"] and not f["is_binary"] and not f["too_large"] and f["status"] != "D"]
    lint, fmt, ruff_dead = check_lint(repo, py_files)
    write_json(ctx / "lint.json", lint)
    write_json(ctx / "format.json", fmt)
    lint_msg = "통과" if lint["pass"] else f"실패 {len(lint['violations'])}건"
    fmt_msg = "통과" if fmt["pass"] else f"실패 {len(fmt['violations'])}건"
    log("precheck", f"3-1 lint {lint_msg}, format {fmt_msg} (설정: {lint['config']})")

    tests = check_tests(repo, work, files, a.skip_tests, a.pytest_args, a.test_timeout)
    write_json(ctx / "tests.json", tests)
    log(
        "precheck",
        f"5 pytest {tests['outcome']} (tests={tests.get('tests', 0)}, failed={tests.get('failed', 0)}, errors={tests.get('errors', 0)})",
    )

    syms = symbols.get("symbols", [])
    candidates = find_test_candidates(repo, syms, tests.get("changed_test_files", []), list_test_files(repo))
    dead = find_dead_code(repo, work, py_files, syms, ruff_dead)
    write_json(ctx / "candidates.json", {"test_candidates": candidates, "dead_code_candidates": dead})
    log("precheck", f"테스트 후보 {len(candidates)}개 심볼, 미사용 후보 {len(dead['candidates'])}건")

    ri = build_review_input(work, pr or {}, files, symbols, lint, fmt, tests, candidates, dead)
    write_json(ctx / "review-input.json", ri)
    write_json(ctx / "tree-baseline.json", tree_state(repo, work))  # apply --require-clean 의 비교 기준
    shutil.copyfile(VERDICT_SCHEMA, ctx / "verdict-schema.json")  # orchestrator가 출력 형식을 확인할 때 읽는다
    v = ri["precheck"]["verdict"]
    github_output("precheck", v)
    github_output("escalate", "true" if ri["precheck"]["escalate"] else "false")
    log("precheck", f"판정 {v} (사유 {len(ri['precheck']['reasons'])}건) -> {ctx / 'review-input.json'}")


if __name__ == "__main__":
    main()
