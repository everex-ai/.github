"""scripts/src/jira/client.py의 Jira 호출. urllib.request.urlopen과 time.sleep을 가짜로 바꿔 네트워크 없이 확인한다."""

from __future__ import annotations

import importlib
import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client_mod():
    """scripts/src/jira/client.py (모듈 이름 src.jira.client)."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함
    return importlib.import_module("src.jira.client")


class FakeResponse:
    """urlopen이 돌려주는 응답 대신 쓰는 가짜. read(n)이 받은 n을 기록한다."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.read_sizes: list = []

    def read(self, n=-1):
        self.read_sizes.append(n)
        return self.body if n is None or n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrlopen:
    """urllib.request.urlopen 대신 쓰는 가짜. 응답 목록을 차례로 돌려주거나 HTTPError를 낸다."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        res = self.responses.pop(0)
        if isinstance(res, int):
            raise urllib.error.HTTPError(req.full_url, res, "error", {}, io.BytesIO(b"detail"))
        self.last = FakeResponse(res)
        return self.last


@pytest.fixture
def jira(client_mod, monkeypatch):
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud")
    monkeypatch.setenv("JIRA_EMAIL", "a@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    sleeps: list = []
    monkeypatch.setattr(client_mod.time, "sleep", sleeps.append)
    j = client_mod.Jira()
    j.sleeps = sleeps
    return j


def use(client_mod, monkeypatch, *responses) -> FakeUrlopen:
    fake = FakeUrlopen(*responses)
    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake)
    return fake


def test_attachment_content_url_and_accept(client_mod, jira, monkeypatch):
    fake = use(client_mod, monkeypatch, b"\x89PNG")
    assert jira.attachment_content("46482") == b"\x89PNG"
    req = fake.requests[0]
    assert req.full_url == "https://api.atlassian.com/ex/jira/cloud/rest/api/2/attachment/content/46482?redirect=false"
    assert req.get_method() == "GET" and req.get_header("Accept") == "*/*"
    assert fake.last.read_sizes == [-1]


def test_attachment_content_quotes_id(client_mod, jira, monkeypatch):
    fake = use(client_mod, monkeypatch, b"x")
    jira.attachment_content("../1?a=b")
    assert fake.requests[0].full_url.endswith("/attachment/content/..%2F1%3Fa%3Db?redirect=false")


def test_attachment_content_reads_at_most_max_bytes_plus_one(client_mod, jira, monkeypatch):
    fake = use(client_mod, monkeypatch, b"x" * 100)
    content = jira.attachment_content("1", max_bytes=10)
    assert fake.last.read_sizes == [11] and len(content) == 11  # 10을 넘으면 상한을 넘은 것으로 판단한다
    fake = use(client_mod, monkeypatch, b"x" * 10)
    assert jira.attachment_content("1", max_bytes=10) == b"x" * 10


def test_req_parses_json_and_empty_body(client_mod, jira, monkeypatch):
    fake = use(client_mod, monkeypatch, b'{"a": 1}', b"  ")
    assert jira._req("GET", "/issue/INNO-1", {"fields": "summary"}) == {"a": 1}
    assert jira._req("GET", "/x") is None
    assert fake.requests[0].get_header("Accept") == "application/json"
    assert fake.requests[0].full_url.endswith("/issue/INNO-1?fields=summary")


def test_req_ok404_returns_none(client_mod, jira, monkeypatch):
    use(client_mod, monkeypatch, 404)
    assert jira._req("GET", "/issue/INNO-1/properties/x", ok404=True) is None
    use(client_mod, monkeypatch, 404)
    with pytest.raises(client_mod.JiraError, match="404"):
        jira._req("GET", "/issue/INNO-1")


def test_req_retries_429(client_mod, jira, monkeypatch):
    fake = use(client_mod, monkeypatch, 429, 429, b'{"ok": true}')
    assert jira._req("GET", "/x") == {"ok": True}
    assert len(fake.requests) == 3 and jira.sleeps == [2, 4]


def test_req_gives_up_after_four_attempts(client_mod, jira, monkeypatch):
    fake = use(client_mod, monkeypatch, 503, 503, 503, 503)
    with pytest.raises(client_mod.JiraError, match="503"):
        jira._req("GET", "/x")
    assert len(fake.requests) == 4
