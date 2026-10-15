#!/usr/bin/env python3
"""report 모드: jira-doc 정기 보고 메시지(월간, 분기, 연간 묶음)를 Jira 기록으로 계산해 Slack 본문을 만든다.

- 진입 함수는 run_report다. scripts/apply.py의 main이 --mode report일 때 부르고, 반환한 메시지를 묶음마다 팀장
  채널로 보낸다.
- 지표 정의는 docs/jira-doc-agent/alerts.md(3절 지표 정의, 4절 알림 체계)를 따른다.
- Jira는 읽기만 한다(scripts/jira_api.py의 Jira.search, changelog, comments, prop_get).
- PR은 gh CLI의 검색(gh search prs)으로 읽기만 한다. 실패하면 PR 추가를 업데이트 기준에서 뺀다.
- 검수 comment와 문서화 리뷰 comment의 문구는 scripts/apply.py의 review_comment, doc_review_comment, check_table,
  VERDICT_KO, RESULT_KO가 만든다. apply.py가 이 모듈을 불러오므로 순환 import를 피하려고 apply.py를 불러오지 않고
  같은 문구를 이 파일에 둔다. 두 파일의 값이 같은지는 tests/jira/test_report.py가 확인한다.
- 달, 분기, 해는 한국 시간으로 나눈다.
- Summary 탭(summ.extra)에는 팀 합계만 남긴다. 담당자 이름, 담당자별 수치, 구두 피드백 대상 후보는 Slack 본문에만 적는다.
  public repo의 Actions 실행 화면은 GitHub에 로그인한 누구나 열람할 수 있기 때문이다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).parent))
from jira_api import find_section, is_agent_comment  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))  # 달, 분기, 해를 나누는 시간대
READY_STATUS, IN_PROGRESS, DONE_STATUS = "ready-to-done", "In Progress", "done"  # Jira 상태 이름. 대소문자 무시로 비교
TODO_STATUS, STOP_STATUS, DELETED_STATUS = "To Do", "Backlog", "Deleted"
CLOSED_STATUSES = (DONE_STATUS, STOP_STATUS, DELETED_STATUS)  # 진행 상태가 아닌 상태
# 새 ticket의 상태별 수를 적는 순서. 목록에 없는 상태는 뒤에 이름 순으로 적는다
STATUS_ORDER = (TODO_STATUS, IN_PROGRESS, READY_STATUS, DONE_STATUS, STOP_STATUS, DELETED_STATUS)
KR_TYPE = "KR"  # 분기 묶음에서 상위 ticket으로 묶는 work type
# 대상 work type. scripts/collect.py의 TARGET_TYPES, TYPE_ALIAS와 같음
TARGET_TYPES = [t.strip() for t in os.environ.get("TARGET_TYPES", "Task,Bug,Issue").split(",")]
TYPE_ALIAS = {"작업": "Task", "버그": "Bug", "이슈": "Issue", "task": "Task", "bug": "Bug", "issue": "Issue"}
ISSUE_FIELDS = ["summary", "status", "assignee", "issuetype", "parent", "description", "created"]
SUBTASK_FIELDS = ["parent", "created"]  # sub-task 검색에서 받는 필드
SEARCH_LIMIT = 5000  # Jira.search의 결과 상한. 닿으면 본문에 알린다
PR_LIMIT = 1000  # gh search prs의 결과 상한
# scripts/apply.py의 VERDICT_KO(정리 모드 None 제외), RESULT_KO와 같은 값
VERDICT_KO = {"pass": "통과", "fix": "보류", "escalate": "검토 요청"}
RESULT_KO = {"pass": "✅ 만족", "fail": "❌ 보완 필요", "n/a": "➖ 해당 없음"}
RESULT_CODE = {label: code for code, label in RESULT_KO.items()}  # 검사 표 결과 칸 -> pass, fail, n/a
VERDICT_PREFIX, VERDICT_SUFFIX = "검수 결과: *", "*"  # scripts/apply.py review_comment의 첫 줄 "검수 결과: *통과*"
VERDICT_RE = re.compile(
    rf"^{re.escape(VERDICT_PREFIX)}({'|'.join(map(re.escape, VERDICT_KO.values()))}){re.escape(VERDICT_SUFFIX)}\s*$",
    re.M,
)
# scripts/apply.py doc_review_comment의 첫 줄 "문서화 리뷰 결과(관찰 모드): *통과*"
DOC_REVIEW_RE = re.compile(r"^문서화 리뷰 결과\(관찰 모드\): \*[^*\n]+\*\s*$", re.M)
ROW_RE = re.compile(  # 검사 표의 행 "| 이름 (ID) | 결과 | 내용 |". 내용 칸은 wiki 링크의 "|"를 품을 수 있어 읽지 않는다
    rf"^\|(?!\|)\s*(?P<label>[^|]*?)\s*\|\s*(?P<result>{'|'.join(map(re.escape, RESULT_CODE))})\s*\|", re.M
)
LABEL_ID_RE = re.compile(r"\(([^()\s]+)\)$")  # check_label의 끝 "(ID)"
# 자동 보류 전환이 있던 검수 comment의 문구(자동 보류 전환을 없애기 전 scripts/apply.py의 review_transition)
AUTO_HOLD_NOTE = "상태를 in-progress로 되돌림"
DELIVERABLES_RE = re.compile(r"예상 산출물 (\d+)개 중 (\d+)개 달성")  # apply.py deliverables_summary의 달성 요약 줄
BRANCHES = {"first": "첫 검수", "cycle": "반려 cycle 재검수", "retry": "재실행·실패", "digest": "정리 모드"}
TOKEN_KO = {"input": "input", "output": "output", "cacheWrite": "cache 쓰기", "cacheRead": "cache 읽기"}
HIGH_RATIO, HIGH_MIN_PREV_REVIEWS = 1.5, 10  # 사용량 많음: 비용이 전월의 1.5배 이상이고 전월 검수 실행이 10건 이상
CANDIDATE_MIN = 2  # 구두 피드백 대상 후보: 반려 2회 이상 또는 같은 검사 ID ❌ 2회 이상
TOP_QUARTER = 5  # 분기 묶음의 검사 ID별 ❌ 상위 개수
TOP_YEAR = 3  # 연간 묶음에서 분기별 만족률을 적는 연간 ❌ 상위 검사 ID 수
RECENT_N = 20  # 최근 판정 일치율의 표본 수
MAX_LINES = 30  # 메시지 하나의 줄 수 상한. 넘으면 팀원별 줄을 "외 n명"으로 줄인다
KR_MAX = 5  # 분기 묶음에 적는 KR 행 수 상한. 넘으면 "외 n개"로 줄인다
CANDIDATE_IDS = 2  # 후보 표시에 적는 조건 B 검사 ID 수. 넘으면 "외 n"으로 줄인다
DEF_LINE = "지표 정의: docs/jira-doc-agent/alerts.md"  # 메시지 끝 줄
NAME_MAX = 12  # 팀원별 줄에 적는 담당자 이름 글자 수 상한. 넘으면 자르고 "…"를 붙인다
NEW_STATUS_LINES = 2  # 새 ticket 상태별 수의 하위 줄 수 상한. 넘으면 뒤 상태를 "외 n종"으로 줄인다
# jira-doc(scripts/apply.py의 set_section)이 고치는 description 구역. 이 구역만 바뀐 수정은 업데이트로 세지 않는다
DELIVERABLES_SECTION = "결과 산출물"
LEAD_WAIT, OWNER_IDLE, OTHER_IDLE = "팀장 확인 대기", "담당자", "기타"  # 업데이트 없는 기존 ticket의 원인
INDENT = "    "  # Slack 본문의 하위 항목 들여쓰기(scripts/apply.py의 SLACK_INDENT와 같음)
# 판정 일치율의 결과 값. MATCH, MISMATCH만 일치율에 들어간다
MATCH, MISMATCH, ESCALATE = "일치", "불일치", "검토 요청"
AUTO_HOLD, NOT_LEAD, WAITING, OTHER = "자동 보류", "팀장 외 전환", "전환 없음", "기타 전환"
Span = tuple[dt.datetime, dt.datetime]  # 기간 [시작, 끝)


class JiraReader(Protocol):
    """정기 보고에 필요한 Jira 읽기 메서드. scripts/jira_api.py의 Jira, 테스트의 가짜 객체가 이 모양을 따른다."""

    def search(self, jql: str, fields: list[str], limit: int = 500) -> list[dict]:
        """JQL로 ticket을 찾는다."""
        ...

    def changelog(self, key: str) -> list[dict]:
        """ticket의 변경 이력(Jira 응답의 values)을 돌려준다."""
        ...

    def comments(self, key: str) -> list[dict]:
        """ticket의 comment 목록을 작성 순서로 돌려준다."""
        ...

    def prop_get(self, key: str) -> dict:
        """jira-doc 기록(issue property ai-doc-agent)을 돌려준다."""
        ...


class SummaryLike(Protocol):
    """Summary 탭 기록 객체(scripts/apply.py의 Summary). 이 모듈은 extra만 사용한다."""

    extra: list[str]

    def row(self, key: str, itype: str, mode: str, result: str, note: str = "") -> None:
        """실행 결과 표에 행 하나를 추가한다."""
        ...


class Judged(Protocol):
    """판정 일치율을 계산할 검수 comment. ReviewComment와 scripts/dev/jira_metrics.py의 Review가 이 모양을 따른다."""

    at: dt.datetime
    verdict: str
    auto_hold: bool


@dataclass(frozen=True)
class StatusChange:
    """changelog의 status 변경 하나."""

    at: dt.datetime
    dst: str
    src: str = ""
    author: str = ""  # 전환한 사람의 Jira accountId


@dataclass
class ReviewComment:
    """검수 comment 또는 문서화 리뷰 comment 하나의 판정과 검사 표 결과."""

    at: dt.datetime
    verdict: str  # 검수 결과 문구(통과, 보류, 검토 요청). 문서화 리뷰 comment는 빈 문자열
    results: dict[str, str]  # 검사 ID -> 결과 코드값(pass, fail, n/a)
    auto_hold: bool = False  # 보류이고 AUTO_HOLD_NOTE가 있음


@dataclass
class Run:
    """jira-doc 기록 runs의 실행 하나."""

    at: dt.datetime
    branch: str  # BRANCHES의 키
    mode: str
    request_no: int | None  # 몇 번째 request 전환에 대한 검수인지. 정리 모드는 None
    cost: float | None
    tokens: dict[str, int]
    failed: bool = False  # 반영에 실패한 실행(verdict failed)


@dataclass
class Ticket:
    """Jira에서 읽은 ticket 하나의 기록."""

    key: str
    itype: str
    assignee_id: str
    assignee_name: str
    parent_key: str
    parent_name: str
    parent_type: str
    description: str
    created: dt.datetime | None = None  # 만든 시각
    status: str = ""  # 지금 상태
    changes: list[StatusChange] = field(default_factory=list)
    reviews: list[ReviewComment] = field(default_factory=list)  # 검수 comment. 작성 순
    docs: list[ReviewComment] = field(default_factory=list)  # 문서화 리뷰 comment. 작성 순
    runs: list[Run] = field(default_factory=list)
    human_comment_at: list[dt.datetime] = field(default_factory=list)  # 사람 comment(agent comment 제외) 작성 시각
    description_edit_at: list[dt.datetime] = field(default_factory=list)  # 변경 이력의 description 항목 시각
    subtask_at: list[dt.datetime] = field(default_factory=list)  # 하위 sub-task 생성 시각(attach_updates가 채움)
    pr_at: list[dt.datetime] = field(default_factory=list)  # 제목에 ticket 키가 있는 PR 생성 시각(attach_updates)


@dataclass
class Usage:
    """한 기간의 Claude 사용량."""

    runs: Counter = field(default_factory=Counter)  # 갈래 -> 실행 수
    cost: dict[str, float] = field(default_factory=lambda: dict.fromkeys(BRANCHES, 0.0))  # 갈래 -> API 환산 비용
    tokens: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOKEN_KO, 0))  # 토큰 종류 -> 수
    review_runs: int = 0  # mode가 review인 실행 수
    missing_cost: int = 0  # costUsd가 없는 실행 수

    @property
    def total_runs(self) -> int:
        """네 갈래의 실행 수 합."""
        return sum(self.runs.values())

    @property
    def total_cost(self) -> float:
        """네 갈래의 API 환산 비용 합."""
        return sum(self.cost.values())


@dataclass
class Candidate:
    """구두 피드백 대상 후보 한 명."""

    name: str
    rejected: list[tuple[str, int]]  # 조건 A: (ticket 키, 반려 횟수)
    failed: list[tuple[str, int]]  # 조건 B: (검사 ID, ❌ 횟수)


@dataclass
class KrRow:
    """분기 묶음의 KR 한 행."""

    key: str
    name: str
    done: int = 0
    expected: int = 0
    achieved: int = 0
    no_line: int = 0  # 달성 요약 줄이 없어 달성률 집계에서 뺀 Task 수


@dataclass
class Overview:
    """대상 기간의 ticket 현황(ticket_overview의 결과)."""

    existing: int = 0  # 기존 ticket 수
    updated: int = 0  # 기존 ticket 중 업데이트가 있는 수
    updated_done: int = 0  # 그중 대상 기간에 done으로 전환된 수
    idle: Counter = field(default_factory=Counter)  # 업데이트 없는 기존 ticket의 원인 -> 수
    new: Counter = field(default_factory=Counter)  # 새 ticket의 지금 상태 -> 수

    @property
    def idle_total(self) -> int:
        """업데이트 없는 기존 ticket 수."""
        return sum(self.idle.values())

    @property
    def new_total(self) -> int:
        """새 ticket 수."""
        return sum(self.new.values())


@dataclass
class MemberRow:
    """팀원별 절의 담당자 한 명(member_rows의 결과)."""

    aid: str  # 담당자 Jira accountId
    name: str
    first: tuple[int, int]  # 첫 검수 통과율(통과 수, 첫 검수 comment 수)
    max_request: tuple[int, str]  # 최대 검수 반복 횟수(횟수, ticket 키)
    stop: tuple[int, int]  # stop 사유 기록률(R4 만족 수, R4 판정 수)
    idle: int  # 업데이트 없는 기존 ticket 수


def parse_ts(s: str | None) -> dt.datetime | None:
    """Jira 시각 문자열(예: 2026-09-02T10:31:00.000+0900)을 시간대가 있는 datetime으로 바꾼다.

    Args:
        s: Jira 시각 문자열 또는 ISO 8601 문자열. 시간대가 없으면 한국 시간으로 본다.

    Returns:
        datetime. 비었거나 읽을 수 없으면 None.
    """
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=KST)


def is_status(name: str, target: str) -> bool:
    """Jira 상태 이름이 target과 같은지 대소문자를 무시하고 비교한다.

    Args:
        name: 상태 이름.
        target: 비교할 상태 이름.

    Returns:
        같으면 True.
    """
    return name.strip().lower() == target.strip().lower()


def table_rows(body: str) -> list[tuple[str, str, str]]:
    """comment 본문의 검사 표에서 행을 읽는다.

    Args:
        body: comment 본문(wiki markup).

    Returns:
        (검사 ID, 검사 칸 문구 "검사 이름 (ID)", 결과 코드값(pass, fail, n/a)) 목록. 표 순서.
    """
    out: list[tuple[str, str, str]] = []
    for row in ROW_RE.finditer(body):
        idm = LABEL_ID_RE.search(row["label"])
        if idm:
            out.append((idm.group(1), row["label"], RESULT_CODE[row["result"]]))
    return out


def parse_review(body: str) -> tuple[str, dict[str, str]] | None:
    """검수 comment 본문에서 판정과 검사 표의 결과를 읽는다.

    Args:
        body: comment 본문(wiki markup).

    Returns:
        (판정 문구, {검사 ID: 결과 코드값(pass, fail, n/a)}). 판정 줄이 없으면 None.
    """
    m = VERDICT_RE.search(body)
    if not m:
        return None
    return m.group(1), {cid: code for cid, _, code in table_rows(body)}


def parse_doc_review(body: str) -> dict[str, str] | None:
    """문서화 리뷰 comment 본문에서 검사 표의 결과를 읽는다.

    Args:
        body: comment 본문(wiki markup).

    Returns:
        {검사 ID: 결과 코드값(pass, fail, n/a)}. "문서화 리뷰 결과(관찰 모드): …" 줄이 없으면 None.
    """
    if not DOC_REVIEW_RE.search(body):
        return None
    return {cid: code for cid, _, code in table_rows(body)}


def status_changes(changelog: list[dict]) -> list[StatusChange]:
    """Jira changelog 응답(Jira.changelog)에서 status 변경만 시각 순서로 뽑는다.

    Args:
        changelog: Jira.changelog의 결과. 항목마다 created, author, items(field, fromString, toString).

    Returns:
        status 변경 목록. 전환 전 상태(src)와 전환한 사람의 accountId(author)도 담는다.
    """
    out: list[StatusChange] = []
    for history in changelog:
        at = parse_ts(history.get("created"))
        if at is None:
            continue
        author = (history.get("author") or {}).get("accountId") or ""
        for item in history.get("items") or []:
            if item.get("field") == "status":
                out.append(StatusChange(at, item.get("toString") or "", item.get("fromString") or "", author))
    return sorted(out, key=lambda c: c.at)


def without_deliverables(desc: str) -> str:
    """description에서 결과 산출물 구역(제목 줄 포함)을 빼고 공백을 한 칸으로 줄인 문자열.

    구역은 scripts/jira_api.py의 find_section으로 찾는다. set_section이 구역을 맨 뒤에 새로 붙이며 앞 본문 끝의
    공백을 지우므로 공백 차이는 무시한다.

    Args:
        desc: description(wiki markup).

    Returns:
        비교용 문자열.
    """
    desc = desc or ""
    hit = find_section(desc, DELIVERABLES_SECTION)
    if hit:
        desc = desc[: hit[1]] + desc[hit[3] :]
    return " ".join(desc.split())


def description_edits(changelog: list[dict]) -> list[dt.datetime]:
    """Jira changelog 응답(Jira.changelog)에서 결과 산출물 구역 밖이 바뀐 description 수정 시각을 시각 순서로 뽑는다.

    jira-doc이 결과 산출물 구역만 고친 수정은 사람의 업데이트가 아니므로 뺀다. jira-doc은 팀장 계정으로 기록되어
    작성자로는 구분할 수 없어, 수정 전후(fromString, toString)에서 그 구역을 뺀 나머지를 비교한다.

    Args:
        changelog: Jira.changelog의 결과. 항목마다 created, items(field, fromString, toString).

    Returns:
        결과 산출물 구역 밖이 바뀐 description 항목이 있는 변경 이력의 시각 목록.
    """
    out: list[dt.datetime] = []
    for history in changelog:
        at = parse_ts(history.get("created"))
        items = [i for i in history.get("items") or [] if i.get("field") == "description"]
        changed = any(
            without_deliverables(i.get("fromString") or "") != without_deliverables(i.get("toString") or "")
            for i in items
        )
        if at is not None and changed:
            out.append(at)
    return sorted(out)


def run_branch(entry: dict) -> str:
    """runs 항목 하나를 Claude 사용량 네 갈래 중 하나로 나눈다.

    verdict가 failed이거나 retry가 true면 재실행·실패, mode가 digest면 정리 모드, 그 밖에는 검수다.
    검수는 requestNo가 2 이상이면 반려 cycle 재검수, 1 이하이거나 없으면 첫 검수다.
    같은 request 전환에 다시 실행한 검수는 ticket의 실행 기록을 함께 봐야 하므로 mark_reruns가 바꾼다.

    Args:
        entry: runs 항목.

    Returns:
        BRANCHES의 키(first, cycle, retry, digest).
    """
    if entry.get("verdict") == "failed" or entry.get("retry") is True:
        return "retry"
    if entry.get("mode") == "digest":
        return "digest"
    no = entry.get("requestNo")
    return "cycle" if isinstance(no, int) and not isinstance(no, bool) and no >= 2 else "first"


def parse_run(entry: Any) -> Run | None:
    """runs 항목 하나를 Run으로 바꾼다.

    Args:
        entry: runs 항목.

    Returns:
        Run. dict가 아니거나 at을 읽을 수 없으면 None.
    """
    if not isinstance(entry, dict):
        return None
    at = parse_ts(str(entry.get("at") or ""))
    if at is None:
        return None
    cost = entry.get("costUsd")
    no = entry.get("requestNo")
    tokens = entry.get("tokens") if isinstance(entry.get("tokens"), dict) else {}
    return Run(
        at=at,
        branch=run_branch(entry),
        mode=str(entry.get("mode") or ""),
        request_no=no if isinstance(no, int) and not isinstance(no, bool) else None,
        cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        tokens={k: int(tokens.get(k) or 0) for k in TOKEN_KO},
        failed=entry.get("verdict") == "failed",
    )


def mark_reruns(runs: list[Run]) -> list[Run]:
    """같은 request 전환에 다시 실행한 검수를 재실행·실패 갈래로 바꾼다.

    한 ticket의 review 실행 중 requestNo가 같고 반영에 성공한 실행이 앞서 있으면(실행 시각 순) 그 뒤 실행은
    재실행·실패다. 앞선 실행이 모두 반영에 실패했으면 첫 성공 실행은 원래 갈래(첫 검수, 반려 cycle 재검수)로 둔다.

    Args:
        runs: 한 ticket의 실행 기록.

    Returns:
        실행 시각 순으로 정렬한 실행 기록. 같은 객체의 branch를 바꾼다.
    """
    seen: set[int] = set()
    out = sorted(runs, key=lambda r: r.at)
    for r in out:
        if r.mode != "review" or r.request_no is None:
            continue
        if r.request_no in seen:
            r.branch = "retry"
        if not r.failed:
            seen.add(r.request_no)
    return out


def load_ticket(j: JiraReader, issue: dict) -> Ticket:
    """검색 결과의 ticket 하나에 변경 이력, comment, jira-doc 기록을 더해 읽는다.

    agent comment(scripts/jira_api.py의 is_agent_comment) 중 판정 줄이 있으면 검수 comment,
    "문서화 리뷰 결과(관찰 모드)" 줄이 있으면 문서화 리뷰 comment로 센다. agent comment가 아닌 comment는
    사람 comment로 작성 시각만 남긴다.

    Args:
        j: Jira 읽기 객체.
        issue: Jira.search 결과 항목 하나(key, fields). fields는 ISSUE_FIELDS를 담는다.

    Returns:
        ticket 기록.
    """
    key = issue["key"]
    f = issue.get("fields") or {}
    assignee = f.get("assignee") or {}
    parent = f.get("parent") or {}
    pf = parent.get("fields") or {}
    name = (f.get("issuetype") or {}).get("name") or ""
    log = j.changelog(key)
    t = Ticket(
        key=key,
        itype=TYPE_ALIAS.get(name.strip().lower(), TYPE_ALIAS.get(name, name)),
        assignee_id=assignee.get("accountId") or "",
        assignee_name=assignee.get("displayName") or "",
        parent_key=parent.get("key") or "",
        parent_name=pf.get("summary") or "",
        parent_type=(pf.get("issuetype") or {}).get("name") or "",
        description=f.get("description") or "",
        created=parse_ts(f.get("created")),
        status=(f.get("status") or {}).get("name") or "",
        changes=status_changes(log),
        description_edit_at=description_edits(log),
    )
    for c in j.comments(key):
        at = parse_ts(c.get("created"))
        if at is None:
            continue
        if not is_agent_comment(c):
            t.human_comment_at.append(at)
            continue
        body = c.get("body") or ""
        if parsed := parse_review(body):
            hold = parsed[0] == VERDICT_KO["fix"] and AUTO_HOLD_NOTE in body
            t.reviews.append(ReviewComment(at, parsed[0], parsed[1], hold))
        elif (doc := parse_doc_review(body)) is not None:
            t.docs.append(ReviewComment(at, "", doc))
    t.human_comment_at.sort()
    t.reviews.sort(key=lambda r: r.at)
    t.docs.sort(key=lambda r: r.at)
    runs = (j.prop_get(key) or {}).get("runs")
    t.runs = mark_reruns([r for r in (parse_run(e) for e in (runs if isinstance(runs, list) else [])) if r])
    return t


def build_jql(project: str, since: dt.date) -> str:
    """보고에 필요한 기간을 덮는 ticket 검색 조건.

    JQL의 날짜는 API 계정의 시간대로 해석되므로 한국 시간 since 첫날의 변경이 빠지지 않게 하루 앞선 날부터 찾는다.
    업데이트가 없는 기존 ticket은 updated 조건으로 찾을 수 없으므로 진행 상태(done, Backlog, Deleted가 아닌 상태)인
    ticket도 함께 찾는다.

    Args:
        project: Jira 프로젝트 키.
        since: 읽을 기간의 첫날(한국 시간).

    Returns:
        검색 조건.
    """
    day = since - dt.timedelta(days=1)
    closed = ", ".join(f'"{s}"' for s in CLOSED_STATUSES)
    return (
        f"project = {project} AND issuetype in ({', '.join(TARGET_TYPES)}) "
        f'AND (updated >= "{day.isoformat()}" OR status not in ({closed}))'
    )


def collect(j: JiraReader, jql: str) -> list[Ticket]:
    """JQL로 찾은 ticket마다 기록을 읽는다. Jira 읽기 메서드만 호출한다.

    ticket 필드(ISSUE_FIELDS)는 검색 결과로 함께 받아 ticket마다 Jira.issue를 부르지 않는다.

    Args:
        j: Jira 읽기 객체.
        jql: 검색 조건.

    Returns:
        ticket 기록 목록.
    """
    return [load_ticket(j, issue) for issue in j.search(jql, ISSUE_FIELDS, limit=SEARCH_LIMIT)]


def fetch_subtasks(j: JiraReader, project: str, since: dt.date, until: dt.date) -> dict[str, list[dt.datetime]]:
    """since부터 until 전까지 만들어진 sub-task를 Jira 검색 한 번으로 읽어 상위 ticket 키별 생성 시각으로 묶는다.

    JQL의 날짜는 API 계정의 시간대로 해석되므로 build_jql처럼 하루 앞선 날부터 찾는다. 기간 판정은 시각으로 다시 한다.

    Args:
        j: Jira 읽기 객체.
        project: Jira 프로젝트 키.
        since: 대상 기간의 첫날(한국 시간).
        until: 대상 기간 다음 날(한국 시간). 이 날 전에 만들어진 sub-task만 찾는다.

    Returns:
        상위 ticket 키 -> sub-task 생성 시각 목록.
    """
    day = since - dt.timedelta(days=1)
    # JQL 날짜는 API 계정의 시간대라 앞뒤로 하루씩 넓히고, 기간은 _in으로 다시 거른다
    end = until + dt.timedelta(days=1)
    jql = (
        f"project = {project} AND issuetype in subTaskIssueTypes() "
        f'AND created >= "{day.isoformat()}" AND created < "{end.isoformat()}"'
    )
    out: dict[str, list[dt.datetime]] = {}
    for issue in j.search(jql, SUBTASK_FIELDS, limit=SEARCH_LIMIT):
        f = issue.get("fields") or {}
        parent = (f.get("parent") or {}).get("key") or ""
        at = parse_ts(f.get("created"))
        if parent and at is not None:
            out.setdefault(parent, []).append(at)
    return out


def search_prs(org: str, since: dt.date, until: dt.date) -> list[dict] | None:
    """GitHub organization에서 since–until(양 끝 포함)에 만들어진 PR을 gh search prs 한 번으로 찾는다.

    scripts/collect.py의 gh와 같이 환경 변수 GH_TOKEN이 없거나 명령이 실패하면 None을 돌려준다.

    Args:
        org: GitHub organization(환경 변수 GH_ORG).
        since: 첫날.
        until: 끝날.

    Returns:
        PR 목록(항목마다 title, createdAt). 검색하지 못하면 None.
    """
    if not org or not os.environ.get("GH_TOKEN"):
        return None
    args = ["gh", "search", "prs", "--owner", org, "--created", f"{since.isoformat()}..{until.isoformat()}"]
    args += ["--json", "title,createdAt", "--limit", str(PR_LIMIT)]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[report] gh search prs error: {e}", file=sys.stderr)
        return None
    if res.returncode != 0:
        print(f"[report] gh search prs failed: {res.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        data = json.loads(res.stdout) if res.stdout.strip() else []
    except json.JSONDecodeError:
        print("[report] gh search prs: JSON이 아닌 출력", file=sys.stderr)
        return None
    return data if isinstance(data, list) else None


def pr_times(prs: list[dict], project: str) -> dict[str, list[dt.datetime]]:
    """PR 제목의 ticket 키(<프로젝트 키>-숫자)별로 PR 생성 시각을 묶는다.

    Args:
        prs: search_prs의 결과.
        project: Jira 프로젝트 키.

    Returns:
        ticket 키 -> PR 생성 시각 목록. 한 PR 제목에 키가 여럿이면 키마다 넣는다.
    """
    pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(project)}-\d+")
    out: dict[str, list[dt.datetime]] = {}
    for pr in prs:
        at = parse_ts(str(pr.get("createdAt") or "")) if isinstance(pr, dict) else None
        if at is None:
            continue
        for key in sorted(set(pat.findall(str(pr.get("title") or "")))):
            out.setdefault(key, []).append(at)
    return out


def attach_updates(
    tickets: list[Ticket], subtasks: dict[str, list[dt.datetime]], prs: dict[str, list[dt.datetime]]
) -> None:
    """sub-task 생성 시각과 PR 생성 시각을 ticket 키로 붙인다.

    Args:
        tickets: ticket 기록. subtask_at, pr_at을 바꾼다.
        subtasks: fetch_subtasks의 결과.
        prs: pr_times의 결과. PR 검색이 실패했으면 빈 dict.
    """
    for t in tickets:
        t.subtask_at = sorted(subtasks.get(t.key, []))
        t.pr_at = sorted(prs.get(t.key, []))


def add_months(month: str, n: int) -> str:
    """YYYY-MM에 n달을 더한다.

    Args:
        month: 달(YYYY-MM).
        n: 더할 달 수. 음수면 뺀다.

    Returns:
        달(YYYY-MM).
    """
    y, m = map(int, month.split("-"))
    idx = y * 12 + m - 1 + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def months_span(first: str, last: str) -> Span:
    """first 달 첫날 0시부터 last 달 다음 달 첫날 0시 전까지(한국 시간).

    Args:
        first: 첫 달(YYYY-MM).
        last: 끝 달(YYYY-MM). 이 달을 포함한다.

    Returns:
        기간 [시작, 끝).
    """

    def start(month: str) -> dt.datetime:
        y, m = map(int, month.split("-"))
        return dt.datetime(y, m, 1, tzinfo=KST)

    return start(first), start(add_months(last, 1))


def quarter_first(month: str) -> str:
    """month가 속한 분기의 첫 달.

    Args:
        month: 달(YYYY-MM).

    Returns:
        달(YYYY-MM).
    """
    y, m = map(int, month.split("-"))
    return f"{y:04d}-{(m - 1) // 3 * 3 + 1:02d}"


def span_text(sp: Span) -> str:
    """기간을 "YYYY-MM-DD–YYYY-MM-DD"(양 끝 날짜 포함)로 적는다.

    Args:
        sp: 기간 [시작, 끝).

    Returns:
        기간 문구.
    """
    return f"{sp[0].date().isoformat()}–{(sp[1] - dt.timedelta(days=1)).date().isoformat()}"


def _in(t: dt.datetime, sp: Span) -> bool:
    return sp[0] <= t < sp[1]


def usage(tickets: Iterable[Ticket], sp: Span) -> Usage:
    """기간 안 실행(runs의 at)의 Claude 사용량을 네 갈래로 합산한다.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        사용량. costUsd가 없는 실행은 비용 0으로 더하고 missing_cost에 센다.
    """
    u = Usage()
    for t in tickets:
        for r in t.runs:
            if not _in(r.at, sp):
                continue
            u.runs[r.branch] += 1
            u.cost[r.branch] += r.cost or 0.0
            u.missing_cost += r.cost is None
            u.review_runs += r.mode == "review"
            for k, v in r.tokens.items():
                u.tokens[k] += v
    return u


def usage_judgement(cur: Usage, prev: Usage) -> tuple[bool | None, str, list[str]]:
    """사용량 많음을 판정한다.

    이번 달 API 환산 비용이 전월의 1.5배 이상이고 전월 검수 실행이 10건 이상이면 사용량 많음이다.
    전월 검수 실행이 10건 미만이거나 전월 API 환산 비용이 0이면 판정하지 않는다.

    Args:
        cur: 이번 달 사용량.
        prev: 전월 사용량.

    Returns:
        (사용량 많음이면 True, 아니면 False, 판정 불가면 None, 판정 값(해당함, 해당하지 않음, 판정 불가), 근거 줄).
    """
    reviews = f"전월 검수 실행 {prev.review_runs}건"
    if prev.review_runs < HIGH_MIN_PREV_REVIEWS:
        return None, "판정 불가", [f"{reviews} < {HIGH_MIN_PREV_REVIEWS}건"]
    if round(prev.total_cost, 6) <= 0:
        return None, "판정 불가", ["전월 API 환산 비용 기록 없음"]
    high = round(cur.total_cost, 6) >= round(prev.total_cost * HIGH_RATIO, 6)
    cost = (
        f"이번 달 {_usd(cur.total_cost)} {'≥' if high else '<'} "
        f"전월 × {HIGH_RATIO}({_usd(prev.total_cost * HIGH_RATIO)})"
    )
    return high, "해당함" if high else "해당하지 않음", [cost, f"{reviews} ≥ {HIGH_MIN_PREV_REVIEWS}건"]


def rejections(t: Ticket, sp: Span) -> int:
    """기간 안 반려(ready-to-done에서 In Progress로 전환) 횟수. 전환한 사람은 묻지 않는다.

    Args:
        t: ticket 기록.
        sp: 기간.

    Returns:
        반려 횟수.
    """
    return sum(
        1 for c in t.changes if _in(c.at, sp) and is_status(c.src, READY_STATUS) and is_status(c.dst, IN_PROGRESS)
    )


def comments_in(tickets: Iterable[Ticket], sp: Span, docs: bool = True) -> list[ReviewComment]:
    """기간 안에 작성된 검수 comment(docs가 True면 문서화 리뷰 comment도).

    Args:
        tickets: ticket 기록.
        sp: 기간.
        docs: 문서화 리뷰 comment를 넣을지.

    Returns:
        comment 목록.
    """
    return [r for t in tickets for r in (t.reviews + (t.docs if docs else [])) if _in(r.at, sp)]


def fail_counts(tickets: Iterable[Ticket], sp: Span) -> Counter:
    """기간 안 검수 comment와 문서화 리뷰 comment의 검사 ID별 ❌ 행 수.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        검사 ID -> ❌ 횟수.
    """
    return Counter(cid for r in comments_in(tickets, sp) for cid, code in r.results.items() if code == "fail")


def first_reviews(tickets: Iterable[Ticket], sp: Span) -> list[ReviewComment]:
    """ticket별 첫 검수 comment 중 기간 안에 작성된 것.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        첫 검수 comment 목록.
    """
    return [t.reviews[0] for t in tickets if t.reviews and _in(t.reviews[0].at, sp)]


def pass_count(reviews: list[ReviewComment]) -> tuple[int, int]:
    """검수 결과가 통과인 수와 전체 수.

    Args:
        reviews: 검수 comment 목록.

    Returns:
        (통과 수, 전체 수).
    """
    return sum(1 for r in reviews if r.verdict == VERDICT_KO["pass"]), len(reviews)


def candidates(tickets: list[Ticket], sp: Span) -> dict[str, Candidate]:
    """기간의 구두 피드백 대상 후보를 담당자별로 고른다.

    조건 A: 한 ticket의 기간 안 반려가 2회 이상. 조건 B: 같은 검사 ID의 ❌가 그 담당자 ticket의 기간 안 검수
    comment와 문서화 리뷰 comment에서 합쳐 2회 이상. 담당자가 없는 ticket은 세지 않는다.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        담당자 accountId -> 후보. 조건을 만족하지 않는 담당자는 없다.
    """
    out: dict[str, Candidate] = {}
    for aid in sorted({t.assignee_id for t in tickets if t.assignee_id}):
        mine = [t for t in tickets if t.assignee_id == aid]
        rejected = [(t.key, n) for t in mine if (n := rejections(t, sp)) >= CANDIDATE_MIN]
        failed = sorted(((cid, n) for cid, n in fail_counts(mine, sp).items() if n >= CANDIDATE_MIN), key=_rank)
        if rejected or failed:
            out[aid] = Candidate(mine[0].assignee_name or aid, rejected, failed)
    return out


def _rank(item: tuple[str, int]) -> tuple[int, str]:
    return -item[1], item[0]


def followup(
    prev: dict[str, Candidate], tickets: list[Ticket], cur_sp: Span, prev_sp: Span
) -> list[tuple[str, str, list[str]]]:
    """지난달 후보의 개선 여부를 정한다.

    판단 불가: 이번 달 그 담당자 ticket의 검수 comment가 없음. 재발: 지난달 조건 B의 검사 ID 중 하나가 이번 달에
    다시 ❌. 개선: 재발이 없고 그 담당자의 첫 검수 통과율이 지난달보다 높음. 유지: 그 밖의 경우(두 달 중 첫 검수
    comment가 없는 달이 있어 비교할 수 없는 경우 포함).

    Args:
        prev: 지난달 후보(candidates의 결과).
        tickets: ticket 기록.
        cur_sp: 이번 달.
        prev_sp: 지난달.

    Returns:
        (담당자 이름, 결과 값, 근거 줄 목록) 목록.
    """
    out: list[tuple[str, str, list[str]]] = []
    for aid, cand in prev.items():
        mine = [t for t in tickets if t.assignee_id == aid]
        reviews = comments_in(mine, cur_sp, docs=False)
        if not reviews:
            out.append((cand.name, "판단 불가", ["이번 달 검수 comment 0건"]))
            continue
        fails = fail_counts(mine, cur_sp)
        again = [(cid, fails[cid]) for cid, _ in cand.failed if fails[cid]]
        if again:
            out.append((cand.name, "재발", [f"이번 달 {cid} ❌ {n}회" for cid, n in again]))
            continue
        cp, cn = pass_count(first_reviews(mine, cur_sp))
        pp, pn = pass_count(first_reviews(mine, prev_sp))
        result = "개선" if cn and pn and cp * pn > pp * cn else "유지"
        out.append(
            (cand.name, result, [f"지난달 첫 검수 통과율 {_pct(pp, pn)}", f"이번 달 첫 검수 통과율 {_pct(cp, cn)}"])
        )
    return out


def judge(reviews: list[Judged], changes: list[StatusChange], lead: str) -> list[str]:
    """ticket 하나의 검수 comment마다 판정 일치율의 결과 값을 정한다.

    검수 comment 뒤, 다음 검수 comment 전까지 ticket이 ready-to-done에서 나간 첫 전환을 본다.
    전환한 사람이 lead일 때만 센다. 통과→done, 보류→In Progress는 일치, 통과→In Progress, 보류→done은 불일치다.
    검토 요청, AUTO_HOLD_NOTE가 있는 보류(자동 보류), lead 아닌 사람의 전환, 전환 없음, done과 In Progress가 아닌
    도착 상태는 일치율에서 뺀다.

    Args:
        reviews: 검수 comment 목록. 작성 순.
        changes: status 변경 목록. 시각 순.
        lead: 팀장 accountId.

    Returns:
        reviews와 같은 순서의 결과 값(MATCH, MISMATCH, ESCALATE, AUTO_HOLD, NOT_LEAD, WAITING, OTHER).
    """
    out: list[str] = []
    for i, rv in enumerate(reviews):
        nxt = reviews[i + 1].at if i + 1 < len(reviews) else None
        if rv.verdict == VERDICT_KO["escalate"]:
            out.append(ESCALATE)
            continue
        if rv.auto_hold:
            out.append(AUTO_HOLD)
            continue
        c = next(
            (c for c in changes if c.at > rv.at and (nxt is None or c.at < nxt) and is_status(c.src, READY_STATUS)),
            None,
        )
        if c is None:
            out.append(WAITING)
        elif not lead or c.author != lead:
            out.append(NOT_LEAD)
        elif not (is_status(c.dst, DONE_STATUS) or is_status(c.dst, IN_PROGRESS)):
            out.append(OTHER)
        else:
            want = DONE_STATUS if rv.verdict == VERDICT_KO["pass"] else IN_PROGRESS
            out.append(MATCH if is_status(c.dst, want) else MISMATCH)
    return out


def agreement_cases(tickets: Iterable[Ticket], lead: str) -> list[tuple[dt.datetime, str]]:
    """모든 ticket의 검수 comment별 판정 일치율 결과 값.

    Args:
        tickets: ticket 기록.
        lead: 팀장 accountId.

    Returns:
        (검수 comment 작성 시각, 결과 값) 목록. 시각 순.
    """
    cases = [(rv.at, res) for t in tickets for rv, res in zip(t.reviews, judge(t.reviews, t.changes, lead))]
    return sorted(cases, key=lambda c: c[0])


def agreement_summary(cases: list[tuple[dt.datetime, str]], sp: Span) -> dict[str, Any]:
    """판정 일치율의 분기값, 최근 20건 값, 검토 요청 비율.

    Args:
        cases: agreement_cases의 결과.
        sp: 기간(분기). 최근 20건은 기간 끝 전에 작성된 검수 comment 중 일치율에 들어가는 최근 20건이다.

    Returns:
        match, judged(분기 일치 수와 일치율 분모), recent_match, recent_n, escalate, total(분기 검수 comment 수),
        excluded(제외 결과 값별 수).
    """
    inside = [res for at, res in cases if _in(at, sp)]
    recent = [res for at, res in cases if at < sp[1] and res in (MATCH, MISMATCH)][-RECENT_N:]
    counts = Counter(inside)
    return {
        "match": counts[MATCH],
        "judged": counts[MATCH] + counts[MISMATCH],
        "recent_match": recent.count(MATCH),
        "recent_n": len(recent),
        "escalate": counts[ESCALATE],
        "total": len(inside),
        "excluded": {k: counts[k] for k in (AUTO_HOLD, NOT_LEAD, WAITING, OTHER)},
    }


def feedback_minutes(tickets: Iterable[Ticket], sp: Span) -> list[float]:
    """기간 안 request 전환마다 다음 request 전환 전 첫 검수 comment까지 걸린 시간(분).

    그런 검수 comment가 없는 request 전환은 뺀다.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        분 목록.
    """
    out: list[float] = []
    for t in tickets:
        reqs = [c.at for c in t.changes if is_status(c.dst, READY_STATUS)]
        for req in (r for r in reqs if _in(r, sp)):
            nxt = next((r for r in reqs if r > req), None)
            hit = next((rv for rv in t.reviews if rv.at >= req and (nxt is None or rv.at < nxt)), None)
            if hit:
                out.append((hit.at - req).total_seconds() / 60)
    return out


def done_in(t: Ticket, sp: Span) -> bool:
    """기간 안에 done으로 전환된 적이 있는지.

    Args:
        t: ticket 기록.
        sp: 기간.

    Returns:
        있으면 True.
    """
    return any(_in(c.at, sp) and is_status(c.dst, DONE_STATUS) for c in t.changes)


def r4_count(tickets: Iterable[Ticket], sp: Span) -> tuple[int, int]:
    """기간 안 검수 comment의 R4 행 중 만족 수와 해당 없음을 뺀 행 수.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        (만족 수, 만족과 보완 필요 수).
    """
    vals = [r.results.get("R4") for r in comments_in(tickets, sp, docs=False)]
    return vals.count("pass"), vals.count("pass") + vals.count("fail")


def kr_rows(tickets: Iterable[Ticket], sp: Span) -> list[KrRow]:
    """KR별 하위 Task의 기간 안 done 수와 결과 산출물 달성 수.

    상위 ticket의 work type이 KR인 Task 중 기간 안에 done으로 전환된 Task를 센다. 달성 수는 description의
    "예상 산출물 n개 중 m개 달성" 줄(첫 줄)을 합산하고, 그 줄이 없는 Task는 no_line에 센다.
    상위 KR이 없는 Task는 키가 빈 행 하나로 모은다.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        KR 행 목록. KR 키 순서이고 상위 KR이 없는 행은 끝에 둔다.
    """
    rows: dict[str, KrRow] = {}
    for t in tickets:
        if t.itype != "Task" or not done_in(t, sp):
            continue
        is_kr = is_status(t.parent_type, KR_TYPE) and t.parent_key
        key = t.parent_key if is_kr else ""
        row = rows.setdefault(key, KrRow(key, t.parent_name if is_kr else "상위 KR 없음"))
        row.done += 1
        m = DELIVERABLES_RE.search(t.description)
        if m:
            row.expected += int(m.group(1))
            row.achieved += int(m.group(2))
        else:
            row.no_line += 1
    return sorted(rows.values(), key=lambda r: (not r.key, len(r.key), r.key))


def check_satisfaction(tickets: Iterable[Ticket], sp: Span) -> dict[str, tuple[int, int]]:
    """기간 안 검수 comment와 문서화 리뷰 comment의 검사 ID별 만족 수와 판정 수(해당 없음 제외).

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        검사 ID -> (만족 수, 만족과 보완 필요 수).
    """
    ok: Counter = Counter()
    judged: Counter = Counter()
    for r in comments_in(tickets, sp):
        for cid, code in r.results.items():
            if code in ("pass", "fail"):
                judged[cid] += 1
                ok[cid] += code == "pass"
    return {cid: (ok[cid], judged[cid]) for cid in judged}


def is_progress(name: str) -> bool:
    """진행 상태(done, Backlog, Deleted가 아닌 상태)인지. 상태 이름이 비었으면 False.

    Args:
        name: 상태 이름.

    Returns:
        진행 상태면 True.
    """
    return bool(name.strip()) and not any(is_status(name, s) for s in CLOSED_STATUSES)


def state_at(t: Ticket, when: dt.datetime) -> str:
    """ticket의 when 시각 상태를 상태 변경 이력으로 되돌려 구한다.

    when 전 마지막 상태 변경이 있으면 그 도착 상태, 없으면 when 뒤(when 포함) 첫 상태 변경의 출발 상태,
    상태 변경이 없으면 지금 상태다.

    Args:
        t: ticket 기록. changes는 시각 순이다.
        when: 시각.

    Returns:
        상태 이름.
    """
    before = [c for c in t.changes if c.at < when]
    if before:
        return before[-1].dst
    after = [c for c in t.changes if c.at >= when]
    return after[0].src if after else t.status


def updated_in(t: Ticket, sp: Span) -> bool:
    """기간 안에 업데이트 기록이 하나 이상 있는지.

    업데이트 기록은 사람 comment, description 수정, 상태 변경, sub-task 생성, PR 생성이다.
    PR 검색이 실패했으면 pr_at이 비어 PR 생성은 세지 않는다.

    Args:
        t: ticket 기록.
        sp: 기간.

    Returns:
        있으면 True.
    """
    times = [*t.human_comment_at, *t.description_edit_at, *(c.at for c in t.changes), *t.subtask_at, *t.pr_at]
    return any(_in(at, sp) for at in times)


def _idle_cause(status: str) -> str:
    if is_status(status, READY_STATUS):
        return LEAD_WAIT
    return OWNER_IDLE if is_status(status, IN_PROGRESS) else OTHER_IDLE


def _status_name(name: str) -> str:
    """상태 이름을 STATUS_ORDER의 표기로 맞춘다. 목록에 없으면 그대로, 비었으면 "-"."""
    return next((s for s in STATUS_ORDER if is_status(name, s)), name.strip() or "-")


def ticket_overview(tickets: Iterable[Ticket], sp: Span, month_start: dt.datetime) -> Overview:
    """대상 기간의 ticket 현황.

    기존 ticket: month_start 전에 만들어졌고(만든 시각이 없으면 전으로 봄) month_start의 상태(state_at)가 진행
    상태인 ticket. 업데이트(updated_in)가 없는 기존 ticket은 대상 기간 끝의 상태로 원인을 나눈다
    (ready-to-done: 팀장 확인 대기, In Progress: 담당자, 그 밖: 기타). 새 ticket: 대상 기간에 만들어진 ticket.
    지금 상태별로 센다.

    Args:
        tickets: ticket 기록.
        sp: 대상 기간.
        month_start: 기존 ticket을 정하는 시각(대상 달 1일 0시, 한국 시간).

    Returns:
        ticket 현황.
    """
    ov = Overview()
    for t in tickets:
        if t.created is not None and _in(t.created, sp):
            ov.new[_status_name(t.status)] += 1
            continue
        if (t.created is not None and t.created >= month_start) or not is_progress(state_at(t, month_start)):
            continue
        ov.existing += 1
        if updated_in(t, sp):
            ov.updated += 1
            ov.updated_done += done_in(t, sp)
        else:
            ov.idle[_idle_cause(state_at(t, sp[1]))] += 1
    return ov


def request_count(t: Ticket, sp: Span) -> int:
    """기간 안 request 전환(ready-to-done 진입) 횟수.

    Args:
        t: ticket 기록.
        sp: 기간.

    Returns:
        횟수.
    """
    return sum(1 for c in t.changes if _in(c.at, sp) and is_status(c.dst, READY_STATUS))


def max_request_count(tickets: Iterable[Ticket], sp: Span) -> tuple[int, str]:
    """최대 검수 반복 횟수: ticket 하나의 기간 안 request 전환 횟수 중 최댓값과 그 ticket 키.

    같은 횟수면 ticket 키 순(짧은 키, 사전 순)으로 앞선 ticket을 고른다.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        (최댓값, ticket 키). request 전환이 없으면 (0, "").
    """
    best = (0, "")
    for t in sorted(tickets, key=lambda t: (len(t.key), t.key)):
        n = request_count(t, sp)
        if n > best[0]:
            best = (n, t.key)
    return best


def business_days(start: dt.datetime, end: dt.datetime) -> int:
    """start 날짜 다음 날부터 end 직전 시각의 날짜까지 영업일(토요일, 일요일 제외) 수. 날짜는 한국 시간이다.

    scripts/precheck.py의 business_days_since와 같은 규칙이다(시작 날짜는 빼고 끝 날짜는 넣음).

    Args:
        start: 시작 시각.
        end: 기간 끝 [시작, 끝)의 끝 시각.

    Returns:
        영업일 수.
    """
    d = start.astimezone(KST).date()
    last = (end - dt.timedelta(microseconds=1)).astimezone(KST).date()
    days = 0
    while d < last:
        d += dt.timedelta(days=1)
        days += d.weekday() < 5
    return days


def lead_wait(tickets: Iterable[Ticket], end: dt.datetime) -> tuple[int, int]:
    """팀장 확인 대기 지표: end 시각에 ready-to-done인 ticket 수와 진입 뒤 가장 오래 머문 영업일 수.

    진입은 end 전 마지막 ready-to-done 진입이다. 진입 기록이 없으면 만든 시각부터 센다. end 뒤에 만든 ticket은 뺀다.

    Args:
        tickets: ticket 기록.
        end: 대상 기간 끝 시각.

    Returns:
        (ticket 수, 최장 영업일 수). ticket이 없으면 (0, 0).
    """
    days: list[int] = []
    for t in tickets:
        if (t.created is not None and t.created >= end) or not is_status(state_at(t, end), READY_STATUS):
            continue
        entry = next((c.at for c in reversed(t.changes) if c.at < end and is_status(c.dst, READY_STATUS)), t.created)
        days.append(business_days(entry, end) if entry else 0)
    return len(days), max(days, default=0)


def lead_confirm_minutes(tickets: Iterable[Ticket], sp: Span, lead: str) -> list[float]:
    """팀장 확인 시간: 기간 안에 팀장 계정이 ready-to-done에서 전환한 경우마다 머문 시간(분).

    머문 시간은 그 전환 전 마지막 ready-to-done 진입부터 그 전환까지다. 진입 기록이 없는 전환과 팀장 외 계정의
    전환은 뺀다.

    Args:
        tickets: ticket 기록.
        sp: 기간.
        lead: 팀장 accountId. 비었으면 빈 목록을 돌려준다.

    Returns:
        분 목록.
    """
    out: list[float] = []
    if not lead:
        return out
    for t in tickets:
        for i, c in enumerate(t.changes):
            if not (_in(c.at, sp) and c.author == lead and is_status(c.src, READY_STATUS)):
                continue
            entry = next((p.at for p in reversed(t.changes[:i]) if is_status(p.dst, READY_STATUS)), None)
            if entry is not None:
                out.append((c.at - entry).total_seconds() / 60)
    return out


def member_rows(tickets: list[Ticket], sp: Span, include: Iterable[str] = ()) -> list[MemberRow]:
    """팀원별 절의 담당자별 값. 담당자가 없는 ticket은 세지 않는다.

    기간에 기록이 있는 담당자만 넣는다. 기록: 기존 ticket이나 새 ticket(ticket_overview, 기준 시각은 기간 첫날),
    첫 검수 comment, request 전환, 판정된 R4 행. include의 담당자는 기록이 없어도 넣는다.

    Args:
        tickets: ticket 기록.
        sp: 기간.
        include: 기록이 없어도 넣을 담당자 accountId(예: 지난달 후보).

    Returns:
        담당자 행 목록. 이름 순(대소문자 무시).
    """
    keep = set(include)
    rows: list[MemberRow] = []
    for aid in sorted({t.assignee_id for t in tickets if t.assignee_id}):
        mine = [t for t in tickets if t.assignee_id == aid]
        ov = ticket_overview(mine, sp, sp[0])
        row = MemberRow(
            aid=aid,
            name=mine[0].assignee_name or aid,
            first=pass_count(first_reviews(mine, sp)),
            max_request=max_request_count(mine, sp),
            stop=r4_count(mine, sp),
            idle=ov.idle_total,
        )
        if aid in keep or ov.existing or ov.new_total or row.first[1] or row.max_request[0] or row.stop[1]:
            rows.append(row)
    return sorted(rows, key=lambda r: (r.name.lower(), r.aid))


def _pct(a: int, b: int, unit: str = "건") -> str:
    return f"{a}/{b}{unit}, {a / b * 100:.1f}%" if b else "기록 없음"


def _ratio(a: int, b: int) -> str:
    """비율 값 "a/b건, x%". 분모가 0이면 기록 없음 표시 "-"."""
    return _pct(a, b) if b else "-"


def _frac(a: int, b: int, unit: str = "") -> str:
    """분수 값 "a/b". 분모가 0이면 "-"."""
    return f"{a}/{b}{unit}" if b else "-"


def _times(n: int, key: str = "") -> str:
    """횟수 값 "n회(키)". 0이면 "-"."""
    if not n:
        return "-"
    return f"{n}회({key})" if key else f"{n}회"


def _dur(minutes: float) -> str:
    """시간 값. 60분 미만은 분, 그 이상은 시간으로 적는다."""
    return f"{_fmt(minutes)}분" if minutes < 60 else f"{_fmt(minutes / 60)}시간"


def _median(values: list[float]) -> str:
    """중앙값과 표본 수 "중앙값 30분(5건)". 표본이 없으면 "-"."""
    return f"중앙값 {_dur(statistics.median(values))}({len(values)}건)" if values else "-"


def _usd(x: float) -> str:
    return f"{x:.2f} USD"


def _fmt(x: float) -> str:
    return f"{x:.1f}".removesuffix(".0")


def _sub(text: str, depth: int = 1) -> str:
    """Slack 본문의 하위 줄. depth 단계만큼 INDENT로 들여 쓰고 "◦"를 붙인다."""
    return f"{INDENT * depth}◦ {text}"


def _esc(text: str) -> str:
    """Slack이 제어 문자로 읽는 &, <, >를 바꾼다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(prefix: str, items: list[str], limit: int = 50) -> list[str]:
    """prefix 뒤에 items를 ", "로 이어 적고, 한 줄이 limit자를 넘으면 다음 줄로 넘긴다.

    Args:
        prefix: 첫 줄 앞 문구.
        items: 이어 적을 값.
        limit: 줄 글자 수 상한(목록 기호 제외).

    Returns:
        줄 목록. items가 비었으면 빈 목록.
    """
    lines: list[str] = []
    cur = ""
    for item in items:
        if not cur:
            cur = f"{prefix}{item}"
        elif len(f"{cur}, {item}") > limit:
            lines.append(cur)
            cur = item
        else:
            cur = f"{cur}, {item}"
    return [*lines, cur] if cur else lines


