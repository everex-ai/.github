from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import apply
import pytest
from common import CHECK_NAMES, COMMENT_MARKER
from schema_check import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "review-verdict.json").read_text())

GOOD = {
    "verdict": "reject",
    "checks": [
        {"id": "3-2", "result": "pass", "detail": "d"},
        {"id": "3-3", "result": "fail", "detail": "scale 테스트 없음"},
        {"id": "4-1", "result": "pass"},
        {"id": "4-2", "result": "pass"},
        {"id": "6", "result": "fail", "detail": "os"},
        {"id": "7", "result": "n/a"},
    ],
    "symbols": [
        {
            "name": "scale",
            "file": "calc/ops.py",
            "needs_test": True,
            "reason": "공개 함수",
            "test_found": False,
            "evidence": "",
        },
        {"name": "PRECISION", "file": "calc/ops.py", "needs_test": False, "reason": "상수"},
    ],
    "dead_code": [
        {"name": "os", "file": "calc/ops.py", "confirmed": True, "reason": "미사용 import"},
        {"name": "scale", "file": "calc/ops.py", "confirmed": False, "reason": "공개 API"},
    ],
    "design": [{"file": "calc/ops.py", "line": 34, "comment": "반올림은 호출부 책임"}],
    "requests": ["scale 테스트 추가"],
}


def test_validate_accepts_good():
    assert apply.validate_verdict(GOOD) is None
    assert validate(GOOD, SCHEMA) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.pop("requests"),
        lambda v: v.update(extra=1),
        lambda v: v.update(verdict="maybe"),
        lambda v: v["checks"].append({"id": "3-1", "result": "pass"}),
        lambda v: v["checks"].append({"id": "9", "result": "pass"}),
        lambda v: v["checks"][0].update(result="ok"),
        lambda v: v["checks"][0].update(detail="x" * 501),
        lambda v: v["symbols"][0].update(needs_test="yes"),
        lambda v: v["symbols"][0].pop("file"),
        lambda v: v["dead_code"][0].update(confirmed=1),
        lambda v: v["design"][0].update(line="34"),
        lambda v: v["design"][0].pop("comment"),
        lambda v: v["requests"].append(123),
    ],
)
def test_validate_rejects(mutate):
    v = copy.deepcopy(GOOD)
    mutate(v)
    assert apply.validate_verdict(v) is not None
    assert validate(v, SCHEMA) != []


def test_validate_rejects_duplicate_check_id():
    v = copy.deepcopy(GOOD)
    v["checks"].append({"id": "7", "result": "pass"})
    assert "중복" in (apply.validate_verdict(v) or "")


def test_validate_non_object():
    assert apply.validate_verdict([]) is not None
    assert apply.validate_verdict(None) is not None


def _pre(verdict: str = "continue", checks: dict | None = None, reasons: list | None = None) -> dict:
    return {
        "verdict": verdict,
        "checks": checks or {"1": "pass", "2": "pass", "3-1": "pass", "5": "pass"},
        "reasons": reasons or [],
        "escalate": False,
        "notes": [],
    }


def test_merge_matrix():
    m = apply.merge(
        _pre("reject", {"1": "pass", "2": "pass", "3-1": "fail", "5": "pass"}, [{"check": "3-1", "message": "x"}]),
        None,
        None,
    )
    assert m["final"] == "reject" and m["agent_error"]
    assert m["checks"]["3-1"] == {"result": "fail", "detail": "반려 사유의 3-1 항목 1건"}

    m = apply.merge(_pre(), None, None)
    assert m["final"] == "error" and "없음" in m["agent_error"]

    m = apply.merge(_pre(), None, "checks[0].id 잘못됨")
    assert m["final"] == "error" and m["agent_error"].endswith("표시): checks[0].id 잘못됨")

    m = apply.merge(_pre(), GOOD, None)
    assert m["final"] == "reject" and [r["check"] for r in m["reasons"]] == ["3-3"]
    assert m["checks"]["6"]["result"] == "fail"  # 6은 comment 항목이라 판정에 영향 없음

    ok = copy.deepcopy(GOOD)
    ok["verdict"] = "pass"
    ok["checks"][1] = {"id": "3-3", "result": "pass"}
    m = apply.merge(_pre(), ok, None)
    assert m["final"] == "pass" and m["reasons"] == []

    # agent가 reject를 냈지만 3-3/4-2 실패가 없으면 스크립트 규칙대로 pass
    ok["verdict"] = "reject"
    m = apply.merge(_pre(), ok, None)
    assert m["final"] == "pass" and m["reasons"][0]["check"] == "-"
    assert "통과로 판정함" in m["reasons"][0]["message"]

    # precheck reject + agent 출력이 함께 있으면 둘 다 반영하되 판정은 reject
    m = apply.merge(
        _pre("reject", {"1": "pass", "2": "pass", "3-1": "pass", "5": "fail"}, [{"check": "5", "message": "t"}]),
        ok,
        None,
    )
    assert m["final"] == "reject" and m["checks"]["3-2"]["result"] == "pass"


