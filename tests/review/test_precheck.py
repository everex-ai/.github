from __future__ import annotations

import json
from pathlib import Path

import precheck
import pytest
from common import Result
from conftest import requires_ruff
from schema_check import validate

ROOT = Path(__file__).resolve().parents[2]


def test_parse_ruff_json_and_filter_changed(tmp_path: Path):
    raw = json.dumps(
        [
            {
                "filename": str(tmp_path / "a.py"),
                "location": {"row": 5, "column": 1},
                "code": "D103",
                "message": "m",
                "url": "u",
            },
            {
                "filename": str(tmp_path / "a.py"),
                "location": {"row": 50, "column": 1},
                "code": "ANN001",
                "message": "old",
            },
            {
                "filename": str(tmp_path / "a.py"),
                "location": {"row": 7, "column": 1},
                "code": None,
                "message": "SyntaxError",
            },
            {"filename": str(tmp_path / "b.py"), "location": {"row": 1, "column": 1}, "code": "E999", "message": "x"},
        ]
    )
    diags = precheck.parse_ruff_json(raw, tmp_path)
    assert diags[0]["file"] == "a.py" and diags[0]["line"] == 5
    kept = precheck.filter_changed(diags, {"a.py": [[1, 10]]})
    assert [(d["line"], d["code"]) for d in kept] == [(5, "D103"), (7, None)]
    assert precheck.parse_ruff_json("not json", tmp_path) == []


def test_parse_format_diff_two_files(tmp_path: Path):
    text = (
        "--- a.py\n+++ a.py\n@@ -3,2 +3,2 @@\n-def f(x,y):\n+def f(x, y):\n context\n"
        "@@ -20 +20 @@\n-x=1\n+x = 1\n"
        "--- sub/b.py\n+++ sub/b.py\n@@ -1,4 +1,3 @@\n-a\n"
    )
    hunks = precheck.parse_format_diff(text, tmp_path)
    assert [h["lines"] for h in hunks["a.py"]] == [[3, 4], [20, 20]]
    assert hunks["a.py"][0]["diff"].startswith("@@ -3,2")
    assert hunks["sub/b.py"][0]["lines"] == [1, 4]


def test_parse_junit(tmp_path: Path):
    xml = tmp_path / "j.xml"
    xml.write_text(
        '<testsuites><testsuite name="pytest" errors="1" failures="1" skipped="1" tests="4">'
        '<testcase classname="t.a" name="ok"/>'
        '<testcase classname="t.a" name="bad"><failure message="assert 1 == 2">trace</failure></testcase>'
        '<testcase classname="t.b" name="broken"><error message="ImportError">trace</error></testcase>'
        '<testcase classname="t.b" name="skip"><skipped message="no"/></testcase>'
        "</testsuite></testsuites>"
    )
    r = precheck.parse_junit(xml)
    assert (r["tests"], r["passed"], r["failed"], r["errors"], r["skipped"]) == (4, 1, 1, 1, 1)
    assert r["failures"][0] == {"id": "t.a::bad", "type": "failure", "message": "assert 1 == 2"}
    assert r["failures"][1]["type"] == "error"
    assert precheck.parse_junit(tmp_path / "missing.xml")["tests"] == 0
    xml.write_text("<broken")
    assert precheck.parse_junit(xml)["tests"] == 0


@pytest.mark.parametrize(
    ("code", "errors", "err", "timed_out", "expected"),
    [
        (0, 0, "", False, "passed"),
        (1, 0, "", False, "failed"),
        (5, 0, "", False, "none"),
        (2, 0, "", False, "error"),
        (4, 0, "", False, "error"),
        (0, 1, "", False, "error"),
        (1, 0, "No module named pytest", False, "error"),
        (0, 0, "", True, "error"),
    ],
)
def test_outcome_from(code, errors, err, timed_out, expected):
    junit = {"errors": errors}
    assert precheck.outcome_from(code, junit, Result(code=code, out="", err=err, timed_out=timed_out)) == expected


def test_parse_vulture():
    text = (
        "calc/ops.py:59: unused function 'unused_helper' (60% confidence)\n"
        "calc/ops.py:5: unused import 'os' (90% confidence)\n"
        "calc/x.py:10: unreachable code after 'return' (100% confidence, 3 lines)\n"
        "garbage line\n"
    )
    items = precheck.parse_vulture(text)
    assert [(i["name"], i["kind"], i["confidence"], i["line"]) for i in items] == [
        ("unused_helper", "function", 60, 59),
        ("os", "import", 90, 5),
    ]