def _capped_wrap(items: list[str], max_lines: int) -> list[str]:
    """_wrap(머리 문구 없음)으로 나눈 줄이 max_lines를 넘으면 뒤 값부터 "외 n종"으로 줄인다.

    Args:
        items: 이어 적을 값.
        max_lines: 줄 수 상한.

    Returns:
        줄 목록.
    """
    for rest in range(len(items) + 1):
        shown = items[: len(items) - rest] + ([f"외 {rest}종"] if rest else [])
        lines = _wrap("", shown)
        if len(lines) <= max_lines:
            return lines
    return [f"외 {len(items)}종"]


def _name(name: str) -> str:
    """담당자 표시 이름. NAME_MAX자를 넘으면 자르고 "…"를 붙인다. Slack 제어 문자는 바꾼다."""
    return _esc(name if len(name) <= NAME_MAX else name[:NAME_MAX] + "…")


def _fit_members(head: str, groups: list[list[str]], budget: int) -> list[str]:
    """팀원별 절 줄. 절이 budget 줄을 넘으면 앞 담당자부터 넣고 남은 담당자는 "• 외 n명"으로 줄인다.

    budget이 3줄 미만이면 절 제목과 요약 한 줄(두 줄)만 적는다.

    Args:
        head: "순서:" 줄의 지표 순서 문구.
        groups: 담당자마다의 줄(한 명당 두 줄 이내).
        budget: 절에 쓸 수 있는 줄 수.

    Returns:
        Slack 본문 줄.
    """
    if budget < 3:
        return ["*팀원별*", f"• 담당자 {len(groups)}명: 줄 수 상한으로 생략" if groups else "• 기록 없음"]
    lines = ["*팀원별*", f"• 순서: {head}"]
    if not groups:
        return [*lines, "• 기록 없음"]
    if len(lines) + sum(map(len, groups)) <= budget:
        return lines + [x for g in groups for x in g]
    kept: list[str] = []
    n = 0
    for g in groups:
        if len(lines) + len(kept) + len(g) + 1 > budget:
            break
        kept += g
        n += 1
    return [*lines, *kept, f"• 외 {len(groups) - n}명"]


