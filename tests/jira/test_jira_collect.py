"""scripts/collect.py의 실행 조건. Jira와 GitHub를 부르지 않고 확인한다."""

from __future__ import annotations

import importlib.util
import json
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


def test_jira_error_leaves_run_error_and_reraises(jc, monkeypatch, tmp_path):
    """Jira 호출이 예외를 내면 out/_run_error.json(step "1. 입력 수집")을 남기고 예외를 다시 올린다."""

    def broken():
        raise RuntimeError("Jira 연결 실패")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jc, "Jira", broken)
    monkeypatch.setattr(sys, "argv", ["collect.py", "--mode", "review", "--issue", "INNO-17"])
    with pytest.raises(RuntimeError, match="Jira 연결 실패"):
        jc.main()
    error = json.loads((tmp_path / "out" / "_run_error.json").read_text(encoding="utf-8"))
    assert error == {
        "step": "1. 입력 수집",
        "error": "RuntimeError: Jira 연결 실패",
    }


def test_argument_check_exit_leaves_no_run_error(jc, no_jira, monkeypatch, tmp_path):
    """인자 검사의 SystemExit은 실패 기록을 남기지 않는다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["collect.py", "--mode", "digest"])
    with pytest.raises(SystemExit):
        jc.main()
    assert not (tmp_path / "out").exists()


@pytest.fixture(scope="module")
def jci():
    """scripts/src/jira/collect.py (모듈 이름 src.jira.collect). collect_issue를 가진다."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함
    return importlib.import_module("src.jira.collect")


class TypeOnlyJira:
    """issue()만 있는 가짜 Jira. collect_issue가 건너뛰기 전에 다른 Jira 메서드를 부르면 AttributeError가 난다."""

    tldr_field = ""

    def __init__(self, issuetype: dict) -> None:
        self.issuetype = issuetype

    def issue(self, key: str, fields: list[str]) -> dict:
        """work type만 있는 issue 응답을 돌려준다."""
        return {"fields": {"issuetype": self.issuetype}}


@pytest.mark.parametrize(
    "issuetype",
    [{"name": "Story"}, {"name": "Task", "subtask": True}, {"name": "Sub-task", "subtask": True}],
    ids=["Story", "sub-task 표시가 있는 Task", "Sub-task"],
)
def test_collect_issue_skips_non_target_type_and_subtask(jci, monkeypatch, tmp_path, capsys, issuetype):
    """CM-7: 대상이 아닌 work type과 sub-task는 None을 돌려주고 ctx를 만들지 않는다."""
    monkeypatch.setattr(jci, "CTX", tmp_path / "ctx")
    assert jci.collect_issue(TypeOnlyJira(issuetype), "INNO-60", "https://example.atlassian.net", "", "review") is None
    assert not (tmp_path / "ctx").exists()
    assert "INNO-60: work type" in capsys.readouterr().out
