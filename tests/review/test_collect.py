from __future__ import annotations

import json
from pathlib import Path

import collect
import pytest
from common import is_test_path


def test_parse_hunk_ranges_add_modify_delete():
    diff = "@@ -0,0 +1,3 @@\n+a\n@@ -10,2 +13,5 @@\n-x\n@@ -20 +25,0 @@\n-y\n@@ -30,0 +36 @@\n+z\n"
    changed, removed = collect.parse_hunk_ranges(diff)
    assert changed == [[1, 3], [13, 17], [36, 36]]
    assert removed == [[10, 11], [20, 20]]


def test_parse_name_status_with_rename_and_copy():
    text = "M\0a.py\0R087\0old.py\0new.py\0A\0b.txt\0D\0c.py\0"
    files = collect.parse_name_status(text)
    assert [(f["path"], f["status"], f["old_path"]) for f in files] == [
        ("a.py", "M", None),
        ("new.py", "R", "old.py"),
        ("b.txt", "A", None),
        ("c.py", "D", None),
    ]
    assert files[1]["similarity"] == 87


def test_parse_numstat_binary_and_rename():
    text = "3\t1\ta.py\x00-\t-\timg.png\x000\t0\t\x00old.py\x00new.py\x00"
    stats = collect.parse_numstat(text)
    assert stats["a.py"] == (3, 1)
    assert stats["img.png"] == (None, None)
    assert stats["new.py"] == (0, 0)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_ops.py", True),
        ("test/unit/x.py", True),
        ("pkg/test_x.py", True),
        ("pkg/x_test.py", True),
        ("conftest.py", True),
        ("pkg/ops.py", False),
        ("testing/ops.py", False),
        ("contest.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool):
    assert is_test_path(path) is expected


def test_collect_on_fixture(collected: Path):
    files = {f["path"]: f for f in json.loads((collected / "ctx" / "files.json").read_text())}
    assert set(files) == {"calc/ops.py", "calc/compat.py", "tests/test_ops.py", "requirements.txt"}
    assert files["calc/compat.py"]["status"] == "R" and files["calc/compat.py"]["old_path"] == "calc/legacy.py"
    assert files["calc/compat.py"]["before_path"] == "ctx/before/calc/compat.py"  # rename은 새 경로로 저장
    assert files["tests/test_ops.py"]["is_test"] is True
    assert files["requirements.txt"]["is_python"] is False and files["requirements.txt"]["after_path"] is None
    ops = files["calc/ops.py"]
    assert ops["status"] == "M" and ops["changed_lines"] and ops["removed_lines"]
    assert (collected / ops["before_path"]).read_text() == collect_show(collected, "main", "calc/ops.py")
    pr = json.loads((collected / "ctx" / "pr.json").read_text())
    assert pr["number"] is None and pr["merge_base"] and pr["head_sha"] != pr["base_sha"]


def collect_show(work: Path, rev: str, path: str) -> str:
    from common import git

    return git(work.parent, "show", f"{rev}:{path}").out


def test_pr_meta_from_event(tmp_path: Path):
    ev = tmp_path / "event.json"
    ev.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 7,
                    "title": "t",
                    "body": None,
                    "draft": True,
                    "html_url": "https://github.com/o/r/pull/7",
                    "user": {"login": "kim"},
                    "base": {"ref": "main", "sha": "aaa", "repo": {"full_name": "o/r"}},
                    "head": {"ref": "feat", "sha": "bbb"},
                }
            }
        )
    )
    pr = collect.pr_meta_from_event(ev)
    assert pr == {
        "number": 7,
        "title": "t",
        "body": "",
        "author": "kim",
        "url": "https://github.com/o/r/pull/7",
        "is_draft": True,
        "base_ref": "main",
        "head_ref": "feat",
        "base_sha": "aaa",
        "head_sha": "bbb",
        "repo_slug": "o/r",
    }
    ev.write_text(json.dumps({"push": {}}))
    with pytest.raises(SystemExit):
        collect.pr_meta_from_event(ev)