@pytest.mark.parametrize(
    ("path", "mod"),
    [("calc/ops.py", "calc.ops"), ("src/pkg/mod.py", "pkg.mod"), ("pkg/__init__.py", "pkg"), ("top.py", "top")],
)
def test_module_name(path, mod):
    assert precheck.module_name(path) == mod


def test_find_test_candidates(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ops.py").write_text(
        "from calc.ops import add\nfrom calc import ops\n# add is mentioned in a comment\n\ndef test_add():\n    assert add(1, 1) == 2\n    assert ops.Calc().go() == 1\n"
    )
    symbols = [
        {"file": "calc/ops.py", "name": "add", "change": "added", "is_test": False},
        {"file": "calc/ops.py", "name": "Calc.go", "change": "modified", "is_test": False},
        {"file": "calc/ops.py", "name": "nope", "change": "added", "is_test": False},
        {"file": "calc/ops.py", "name": "gone", "change": "removed", "is_test": False},
        {"file": "tests/test_ops.py", "name": "test_add", "change": "added", "is_test": True},
    ]
    c = precheck.find_test_candidates(tmp_path, symbols, ["tests/test_ops.py"], ["tests/test_ops.py"])
    assert set(c) == {"calc/ops.py::add", "calc/ops.py::Calc.go", "calc/ops.py::nope"}
    add = c["calc/ops.py::add"]
    assert [h["line"] for h in add["name_hits"]] == [1, 6]  # 주석 줄(3) 제외, test_add(5)는 단어 경계로 제외
    assert add["direct"] is True and add["weak"] is True
    assert len(add["import_hits"]) == 2
    go = c["calc/ops.py::Calc.go"]
    assert go["name_hits"][0]["line"] == 7 and go["name_hits"][0]["mentions_class"] is True
    assert c["calc/ops.py::nope"]["name_hits"] == []


def test_target_has_ruff_config(tmp_path: Path):
    assert precheck.target_has_ruff_config(tmp_path) is False
    (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 100\n")
    assert precheck.target_has_ruff_config(tmp_path) is False
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    assert precheck.target_has_ruff_config(tmp_path) is True
    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "ruff.toml").write_text("")
    assert precheck.target_has_ruff_config(tmp_path) is True


def _base_inputs(work: Path):
    pr = {"number": None, "base_sha": "a", "head_sha": "b", "merge_base": "a", "repo_path": str(work.parent)}
    files = [
        {
            "path": "a.py",
            "old_path": None,
            "status": "M",
            "similarity": None,
            "is_binary": False,
            "additions": 1,
            "deletions": 0,
            "is_python": True,
            "is_test": False,
            "changed_lines": [[1, 2]],
            "removed_lines": [],
            "too_large": False,
            "before_path": None,
            "after_path": None,
            "diff_path": None,
        }
    ]
    symbols = {
        "symbols": [],
        "parse_errors": [],
        "notes": [],
        "summary": {"added": 0, "modified": 0, "removed": 0, "kind_of_change": "none"},
    }
    lint = {"config": "default", "ran": True, "violations": [], "pass": True}
    fmt = {"config": "default", "ran": True, "violations": [], "pass": True}
    tests = {
        "outcome": "passed",
        "exit_code": 0,
        "changed_test_files": [],
        "output_tail": "",
        "tests": 1,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "failures": [],
    }
    dead = {"skipped": True, "note": "n", "candidates": []}
    return pr, files, symbols, lint, fmt, tests, {}, dead