def _ri(precheck: dict) -> dict:
    return {
        "pr": {"number": 12, "repo_slug": "org/repo"},
        "files": [],
        "symbol_summary": {"added": 1, "modified": 0, "removed": 0},
        "symbols": [
            {
                "file": "calc/ops.py",
                "name": "scale",
                "kind": "function",
                "change": "added",
                "lines": [55, 56],
                "is_test": False,
            },
            {
                "file": "tests/test_ops.py",
                "name": "test_x",
                "kind": "function",
                "change": "added",
                "lines": [1, 2],
                "is_test": True,
            },
        ],
        "lint": {"config": "default"},
        "format": {},
        "tests": {"outcome": "passed"},
        "test_candidates": {},
        "dead_code_candidates": {"skipped": False, "candidates": []},
        "precheck": precheck,
    }


def test_render_comment_structure():
    ri = _ri(_pre())
    merged = apply.merge(ri["precheck"], GOOD, None)
    body = apply.render_comment(ri["pr"], ri, merged, GOOD)
    assert body.startswith(COMMENT_MARKER + "\n## 코드 검수 결과: 반려")
    for cid, name in CHECK_NAMES.items():
        assert f"| {cid} | {name} |" in body
    assert "| 3-3 | 테스트 존재 여부 (추가 코드) | ❌ 실패 | scale 테스트 없음 |" in body  # 반려로 이어지는 검사
    assert "| 6 | 미사용 코드 | ❌ 보완 필요 | os |" in body  # 판정에 영향이 없는 검사
    v = copy.deepcopy(GOOD)
    v["checks"][0] = {"id": "3-2", "result": "fail", "detail": "x"}
    body = apply.render_comment(ri["pr"], ri, apply.merge(ri["precheck"], v, None), v)
    assert "| 3-2 | 테스트 필요 여부 (추가 코드) | ❌ 보완 필요 | x |" in body
    assert (
        "| 1 | 변경 범위 수집 | ✅ 통과 | 변경 파일 0개(Python 파일 0개) 수집 |" in body
    )  # 통과한 스크립트 검사의 근거
    assert "### 반려 사유\n- 3-3 테스트 존재 여부 (추가 코드)\n  - scale 테스트 없음\n" in body
    assert body.index("### 반려 사유") < body.index("| # | 검사 |")  # 판정의 이유를 검사 표보다 먼저 적음
    assert "### 변경 심볼(테스트 파일 제외): 추가 1개, 수정 0개, 삭제 0개" in body
    assert "| `scale` | calc/ops.py:55 | 함수 | 추가 | 예 | 아니오 | 필요 판단: 공개 함수 |" in body
    assert "test_x" not in body  # 테스트 파일 심볼은 표에 넣지 않음
    assert (
        "- `os` (calc/ops.py): 미사용 import" in body
        and "`scale` (calc/ops.py)" not in body.split("### 미사용 코드")[1].split("###")[0]
    )
    assert "### 미사용 코드 (판정에 영향 없음)" in body and "### 설계 의견 (판정에 영향 없음)" in body
    assert "- calc/ops.py:34: 반올림은 호출부 책임" in body
    assert "1. scale 테스트 추가" in body
    assert body.endswith(
        "\n---\n스크립트 검사(검사 표의 1, 2, 3-1, 5번) 판정: 통과"
        " · ruff 설정: 기본 규칙(everex-ai/.github의 ruff-default.toml)"
        " · pytest: 통과\n"
    )


