"""Notion REST API 읽기 클라이언트. 호출 간격 제한과 일시적인 오류의 재시도를 처리한다."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"  # https://developers.notion.com/reference/query-a-data-source
PAGE_SIZE = 100  # 목록 API의 page_size 최댓값
MIN_INTERVAL = 0.34  # 호출 간격(초). 연결당 분당 180회(평균 초당 3회) 제한을 넘지 않게 둔다
MAX_ATTEMPTS = 5  # 429, 5xx, 연결·응답 읽기 오류일 때 같은 요청을 보내는 최대 횟수
BACKOFF_BASE = 1.0  # 다시 시도하기 전 첫 대기(초). 시도마다 두 배로 늘린다. 429는 Retry-After가 있으면 그 값을 따른다
REQUEST_TIMEOUT = 60  # 요청 하나의 시간 제한(초)
COMMENTS_PATH = "/comments"


class NotionError(RuntimeError):
    """Notion API가 오류 응답을 돌려줬거나 다시 시도한 횟수가 MAX_ATTEMPTS에 닿았다."""


class CommentPermissionError(NotionError):
    """comment 조회가 HTTP 403이다. integration에 comment 읽기 권한이 없다."""


class NotionReader(Protocol):
    """run이 부르는 Notion 읽기 메서드."""

    def query_pages(self, data_source_id: str) -> list[dict]:
        """data source의 모든 페이지 객체."""
        ...

    def block_children(self, block_id: str) -> list[dict]:
        """블록(또는 페이지)의 첫 단계 하위 블록 전체."""
        ...

    def comments(self, block_id: str) -> list[dict]:
        """블록(또는 페이지)의 해결되지 않은 comment 전체."""
        ...


class NotionClient:
    """Notion REST API 읽기 클라이언트. 호출 간격을 MIN_INTERVAL초 이상 두고, 일시적인 오류면 다시 시도한다."""

    def __init__(
        self,
        token: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        interval: float = MIN_INTERVAL,
    ):
        self.token = token
        self.opener = opener
        self.sleep = sleep
        self.clock = clock
        self.interval = interval
        self.last_call: float | None = None

    def _wait_interval(self) -> None:
        if self.last_call is not None:
            left = self.interval - (self.clock() - self.last_call)
            if left > 0:
                self.sleep(left)
        self.last_call = self.clock()

    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: dict | None = None) -> dict:
        """API를 호출하고 JSON 응답을 돌려준다. 일시적인 오류면 기다렸다가 MAX_ATTEMPTS번까지 다시 시도한다.

        다시 시도하는 오류는 HTTP 429, 5xx, OSError(URLError, TimeoutError, ConnectionError 포함),
        http.client.HTTPException(RemoteDisconnected, IncompleteRead 포함)이다. urllib은 응답을 받는 중에 난 오류를
        URLError로 감싸지 않기 때문이다. 대기 시간은 BACKOFF_BASE초에서 시작해 시도마다 두 배로 늘리고, Retry-After
        헤더가 있으면 그 값을 따른다. 마지막 시도 뒤에는 기다리지 않는다.

        Args:
            method: HTTP 메서드(GET, POST).
            path: API_BASE 뒤 경로. 예: /blocks/<ID>/children.
            query: URL query 값. 값이 None인 키는 뺀다.
            body: JSON body. 없으면 보내지 않는다.

        Returns:
            응답 JSON.

        Raises:
            CommentPermissionError: comment 조회가 HTTP 403이다.
            NotionError: 다시 시도하지 않는 오류 응답(4xx)이거나, MAX_ATTEMPTS번 모두 실패했다.
        """
        url = API_BASE + path
        params = {k: v for k, v in (query or {}).items() if v is not None}
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}", "Notion-Version": NOTION_VERSION}
        if data is not None:
            headers["Content-Type"] = "application/json"
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            self._wait_interval()
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            backoff = BACKOFF_BASE * 2**attempt
            try:
                with self.opener(req, timeout=REQUEST_TIMEOUT) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 403 and path == COMMENTS_PATH:
                    raise CommentPermissionError(
                        "integration에 comment 읽기 권한 필요. integration 설정에서 comment 읽기 권한을 켠 뒤 다시 "
                        f"실행해야 함({method} {path} -> HTTP 403: {_error_body(e)})"
                    ) from e
                if e.code != 429 and e.code < 500:
                    raise NotionError(f"{method} {path} -> HTTP {e.code}: {_error_body(e)}") from e
                last, wait = f"HTTP {e.code}", _retry_after(e, backoff)
            except (OSError, http.client.HTTPException) as e:
                last, wait = f"{type(e).__name__}: {e}", backoff
            if attempt < MAX_ATTEMPTS - 1:
                self.sleep(wait)
        raise NotionError(f"{method} {path} -> {MAX_ATTEMPTS}번 모두 실패(마지막 오류: {last})")

    def _paginate(self, method: str, path: str, query: dict[str, Any] | None = None) -> list[dict]:
        results: list[dict] = []
        cursor = None
        while True:
            if method == "POST":
                body = {"page_size": PAGE_SIZE, **({"start_cursor": cursor} if cursor else {})}
                resp = self.request(method, path, body=body)
            else:
                resp = self.request(
                    method, path, query={**(query or {}), "page_size": PAGE_SIZE, "start_cursor": cursor}
                )
            results += resp.get("results") or []
            cursor = resp.get("next_cursor")
            if not resp.get("has_more") or not cursor:
                return results

    def query_pages(self, data_source_id: str) -> list[dict]:
        """data source의 모든 페이지 객체를 다음 페이지까지 읽는다.

        Args:
            data_source_id: 'Task 단위'의 data source ID.

        Returns:
            페이지 객체 목록.
        """
        return self._paginate("POST", f"/data_sources/{data_source_id}/query")

    def block_children(self, block_id: str) -> list[dict]:
        """블록(또는 페이지)의 첫 단계 하위 블록을 다음 페이지까지 읽는다.

        Args:
            block_id: 블록 ID 또는 페이지 ID.

        Returns:
            블록 객체 목록.
        """
        return self._paginate("GET", f"/blocks/{block_id}/children")

    def comments(self, block_id: str) -> list[dict]:
        """블록(또는 페이지)의 해결되지 않은 comment를 다음 페이지까지 읽는다.

        Args:
            block_id: 블록 ID 또는 페이지 ID.

        Returns:
            comment 객체 목록.
        """
        return self._paginate("GET", COMMENTS_PATH, {"block_id": block_id})


def _retry_after(e: urllib.error.HTTPError, fallback: float) -> float:
    headers = e.headers or {}
    try:
        return float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return fallback


def _error_body(e: urllib.error.HTTPError) -> str:
    try:
        return " ".join(e.read().decode("utf-8", "replace").split())[:300]
    except Exception:  # 오류 본문은 메시지용이라 읽지 못해도 넘어간다
        return ""
