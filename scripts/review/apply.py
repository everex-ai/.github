#!/usr/bin/env python3
"""4단계: precheck 결과(ctx/review-input.json)와 Claude 출력(out/verdict.json)을 합쳐 PR에 반영한다. GitHub에 쓰는 유일한 단계.

- 스크립트 판정이 우선이다. 1, 2, 3-1, 5는 precheck 값만 쓰고 agent가 같은 번호를 쓰면 verdict 전체를 무효로 본다.
- 최종 판정: precheck가 reject이거나 agent의 3-3/4-2가 fail이면 reject, 아니면 pass.
  precheck는 continue인데 verdict.json이 없거나 무효면 error (Claude 단계 실패). 조용히 통과시키지 않는다.
  precheck가 등급 0(판단 대상 없음)으로 Claude를 생략했으면 verdict 없이 pass.
- PR comment는 첫 줄의 표식(<!-- everex-review -->)으로 찾아 같은 comment를 갱신한다. 라벨은 review/pass, review/reject.
- verdict의 suggestions 는 변경 줄 안에 있는 것만 PR review의 inline 수정 제안(suggestion 블록)으로 단다.
- --override <login>: 지정 리뷰어가 review/override 라벨로 판정을 뒤집은 경우. review-input 없이 기존 comment 앞에
  override 표시를 붙이고 라벨을 review/override 로 바꾼다. 게이트를 통과시킨다.
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
    CHANGE_KO,
    CHECK_NAMES,
    COMMENT_MARKER,
    KIND_KO,
    NOTE_FAIL_MARK,
    REJECT_CHECKS,
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

VERDICT_KO = {"pass": "통과", "reject": "반려", "error": "오류", "override": "통과 (리뷰어 override)"}
LABELS = {
    "pass": ("review/pass", "0E8A16"),
    "reject": ("review/reject", "B60205"),
    "override": ("review/override", "FBCA04"),
}
MAX_STR = {"detail": 500, "reason": 500, "evidence": 500, "comment": 1000, "request": 500, "replacement": 2000}
MAX_SUGGESTIONS = 5
OVERRIDE_MARK = "<!-- everex-review-override -->"
SUGGESTION_MARK = "<!-- everex-review-suggestion -->"
# 꼬리말에 나가는 검사 묶음 이름. 꼬리말은 검사 표 뒤에 있으므로 검사 ID가 검사 표의 # 칸임을 함께 적는다
SCRIPT_STAGE = f"스크립트 검사(검사 표의 {', '.join(SCRIPT_CHECKS)}번)"
# 꼬리말에 코드값 대신 나가는 표시 이름. precheck 판정, ruff 설정 출처, pytest 결과
PRECHECK_KO = {"continue": "통과", "reject": "반려"}
LINT_CONFIG_KO = {"target": "대상 repo의 ruff 설정", "default": "기본 규칙(everex-ai/.github의 ruff-default.toml)"}
PYTEST_KO = {
    "passed": "통과",
    "failed": "실패",
    "error": "실행 오류",
    "none": "수집된 테스트 없음",
    "skipped": "건너뜀",
}
# 등급 0(Claude 단계 생략)의 뜻. agent 검사의 내용 칸과 꼬리말의 등급에 같은 문구로 나간다
TIER0_NOTE = "판단할 심볼, 삭제된 심볼, 미사용 코드 후보가 없어 Claude 판단 단계를 실행하지 않음"
# 꼬리말에 등급 숫자 대신 나가는 뜻. 등급 정의는 precheck.py의 compute_tier
TIER_KO = {
    0: f"0({TIER0_NOTE})",
    1: "1(작은 변경이라 Claude 하나가 혼자 판단함)",
    2: "2(큰 변경이라 Claude가 검사 항목별 subagent(하위 agent)에 나눠 맡김)",
}
# 3-2(추가 코드), 4-1(수정 코드)이 판단하는 심볼의 변경 구분(classify.py의 change)
NEEDS_TEST_CHANGE = {"3-2": "added", "4-1": "modified"}


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
    extra = set(v) - required - {"suggestions"}
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

    sugg = v.get("suggestions", [])
    if not isinstance(sugg, list) or len(sugg) > MAX_SUGGESTIONS:
        return f"suggestions 는 {MAX_SUGGESTIONS}개 이하 배열이어야 함"
    for i, g in enumerate(sugg):
        where = f"suggestions[{i}]"
        allowed = {"file", "start_line", "line", "replacement", "comment"}
        if not isinstance(g, dict) or set(g) - allowed or not {"file", "line", "replacement", "comment"} <= set(g):
            return f"{where} 형식 오류 (file, line, replacement, comment 필수, start_line 선택)"
        if not isinstance(g["line"], int) or g["line"] < 1:
            return f"{where}.line 은 1 이상의 정수여야 함"
        start = g.get("start_line")
        if start is not None and (not isinstance(start, int) or not 1 <= start <= g["line"]):
            return f"{where}.start_line 은 1 이상 line 이하의 정수여야 함"
        for k in ("replacement", "comment"):
            if err := _check_str(g, k, MAX_STR[k], where, required=True):
                return err
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
            checks[cid]["detail"] = f"반려 사유의 {cid} 항목 {n}건"

    agent_error = None
    if precheck.get("verdict") == "reject":
        final = "reject"
        if verdict is None:
            agent_error = "스크립트 검사 반려로 agent 검사를 실행하지 않음(아래 검사 표에서 ➖ 미실행으로 표시)"
    elif precheck.get("tier") == 0 and verdict is None and not verdict_error:
        final = "pass"
        for cid in AGENT_CHECKS:
            checks[cid]["detail"] = TIER0_NOTE
    elif verdict is None or verdict_error:
        final = "error"
        if verdict_error:
            agent_error = f"agent 검사 결과를 사용할 수 없음(아래 검사 표에서 ➖ 결과 없음으로 표시): {verdict_error}"
        else:
            agent_error = (
                "agent 검사 결과 파일(verdict.json)이 없음(아래 검사 표에서 ➖ 결과 없음으로 표시). "
                "agent 검사가 실행되지 않았거나 실패함"
            )
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
                    "message": "agent 판정은 반려였지만 반려 조건(3-3 또는 4-2 실패)이 없어 통과로 판정함. 판정 규칙은 스크립트가 적용함",
                }
            )
    return {"final": final, "checks": checks, "reasons": reasons, "agent_error": agent_error}


# ---------- 렌더링 ----------


def needs_test_flags(cid: str, ri: dict, verdict: dict | None) -> list[bool]:
    """3-2, 4-1이 판단한 심볼마다 테스트가 필요한지(verdict.json의 `symbols[].needs_test`)를 모은다.

    Args:
        cid: 검사 ID. 3-2는 추가된 심볼, 4-1은 수정된 심볼을 모은다.
        ri: precheck가 만든 review-input. 심볼의 변경 구분(`symbols[].change`)을 여기서 찾는다.
        verdict: agent 출력. 없으면 빈 목록을 돌려준다.

    Returns:
        심볼별 `needs_test` 값. 3-2, 4-1이 아니거나 판단한 심볼이 없으면 빈 목록.
    """
    change = NEEDS_TEST_CHANGE.get(cid)
    if change is None or verdict is None:
        return []
    keys = {(s["file"], s["name"]) for s in ri.get("symbols", []) if s["change"] == change and not s["is_test"]}
    return [s["needs_test"] for s in verdict.get("symbols", []) if (s["file"], s["name"]) in keys]


def result_mark(cid: str, result: str, needs_test: list[bool] | None = None) -> str:
    """검사 표의 결과 칸 표시를 만든다.

    3-2, 4-1은 테스트 필요 여부를 판단하는 검사라, 판단한 심볼 중 하나라도 테스트가 필요하면 "필요함",
    모두 필요 없으면 "필요없음"으로 적는다. 판단한 심볼이 없으면 다른 검사와 같은 표시를 쓴다.
    반려로 이어지지 않는 검사(3-2, 4-1, 6, 7)의 fail은 반려로 이어지는 "실패"와 구분해 "보완 필요"로 적는다.

    Args:
        cid: 검사 ID.
        result: 검사 결과(pass, fail, n/a).
        needs_test: 3-2, 4-1이 판단한 심볼별 `needs_test` 값(`needs_test_flags`의 결과). 다른 검사는 비운다.

    Returns:
        결과 칸에 적을 표시.
    """
    if cid in NEEDS_TEST_CHANGE and result == "pass" and needs_test:
        return "필요함" if any(needs_test) else "필요없음"
    if result == "fail" and cid not in REJECT_CHECKS:
        return NOTE_FAIL_MARK
    return RESULT_MARK.get(result, result)


def _cell(s: object) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _test_cells(a: dict | None) -> tuple[str, str]:
    """변경 심볼 표의 "테스트 필요", "테스트 있음" 칸. agent 판단이 없으면 빈칸 대신 "미판단"으로 적는다."""
    if a is None:
        return "미판단", "미판단"
    need = "예" if a["needs_test"] else "아니오"
    if a.get("test_found") is None:
        found = "미확인" if a["needs_test"] else "해당 없음"
    else:
        found = "예" if a["test_found"] else "아니오"
    return need, found


def _evidence_cell(a: dict) -> str:
    """변경 심볼 표의 "근거" 칸. 테스트 필요 판단(reason)과 테스트 존재 확인(evidence)을 줄을 나눠 적는다.

    표 칸 문자 처리(`|`, 줄바꿈)는 호출부의 `_cell`이 한 번만 한다.
    """
    parts = []
    if a.get("reason"):
        parts.append(f"필요 판단: {a['reason']}")
    if a.get("evidence"):
        parts.append(f"존재 확인: {a['evidence']}")
    return "<br>".join(parts)


def _pytest_text(tests: dict) -> str:
    """꼬리말의 pytest 결과. 테스트를 실행했으면 개수를 함께 적는다."""
    outcome = tests.get("outcome")
    text = PYTEST_KO.get(outcome, str(outcome))
    if outcome in ("passed", "failed", "error") and "tests" in tests:
        text += (
            f"(테스트 {tests.get('tests', 0)}개 중 통과 {tests.get('passed', 0)}개, "
            f"실패 {tests.get('failed', 0)}개, 오류 {tests.get('errors', 0)}개, 건너뜀 {tests.get('skipped', 0)}개)"
        )
    return text


def _script_pass_detail(cid: str, ri: dict) -> str:
    """통과한 스크립트 검사의 내용 칸. 무엇을 검사했는지 근거가 되는 개수를 적는다."""
    files = ri.get("files", [])
    if cid == "1":
        py = sum(1 for f in files if f.get("is_python"))
        return f"변경 파일 {len(files)}개(Python 파일 {py}개) 수집"
    if cid == "2":
        return f"변경 심볼 {len(ri.get('symbols', []))}개(테스트 파일 포함) 분류, 파싱 오류 0건"
    if cid == "3-1":
        if not ri.get("lint", {}).get("ran", True):
            return "검사할 Python 파일 없음"
        return "변경 줄의 ruff check 위반 0건, ruff format 차이 0건"
    if cid == "5":
        return f"pytest {_pytest_text(ri.get('tests', {}))}"
    return ""


def _dead_code_loc(d: dict, ri: dict) -> str:
    """미사용 코드 항목의 파일:줄. 줄 번호는 스크립트가 찾은 후보(dead_code_candidates)에서 가져온다."""
    for c in ri.get("dead_code_candidates", {}).get("candidates", []):
        if c.get("file") == d["file"] and c.get("source") == "vulture" and c.get("name") == d["name"] and c.get("line"):
            return f"{d['file']}:{c['line']}"
    return d["file"]


def render_comment(
    pr: dict, ri: dict, merged: dict, verdict: dict | None, suggestions: tuple[list, list] | None = None
) -> str:
    """PR comment 본문(markdown)을 고정 구조로 만든다. 첫 줄은 갱신용 표식이다.

    판정 줄 다음에 판정의 이유(반려 사유, agent 검사 오류)를 먼저 적고, 검사 표와 세부 내용을 그 뒤에 적는다.
    """
    m = merged
    lines = [COMMENT_MARKER, f"## 코드 검수 결과: {VERDICT_KO[m['final']]}", ""]
    if m["reasons"]:
        lines += ["### 반려 사유" if m["final"] == "reject" else "### 판정 참고"]
        by_check: dict[str, list[str]] = {}
        for r in m["reasons"]:
            by_check.setdefault(r["check"], []).append(r["message"])
        for cid, messages in by_check.items():  # 검사 표보다 먼저 나오므로 검사 ID에 검사 이름을 붙인다
            if cid in CHECK_NAMES:
                lines.append(f"- {cid} {CHECK_NAMES[cid]}")
                lines += [f"  - {msg}" for msg in messages]
            else:
                lines += [f"- {msg}" for msg in messages]
        lines.append("")
    if m["agent_error"]:
        lines += [f"> {m['agent_error']}", ""]
    if ri["precheck"].get("escalate"):
        lines += ["> ⚠️ 크기 초과로 검사하지 못한 파일이 있어 사람의 확인 필요", ""]

    lines += ["| # | 검사 | 결과 | 내용 |", "|---|---|---|---|"]
    for cid, name in CHECK_NAMES.items():
        c = m["checks"][cid]
        mark, detail = result_mark(cid, c["result"], needs_test_flags(cid, ri, verdict)), c["detail"]
        # 대상이 없어 해당 없음인 것과 구분한다. 통과인데 verdict가 없으면 등급 0이라 Claude 단계를 생략한 경우다
        if verdict is None and cid in AGENT_CHECKS and m["final"] != "pass":
            mark, detail = ("➖ 결과 없음", "") if m["final"] == "error" else ("➖ 미실행", "")
        elif cid in SCRIPT_CHECKS and c["result"] == "pass" and not detail:
            detail = _script_pass_detail(cid, ri)
        lines.append(f"| {cid} | {name} | {mark} | {_cell(detail)} |")
    lines.append("")

    syms = [s for s in ri.get("symbols", []) if not s["is_test"]]
    if syms:
        agent_by_key = {(s["file"], s["name"]): s for s in (verdict or {}).get("symbols", [])}
        counts = ", ".join(f"{ko} {sum(1 for s in syms if s['change'] == code)}개" for code, ko in CHANGE_KO.items())
        lines += [
            f"### 변경 심볼(테스트 파일 제외): {counts}",
            "| 심볼 | 파일 | 종류 | 변경 | 테스트 필요 | 테스트 있음 | 근거 |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in syms:
            a = agent_by_key.get((s["file"], s["name"]))
            need, found = _test_cells(a)
            evid = "" if a is None else _evidence_cell(a)
            if a is None and s.get("docstring_only"):
                need, found, evid = "해당 없음", "해당 없음", "docstring만 변경 (판단 대상 아님)"
            loc = f"{s['file']}:{s['lines'][0]}" if s.get("lines") else s["file"]
            kind = KIND_KO.get(s["kind"], s["kind"])
            change = CHANGE_KO.get(s["change"], s["change"])
            lines.append(f"| `{s['name']}` | {loc} | {kind} | {change} | {need} | {found} | {_cell(evid)} |")
        lines.append("")

    dead = [d for d in (verdict or {}).get("dead_code", []) if d["confirmed"]]
    if dead:
        lines += ["### 미사용 코드 (판정에 영향 없음)"]
        for d in dead:
            lines.append(f"- `{d['name']}` ({_dead_code_loc(d, ri)}): {d.get('reason', '')}")
        lines.append("")

    design = (verdict or {}).get("design", [])
    if design:
        lines += ["### 설계 의견 (판정에 영향 없음)"]
        for d in design:
            loc = f"{d['file']}:{d['line']}" if d.get("line") else d["file"]
            lines.append(f"- {loc}: {d['comment']}")
        lines.append("")

    kept, dropped = suggestions or ([], [])
    if kept or dropped:
        lines += [f"### 수정 제안 ({len(kept)}건, 코드 줄에 inline으로 달았음)"]
        for g in kept:
            lines.append(f"- {g['file']}:{g.get('start_line') or g['line']}-{g['line']}: {g['comment']}")
        if dropped:
            lines.append(f"- 변경 줄 밖이라 달지 않은 제안 {len(dropped)}건")
        lines.append("")

    reqs = (verdict or {}).get("requests", [])
    if reqs:
        lines += ["### 요청"]
        for i, r in enumerate(reqs, 1):
            lines.append(f"{i}. {r}")
        lines.append("")

    pre = ri["precheck"]["verdict"]
    lint = ri["lint"].get("config")
    tier = ri["precheck"].get("tier")
    foot = [f"{SCRIPT_STAGE} 판정: {PRECHECK_KO.get(pre, pre)}"]
    if pre == "continue" and tier is not None:  # 스크립트 검사 반려면 등급과 관계없이 Claude 단계를 실행하지 않는다
        foot.append(f"PR 크기 등급: {TIER_KO.get(tier, tier)}")
    foot += [f"ruff 설정: {LINT_CONFIG_KO.get(lint, lint)}", f"pytest: {_pytest_text(ri['tests'])}"]
    if url := run_url():
        foot.append(f"실행 로그: {url}")
    lines += ["---", " · ".join(foot)]
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
    """review/pass, review/reject, review/override 라벨을 만들고(있으면 유지) 최종 판정의 것만 남긴다."""
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


def select_suggestions(verdict: dict | None, files: list[dict]) -> tuple[list[dict], list[dict]]:
    """변경 줄 안에 있는 제안만 남긴다. GitHub는 diff 밖 줄에 inline comment를 달 수 없다. (남긴 것, 버린 것)을 돌려준다."""
    changed = {f["path"]: f["changed_lines"] for f in files}
    kept: list[dict] = []
    dropped: list[dict] = []
    for g in (verdict or {}).get("suggestions", []):
        start = g.get("start_line") or g["line"]
        ranges = changed.get(g["file"], [])
        inside = any(a <= start and g["line"] <= b for a, b in ranges)
        (kept if inside else dropped).append(g)
    return kept, dropped


def suggestion_comment(g: dict) -> dict:
    """PR review API의 inline comment 한 개."""
    body = f"{g['comment']}\n\n```suggestion\n{g['replacement'].rstrip(chr(10))}\n```\n{SUGGESTION_MARK}"
    c = {"path": g["file"], "line": g["line"], "side": "RIGHT", "body": body}
    if g.get("start_line") and g["start_line"] < g["line"]:
        c["start_line"] = g["start_line"]
        c["start_side"] = "RIGHT"
    return c


def post_suggestions(slug: str, number: int, head_sha: str, kept: list[dict], repo: Path) -> int:
    """수정 제안을 PR review(COMMENT) 하나로 단다. 같은 위치, 같은 내용은 다시 달지 않는다. 단 개수를 돌려준다."""
    if not kept:
        return 0
    existing = gh_json(["api", f"repos/{slug}/pulls/{number}/comments", "--paginate"], cwd=repo) or []
    seen = {(c.get("path"), c.get("line"), c.get("body")) for c in existing}
    comments = [c for c in map(suggestion_comment, kept) if (c["path"], c["line"], c["body"]) not in seen]
    if not comments:
        return 0
    payload = {"commit_id": head_sha, "event": "COMMENT", "body": "everex-review 수정 제안", "comments": comments}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump(payload, tf, ensure_ascii=False)
        path = tf.name
    try:
        gh_json(["api", "-X", "POST", f"repos/{slug}/pulls/{number}/reviews", "--input", path], cwd=repo)
    finally:
        os.unlink(path)
    return len(comments)


def override_body(previous: str | None, login: str, head_sha: str) -> str:
    """기존 검수 comment 앞에 override 표시를 붙인다. 이전 override 표시는 지운다 (여러 번 해도 하나만 남는다)."""
    banner = [
        OVERRIDE_MARK,
        f"> ✋ 리뷰어 @{login} 이(가) commit `{head_sha[:7]}`의 검수 판정을 override함"
        "(review/override 라벨로 판정을 통과로 바꿈). 아래는 override 전 검수 결과임.",
        "",
    ]
    rest = (previous or "").split("\n")
    if rest and rest[0] == COMMENT_MARKER:
        rest = rest[1:]
    if rest and rest[0] == OVERRIDE_MARK:
        rest = rest[3:]
    if not "".join(rest).strip():
        rest = ["(이전 검수 결과 없음)"]
    return "\n".join([COMMENT_MARKER, *banner, *rest]).rstrip("\n") + "\n"


def find_review_comment(slug: str, number: int, repo: Path) -> dict | None:
    """표식으로 시작하는 검수 comment. 없으면 None."""
    comments = gh_json(["api", f"repos/{slug}/issues/{number}/comments", "--paginate"], cwd=repo) or []
    return next((c for c in comments if (c.get("body") or "").startswith(COMMENT_MARKER)), None)


def apply_override(slug: str, number: int, login: str, head_sha: str, repo: Path) -> None:
    """override 표시를 comment에 붙이고 라벨을 review/override 로 바꾼다."""
    prev = find_review_comment(slug, number, repo)
    upsert_comment(slug, number, override_body(prev["body"] if prev else None, login, head_sha), repo)
    set_labels(slug, number, "override", repo)


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
    ap.add_argument(
        "--override",
        default="",
        help="이 리뷰어의 override를 반영한다 (검수 대신). PR 정보는 GITHUB_EVENT_PATH 에서 읽는다",
    )
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    work = resolve_work(repo, a.work)
    ctx, out = work / "ctx", work / "out"
    gate = str(a.gate).lower() == "true"

    if a.override:
        from collect import pr_meta_from_event

        pr = pr_meta_from_event(Path(os.environ["GITHUB_EVENT_PATH"]))
        slug = a.repo_slug or pr["repo_slug"]
        apply_override(slug, pr["number"], a.override, pr["head_sha"], repo)
        log("apply", f"override 반영: @{a.override}, commit {pr['head_sha'][:7]}, label review/override")
        summ = Summary(a.summary)
        summ.add("| PR | 판정 | 비고 |")
        summ.add("|---|---|---|")
        summ.add(f"| #{pr['number']} | {VERDICT_KO['override']} | @{a.override} |")
        summ.flush()
        return

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
    sugg = select_suggestions(verdict, ri.get("files", []))
    body = render_comment(pr, ri, merged, verdict, sugg)
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
        try:
            n = post_suggestions(slug, number, pr.get("head_sha") or "", sugg[0], repo)
            if n:
                log("apply", f"수정 제안 {n}건을 inline으로 닮")
        except RuntimeError as e:  # 제안은 부가 기능이라 실패해도 판정 반영은 유지한다
            log("apply", f"수정 제안 게시 실패: {e}")
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