def test_build_review_input_verdicts(tmp_path: Path):
    pr, files, symbols, lint, fmt, tests, cands, dead = _base_inputs(tmp_path)
    ri = precheck.build_review_input(tmp_path, pr, files, symbols, lint, fmt, tests, cands, dead)
    assert ri["precheck"]["verdict"] == "continue" and ri["precheck"]["checks"] == {
        "1": "pass",
        "2": "pass",
        "3-1": "pass",
        "5": "pass",
    }

    symbols["parse_errors"] = [{"file": "a.py", "line": 3, "message": "invalid syntax"}]
    ri = precheck.build_review_input(tmp_path, pr, files, symbols, lint, fmt, tests, cands, dead)
    assert ri["precheck"]["verdict"] == "reject" and ri["precheck"]["checks"]["2"] == "fail"
    symbols["parse_errors"] = []

    lint2 = {
        **lint,
        "violations": [{"file": "a.py", "line": 1, "code": "D103", "message": "m", "url": None}],
        "pass": False,
    }
    ri = precheck.build_review_input(tmp_path, pr, files, symbols, lint2, fmt, tests, cands, dead)
    assert ri["precheck"]["verdict"] == "reject" and ri["precheck"]["reasons"][0]["check"] == "3-1"

    tests_none = {**tests, "outcome": "none", "exit_code": 5, "tests": 0, "passed": 0}
    ri = precheck.build_review_input(tmp_path, pr, files, symbols, lint, fmt, tests_none, cands, dead)
    assert ri["precheck"]["verdict"] == "continue" and ri["precheck"]["checks"]["5"] == "n/a"

    tests_fail = {
        **tests,
        "outcome": "failed",
        "exit_code": 1,
        "failed": 1,
        "failures": [{"id": "t::x", "type": "failure", "message": "boom"}],
    }
    ri = precheck.build_review_input(tmp_path, pr, files, symbols, lint, fmt, tests_fail, cands, dead)
    assert ri["precheck"]["verdict"] == "reject" and ri["precheck"]["reasons"][0] == {
        "check": "5",
        "message": "t::x: boom",
    }

    files[0]["too_large"] = True
    ri = precheck.build_review_input(tmp_path, pr, files, symbols, lint, fmt, tests, cands, dead)
    assert ri["precheck"]["escalate"] is True


def test_review_input_diff_size_guard(tmp_path: Path):
    pr, files, symbols, lint, fmt, tests, cands, dead = _base_inputs(tmp_path)
    (tmp_path / "ctx" / "diff").mkdir(parents=True)
    (tmp_path / "ctx" / "diff" / "a.py.diff").write_text("x" * (precheck.MAX_DIFF_BYTES + 1))
    files[0]["diff_path"] = "ctx/diff/a.py.diff"
    ri = precheck.build_review_input(tmp_path, pr, files, symbols, lint, fmt, tests, cands, dead)
    assert (
        ri["files"][0]["diff"] is None and ri["files"][0]["diff_omitted"] is True and ri["precheck"]["escalate"] is True
    )


@requires_ruff
def test_precheck_on_fixture(collected: Path):
    """실제 ruff, pytest, vulture(있으면)로 fixture PR을 검사한다. 판정은 3-1 실패로 reject여야 한다."""
    repo = collected.parent
    files = json.loads((collected / "ctx" / "files.json").read_text())
    symbols = json.loads((collected / "ctx" / "symbols.json").read_text())
    pr = json.loads((collected / "ctx" / "pr.json").read_text())
    py_files = [f for f in files if f["is_python"] and f["status"] != "D"]

    lint, fmt, ruff_dead = precheck.check_lint(repo, py_files)
    assert lint["config"] == "default"
    assert {v["code"] for v in lint["violations"]} == {"D103", "ANN001", "ANN201"}
    assert all(v["line"] == 55 for v in lint["violations"])  # scale 만. 기존 줄의 위반은 없음
    assert [d["code"] for d in ruff_dead] == ["F401"]
    assert len(fmt["violations"]) == 1 and fmt["violations"][0]["file"] == "calc/ops.py"

    tests = precheck.check_tests(repo, collected, files, False, "", 120)
    assert tests["outcome"] == "passed" and tests["tests"] == 4 and tests["changed_test_files"] == ["tests/test_ops.py"]

    cands = precheck.find_test_candidates(
        repo, symbols["symbols"], tests["changed_test_files"], precheck.list_test_files(repo)
    )
    assert cands["calc/ops.py::divide"]["name_hits"] and cands["calc/ops.py::scale"]["name_hits"] == []
    dead = precheck.find_dead_code(repo, collected, py_files, symbols["symbols"], ruff_dead)
    names = {d["name"] for d in dead["candidates"]}
    assert "F401" in names
    if not dead["skipped"]:
        assert {"unused_helper", "os"} <= names

    ri = precheck.build_review_input(collected, pr, files, symbols, lint, fmt, tests, cands, dead)
    assert ri["precheck"]["verdict"] == "reject"
    assert {r["check"] for r in ri["precheck"]["reasons"]} == {"3-1"}
    schema = json.loads((ROOT / "schemas" / "review-input.json").read_text())
    assert validate(ri, schema) == []
