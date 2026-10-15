from __future__ import annotations

import copy
import csv
import subprocess
import sys
from pathlib import Path

import backfill_precheck
import pytest
from conftest import has, requires_ruff

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "review" / "dev" / "backfill_precheck.py"
MISSING_SHA = "deadbeef" * 5

requires_vulture = pytest.mark.skipif(not has("vulture"), reason="vulture 없음")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def _review_input() -> dict:
    """도구가 모두 실행된 review-input.json의 필요한 부분만 흉내 낸다."""
    return {
        "files": [
            {
                "path": "a/m.py",
                "additions": 10,
                "deletions": 2,
                "is_python": True,
                "is_test": False,
                "too_large": False,
            },
            {"path": "tests/test_m.py", "additions": 5, "deletions": 0, "is_python": True, "is_test": True},
        ],
        "lint": {"config": "default", "ran": True, "violations": [{"code": "D103"}], "pass": False},
        "format": {"config": "default", "ran": True, "violations": [], "pass": True},
        "test_candidates": {"a/m.py::f": {"name_hits": [{"file": "tests/test_m.py"}], "import_hits": []}},
        "dead_code_candidates": {
            "skipped": False,
            "candidates": [
                {"file": "a/m.py", "line": 1, "name": "F401", "source": "ruff", "message": "`os` imported but unused"},
                {"file": "a/m.py", "line": 1, "name": "os", "source": "vulture"},
                {"file": "a/m.py", "line": 2, "name": "F401", "source": "ruff", "message": "`sys` imported but unused"},
                {"file": "a/m.py", "line": 2, "name": "json", "source": "vulture"},
                {"file": "a/m.py", "line": 9, "name": "g", "source": "vulture"},
            ],
        },
        "precheck": {"verdict": "reject", "tier": 1, "targets": ["a/m.py::f", "a/m.py::g"]},
    }


def test_summarize_counts_and_dead_code_dedupe():
    row = backfill_precheck.summarize(_review_input())
    assert row["error"] == ""
    assert row["py_lines"] == 12
    assert (row["targets"], row["targets_without_candidates"], row["targets_without_candidates_names"]) == (
        2,
        1,
        "a/m.py::g",
    )
    assert (row["lint_violations"], row["format_violations"]) == (1, 0)
    # ruff의 os와 vulture의 os는 같은 줄의 같은 이름이라 한 번만 셈. 같은 줄의 다른 이름(sys, json)은 따로 셈
    assert row["dead_code_candidates"] == 4
    assert row["dead_code_names"] == "g;json;os;sys"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"lint": {"ran": False, "pass": False, "violations": [], "note": "ruff가 설치되어 있지 않음"}},
            "ruff가 설치되어",
        ),
        ({"format": {"ran": True, "pass": False, "violations": [], "note": "ruff format 실행 오류: x"}}, "실행 오류"),
        ({"lint": {"ran": False, "pass": False, "violations": []}}, "ruff가 실행되지 않음"),
        (
            {
                "dead_code_candidates": {
                    "skipped": True,
                    "note": "vulture가 설치되어 있지 않아 ruff 결과만 사용",
                    "candidates": [],
                }
            },
            "vulture",
        ),
        (
            {"dead_code_candidates": {"skipped": False, "note": "vulture 실행 오류(exit 1): x", "candidates": []}},
            "vulture 실행 오류",
        ),
    ],
)
def test_summarize_marks_error_when_tool_did_not_run(change, reason):
    ri = {**_review_input(), **copy.deepcopy(change)}
    row = backfill_precheck.summarize(ri)
    assert reason in row["error"]


def test_summarize_no_python_files_is_not_error():
    ri = _review_input()
    ri["lint"] = {"config": "default", "ran": False, "violations": [], "pass": True}
    ri["format"] = {"config": "default", "ran": False, "violations": [], "pass": True}
    assert backfill_precheck.summarize(ri)["error"] == ""


@requires_ruff
@requires_vulture
def test_backfill_on_fixture(fixture_repo: Path, tmp_path: Path):
    """fixture의 main...feature를 PR 한 개로 소급 실행하면 docs/pr-review-agent/README.md의 기본 fixture 기대 결과가 나온다."""
    main_sha = _git(fixture_repo, "rev-parse", "main").strip()
    prs = tmp_path / "prs.csv"
    with prs.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "number", "base_sha", "head_sha"])
        w.writeheader()
        w.writerow(
            {
                "repo": "fx",
                "number": 1,
                "base_sha": main_sha,
                "head_sha": _git(fixture_repo, "rev-parse", "feature").strip(),
            }
        )
        w.writerow({"repo": "fx", "number": 2, "base_sha": main_sha, "head_sha": MISSING_SHA})  # clone에 없는 head
    out = tmp_path / "backfill.csv"
    work_root = tmp_path / "work"
    args = ["--repo", str(fixture_repo), "--prs", str(prs), "--repo-name", "fx"]
    args += ["--out", str(out), "--work-root", str(work_root)]
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    ok, failed = rows
    assert ok["error"] == ""
    assert ok["precheck_verdict"] == "reject"
    assert ok["py_lines"] == "34"  # calc/ops.py 추가 33줄 + 삭제 1줄. tests/는 빠짐
    assert int(ok["lint_violations"]) >= 1 or int(ok["format_violations"]) >= 1
    without = {key.split("::")[-1] for key in ok["targets_without_candidates_names"].split(";")}
    assert "scale" in without
    assert ok["dead_code_candidates"] == "3"
    assert set(ok["dead_code_names"].split(";")) == {"os", "scale", "unused_helper"}

    assert "head SHA" in failed["error"] and failed["precheck_verdict"] == ""

    worktrees = [
        line
        for line in _git(fixture_repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    assert worktrees == [f"worktree {fixture_repo.resolve()}"]
    assert not (work_root / "wt-1").exists() and not (work_root / "wt-2").exists()