class Report:
    """정기 보고 하나의 계산 결과와 묶음별 메시지 조립.

    메시지마다 제목, 대상 기간, 팀장 업무, 팀 전체, 팀원별, Claude 사용량 절, 지표 정의 줄 순서로 적는다.
    메시지는 MAX_LINES줄 이내다. 넘으면 팀원별 줄을 줄인다.
    """

    def __init__(self, tickets: list[Ticket], month: str, lead: str, pr_ok: bool = True, pr_capped: bool = False):
        self.tickets, self.month, self.lead, self.pr_ok, self.pr_capped = tickets, month, lead, pr_ok, pr_capped
        self.year = month[:4]
        self.cur = months_span(month, month)
        self.prev_month = add_months(month, -1)
        self.prev = months_span(self.prev_month, self.prev_month)
        self.quarter = months_span(quarter_first(month), month)
        self.cur_usage, self.prev_usage = usage(tickets, self.cur), usage(tickets, self.prev)
        self.high = usage_judgement(self.cur_usage, self.prev_usage)
        self.cases = agreement_cases(tickets, lead)

    def quarters(self) -> list[tuple[str, Span]]:
        """대상 해의 네 분기.

        Returns:
            (분기 이름 Q1–Q4, 기간) 목록.
        """
        y = self.year
        return [(f"Q{q}", months_span(f"{y}-{q * 3 - 2:02d}", f"{y}-{q * 3:02d}")) for q in range(1, 5)]

    def assemble(
        self, title: str, sp: Span, sections: list[list[str]], member_head: str, groups: list[list[str]]
    ) -> str:
        """메시지 하나를 조립한다. 팀원별 절은 sections의 셋째 자리에 넣는다.

        팀원별 절은 남은 줄 수(budget) 안에서 줄인다(_fit_members). 다른 절은 줄 수 상한이 있는 줄만 두어
        메시지가 MAX_LINES줄을 넘지 않는다(월간 묶음의 최악 경우 팀원별 절 두 줄로 30줄).

        Args:
            title: 제목 줄.
            sp: 대상 기간.
            sections: 팀장 업무, 팀 전체, Claude 사용량 절의 줄.
            member_head: 팀원별 절 "순서:" 줄의 지표 순서 문구.
            groups: 담당자마다의 줄.

        Returns:
            mrkdwn 문자열.
        """
        lead, team, use = sections
        head = [title, f"대상 기간: {span_text(sp)}"]
        budget = MAX_LINES - len(head) - len(lead) - len(team) - len(use) - 1
        members = _fit_members(member_head, groups, budget)
        return "\n".join([*head, *lead, *team, *members, *use, DEF_LINE])

    def overview_lines(self, sp: Span) -> list[str]:
        """월간 묶음 팀 전체 절의 ticket 현황 줄.

        Args:
            sp: 대상 기간.

        Returns:
            Slack 본문 줄.
        """
        ov = ticket_overview(self.tickets, sp, sp[0])
        idle = ", ".join(f"{c} {ov.idle[c]}" for c in (LEAD_WAIT, OWNER_IDLE, OTHER_IDLE) if ov.idle[c])
        lines = [
            f"• 기존 ticket {ov.existing}개",
            _sub(f"업데이트 {ov.updated}개(그중 done 전환 {ov.updated_done}개)"),
            _sub(f"업데이트 없음 {ov.idle_total}개" + (f": {idle}" if idle else "")),
        ]
        if not self.pr_ok:
            lines.append("• PR 검색 실패: PR 추가는 업데이트에서 뺌")
        if self.pr_capped:
            lines.append(f"• PR 검색이 상한 {PR_LIMIT}건에 닿음")
        order = {s: i for i, s in enumerate(STATUS_ORDER)}
        statuses = sorted(ov.new, key=lambda s: (order.get(s, len(order)), s))
        lines.append(f"• 새 ticket {ov.new_total}개")
        lines += [_sub(x) for x in _capped_wrap([f"{s} {ov.new[s]}" for s in statuses], NEW_STATUS_LINES)]
        if len(self.tickets) >= SEARCH_LIMIT:
            lines.append(f"• 검색 결과 상한 {SEARCH_LIMIT}개에 닿음: 빠진 ticket 있을 수 있음")
        return lines

    def usage_lines(self) -> list[str]:
        """월간 묶음의 Claude 사용량 절.

        Returns:
            Slack 본문 줄.
        """
        cur, prev = self.cur_usage, self.prev_usage
        flag, word, basis = self.high
        lines = [
            "*Claude 사용량(API 환산 비용)*",
            f"• 합계 {_usd(cur.total_cost)}(실행 {cur.total_runs}건), 전월 {_usd(prev.total_cost)}",
        ]
        if cur.missing_cost:
            lines.append(_sub(f"비용 기록 없는 실행 {cur.missing_cost}건은 0 USD로 셈"))
        if flag is None:
            reason = basis[0]
        else:
            reason = f"전월의 {HIGH_RATIO}배 {'이상' if flag else '미만'}"
        lines.append(f"• 사용량 많음: {word}({reason})")
        if flag:
            up = sorted(((b, cur.cost[b] - prev.cost[b]) for b in BRANCHES), key=lambda x: -x[1])
            lines += [_sub(x) for x in _wrap("늘어난 갈래: ", [f"{BRANCHES[b]} {dc:+.2f}" for b, dc in up if dc > 0])]
        return lines

    @staticmethod
    def candidate_mark(c: Candidate) -> str:
        """구두 피드백 대상 후보 표시 "후보(A, B: T2)".

        Args:
            c: 후보.

        Returns:
            표시 문구. 조건 B의 검사 ID는 CANDIDATE_IDS개까지 적고 나머지는 "외 n"으로 줄인다.
        """
        parts = ["A"] if c.rejected else []
        if c.failed:
            ids = [cid for cid, _ in c.failed]
            more = f" 외 {len(ids) - CANDIDATE_IDS}" if len(ids) > CANDIDATE_IDS else ""
            parts.append(f"B: {', '.join(ids[:CANDIDATE_IDS])}{more}")
        return f"후보({', '.join(parts)})"

    def monthly_message(self) -> str:
        """월간 묶음 메시지.

        팀원별 줄은 구두 피드백 대상 후보, 지난달 후보, 그 밖의 담당자 순이고 같은 묶음 안에서는 이름 순이다.

        Returns:
            mrkdwn 문자열.
        """
        sp = self.cur
        a = agreement_summary(self.cases, sp)
        wait_n, wait_days = lead_wait(self.tickets, sp[1])
        lead = [
            "*팀장 업무*",
            f"• 피드백 대기 시간: {_median(feedback_minutes(self.tickets, sp))}",
            f"• 판정 일치율: {_ratio(a['match'], a['judged'])}",
            _sub(f"최근 {RECENT_N}건: {_ratio(a['recent_match'], a['recent_n'])}"),
            f"• 팀장 확인 대기: {wait_n}건" + (f"(최장 {wait_days}영업일)" if wait_n else ""),
            f"• 팀장 확인 시간: {_median(lead_confirm_minutes(self.tickets, sp, self.lead))}",
        ]
        team = [
            "*팀 전체*",
            *self.overview_lines(sp),
            f"• 첫 검수 통과율: {_ratio(*pass_count(first_reviews(self.tickets, sp)))}",
            f"• 최대 검수 반복 횟수: {_times(*max_request_count(self.tickets, sp))}",
            f"• stop 사유 기록률: {_ratio(*r4_count(self.tickets, sp))}",
        ]
        cands = candidates(self.tickets, sp)
        prev_cands = candidates(self.tickets, self.prev)
        follow = {aid: res for aid, (_, res, _) in zip(prev_cands, followup(prev_cands, self.tickets, sp, self.prev))}
        rows = member_rows(self.tickets, sp, include=follow)
        rows.sort(key=lambda r: 0 if r.aid in cands else 1 if r.aid in follow else 2)
        groups = []
        for r in rows:
            sub = [f"업데이트 없음 {r.idle}개"]
            if r.aid in cands:
                sub.append(self.candidate_mark(cands[r.aid]))
            if r.aid in follow:
                sub.append(f"지난달 후보: {follow[r.aid]}")
            main = f"• {_name(r.name)}: {_frac(*r.first)} / {_times(r.max_request[0])} / {_frac(*r.stop)}"
            groups.append([main, _sub(", ".join(sub))])
        title = f"[{self.month} jira-doc 월간 보고]"
        head = "첫 검수 통과율 / 최대 검수 반복 횟수 / stop 사유 기록률"
        return self.assemble(title, sp, [lead, team, self.usage_lines()], head, groups)

    def quarterly_message(self) -> str:
        """분기 묶음 메시지.

        Returns:
            mrkdwn 문자열.
        """
        sp = self.quarter
        a = agreement_summary(self.cases, sp)
        lead = [
            "*팀장 업무*",
            f"• 판정 일치율: {_ratio(a['match'], a['judged'])}",
            _sub(f"최근 {RECENT_N}건: {_ratio(a['recent_match'], a['recent_n'])}"),
            f"• 검토 요청 비율: {_ratio(a['escalate'], a['total'])}",
            f"• 피드백 대기 시간: {_median(feedback_minutes(self.tickets, sp))}",
            f"• 팀장 확인 시간: {_median(lead_confirm_minutes(self.tickets, sp, self.lead))}",
        ]
        rows = kr_rows(self.tickets, sp)
        team = ["*팀 전체*", "• KR별 하위 Task" + ("" if rows else ": -")]
        for r in rows[:KR_MAX]:
            rate = f"달성률 {_frac(r.achieved, r.expected, '개')}"
            miss = f"(달성 요약 줄 없음 {r.no_line}개)" if r.no_line else ""
            team.append(_sub(f"{r.key or _esc(r.name)}: done {r.done}개, {rate}{miss}"))
        if len(rows) > KR_MAX:
            team.append(_sub(f"외 {len(rows) - KR_MAX}개"))
        top = sorted(fail_counts(self.tickets, sp).items(), key=_rank)[:TOP_QUARTER]
        top_text = ", ".join(f"{cid} {n}" for cid, n in top) or "-"
        team += [
            f"• ❌ 상위 {TOP_QUARTER}개: {top_text}",
            f"• 첫 검수 통과율: {_ratio(*pass_count(first_reviews(self.tickets, sp)))}",
            f"• 최대 검수 반복 횟수: {_times(*max_request_count(self.tickets, sp))}",
        ]
        groups = [
            [f"• {_name(r.name)}: {_frac(*r.first)} / {_times(r.max_request[0])} / {_frac(*r.stop)}"]
            for r in member_rows(self.tickets, sp)
        ]
        u = usage(self.tickets, sp)
        cost = [f"{ko} {u.cost[b]:.2f}" for b, ko in BRANCHES.items()]
        use = [
            "*Claude 사용량(API 환산 비용)*",
            f"• 합계 {_usd(u.total_cost)}(실행 {u.total_runs}건)",
            _sub(", ".join(cost[:2])),
            _sub(", ".join(cost[2:])),
        ]
        y, m = map(int, self.month.split("-"))
        title = f"[{y} Q{(m - 1) // 3 + 1} jira-doc 분기 보고]"
        head = "첫 검수 통과율 / 최대 검수 반복 횟수 / stop 사유 기록률"
        return self.assemble(title, sp, [lead, team, use], head, groups)

    def yearly_message(self) -> str:
        """연간 묶음 메시지. 분기별 값은 그 분기에 기록이 없으면 "-"로 적는다.

        만족률은 연간 ❌ 상위 TOP_YEAR개 검사 ID의 분기별 값이다. 같은 횟수면 검사 ID 순(_rank)이다.

        Returns:
            mrkdwn 문자열.
        """
        sp = months_span(f"{self.year}-01", f"{self.year}-12")
        quarters = self.quarters()

        def per_quarter(value: Callable[[Span], str]) -> str:
            return ", ".join(f"{name} {value(qs)}" for name, qs in quarters)

        def agree(qs: Span) -> str:
            a = agreement_summary(self.cases, qs)
            return _frac(a["match"], a["judged"])

        a = agreement_summary(self.cases, sp)
        lead = [
            "*팀장 업무*",
            f"• 판정 일치율: {_ratio(a['match'], a['judged'])}",
            _sub(f"분기별: {per_quarter(agree)}"),
        ]
        team = [
            "*팀 전체*",
            f"• 첫 검수 통과율: {_ratio(*pass_count(first_reviews(self.tickets, sp)))}",
            _sub(f"분기별: {per_quarter(lambda qs: _frac(*pass_count(first_reviews(self.tickets, qs))))}"),
        ]
        top = [cid for cid, _ in sorted(fail_counts(self.tickets, sp).items(), key=_rank)[:TOP_YEAR]]
        team.append(f"• ❌ 상위 {TOP_YEAR}개의 분기별 만족률" + ("" if top else ": -"))
        sat = {name: check_satisfaction(self.tickets, qs) for name, qs in quarters}
        for cid in top:
            team.append(
                _sub(f"{cid}: " + ", ".join(f"{name} {_frac(*sat[name].get(cid, (0, 0)))}" for name, _ in quarters))
            )
        groups = [
            [f"• {_name(r.name)}: {_frac(*r.first)} / {_times(r.max_request[0])}"]
            for r in member_rows(self.tickets, sp)
        ]

        def cost(qs: Span) -> str:
            u = usage(self.tickets, qs)
            return f"{u.total_cost:.2f}" if u.total_runs else "-"

        u = usage(self.tickets, sp)
        use = [
            "*Claude 사용량(API 환산 비용)*",
            f"• 합계 {_usd(u.total_cost)}(실행 {u.total_runs}건)",
            _sub(f"분기별: {per_quarter(cost)}"),
        ]
        title = f"[{self.year} jira-doc 연간 보고]"
        return self.assemble(title, sp, [lead, team, use], "첫 검수 통과율 / 최대 검수 반복 횟수", groups)

    def messages(self) -> list[str]:
        """팀장 채널로 보낼 Slack 메시지 목록. 묶음마다 메시지 하나다.

        월간 묶음은 매달, 분기 묶음은 대상 달이 3, 6, 9, 12월이면, 연간 묶음은 12월이면 이 순서로 붙는다.

        Returns:
            mrkdwn 문자열 목록.
        """
        out = [self.monthly_message()]
        if int(self.month[5:]) % 3 == 0:
            out.append(self.quarterly_message())
        if self.month.endswith("-12"):
            out.append(self.yearly_message())
        return out

    def summary_lines(self) -> list[str]:
        """Summary 탭에 남길 팀 합계 줄. 담당자 이름, 담당자별 수치, 구두 피드백 대상 후보는 넣지 않는다.

        Returns:
            markdown 줄.
        """
        cur, sp = self.cur_usage, self.cur
        fb = feedback_minutes(self.tickets, sp)
        ov = ticket_overview(self.tickets, sp, sp[0])
        a = agreement_summary(self.cases, sp)
        wait_n, wait_days = lead_wait(self.tickets, sp[1])
        idle = ", ".join(f"{c} {ov.idle[c]}" for c in (LEAD_WAIT, OWNER_IDLE, OTHER_IDLE))
        lines = [
            f"### jira-doc 정기 보고 {self.month} (팀 합계)",
            "",
            f"- Claude 사용량: 실행 {cur.total_runs}건, API 환산 비용 {_usd(cur.total_cost)}",
            f"- 사용량 많음 판정: {self.high[1]}({'; '.join(self.high[2])})",
            *(f"- {ko}: 실행 {cur.runs[b]}건, {_usd(cur.cost[b])}" for b, ko in BRANCHES.items()),
            "- Claude 토큰: " + ", ".join(f"{ko} {cur.tokens[k]:,}" for k, ko in TOKEN_KO.items()),
            f"- 기존 ticket: {ov.existing}개(업데이트 {ov.updated}개, 그중 done 전환 {ov.updated_done}개)",
            f"- 업데이트 없는 기존 ticket: {ov.idle_total}개({idle})",
            f"- 새 ticket: {ov.new_total}개",
        ]
        if not self.pr_ok:
            lines.append("- PR 검색 실패: PR 추가는 업데이트에서 뺌")
        if self.pr_capped:
            lines.append(f"- PR 검색이 상한 {PR_LIMIT}건에 닿음")
        lines += [
            f"- 첫 검수 통과율: {_ratio(*pass_count(first_reviews(self.tickets, sp)))}",
            f"- 최대 검수 반복 횟수: {_times(*max_request_count(self.tickets, sp))}",
            f"- stop 사유 기록률: {_ratio(*r4_count(self.tickets, sp))}",
            f"- 피드백 대기 시간: {_median(fb)}",
            f"- 판정 일치율: {_ratio(a['match'], a['judged'])}",
            f"- 팀장 확인 대기: {wait_n}건(최장 {wait_days}영업일)",
            f"- 팀장 확인 시간: {_median(lead_confirm_minutes(self.tickets, sp, self.lead))}",
        ]
        if int(self.month[5:]) % 3 == 0:
            q = agreement_summary(self.cases, self.quarter)
            lines.append(f"- 분기 판정 일치율: {_ratio(q['match'], q['judged'])}")
        lines += ["- 담당자별 수치와 구두 피드백 대상 후보는 팀장 채널 메시지에만 적음", ""]
        return lines