def test_render_comment_without_agent_symbols_and_pytest_counts():
    ri = _ri(_pre())
    ri["tests"] = {"outcome": "failed", "tests": 5, "passed": 3, "failed": 1, "errors": 0, "skipped": 1}
    ri["lint"] = {"config": "target"}
    v = {**GOOD, "symbols": []}
    body = apply.render_comment(ri["pr"], ri, apply.merge(ri["precheck"], v, None), v)
    assert "| `scale` | calc/ops.py:55 | 함수 | 추가 | 미판단 | 미판단 |  |" in body
    assert "ruff 설정: 대상 repo의 ruff 설정" in body
    assert "pytest: 실패(테스트 5개 중 통과 3개, 실패 1개, 오류 0개, 건너뜀 1개)" in body


def test_render_comment_pass_with_agent_reject_note():
    ri = _ri(_pre())
    v = copy.deepcopy(GOOD)
    v["checks"][1] = {"id": "3-3", "result": "pass"}
    body = apply.render_comment(ri["pr"], ri, apply.merge(ri["precheck"], v, None), v)
    assert "## 코드 검수 결과: 통과" in body and "### 판정 참고\n- agent 판정은 반려였지만" in body
    assert "### 반려 사유" not in body


def test_render_comment_error_and_escalate():
    pre = _pre()
    pre["escalate"] = True
    ri = _ri(pre)
    merged = apply.merge(pre, None, None)
    body = apply.render_comment(ri["pr"], ri, merged, None)
    assert "## 코드 검수 결과: 오류" in body and "⚠️" in body
    assert "> agent 검사 결과 파일(verdict.json)이 없음(아래 검사 표에서 ➖ 결과 없음으로 표시)" in body
    assert "| 3-2 | 테스트 필요 여부 (추가 코드) | ➖ 결과 없음 |  |" in body  # 대상이 없는 "해당 없음"과 구분


def _run_apply(work: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "review" / "apply.py"),
            "--repo",
            str(work.parent),
            "--work",
            str(work),
            "--dry-run",
            *args,
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "GITHUB_STEP_SUMMARY": ""},
    )


def test_cli_dry_run_writes_comment_and_gate_exit(tmp_path: Path):
    work = tmp_path / ".everex-review"
    (work / "ctx").mkdir(parents=True)
    (work / "ctx" / "review-input.json").write_text(
        json.dumps(
            _ri(
                _pre(
                    "reject", {"1": "pass", "2": "pass", "3-1": "fail", "5": "pass"}, [{"check": "3-1", "message": "x"}]
                )
            )
        )
    )
    r = _run_apply(work, "--gate", "false")
    assert r.returncode == 0, r.stderr
    assert (work / "out" / "review-comment.md").read_text().startswith(COMMENT_MARKER)
    assert "| #12 | 반려 |" in r.stdout
    r = _run_apply(work, "--gate", "true")
    assert r.returncode == 1

    (work / "ctx" / "review-input.json").write_text(json.dumps(_ri(_pre())))
    (work / "out" / "verdict.json").write_text(
        json.dumps({**GOOD, "verdict": "pass", "checks": [{"id": "3-3", "result": "pass"}]})
    )
    r = _run_apply(work, "--gate", "true")
    assert r.returncode == 0 and "## 코드 검수 결과: 통과" in r.stdout

    (work / "out" / "verdict.json").write_text("{broken")
    r = _run_apply(work, "--gate", "true")
    assert r.returncode == 1 and "오류" in r.stdout


def test_require_clean_turns_dirty_tree_into_error(fixture_repo: Path):
    work = fixture_repo / ".everex-review"
    (work / "ctx").mkdir(parents=True, exist_ok=True)
    (work / "ctx" / "review-input.json").write_text(json.dumps(_ri(_pre())))
    (work / "out").mkdir(exist_ok=True)
    (work / "out" / "verdict.json").write_text(
        json.dumps({**GOOD, "verdict": "pass", "checks": [{"id": "3-3", "result": "pass"}]})
    )
    assert apply.dirty_files(fixture_repo, work) == []
    r = _run_apply(work, "--gate", "true", "--require-clean")
    assert r.returncode == 0, r.stderr
    # Claude 이전부터 있던 변경(기준선에 있는 것)은 문제 삼지 않는다
    pre = fixture_repo / "pytest-artifact.txt"
    pre.write_text("left by tests\n")
    from common import tree_state, write_json

    write_json(work / "ctx" / "tree-baseline.json", tree_state(fixture_repo, work))
    assert apply.dirty_files(fixture_repo, work) == []
    pre.write_text("changed by agent\n")
    assert apply.dirty_files(fixture_repo, work) == ["pytest-artifact.txt"]
    pre.unlink()
    assert apply.dirty_files(fixture_repo, work) == ["pytest-artifact.txt"]  # 기준선에 있던 파일이 사라짐
    (work / "ctx" / "tree-baseline.json").unlink()

    stray = fixture_repo / "calc" / "stray.py"
    stray.write_text("x = 1\n")
    try:
        assert apply.dirty_files(fixture_repo, work) == ["calc/stray.py"]
        r = _run_apply(work, "--gate", "true", "--require-clean")
        assert r.returncode == 1 and "agent가 대상 repo 파일을 변경함" in r.stdout
    finally:
        stray.unlink()


