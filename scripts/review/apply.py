#!/usr/bin/env python3
"""4단계: precheck 결과(ctx/review-input.json)와 Claude 출력(out/verdict.json)을 합쳐 PR에 반영한다. GitHub에 쓰는 유일한 단계.

- 스크립트 판정이 우선이다. 1, 2, 3-1, 5는 precheck 값만 쓰고 agent가 같은 번호를 쓰면 verdict 전체를 무효로 본다.
- 최종 판정: precheck가 reject이거나 agent의 3-3/4-2가 fail이면 reject, 아니면 pass.
  precheck는 continue인데 verdict.json이 없거나 무효면 error (Claude 단계 실패). 조용히 통과시키지 않는다.
- PR comment는 첫 줄의 표식(<!-- everex-review -->)으로 찾아 같은 comment를 갱신한다. 라벨은 review/pass, review/reject.
- --gate true 이고 reject/error면 종료 코드 1로 check를 실패시킨다. --dry-run 이면 GitHub에 쓰지 않고 out/review-comment.md만 만든다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    AGENT_CHECKS,
    CHECK_NAMES,
    COMMENT_MARKER,
    RESULT_MARK,
    SCRIPT_CHECKS,
    WORK_DIR_NAME,
    Summary,
    load_json,
    log,
    resolve_work,
    run,
    run_url,
    tree_state,
)

VERDICT_KO = {"pass": "통과", "reject": "반려", "error": "오류"}
LABELS = {"pass": ("review/pass", "0E8A16"), "reject": ("review/reject", "B60205")}
MAX_STR = {"detail": 500, "reason": 500, "evidence": 500, "comment": 1000, "request": 500}


# ---------- 검증 ----------


def _check_str(obj: dict, key: str, limit: int, where: str, required: bool = False) -> str | None:
    v = obj.get(key)
    if v is None:
        return f"{where}.{key} 누락" if required else None
    if not isinstance(v, str):
        return f"{where}.{key} 는 문자열이어야 함"
    if len(v) > limit:
        return f"{where}.{key} 가 {limit}자를 넘음"
    return None


def validate_verdict(v: object) -> str | None:
    """out/verdict.json 이 schemas/review-verdict.json 의 핵심 제약을 지키는지 본다. 문제가 있으면 이유를 돌려준다."""
    if not isinstance(v, dict):
        return "verdict.json 이 객체가 아님"
    required = {"verdict", "checks", "symbols", "dead_code", "design", "requests"}
    missing = required - set(v)
    if missing:
        return "필수 키 누락: " + ", ".join(sorted(missing))
    extra = set(v) - required
    if extra:
        return "허용되지 않는 키: " + ", ".join(sorted(extra))
    if v["verdict"] not in ("pass", "reject"):
        return f"verdict 값이 잘못됨: {v['verdict']!r}"
    for key in ("checks", "symbols", "dead_code", "design", "requests"):
        if not isinstance(v[key], list):
            return f"{key} 는 배열이어야 함"

    seen: set[str] = set()
    for i, c in enumerate(v["checks"]):
        where = f"checks[{i}]"
        if not isinstance(c, dict) or set(c) - {"id", "result", "detail"} or "id" not in c or "result" not in c:
            return f"{where} 형식 오류 (id, result, detail만 허용)"
        if c["id"] in SCRIPT_CHECKS:
            return f"{where}.id {c['id']} 는 스크립트가 판정하므로 agent가 쓸 수 없음"
        if c["id"] not in AGENT_CHECKS:
            return f"{where}.id 가 잘못됨: {c['id']!r}"
        if c["id"] in seen:
            return f"{where}.id {c['id']} 중복"
        seen.add(c["id"])
        if c["result"] not in ("pass", "fail", "n/a"):
            return f"{where}.result 값이 잘못됨: {c['result']!r}"
        if err := _check_str(c, "detail", MAX_STR["detail"], where):
            return err

    for i, s in enumerate(v["symbols"]):
        where = f"symbols[{i}]"
        allowed = {"name", "file", "needs_test", "reason", "test_found", "evidence"}
        if not isinstance(s, dict) or set(s) - allowed or not {"name", "file", "needs_test"} <= set(s):
            return f"{where} 형식 오류"
        if not isinstance(s["needs_test"], bool):
            return f"{where}.needs_test 는 bool 이어야 함"
        if "test_found" in s and s["test_found"] is not None and not isinstance(s["test_found"], bool):
            return f"{where}.test_found 는 bool 또는 null 이어야 함"
        for k in ("name", "file"):
            if not isinstance(s[k], str) or not s[k]:
                return f"{where}.{k} 는 비어 있지 않은 문자열이어야 함"
        for k in ("reason", "evidence"):
            if err := _check_str(s, k, MAX_STR[k], where):
                return err

    for i, d in enumerate(v["dead_code"]):
        where = f"dead_code[{i}]"
        if (
            not isinstance(d, dict)
            or set(d) - {"name", "file", "confirmed", "reason"}
            or not {"name", "file", "confirmed"} <= set(d)
        ):
            return f"{where} 형식 오류"
        if not isinstance(d["confirmed"], bool):
            return f"{where}.confirmed 는 bool 이어야 함"
        if err := _check_str(d, "reason", MAX_STR["reason"], where):
            return err

    for i, d in enumerate(v["design"]):
        where = f"design[{i}]"
        if not isinstance(d, dict) or set(d) - {"file", "line", "comment"} or not {"file", "comment"} <= set(d):
            return f"{where} 형식 오류"
        if "line" in d and d["line"] is not None and not isinstance(d["line"], int):
            return f"{where}.line 은 정수 또는 null 이어야 함"
        if err := _check_str(d, "comment", MAX_STR["comment"], where, required=True):
            return err

    for i, r in enumerate(v["requests"]):
        if not isinstance(r, str) or len(r) > MAX_STR["request"]:
            return f"requests[{i}] 는 {MAX_STR['request']}자 이하 문자열이어야 함"
    return None


# ---------- 병합 ----------


def merge(precheck: dict, verdict: dict | None, verdict_error: str | None) -> dict:
    """스크립트 판정과 agent 판정을 합친다.

    결과: {final, checks: {id: {result, detail}}, reasons: [...], agent_error}
    """
    checks: dict[str, dict] = {}
    for cid in CHECK_NAMES:
        checks[cid] = {"result": "n/a", "detail": ""}
    for cid, res in precheck.get("checks", {}).items():
        checks[cid] = {"result": res, "detail": ""}
    reasons = [dict(r) for r in precheck.get("reasons", [])]
    for cid in SCRIPT_CHECKS:
        n = sum(1 for r in reasons if r["check"] == cid)
        if n:
            checks[cid]["detail"] = f"{n}건"

    agent_error = None
    if precheck.get("verdict") == "reject":
        final = "reject"
        if verdict is None:
            agent_error = "precheck 반려로 agent 단계를 실행하지 않음"
    elif verdict is None or verdict_error:
        final = "error"
        agent_error = verdict_error or "verdict.json 이 없음 (agent 단계가 실행되지 않았거나 실패함)"
    else:
        final = "pass"

    if verdict and not verdict_error:
        for c in verdict["checks"]:
            checks[c["id"]] = {"result": c["result"], "detail": c.get("detail", "")}
            if c["result"] == "fail":
                if c["id"] in ("3-3", "4-2"):
                    final = "reject" if final != "error" else final
                    reasons.append({"check": c["id"], "message": c.get("detail") or CHECK_NAMES[c["id"]] + " 실패"})
        if verdict["verdict"] == "reject" and final == "pass":
            # agent가 reject라고 했는데 반려 항목이 없으면 이유를 남기되 판정은 스크립트 규칙을 따른다
            reasons.append(
                {
                    "check": "-",
                    "message": "agent가 reject를 냈지만 3-3/4-2 실패가 없어 pass로 기록 (스크립트 규칙 우선)",
                }
            )
    return {"final": final, "checks": checks, "reasons": reasons, "agent_error": agent_error}


# ---------- 렌더링 ----------


def _cell(s: object) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def render_comment(pr: dict, ri: dict, merged: dict, verdict: dict | None) -> str:
    """PR comment 본문(markdown)을 고정 구조로 만든다. 첫 줄은 갱신용 표식이다."""
    m = merged
    lines = [COMMENT_MARKER, f"## 코드 검수 결과: {VERDICT_KO[m['final']]}", ""]
    if m["agent_error"]:
        lines += [f"> {m['agent_error']}", ""]
    if ri["precheck"].get("escalate"):
        lines += ["> ⚠️ 크기 초과로 검사하지 못한 파일이 있어 사람의 확인이 필요함", ""]

    lines += ["| # | 검사 | 결과 | 내용 |", "|---|---|---|---|"]
    for cid, name in CHECK_NAMES.items():
        c = m["checks"][cid]
        lines.append(f"| {cid} | {name} | {RESULT_MARK.get(c['result'], c['result'])} | {_cell(c['detail'])} |")
    lines.append("")

    if m["reasons"]:
        lines += ["### 반려 사유"]
        for r in m["reasons"]:
            lines.append(f"- [{r['check']}] {r['message']}")
        lines.append("")

    ss = ri.get("symbol_summary", {})
    syms = [s for s in ri.get("symbols", []) if not s["is_test"]]
    if syms:
        agent_by_key = {(s["file"], s["name"]): s for s in (verdict or {}).get("symbols", [])}
        lines += [
            f"### 변경 심볼 (추가 {ss.get('added', 0)}, 수정 {ss.get('modified', 0)}, 삭제 {ss.get('removed', 0)})",
            "| 심볼 | 파일 | 종류 | 변경 | 테스트 필요 | 테스트 있음 | 근거 |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in syms:
            a = agent_by_key.get((s["file"], s["name"]))
            need = "" if a is None else ("예" if a["needs_test"] else "아니오")
            found = "" if a is None or a.get("test_found") is None else ("예" if a["test_found"] else "아니오")
            evid = "" if a is None else (a.get("evidence") or a.get("reason") or "")
            loc = f"{s['file']}:{s['lines'][0]}" if s.get("lines") else s["file"]
            lines.append(f"| `{s['name']}` | {loc} | {s['kind']} | {s['change']} | {need} | {found} | {_cell(evid)} |")
        lines.append("")

    dead = [d for d in (verdict or {}).get("dead_code", []) if d["confirmed"]]
    if dead:
        lines += ["### 미사용 코드 (comment)"]
        for d in dead:
            lines.append(f"- `{d['name']}` ({d['file']}): {d.get('reason', '')}")
        lines.append("")

    design = (verdict or {}).get("design", [])
    if design:
        lines += ["### 설계 의견 (comment)"]
        for d in design:
            loc = f"{d['file']}:{d['line']}" if d.get("line") else d["file"]
            lines.append(f"- {loc}: {d['comment']}")
        lines.append("")

    reqs = (verdict or {}).get("requests", [])
    if reqs:
        lines += ["### 요청"]
        for i, r in enumerate(reqs, 1):
            lines.append(f"{i}. {r}")
        lines.append("")

    foot = [
        "---",
        f"precheck: {ri['precheck']['verdict']} · lint 설정: {ri['lint'].get('config')} · pytest: {ri['tests'].get('outcome')}",
    ]
    if url := run_url():
        foot.append(f"실행 로그: {url}")
    lines += [" · ".join(foot)]
    return "\n".join(lines) + "\n"


def dirty_files(repo: Path, work: Path) -> list[str]:
    """Claude 단계 동안 바뀐 파일 목록. precheck가 남긴 기준선(ctx/tree-baseline.json)과 지금 상태를 비교한다."""
    baseline = load_json(work / "ctx" / "tree-baseline.json", {}) or {}
    now = tree_state(repo, work)
    changed = [p for p, h in now.items() if baseline.get(p) != h]
    changed += [p for p in baseline if p not in now]
    return sorted(changed)


# ---------- GitHub 반영 ----------


def gh_json(args: list[str], cwd: Path | None = None) -> object:
    """gh 명령을 실행해 JSON 출력을 돌려준다. 실패하면 RuntimeError."""
    r = run(["gh", *args], cwd=cwd)
    if not r.ok:
        raise RuntimeError(f"gh {' '.join(args[:3])} 실패: {r.err.strip()[:300]}")
    return json.loads(r.out) if r.out.strip() else None


def upsert_comment(slug: str, number: int, body: str, repo: Path) -> str:
    """표식으로 시작하는 기존 comment가 있으면 갱신하고 없으면 새로 단다. 'updated' 또는 'created'를 돌려준다."""
    comments = gh_json(["api", f"repos/{slug}/issues/{number}/comments", "--paginate"], cwd=repo) or []
    existing = next((c["id"] for c in comments if (c.get("body") or "").startswith(COMMENT_MARKER)), None)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump({"body": body}, tf, ensure_ascii=False)
        payload = tf.name
    try:
        if existing:
            gh_json(["api", "-X", "PATCH", f"repos/{slug}/issues/comments/{existing}", "--input", payload], cwd=repo)
            return "updated"
        gh_json(["api", "-X", "POST", f"repos/{slug}/issues/{number}/comments", "--input", payload], cwd=repo)
        return "created"
    finally:
        os.unlink(payload)


def set_labels(slug: str, number: int, final: str, repo: Path) -> None:
    """review/pass, review/reject 라벨을 만들고(있으면 유지) 최종 판정에 맞게 붙이고 뗀다."""
    if final not in LABELS:
        return
    for name, color in LABELS.values():
        run(
            [
                "gh",
                "label",
                "create",
                name,
                "--repo",
                slug,
                "--color",
                color,
                "--force",
                "--description",
                "everex-review 자동 검수",
            ],
            cwd=repo,
        )
    add, _ = LABELS[final]
    remove = [n for k, (n, _) in LABELS.items() if k != final]
    args = ["gh", "pr", "edit", str(number), "--repo", slug, "--add-label", add]
    for n in remove:
        args += ["--remove-label", n]
    r = run(args, cwd=repo)
    if not r.ok:
        log("apply", f"라벨 변경 실패: {r.err.strip()[:200]}")


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="대상 repo 경로")
    ap.add_argument("--work", default=None, help=f"작업 디렉터리 (기본 <repo>/{WORK_DIR_NAME})")
    ap.add_argument("--gate", default="false", help="true면 reject/error 시 종료 코드 1")
    ap.add_argument("--dry-run", action="store_true", help="GitHub에 쓰지 않고 out/review-comment.md 만 만든다")
    ap.add_argument(
        "--request-changes", action="store_true", help="게이트 모드에서 reject면 PR review(Request changes)도 남긴다"
    )
    ap.add_argument("--repo-slug", default=None, help="owner/name. 비우면 ctx/pr.json 의 값을 쓴다")
    ap.add_argument(
        "--require-clean",
        action="store_true",
        help="작업 트리에 변경이 있으면(agent가 repo 파일을 고쳤으면) verdict를 버리고 error 로 처리한다. CI에서 켠다",
    )
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    work = resolve_work(repo, a.work)
    ctx, out = work / "ctx", work / "out"
    gate = str(a.gate).lower() == "true"

    ri = load_json(ctx / "review-input.json")
    if not isinstance(ri, dict):
        raise SystemExit(f"[apply] {ctx / 'review-input.json'} 이 없음. precheck.py를 먼저 실행할 것")
    pr = ri.get("pr") or {}
    verdict = load_json(out / "verdict.json")
    verdict_error = None
    if (out / "verdict.json").exists():
        verdict_error = validate_verdict(verdict)
        if verdict_error:
            log("apply", f"verdict.json 무효: {verdict_error}")
            verdict = None
    if a.require_clean and (dirty := dirty_files(repo, work)):
        verdict_error = "agent가 대상 repo 파일을 변경함: " + ", ".join(dirty[:10])
        log("apply", verdict_error)
        verdict = None
    merged = merge(ri["precheck"], verdict, verdict_error)
    body = render_comment(pr, ri, merged, verdict)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review-comment.md").write_text(body, encoding="utf-8")
    log("apply", f"최종 판정 {merged['final']} (반려 사유 {len(merged['reasons'])}건) -> {out / 'review-comment.md'}")

    slug = a.repo_slug or pr.get("repo_slug")
    number = pr.get("number")
    posted = "dry-run" if a.dry_run else "skipped"
    if a.dry_run or not number or not slug or not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        if not a.dry_run:
            log("apply", "PR 번호, repo slug, GH_TOKEN 중 하나가 없어 GitHub에 쓰지 않음")
        print(body)
    else:
        posted = upsert_comment(slug, number, body, repo)
        set_labels(slug, number, merged["final"], repo)
        if gate and a.request_changes and merged["final"] == "reject":
            run(
                [
                    "gh",
                    "pr",
                    "review",
                    str(number),
                    "--repo",
                    slug,
                    "--request-changes",
                    "--body-file",
                    str(out / "review-comment.md"),
                ],
                cwd=repo,
            )
        log("apply", f"comment {posted}, label review/{merged['final']}")

    summ = Summary(a.summary)
    summ.add("| PR | 판정 | 반려 사유 | comment | 비고 |")
    summ.add("|---|---|---|---|---|")
    summ.add(
        f"| {('#' + str(number)) if number else '(로컬)'} | {VERDICT_KO[merged['final']]} | {len(merged['reasons'])}건 | {posted} | {merged['agent_error'] or ''} |"
    )
    summ.flush()

    if gate and merged["final"] in ("reject", "error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
