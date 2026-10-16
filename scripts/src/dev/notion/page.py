"""Notion 페이지 값, 판정 결과, pages.csv 행. 페이지 선택, 블록과 comment 조회, 페이지 하나의 판정을 담는다."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from src.common.period import in_period
from src.constant.time import KST
from src.dev.notion.client import NotionReader
from src.dev.notion.template import (
    FAIL_MARK,
    NO_DESCEND,
    OK_MARK,
    SECTION_LABELS,
    TEMPLATE_TITLES,
    _rt_text,
    children,
    find_sections,
    has_evidence,
    is_content,
    item_mark,
    template_kind,
    top_items,
    walk,
)

STATUS_PROP = "상태"
ASSIGNEE_PROP = "담당자"
DONE = "done"  # 상태 값 Done. 대소문자 무시로 비교
HOLDING = "holding"  # 상태 값 Holding. 대소문자 무시로 비교
STATUS_ORDER = ["Not started", "In progress", "Holding", "Done"]
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
