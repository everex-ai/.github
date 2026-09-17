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
    assert m["final"] == "reject" and m["checks"]["3-1"] == {"result": "fail", "detail": "1건"} and m["agent_error"]

    m = apply.merge(_pre(), None, None)
    assert m["final"] == "error" and "없음" in m["agent_error"]

    m = apply.merge(_pre(), None, "checks[0].id 잘못됨")
    assert m["final"] == "error" and m["agent_error"] == "checks[0].id 잘못됨"

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
    assert "### 반려 사유\n- [3-3] scale 테스트 없음" in body
    assert "| `scale` | calc/ops.py:55 | function | added | 예 | 아니오 | 공개 함수 |" in body
    assert "test_x" not in body  # 테스트 파일 심볼은 표에 넣지 않음
    assert (
        "- `os` (calc/ops.py): 미사용 import" in body
        and "`scale` (calc/ops.py)" not in body.split("### 미사용 코드")[1].split("###")[0]
    )
    assert "- calc/ops.py:34: 반올림은 호출부 책임" in body
    assert "1. scale 테스트 추가" in body


def test_render_comment_error_and_escalate():
    pre = _pre()
    pre["escalate"] = True
    ri = _ri(pre)
    merged = apply.merge(pre, None, None)
    body = apply.render_comment(ri["pr"], ri, merged, None)
    assert "## 코드 검수 결과: 오류" in body and "> verdict.json 이 없음" in body and "⚠️" in body


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
