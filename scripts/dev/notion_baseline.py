#!/usr/bin/env python3
"""업무 공유 체계(Kanban)의 'Task 단위' 페이지에서 문서화 Agent 도입 이전 현황 지표(N1–N9)를 집계한다. Notion에 쓰지 않는다.

    NOTION_TOKEN=... python scripts/dev/notion_baseline.py --data-source <data source ID> \
        --assignees <사용자 ID>,<사용자 ID> --since 2026-03-01 --until 2026-08-31 \
        --out reports/agent-impact/jira-doc/notion-baseline

- 지표 정의는 docs/jira-doc-agent/metrics.md에 있다.
- Notion REST API(Notion-Version 2026-03-11)를 표준 라이브러리 urllib로 읽기만 한다. data source의 페이지 목록,
  블록 하위 목록(has_children이면 재귀), comment 목록을 읽는다.
- HTTP 429, 5xx, 연결·응답 읽기 오류(OSError, http.client.HTTPException)면 기다렸다가 다시 시도한다. 429는 Retry-After만큼
  기다린다.
- API를 호출하는 NotionClient와 판정·집계 함수(page_info 이하)를 나눈다. run은 클라이언트 객체를 인자로 받으므로
  테스트는 가짜 객체를 넘긴다.
- 대상 페이지: 최초 생성 일자(created_time)가 기간(--since, --until, 한국 시간 날짜, 두 날짜 포함) 안이고, 담당자에
  --assignees 중 한 명 이상이 있는 페이지. 제목이 템플릿 페이지와 같으면 뺀다. --limit N이면 최초 생성 일자 순으로
  앞의 N개만 처리한다.
- comment는 Done과 Holding 페이지만 조회한다(N1, N8에만 사용). 기본은 페이지 ID와 페이지 안 모든 블록 ID로 조회하고,
  --page-comments-only면 페이지 ID로만 조회한다. comment 조회가 HTTP 403이면 권한 안내를 출력하고 바로 멈춘다.
- 페이지 하나의 조회나 판정이 실패하면 그 페이지의 error 열에 적고 다음 페이지로 넘어간다. error가 있는 페이지는
  summary.md 집계에서 뺀다(scripts/review/dev/pr_metrics.py의 error_row와 같은 방법).
- 출력: <out>/pages.csv(페이지별), <out>/summary.md(지표 요약). 담당자는 넣지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import http.client
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"  # https://developers.notion.com/reference/query-a-data-source
PAGE_SIZE = 100  # 목록 API의 page_size 최댓값
MIN_INTERVAL = 0.34  # 호출 간격(초). 연결당 분당 180회(평균 초당 3회) 제한을 넘지 않게 둔다
MAX_ATTEMPTS = 5  # 429, 5xx, 연결·응답 읽기 오류일 때 같은 요청을 보내는 최대 횟수
BACKOFF_BASE = 1.0  # 다시 시도하기 전 첫 대기(초). 시도마다 두 배로 늘린다. 429는 Retry-After가 있으면 그 값을 따른다
REQUEST_TIMEOUT = 60  # 요청 하나의 시간 제한(초)
COMMENTS_PATH = "/comments"
KST = dt.timezone(dt.timedelta(hours=9))  # 기간 경계와 월을 정하는 시간대

STATUS_PROP = "상태"
ASSIGNEE_PROP = "담당자"
DONE = "done"  # 상태 값 Done. 대소문자 무시로 비교
HOLDING = "holding"  # 상태 값 Holding. 대소문자 무시로 비교
STATUS_ORDER = ["Not started", "In progress", "Holding", "Done"]
TEMPLATE_TITLES = {"Engineering Template", "Research Template", "[] Engineering Template (James)"}

# 템플릿별 구역 heading 문구 -> 구역 키. "[] Engineering Template (James)"는 Engineering Template과 heading이 같다
TEMPLATE_HEADINGS = {
    "engineering": {
        "관련 링크": "links",
        "진행 배경": "background",
        "Task 완료 기준": "criteria",
        "Method(진행 내용)": "method",
        "결과 산출물": "result",
        "참고 자료(reference)": "reference",
    },
    "research": {
        "본 task 목적": "background",
        "Task 완료 기준": "criteria",
        "진행 사항": "method",
        "결과(완료 기준 달성 여부)": "result",
        "참고 자료": "reference",
    },
}
KIND_NONE = "none"  # 템플릿 구역 heading을 찾지 못한 페이지
SECTION_LABELS = {  # N2에서 작성 여부를 판정하는 구역
    "background": "진행 배경(Research Template은 본 task 목적)",
    "criteria": "Task 완료 기준",
    "result": "결과(Engineering Template은 결과 산출물, Research Template은 결과(완료 기준 달성 여부))",
}
GUIDE_PHRASES = (  # 각 구역 heading 바로 아래의 안내 문구(따옴표 안 문자열)
    "본 task와 관련된 문서가 있으면 추가",
    "왜 이 task를 진행하게 되었는가?",
    "Task를 달성했다는 것은 어떻게, 무엇으로 판단하는가?",
    "예상 산출물은 무엇인가?",
    "어떻게 진행할 것인가?",
    "Task 완료 기준과 1:1 대응해야 함",
    "완료는 ✅, 미완료는 사유와 함께 ❌ 표기",
    "관련하여 참고한 자료가 있는가?",
    "무엇을 위해, 무엇을 알아내기위해, 본 task를 진행하는가?",
    "무엇으로 본 task가 완료되었음을 판단하는가?",
    "어떻게 진행했는가?",
)
EXAMPLE_TEXTS = (  # 템플릿 예시 항목. 내용 없는 체크박스와 빈 목록 항목은 빈 블록으로 따로 판정한다
    "어떤 문서인지 설명",  # "[] Engineering Template (James)"의 관련 링크 목록 항목
    "링크",  # 위 목록 항목의 하위 항목
    "-",  # Research Template 참고 자료의 빈 목록 항목
)
HEADING_TYPES = {"heading_1", "heading_2", "heading_3"}
LIST_TYPES = {"bulleted_list_item", "numbered_list_item", "to_do"}
EMPTY_TYPES = {"paragraph", "quote", "callout", "toggle", "table_row", *LIST_TYPES}  # 텍스트가 없으면 빈 블록
NON_CONTENT_TYPES = {"divider", "table_of_contents", "breadcrumb"}  # 작성한 내용으로 세지 않는 블록 종류
CONTAINER_TYPES = {"column_list", "column", "synced_block", "table"}  # 하위 블록에 내용이 있을 때만 작성
NO_DESCEND = {"child_page", "child_database"}  # 하위 블록을 조회하지 않는 블록 종류(다른 페이지, 데이터베이스)
EVIDENCE_BLOCKS = {  # 근거로 세는 블록 종류 -> summary.md에 적는 이름
    "file": "파일",
    "pdf": "PDF 파일",
    "image": "이미지",
    "video": "동영상",
    "bookmark": "bookmark",
    "embed": "embed",
    "link_preview": "링크 미리보기",
    "link_to_page": "페이지 링크",
    "child_page": "하위 페이지",
}
EVIDENCE_MENTIONS = {  # 근거로 세는 mention 종류 -> summary.md에 적는 이름. 날짜, 사용자 mention은 근거가 아니다
    "page": "페이지 mention",
    "database": "데이터베이스 mention",
    "link_preview": "링크 미리보기 mention",
    "link_mention": "링크 mention",
}
OK_MARK, FAIL_MARK = "✅", "❌"
SCOPE_ALL = "page+blocks"  # comment를 페이지 ID와 페이지 안 모든 블록 ID로 조회함
SCOPE_PAGE = "page"  # comment를 페이지 ID로만 조회함
BASE_COLUMNS = ["page_id", "url", "title", "created_month", "status"]
PAGE_COLUMNS = [
    *BASE_COLUMNS,
    "template",
    "background_written",
    "criteria_written",
    "result_written",
    "criteria_items",
    "result_items",
    "ok_items",
    "fail_items",
    "n1_review",
    "n3_one_to_one",
    "n4_marked",
    "n5_fail_reason",
    "n6_evidence",
    "n7_complete",
    "n8_holding_comment",
    "comment_count",
    "comment_scope",
    "error",
]


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


def norm_id(s: str) -> str:
    """Notion ID 비교용 문자열. 하이픈을 지우고 소문자로 맞춘다.

    Args:
        s: Notion ID.

    Returns:
        예: 1a2b-3C -> 1a2b3c.
    """
    return s.replace("-", "").strip().lower()


@dataclass
class PageInfo:
    """페이지 객체에서 읽은 값. assignees는 norm_id로 맞춘 담당자 ID다."""

    id: str
    url: str
    title: str
    created: dt.datetime
    status: str
    assignees: set[str]


@dataclass
class PageResult:
    """대상 페이지 하나의 판정 결과. pages.csv의 한 행이 된다. 판정 값 None은 계산 대상이 아님을 뜻한다."""

    page: PageInfo
    kind: str
    written: dict[str, bool]  # SECTION_LABELS의 구역 키 -> 작성 여부
    criteria_items: int
    result_items: int
    ok_items: int
    fail_items: int
    n1: bool | None = None
    n3: bool | None = None
    n4: bool | None = None
    n5: bool | None = None
    n6: bool | None = None
    n7: bool | None = None
    n8: bool | None = None
    comment_count: int | None = None  # comment를 조회하지 않은 페이지(Done, Holding이 아님)는 None
    comment_scope: str = ""

    @property
    def done(self) -> bool:
        """상태가 Done인지 여부."""
        return is_status(self.page.status, DONE)

    def all_written(self) -> bool:
        """N2의 세 구역이 모두 작성됐는지 확인한다.

        Returns:
            세 구역이 모두 작성됐으면 True.
        """
        return all(self.written.get(key, False) for key in SECTION_LABELS)

    def row(self) -> dict[str, Any]:
        """pages.csv의 한 행. 판정 값은 1(만족), 0(불만족), 빈 값(계산 대상 아님)으로 적는다.

        Returns:
            PAGE_COLUMNS를 키로 하는 dict. error는 빈 문자열이다.
        """
        return {
            **base_fields(self.page),
            "template": self.kind,
            "background_written": _flag(self.written["background"]),
            "criteria_written": _flag(self.written["criteria"]),
            "result_written": _flag(self.written["result"]),
            "criteria_items": self.criteria_items,
            "result_items": self.result_items,
            "ok_items": self.ok_items,
            "fail_items": self.fail_items,
            "n1_review": _flag(self.n1),
            "n3_one_to_one": _flag(self.n3),
            "n4_marked": _flag(self.n4),
            "n5_fail_reason": _flag(self.n5),
            "n6_evidence": _flag(self.n6),
            "n7_complete": _flag(self.n7),
            "n8_holding_comment": _flag(self.n8),
            "comment_count": "" if self.comment_count is None else self.comment_count,
            "comment_scope": self.comment_scope,
            "error": "",
        }


def _flag(x: bool | None) -> str:
    return "" if x is None else str(int(x))


def base_fields(info: PageInfo) -> dict[str, str]:
    """pages.csv의 기본 열(페이지 ID, URL, 제목, 최초 생성 월, 상태).

    Args:
        info: 페이지 값.

    Returns:
        BASE_COLUMNS를 키로 하는 dict.
    """
    return {
        "page_id": info.id,
        "url": info.url,
        "title": info.title,
        "created_month": month(info.created),
        "status": info.status,
    }


def error_row(info: PageInfo, message: str) -> dict[str, Any]:
    """조회나 판정이 실패한 페이지의 pages.csv 한 줄. 기본 열과 error만 채운다.

    Args:
        info: 페이지 값.
        message: 오류 메시지. 줄바꿈과 겹친 공백은 한 칸으로 줄인다.

    Returns:
        PAGE_COLUMNS를 키로 하는 dict.
    """
    return {**dict.fromkeys(PAGE_COLUMNS), **base_fields(info), "error": " ".join(message.split())}


def parse_ts(s: str | None) -> dt.datetime | None:
    """Notion 시각 문자열(예: 2026-02-28T15:00:00.000Z)을 시간대가 있는 datetime으로 바꾼다.

    Args:
        s: Notion 시각 문자열.

    Returns:
        datetime. 비었거나 읽을 수 없으면 None.
    """
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def month(t: dt.datetime) -> str:
    """한국 시간 기준 월.

    Args:
        t: 시각.

    Returns:
        예: 2026-03.
    """
    return t.astimezone(KST).strftime("%Y-%m")


def is_status(name: str, target: str) -> bool:
    """상태 이름이 target과 같은지 대소문자 무시로 확인한다.

    Args:
        name: 페이지의 상태 이름.
        target: 비교할 상태 이름.

    Returns:
        같으면 True.
    """
    return name.strip().casefold() == target.casefold()


def _rt_text(rt: dict) -> str:
    return rt.get("plain_text") or (rt.get("text") or {}).get("content") or ""


def page_info(page: dict) -> PageInfo:
    """페이지 객체에서 ID, URL, 제목, 최초 생성 일자, 상태, 담당자 ID를 읽는다.

    상태는 status 타입 값을 먼저 읽고, 없으면 select 타입 값을 읽는다.

    Args:
        page: data source query 응답의 페이지 객체.

    Returns:
        PageInfo. 최초 생성 일자를 읽을 수 없으면 1970-01-01(UTC)로 둔다.
    """
    props = page.get("properties") or {}
    title_prop = next((p for p in props.values() if (p or {}).get("type") == "title"), {})
    status_prop = props.get(STATUS_PROP) or {}
    people = (props.get(ASSIGNEE_PROP) or {}).get("people") or []
    return PageInfo(
        id=page.get("id") or "",
        url=page.get("url") or "",
        title="".join(_rt_text(rt) for rt in title_prop.get("title") or []).strip(),
        created=parse_ts(page.get("created_time")) or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
        status=((status_prop.get("status") or status_prop.get("select")) or {}).get("name") or "",
        assignees={norm_id(p["id"]) for p in people if p.get("id")},
    )


def period_bounds(since: dt.date | None, until: dt.date | None) -> tuple[dt.datetime | None, dt.datetime | None]:
    """기간 날짜를 한국 시간 경계로 바꾼다. until 날짜는 그날 끝까지 포함한다.

    Args:
        since: 시작 날짜. 없으면 제한 없음.
        until: 끝 날짜. 없으면 제한 없음.

    Returns:
        (시작 시각, 끝 시각(이 시각은 포함하지 않음)).
    """
    start = dt.datetime.combine(since, dt.time(), KST) if since else None
    end = dt.datetime.combine(until + dt.timedelta(days=1), dt.time(), KST) if until else None
    return start, end


def in_period(t: dt.datetime, start: dt.datetime | None, end: dt.datetime | None) -> bool:
    """시각이 기간 [start, end) 안인지 확인한다.

    Args:
        t: 시각.
        start: 시작 시각. 없으면 제한 없음.
        end: 끝 시각(포함하지 않음). 없으면 제한 없음.

    Returns:
        기간 안이면 True.
    """
    return (start is None or t >= start) and (end is None or t < end)


def select_pages(
    pages: list[PageInfo], assignees: set[str], start: dt.datetime | None, end: dt.datetime | None
) -> list[PageInfo]:
    """대상 페이지를 골라 최초 생성 일자 순으로 돌려준다.

    Args:
        pages: 조회한 모든 페이지.
        assignees: 대상 담당자의 Notion 사용자 ID(norm_id로 맞춘 값).
        start: 기간 시작 시각.
        end: 기간 끝 시각(포함하지 않음).

    Returns:
        최초 생성 일자가 기간 안이고 담당자에 assignees 중 한 명 이상이 있으며, 제목이 템플릿 페이지가 아닌 페이지.
    """
    chosen = [
        p
        for p in pages
        if in_period(p.created, start, end) and p.assignees & assignees and p.title not in TEMPLATE_TITLES
    ]
    return sorted(chosen, key=lambda p: p.created)


def children(block: dict) -> list[dict]:
    """fetch_tree가 붙인 하위 블록 목록.

    Args:
        block: 블록 객체.

    Returns:
        하위 블록 목록. 없으면 빈 목록.
    """
    return block.get("children") or []


def walk(blocks: list[dict]) -> Iterator[dict]:
    """블록과 그 하위 블록을 깊이 우선으로 모두 돌려준다.

    Args:
        blocks: 블록 목록.

    Yields:
        블록 객체.
    """
    for b in blocks:
        yield b
        yield from walk(children(b))


def fetch_tree(client: NotionReader, block_id: str) -> list[dict]:
    """블록(또는 페이지)의 하위 블록을 읽고, has_children이 참인 블록은 재귀로 읽어 children 키에 붙인다.

    Args:
        client: Notion 읽기 객체.
        block_id: 블록 ID 또는 페이지 ID.

    Returns:
        블록 객체 목록(원본을 복사해 children 키를 붙인 것).
    """
    blocks = []
    for b in client.block_children(block_id):
        if b.get("has_children") and b.get("type") not in NO_DESCEND:
            b = {**b, "children": fetch_tree(client, b["id"])}
        blocks.append(b)
    return blocks


def fetch_comments(client: NotionReader, page_id: str, blocks: list[dict], page_only: bool) -> list[dict]:
    """페이지의 comment를 읽는다. 같은 comment ID는 한 번만 넣는다.

    하위 페이지와 데이터베이스 블록(NO_DESCEND)은 다른 페이지이므로 그 ID로는 조회하지 않는다.

    Args:
        client: Notion 읽기 객체.
        page_id: 페이지 ID.
        blocks: fetch_tree로 읽은 페이지 블록.
        page_only: True면 페이지 ID로만 조회하고, False면 페이지 안 모든 블록 ID로도 조회한다.

    Returns:
        comment 객체 목록.
    """
    block_ids = [b["id"] for b in walk(blocks) if b.get("id") and b.get("type") not in NO_DESCEND]
    ids = [page_id] if page_only else [page_id, *block_ids]
    seen: set[str] = set()
    found = []
    for block_id in ids:
        for c in client.comments(block_id):
            cid = c.get("id")
            if cid in seen:
                continue
            if cid:
                seen.add(cid)
            found.append(c)
    return found


def rich_texts(block: dict) -> list[dict]:
    """블록의 rich_text 목록. 표 행(table_row)은 모든 칸의 rich_text를 이어 붙인다.

    Args:
        block: 블록 객체.

    Returns:
        rich_text 객체 목록.
    """
    data = block.get(block.get("type") or "") or {}
    out = list(data.get("rich_text") or [])
    for cell in data.get("cells") or []:
        out += cell
    return out


def block_text(block: dict) -> str:
    """블록의 텍스트(rich_text의 plain_text를 이어 붙인 것).

    Args:
        block: 블록 객체.

    Returns:
        텍스트.
    """
    return "".join(_rt_text(rt) for rt in rich_texts(block))


def _norm(s: str) -> str:
    """비교용 문자열. 따옴표와 모든 공백(물음표 주변 공백 포함)을 지우고 대소문자를 무시한다."""
    s = unicodedata.normalize("NFC", s)
    s = "".join(ch for ch in s if not ch.isspace() and ch not in "\"'“”‘’")
    return s.casefold()


GUIDE_NORMS = tuple(_norm(p) for p in GUIDE_PHRASES)
EXAMPLE_NORMS = {_norm(t) for t in EXAMPLE_TEXTS}
ALL_HEADING_NORMS = {_norm(h) for heads in TEMPLATE_HEADINGS.values() for h in heads}


def _is_link(rt: dict) -> bool:
    if rt.get("type") == "mention":
        return (rt.get("mention") or {}).get("type") in EVIDENCE_MENTIONS
    return bool(rt.get("href") or (rt.get("text") or {}).get("link"))


def is_guide(block: dict) -> bool:
    """안내 문구가 들어 있는 블록인지 판정한다. 정규화한 텍스트에 안내 문구가 들어 있으면 True다.

    Args:
        block: 블록 객체.

    Returns:
        안내 문구가 들어 있으면 True.
    """
    text = _norm(block_text(block))
    return bool(text) and any(g in text for g in GUIDE_NORMS)


def guide_residual(block: dict) -> str:
    """정규화한 텍스트에서 안내 문구를 모두 지우고 문장부호와 공백을 뺀 나머지 글자.

    Args:
        block: 블록 객체.

    Returns:
        남은 글자. 안내 문구 외에 작성한 글자가 없으면 빈 문자열.
    """
    text = _norm(block_text(block))
    for g in GUIDE_NORMS:
        text = text.replace(g, "")
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))


def is_empty(block: dict) -> bool:
    """빈 블록(텍스트 없는 paragraph, 목록 항목, to_do, 인용, callout, toggle, 표 행)인지 판정한다.

    Args:
        block: 블록 객체.

    Returns:
        빈 블록이면 True.
    """
    return block.get("type") in EMPTY_TYPES and not block_text(block).strip()


def is_example(block: dict) -> bool:
    """템플릿 예시 항목인지 판정한다. 텍스트가 예시 문구와 같고 링크가 없어야 한다.

    Args:
        block: 블록 객체.

    Returns:
        템플릿 예시 항목이면 True.
    """
    if block.get("type") not in EMPTY_TYPES or _norm(block_text(block)) not in EXAMPLE_NORMS:
        return False
    return not any(_is_link(rt) for rt in rich_texts(block))


def is_content(block: dict) -> bool:
    """작성한 내용인지 판정한다.

    - 구분선, 목차, 상위 경로 블록(NON_CONTENT_TYPES)은 내용이 아니다.
    - 다른 블록을 담는 블록(CONTAINER_TYPES), heading, 빈 블록, 템플릿 예시 항목은 하위 블록에 내용이 있을 때만
      내용이다. 구역 안의 heading은 템플릿에 없는 heading이므로 heading 문구로는 세지 않는다.
    - 안내 문구가 들어 있는 블록은 안내 문구를 지운 뒤 글자가 남거나, 하위 블록에 내용이 있을 때만 내용이다.
    - toggle은 다른 텍스트 블록과 같다. 제목이 내용이거나 하위 블록에 내용이 있으면 내용이다.

    Args:
        block: 블록 객체.

    Returns:
        작성한 내용이면 True.
    """
    kind = block.get("type")
    if kind in NON_CONTENT_TYPES:
        return False
    if kind in CONTAINER_TYPES or kind in HEADING_TYPES or is_empty(block) or is_example(block):
        return any(is_content(c) for c in children(block))
    if is_guide(block):
        return bool(guide_residual(block)) or any(is_content(c) for c in children(block))
    return True


def template_kind(blocks: list[dict]) -> str:
    """최상위 heading 문구의 집합으로 템플릿 종류를 판정한다.

    Args:
        blocks: 페이지의 최상위 블록.

    Returns:
        heading이 더 많이 겹치는 템플릿(engineering, research). 같으면 engineering, 하나도 없으면 none.
    """
    found = {_norm(block_text(b)) for b in blocks if b.get("type") in HEADING_TYPES}
    scores = {kind: len(found & {_norm(h) for h in heads}) for kind, heads in TEMPLATE_HEADINGS.items()}
    best = max(scores, key=lambda kind: scores[kind])  # 같은 점수면 앞의 engineering
    return best if scores[best] else KIND_NONE


def find_sections(blocks: list[dict], kind: str) -> dict[str, list[dict]]:
    """템플릿 heading부터 다음 템플릿 heading 전까지의 최상위 블록을 구역으로 묶는다.

    - 구역은 TEMPLATE_HEADINGS의 heading을 만날 때만 바뀐다. 다른 템플릿의 heading이면 구역 밖이 된다.
    - 템플릿 heading이 toggle heading(has_children이 참)이면 그 하위 블록도 구역에 넣는다.
    - 템플릿에 없는 heading은 현재 구역의 블록으로 남긴다. toggle heading이면 하위 블록을 구역의 최상위 블록으로
      넣는다.

    Args:
        blocks: 페이지의 최상위 블록.
        kind: template_kind의 결과.

    Returns:
        구역 키(TEMPLATE_HEADINGS의 값) -> 구역 블록 목록. kind가 none이면 빈 dict.
    """
    names = {_norm(h): key for h, key in TEMPLATE_HEADINGS.get(kind, {}).items()}
    sections: dict[str, list[dict]] = {}
    current = None
    for b in blocks:
        heading = _norm(block_text(b)) if b.get("type") in HEADING_TYPES else None
        if heading in ALL_HEADING_NORMS:
            current = names.get(heading)
            if current is not None:
                sections.setdefault(current, []).extend(children(b))
        elif current is not None:
            if heading is not None and children(b):
                sections[current] += [{**b, "children": []}, *children(b)]
            else:
                sections[current].append(b)
    return sections


def top_items(blocks: list[dict]) -> list[dict]:
    """구역의 최상위 목록 항목(bulleted_list_item, numbered_list_item, to_do 중 텍스트가 있는 것).

    Args:
        blocks: 구역 블록 목록.

    Returns:
        목록 항목 블록. 하위 항목과 paragraph는 넣지 않는다.
    """
    return [b for b in blocks if b.get("type") in LIST_TYPES and block_text(b).strip()]


def item_mark(block: dict) -> str | None:
    """결과 항목의 표기. to_do는 체크를 ✅, 미체크를 ❌로 간주한다. ❌와 ✅가 함께 있으면 ❌로 간주한다.

    Args:
        block: 목록 항목 블록.

    Returns:
        OK_MARK, FAIL_MARK 중 하나. 표기가 없으면 None.
    """
    if block.get("type") == "to_do":
        return OK_MARK if (block.get("to_do") or {}).get("checked") else FAIL_MARK
    text = block_text(block)
    if FAIL_MARK in text:
        return FAIL_MARK
    return OK_MARK if OK_MARK in text else None


def has_evidence(blocks: list[dict]) -> bool:
    """근거(링크가 걸린 텍스트, EVIDENCE_MENTIONS의 mention, EVIDENCE_BLOCKS의 블록)가 하위 블록까지 하나라도 있는지 확인한다.

    Args:
        blocks: 구역 블록 목록.

    Returns:
        근거가 있으면 True.
    """
    return any(b.get("type") in EVIDENCE_BLOCKS or any(_is_link(rt) for rt in rich_texts(b)) for b in walk(blocks))


def analyze_page(info: PageInfo, blocks: list[dict], comments: list[dict] | None, scope: str) -> PageResult:
    """페이지 하나의 구역 작성 여부와 N1, N3–N8을 판정한다.

    Args:
        info: 페이지 값.
        blocks: fetch_tree로 읽은 페이지 블록.
        comments: fetch_comments로 읽은 comment. 조회하지 않은 페이지는 None.
        scope: comment 조회 방식(SCOPE_ALL, SCOPE_PAGE). 조회하지 않은 페이지는 빈 문자열.

    Returns:
        PageResult.
    """
    kind = template_kind(blocks)
    sections = find_sections(blocks, kind)
    written = {key: any(is_content(b) for b in sections.get(key, [])) for key in SECTION_LABELS}
    criteria = top_items(sections.get("criteria", []))
    results = top_items(sections.get("result", []))
    marks = [item_mark(b) for b in results]
    fails = [b for b, m in zip(results, marks) if m == FAIL_MARK]
    found = comments or []
    r = PageResult(
        page=info,
        kind=kind,
        written=written,
        criteria_items=len(criteria),
        result_items=len(results),
        ok_items=marks.count(OK_MARK),
        fail_items=len(fails),
        comment_count=None if comments is None else len(comments),
        comment_scope=scope if comments is not None else "",
    )
    if is_status(info.status, DONE):
        authors = {norm_id(a) for a in ((c.get("created_by") or {}).get("id") for c in found) if a}
        r.n1 = bool(authors - info.assignees)
        r.n3 = len(criteria) == len(results) >= 1
        r.n4 = bool(results) and all(m is not None for m in marks)
        r.n5 = all(any(is_content(c) for c in children(b)) for b in fails) if fails else None
        r.n6 = has_evidence(sections.get("result", []))
        r.n7 = all(written.values()) and r.n3 and r.n4 and r.n6 and r.n5 is not False
    if is_status(info.status, HOLDING):
        r.n8 = bool(found)
    return r


def needs_comments(info: PageInfo) -> bool:
    """comment를 조회하는 페이지(Done, Holding)인지 확인한다.

    Args:
        info: 페이지 값.

    Returns:
        Done이나 Holding이면 True.
    """
    return is_status(info.status, DONE) or is_status(info.status, HOLDING)


def process_page(client: NotionReader, info: PageInfo, page_only: bool) -> PageResult:
    """페이지 하나의 블록을 읽고, Done이나 Holding이면 comment도 읽어 판정한다.

    Args:
        client: Notion 읽기 객체.
        info: 페이지 값.
        page_only: True면 comment를 페이지 ID로만 조회한다.

    Returns:
        PageResult.
    """
    blocks = fetch_tree(client, info.id)
    if not needs_comments(info):
        return analyze_page(info, blocks, None, "")
    comments = fetch_comments(client, info.id, blocks, page_only)
    return analyze_page(info, blocks, comments, SCOPE_PAGE if page_only else SCOPE_ALL)


def _rate(a: int, b: int) -> str:
    """비율 문구. 예: 50.0% (2/4)."""
    return f"{a / b * 100:.1f}% ({a}/{b})" if b else "계산할 표본이 없음 (0/0)"


def _judged(results: list[PageResult], attr: str) -> tuple[int, int]:
    """판정 값이 있는 페이지 중 만족한 페이지 수와 판정 값이 있는 페이지 수."""
    values = [getattr(r, attr) for r in results if getattr(r, attr) is not None]
    return sum(values), len(values)


def _metric(title: str, label: str, a: int, b: int, method: str, extra: list[str] | None = None) -> list[str]:
    """지표 절 하나. 비율(a/b), 표본 수 n, 계산 방법을 적는다."""
    return [f"## {title}", "", f"- {label}은 {_rate(a, b)}, n={b}임", f"  - 계산 방법: {method}", *(extra or []), ""]


def _n2(results: list[PageResult]) -> list[str]:
    """N2 구역별 작성률."""
    n = len(results)
    lines = [
        "## N2 구역별 작성률",
        "",
        f"- 대상 페이지 전체(n={n}) 중 구역마다 작성한 페이지의 비율은 아래와 같음",
        "  - 계산 방법: 구역 안에 작성한 내용이 있는 블록이 1개 이상 있으면 작성으로 셈",
        "  - 안내 문구만 있는 블록, 빈 블록(텍스트 없는 paragraph·목록 항목·체크박스 등), 템플릿 예시 항목, 구분선·목차·"
        "상위 경로 블록은 작성한 내용으로 세지 않음",
        "  - 안내 문구 뒤에 글자를 이어 적었거나 안내 문구 아래에 하위 블록을 적었으면 작성으로 셈",
        "  - 다른 블록을 담는 블록(열, 표 등)은 하위 블록에 작성한 내용이 있을 때만 셈. 토글은 제목이나 하위 블록에 "
        "작성한 내용이 있으면 셈",
        "  - 구역 안에 템플릿에 없는 heading이 있으면 heading 문구는 세지 않고 그 아래 블록으로 판정함",
        "  - 템플릿 구역 heading을 찾지 못한 페이지(템플릿 종류 none)는 모든 구역을 미작성으로 셈",
    ]
    for key, label in SECTION_LABELS.items():
        lines.append(f"- {label}: {_rate(sum(r.written[key] for r in results), n)}")
    lines.append(f"- 세 구역 모두: {_rate(sum(r.all_written() for r in results), n)}")
    return lines + [""]


def _n9(results: list[PageResult]) -> list[str]:
    """N9 월별 task 수."""
    lines = [
        "## N9 월별 task 수",
        "",
        "- 최초 생성 월(한국 시간)별 대상 페이지 수, Done 수, Done 비중은 아래 표와 같음",
        "  - 계산 방법: Done 비중은 그 달 Done 수 / 그 달 대상 페이지 수임",
        "",
        "| 최초 생성 월 | 대상 페이지 수 | Done 수 | Done 비중(Done 수/대상 페이지 수) |",
        "|---|---|---|---|",
    ]
    by_month: dict[str, list[PageResult]] = {}
    for r in results:
        by_month.setdefault(month(r.page.created), []).append(r)
    for m, rs in sorted(by_month.items()):
        done = sum(r.done for r in rs)
        lines.append(f"| {m} | {len(rs)} | {done} | {_rate(done, len(rs))} |")
    return lines + [""]


def _count_line(counter: Counter, order: list[str]) -> str:
    """'이름 n개' 목록 문구. order에 있는 이름을 먼저 적고 나머지는 이름순으로 적는다."""
    names = [x for x in order if x in counter] + sorted(x for x in counter if x not in order)
    return ", ".join(f"{x or '(빈 값)'} {counter[x]}개" for x in names) or "없음"


def render_summary(
    results: list[PageResult],
    *,
    fetched: int,
    matched: int,
    errors: int,
    assignees: int,
    since: dt.date | None,
    until: dt.date | None,
    page_only: bool,
    limit: int | None,
) -> str:
    """summary.md 본문. 지표마다 비율(a/b), 표본 수 n, 계산 방법을 적고 개인별 값과 담당자는 넣지 않는다.

    Args:
        results: 집계한 페이지(error가 없는 대상 페이지)의 판정 결과.
        fetched: data source에서 조회한 페이지 수.
        matched: 대상 조건을 만족한 페이지 수(--limit 적용 전).
        errors: 조회나 판정이 실패해 집계에서 뺀 페이지 수.
        assignees: --assignees로 받은 담당자 수.
        since: 기간 시작 날짜.
        until: 기간 끝 날짜.
        page_only: comment를 페이지 ID로만 조회했으면 True.
        limit: --limit 값. 없으면 None.

    Returns:
        markdown 본문.
    """
    period = f"{since.isoformat() if since else '제한 없음'}–{until.isoformat() if until else '제한 없음'}"
    statuses = Counter(r.page.status for r in results)
    kinds = Counter(r.kind for r in results)
    scope = "페이지 ID로만 조회함" if page_only else "페이지 ID와 페이지 안 모든 블록 ID로 조회함"
    lines = [
        "# 문서화 Agent 도입 이전 현황 지표 집계(업무 공유 체계(Kanban)의 'Task 단위')",
        "",
        f"- 기간은 최초 생성 일자 {period}임(한국 시간 날짜, 양 끝 날짜 포함)",
        f"- 대상 조건은 기간 안에 만든 페이지 중 `--assignees`로 받은 담당자 {assignees}명 중 한 명 이상이 담당한 페이지임",
        "  - 제목이 템플릿 페이지(Engineering Template, Research Template과 그 변형 1개)와 같은 페이지는 뺌",
        f"- 대상 조건을 만족한 페이지는 {matched}개임(data source에서 조회한 페이지 {fetched}개 중)",
    ]
    if limit is not None:
        lines.append(f"  - `--limit {limit}`을 적용해 최초 생성 일자 순으로 앞의 {min(limit, matched)}개만 처리함")
    if errors:
        lines.append(f"  - Notion 조회나 판정이 실패해 집계에서 뺀 페이지는 {errors}개임(pages.csv의 error 열)")
    lines += [
        f"- 집계한 페이지는 {len(results)}개임",
        f"  - 상태별: {_count_line(statuses, STATUS_ORDER)}",
        f"  - 템플릿 종류별: {_count_line(kinds, [*TEMPLATE_HEADINGS, KIND_NONE])}",
        "  - 템플릿 종류는 최상위 heading 문구로 판정함. none은 템플릿 구역 heading을 찾지 못한 페이지임",
        f"- comment는 Done과 Holding인 페이지만 조회함(N1, N8에만 사용). 조회 방식은 {scope}",
        "  - Notion API는 해결되지 않은 comment만 돌려주므로 해결된 comment는 세지 않음",
        "- 수치는 팀 합계이고 개인별 값은 넣지 않음. 판정은 집계 시점의 페이지 상태로 함",
        "",
    ]
    a, b = _judged(results, "n1")
    lines += _metric(
        "N1 리뷰 흔적 비율",
        "리뷰 흔적 비율",
        a,
        b,
        "Done인 대상 페이지(분모) 중 comment 작성자에 그 페이지 담당자가 아닌 사람이 1명 이상 있는 페이지(분자)의 비율임",
    )
    lines += _n2(results)
    a, b = _judged(results, "n3")
    lines += _metric(
        "N3 1:1 대응률",
        "1:1 대응률",
        a,
        b,
        "Done인 대상 페이지(분모) 중 Task 완료 기준과 결과 구역의 최상위 목록 항목(텍스트가 있는 글머리 기호·번호·체크박스 "
        "항목) 수가 같고 1개 이상인 페이지(분자)의 비율임",
        ["  - 구역 안에 템플릿에 없는 heading이 있으면 그 아래 목록 항목도 최상위 목록 항목으로 셈"],
    )
    a, b = _judged(results, "n4")
    lines += _metric(
        "N4 ✅/❌ 표기율",
        "✅/❌ 표기율",
        a,
        b,
        "Done인 대상 페이지(분모) 중 결과 구역의 최상위 목록 항목이 1개 이상이고 모두 ✅ 또는 ❌를 포함하는 페이지(분자)의 "
        "비율임. 체크박스는 체크를 ✅, 미체크를 ❌로 셈",
    )
    a, b = _judged(results, "n5")
    lines += _metric(
        "N5 미달성 사유율",
        "미달성 사유율",
        a,
        b,
        "❌ 항목이 1개 이상인 Done 대상 페이지(분모) 중 ❌ 항목마다 작성한 내용(N2와 같은 판정)이 있는 하위 블록이 1개 "
        "이상인 페이지(분자)의 비율임",
        ["  - 추정: 내용이 있는 하위 블록이 있으면 사유가 있다고 간주하는 근사 규칙이라 실제 사유 기록과 다를 수 있음"],
    )
    a, b = _judged(results, "n6")
    lines += _metric(
        "N6 근거 링크율",
        "근거 링크율",
        a,
        b,
        "Done인 대상 페이지(분모) 중 결과 구역(하위 블록 포함)에 근거가 1개 이상 있는 페이지(분자)의 비율임",
        [
            f"  - 근거: 링크가 걸린 텍스트, {', '.join(EVIDENCE_MENTIONS.values())}, "
            f"{', '.join(EVIDENCE_BLOCKS.values())} 블록",
            "  - 날짜 mention과 사용자 mention은 근거로 세지 않음",
        ],
    )
    a, b = _judged(results, "n7")
    lines += _metric(
        "N7 문서 완성률",
        "문서 완성률",
        a,
        b,
        "Done인 대상 페이지(분모) 중 N2의 세 구역 작성, N3, N4, N6을 모두 만족하고, ❌ 항목이 있으면 N5도 만족하는 "
        "페이지(분자)의 비율임",
    )
    a, b = _judged(results, "n8")
    lines += _metric(
        "N8 Holding 사유 comment율",
        "Holding 사유 comment율",
        a,
        b,
        "Holding인 대상 페이지(분모) 중 comment가 1개 이상 있는 페이지(분자)의 비율임",
        ["  - 추정: comment가 있으면 사유가 있다고 간주하는 근사 규칙이라 실제 사유 기록과 다를 수 있음"],
    )
    lines += _n9(results)
    return "\n".join(lines).rstrip() + "\n"


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """CSV 파일을 쓴다.

    Args:
        path: 파일 경로.
        columns: 열 이름 목록.
        rows: 행 목록.
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(client: NotionReader, args: argparse.Namespace) -> list[PageResult]:
    """Notion 페이지를 읽어 지표를 계산하고 pages.csv, summary.md를 쓴다.

    페이지 하나가 실패하면 pages.csv의 error 열에 적고 다음 페이지로 넘어간다. comment 조회가 HTTP 403이면
    CommentPermissionError를 그대로 올려 바로 멈춘다.

    Args:
        client: Notion 읽기 객체.
        args: parse_args의 결과.

    Returns:
        집계한 페이지(error가 없는 대상 페이지)의 판정 결과.
    """
    start, end = period_bounds(args.since, args.until)
    assignees = {norm_id(x) for x in args.assignees}
    pages = client.query_pages(args.data_source)
    matched = select_pages([page_info(p) for p in pages], assignees, start, end)
    targets = matched if args.limit is None else matched[: args.limit]
    results: list[PageResult] = []
    rows: list[dict[str, Any]] = []
    for i, info in enumerate(targets, 1):
        try:
            result = process_page(client, info, args.page_comments_only)
        except CommentPermissionError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError, http.client.HTTPException) as e:
            print(f"[notion_baseline] 페이지 {info.id} 실패: {e}", file=sys.stderr)
            rows.append(error_row(info, f"{type(e).__name__}: {e}"))
            continue
        results.append(result)
        rows.append(result.row())
        if i % 10 == 0 or i == len(targets):
            print(f"[notion_baseline] 페이지 {i}/{len(targets)} 읽음", file=sys.stderr)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "pages.csv", PAGE_COLUMNS, rows)
    summary = render_summary(
        results,
        fetched=len(pages),
        matched=len(matched),
        errors=len(rows) - len(results),
        assignees=len(assignees),
        since=args.since,
        until=args.until,
        page_only=args.page_comments_only,
        limit=args.limit,
    )
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print(f"[notion_baseline] 대상 페이지 {len(rows)}개 중 {len(results)}개 집계: {out}/pages.csv, summary.md")
    return results