def test_render_comment_dead_code_line_from_candidates():
    ri = _ri(_pre())
    ri["dead_code_candidates"]["candidates"] = [
        {"file": "calc/ops.py", "line": 5, "name": "F401", "source": "ruff"},
        {"file": "calc/ops.py", "line": 3, "name": "os", "source": "vulture"},
    ]
    body = apply.render_comment(ri["pr"], ri, apply.merge(ri["precheck"], GOOD, None), GOOD)
    assert "- `os` (calc/ops.py:3): 미사용 import" in body


def test_render_comment_precheck_reject_marks_agent_checks_not_run():
    pre = _pre("reject", {"1": "pass", "2": "pass", "3-1": "fail", "5": "pass"}, [{"check": "3-1", "message": "x"}])
    ri = _ri(pre)
    body = apply.render_comment(ri["pr"], ri, apply.merge(pre, None, None), None)
    assert "| 3-3 | 테스트 존재 여부 (추가 코드) | ➖ 미실행 |  |" in body
    assert "| `scale` | calc/ops.py:55 | 함수 | 추가 | 미판단 | 미판단 |  |" in body


def test_script_pass_detail_and_test_cells():
    ri = _ri(_pre())
    ri["files"] = [{"is_python": True}, {"is_python": False}]
    ri["lint"] = {"config": "default", "ran": True}
    ri["tests"] = {"outcome": "passed", "tests": 2, "passed": 2, "failed": 0, "errors": 0, "skipped": 0}
    assert apply._script_pass_detail("1", ri) == "변경 파일 2개(Python 파일 1개) 수집"
    assert apply._script_pass_detail("2", ri) == "변경 심볼 2개(테스트 파일 포함) 분류, 파싱 오류 0건"
    assert apply._script_pass_detail("3-1", ri) == "변경 줄의 ruff check 위반 0건, ruff format 차이 0건"
    assert apply._script_pass_detail("5", ri).startswith("pytest 통과(테스트 2개 중 통과 2개")
    ri["lint"]["ran"] = False
    assert apply._script_pass_detail("3-1", ri) == "검사할 Python 파일 없음"
    assert apply._test_cells({"needs_test": True, "test_found": None}) == ("예", "미확인")
    assert apply._test_cells({"needs_test": False, "test_found": None}) == ("아니오", "해당 없음")
    assert apply._test_cells(None) == ("미판단", "미판단")


def test_render_comment_groups_reasons_by_check_name():
    reasons = [{"check": "3-1", "message": "a.py:1 D103"}, {"check": "3-1", "message": "a.py:2 ANN201"}]
    pre = _pre("reject", {"1": "pass", "2": "pass", "3-1": "fail", "5": "pass"}, reasons)
    ri = _ri(pre)
    body = apply.render_comment(ri["pr"], ri, apply.merge(pre, None, None), None)
    assert (
        "### 반려 사유\n- 3-1 형식, 타입 힌트, docstring 규칙 (추가 코드)\n  - a.py:1 D103\n  - a.py:2 ANN201\n" in body
    )
    assert "> 스크립트 검사 반려로 agent 검사를 실행하지 않음(아래 검사 표에서 ➖ 미실행으로 표시)" in body
    assert "| 3-1 | 형식, 타입 힌트, docstring 규칙 (추가 코드) | ❌ 실패 | 반려 사유의 3-1 항목 2건 |" in body


def test_evidence_cell_separates_need_and_found():
    assert apply._evidence_cell({"reason": "공개 함수", "evidence": "tests/t.py:3 test_a"}) == (
        "필요 판단: 공개 함수<br>존재 확인: tests/t.py:3 test_a"
    )
    assert (
        apply._evidence_cell({"reason": "", "evidence": "a|b"}) == "존재 확인: a|b"
    )  # 표 칸 처리는 _cell이 한 번만 함