def run_report(
    j: JiraReader,
    month: str,
    lead: str,
    summ: SummaryLike,
    pr_search: Callable[[str, dt.date, dt.date], list[dict] | None] | None = None,
) -> list[str]:
    """정기 보고 메시지를 계산해 묶음별 Slack 메시지를 만들고 Summary 탭에 팀 합계를 남긴다.

    읽는 기간은 전월 첫날부터이고, 분기 묶음이 붙는 달(3, 6, 9, 12월)은 분기 첫날부터, 연간 묶음이 붙는 달(12월)은
    그해 1월 1일부터다. 프로젝트 키는 환경 변수 JIRA_PROJECT_KEY, GitHub organization은 GH_ORG에서 읽는다.
    업데이트 판정은 월간 묶음에서만 쓰므로 sub-task와 PR은 대상 달에 만들어진 것만 한 번씩 검색한다.
    PR 검색 결과가 PR_LIMIT건에 닿으면 월간 메시지에 그 사실을 적는다.
    PR 검색 기간은 build_jql처럼 하루 앞선 날부터이고, 기간 판정은 시각으로 다시 한다.

    Args:
        j: Jira 읽기 객체(scripts/jira_api.py의 Jira).
        month: 보고 대상 달(YYYY-MM).
        lead: 팀장 Jira accountId.
        summ: Summary 탭 기록 객체(scripts/apply.py의 Summary). extra에 팀 합계 줄을 추가한다.
        pr_search: PR 검색 함수(search_prs와 같은 인자와 반환값). None이면 search_prs를 쓴다.

    Returns:
        Slack 메시지 목록(mrkdwn). 월간, 분기, 연간 묶음 순이다(Report.messages).
    """
    project = os.environ.get("JIRA_PROJECT_KEY", "")
    if not project:
        sys.exit("report 모드에는 JIRA_PROJECT_KEY가 필요하다")
    first = add_months(month, -1)
    if int(month[5:]) % 3 == 0:
        first = min(first, quarter_first(month))
    if month.endswith("-12"):
        first = f"{month[:4]}-01"
    jql = build_jql(project, months_span(first, first)[0].date())
    tickets = collect(j, jql)
    cur = months_span(month, month)
    since, until = cur[0].date(), (cur[1] - dt.timedelta(days=1)).date()
    prs = (pr_search or search_prs)(os.environ.get("GH_ORG", ""), since - dt.timedelta(days=1), until)
    attach_updates(tickets, fetch_subtasks(j, project, since, cur[1].date()), pr_times(prs or [], project))
    report = Report(tickets, month, lead, pr_ok=prs is not None, pr_capped=prs is not None and len(prs) >= PR_LIMIT)
    summ.extra += report.summary_lines()
    return report.messages()