def _id_list(s: str) -> list[str]:
    """쉼표로 구분한 ID 목록."""
    return [x.strip() for x in s.split(",") if x.strip()]


def _positive_int(s: str) -> int:
    """1 이상의 정수."""
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 함")
    return n


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """명령줄 인자를 읽는다.

    Args:
        argv: 인자 목록. 없으면 sys.argv를 읽는다.

    Returns:
        인자 값.
    """
    ap = argparse.ArgumentParser(
        description="'Task 단위' 페이지에서 문서화 Agent 도입 이전 현황 지표(N1–N9)를 집계한다."
    )
    ap.add_argument("--data-source", required=True, help="'Task 단위'의 data source ID")
    ap.add_argument("--assignees", type=_id_list, required=True, help="대상 담당자의 Notion 사용자 ID(쉼표로 구분)")
    ap.add_argument("--since", type=dt.date.fromisoformat, help="최초 생성 일자 시작 YYYY-MM-DD(한국 시간)")
    ap.add_argument("--until", type=dt.date.fromisoformat, help="최초 생성 일자 끝 YYYY-MM-DD(한국 시간, 그날 포함)")
    ap.add_argument("--out", type=Path, required=True, help="출력 폴더")
    ap.add_argument("--page-comments-only", action="store_true", help="comment를 페이지 ID로만 조회한다")
    ap.add_argument("--limit", type=_positive_int, help="대상 페이지 중 최초 생성 일자 순으로 앞의 N개만 처리한다")
    return ap.parse_args(argv)


def main() -> None:
    """환경 변수 NOTION_TOKEN으로 클라이언트를 만들고 집계를 실행한다."""
    args = parse_args()
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("[notion_baseline] 환경 변수 NOTION_TOKEN 필요(Notion integration token)", file=sys.stderr)
        sys.exit(1)
    try:
        run(NotionClient(token), args)
    except NotionError as e:
        sys.exit(f"[notion_baseline] {e}")


if __name__ == "__main__":
    main()
