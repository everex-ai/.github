"""scripts/collect.py의 실행 조건. Jira와 GitHub를 부르지 않고 확인한다."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def jc():
    """scripts/collect.py (모듈 이름 jira_collect_main). tests/review/가 불러오는 scripts/review/collect.py와 섞이지 않게 한다."""
    name = "jira_collect_main"
    if name not in sys.modules:
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "collect.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(ROOT / "scripts"))
    return sys.modules[name]


@pytest.fixture
def no_jira(jc, monkeypatch):
    """Jira를 만들려고 하면 실패하게 해서, 그 전에 끝났는지 확인할 수 있게 한다."""

    def forbidden():
        raise AssertionError("Jira를 부르면 안 됨")

    monkeypatch.setattr(jc, "Jira", forbidden)


@pytest.mark.parametrize("issue", [[], ["--issue", ""], ["--issue", "  "]], ids=["인자 없음", "빈 값", "공백"])
def test_digest_without_issue_exits_with_code_1(jc, no_jira, monkeypatch, issue):
    monkeypatch.setattr(sys, "argv", ["collect.py", "--mode", "digest", *issue])
    with pytest.raises(SystemExit) as e:
        jc.main()
    assert e.value.code == "digest 모드에는 --issue가 필요하다"


def test_review_without_issue_exits(jc, no_jira, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["collect.py", "--mode", "review"])
    with pytest.raises(SystemExit) as e:
        jc.main()
    assert e.value.code == "review 모드에는 --issue가 필요하다"


def test_digest_without_issue_exit_status_is_1(tmp_path):
    import subprocess

    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "collect.py"), "--mode", "digest"],
        capture_output=True, text=True, cwd=tmp_path,
    )  # fmt: skip
    assert res.returncode == 1 and "digest 모드에는 --issue가 필요하다" in res.stderr
    assert not (tmp_path / "ctx").exists()
