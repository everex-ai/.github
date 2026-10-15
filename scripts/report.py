#!/usr/bin/env python3
"""report 모드: jira-doc 정기 보고 메시지(월간, 분기, 연간 묶음)를 Jira 기록으로 계산해 Slack 본문을 만든다.

- 진입 함수는 run_report다. scripts/apply.py의 main이 --mode report일 때 부르고, 반환한 본문을 팀장 채널로 보낸다.
- 지표 정의는 docs/jira-doc-agent/alerts.md(3절 지표 정의, 4절 알림 체계)를 따른다.
- Jira는 읽기만 한다(scripts/jira_api.py의 Jira.search, changelog, comments, prop_get).
- 검수 comment와 문서화 리뷰 comment의 문구는 scripts/apply.py의 review_comment, doc_review_comment, check_table,
  VERDICT_KO, RESULT_KO가 만든다. apply.py가 이 모듈을 불러오므로 순환 import를 피하려고 apply.py를 불러오지 않고
  같은 문구를 이 파일에 둔다. 두 파일의 값이 같은지는 tests/jira/test_report.py가 확인한다.
- 달, 분기, 해는 한국 시간으로 나눈다.
- Summary 탭(summ.extra)에는 팀 합계만 남긴다. 담당자 이름, 담당자별 수치, 구두 피드백 대상 후보는 Slack 본문에만 적는다.
  public repo의 Actions 실행 화면은 GitHub에 로그인한 누구나 열람할 수 있기 때문이다.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import statistics
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).parent))
from jira_api import is_agent_comment  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))  # 달, 분기, 해를 나누는 시간대
READY_STATUS, IN_PROGRESS, DONE_STATUS = "ready-to-done", "In Progress", "done"  # Jira 상태 이름. 대소문자 무시로 비교
KR_TYPE = "KR"  # 분기 묶음에서 상위 ticket으로 묶는 work type
# 대상 work type. scripts/collect.py의 TARGET_TYPES, TYPE_ALIAS와 같음
TARGET_TYPES = [t.strip() for t in os.environ.get("TARGET_TYPES", "Task,Bug,Issue").split(",")]
TYPE_ALIAS = {"작업": "Task", "버그": "Bug", "이슈": "Issue", "task": "Task", "bug": "Bug", "issue": "Issue"}
ISSUE_FIELDS = ["summary", "status", "assignee", "issuetype", "parent", "description"]
SEARCH_LIMIT = 5000  # Jira.search의 결과 상한. 닿으면 본문에 알린다
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
BRANCH_DESC = {  # 갈래의 뜻. Slack 본문에서 갈래 줄 아래에 적는다
    "first": ["뜻: ticket의 첫 request 전환에 따른 검수"],
    "cycle": ["뜻: 두 번째 이후 request 전환에 따른 검수"],
    "retry": [
        "뜻: 아래 세 가지 실행",
        "3-R 재실행: Claude 문서화를 3-R 단계에서 다시 실행",
        "같은 request 재검수: 같은 request 전환을 다시 검수",
        "반영 실패: 4단계에서 반영에 실패한 실행",
    ],
    "digest": ["뜻: F3(ticket 화면의 문서 정리 버튼)으로 실행한 정리"],
}
BRANCH_CAUSE = {  # 갈래가 늘어난 원인의 해석. 확인한 사실이 아니라 "추정:"을 붙인다
    "first": "추정: 첫 검수 증가는 done 전환 ticket 증가 때문임",
    "cycle": "추정: 반려 cycle 재검수 증가는 반려 증가 때문임",
    "retry": "추정: 재실행·실패 증가는 CI agent 쪽 문제 때문임",
    "digest": "추정: 정리 모드 증가는 F3 요청 증가 때문임",
}
TOKEN_KO = {"input": "input", "output": "output", "cacheWrite": "cache 쓰기", "cacheRead": "cache 읽기"}
HIGH_RATIO, HIGH_MIN_PREV_REVIEWS = 1.5, 10  # 사용량 많음: 비용이 전월의 1.5배 이상이고 전월 검수 실행이 10건 이상
CANDIDATE_MIN = 2  # 구두 피드백 대상 후보: 반려 2회 이상 또는 같은 검사 ID ❌ 2회 이상
TOP_MONTH, TOP_QUARTER = 3, 5  # 검사 ID별 ❌ 상위 개수(월간, 분기)
TOP_YEAR_MONTHLY = 5  # 연간 묶음에서 월별 만족률을 붙이는 연간 ❌ 횟수 상위 검사 ID 수
RECENT_N = 20  # 최근 판정 일치율의 표본 수
INDENT = "    "  # Slack 본문의 하위 항목 들여쓰기(scripts/apply.py의 SLACK_INDENT와 같음)
# 판정 일치율의 결과 값. MATCH, MISMATCH만 일치율에 들어간다
MATCH, MISMATCH, ESCALATE = "일치", "불일치", "검토 요청"
AUTO_HOLD, NOT_LEAD, WAITING, OTHER = "자동 보류", "팀장 외 전환", "전환 없음", "기타 전환"
EXCLUDED_DESC = {
    AUTO_HOLD: "뜻: 자동 보류 전환을 없애기 전의 보류 판정",
    NOT_LEAD: "뜻: 팀장이 아닌 계정이 한 첫 전환",
    WAITING: "뜻: 다음 검수 comment 전까지 전환이 없음",
    OTHER: "뜻: done, In Progress가 아닌 상태로 간 전환",
}
FOLLOW_DESC = {  # 지난달 후보의 개선 여부 결과 값의 뜻
    "재발": "기준: 지적 검사 ID 중 하나가 이번 달 다시 ❌",
    "개선": "기준: 재발이 없고 첫 검수 통과율이 지난달보다 높음",
    "유지": "기준: 재발도 개선도 아님",
    "판단 불가": "기준: 이번 달 검수 comment가 없음",
}
TERMS = {  # 처음 나올 때 하위 줄로 풀어 적는 말
    "request 전환": "request 전환: 담당자가 완료를 요청하는 Jira 전환",
    "반려": "반려: ready-to-done에서 In Progress로 돌아간 전환",
}
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
    changes: list[StatusChange] = field(default_factory=list)
    reviews: list[ReviewComment] = field(default_factory=list)  # 검수 comment. 작성 순
    docs: list[ReviewComment] = field(default_factory=list)  # 문서화 리뷰 comment. 작성 순
    runs: list[Run] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)  # 검사 ID -> 검사 표의 "검사 이름 (ID)"


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
    "문서화 리뷰 결과(관찰 모드)" 줄이 있으면 문서화 리뷰 comment로 센다.

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
    t = Ticket(
        key=key,
        itype=TYPE_ALIAS.get(name.strip().lower(), TYPE_ALIAS.get(name, name)),
        assignee_id=assignee.get("accountId") or "",
        assignee_name=assignee.get("displayName") or "",
        parent_key=parent.get("key") or "",
        parent_name=pf.get("summary") or "",
        parent_type=(pf.get("issuetype") or {}).get("name") or "",
        description=f.get("description") or "",
        changes=status_changes(j.changelog(key)),
    )
    for c in j.comments(key):
        at = parse_ts(c.get("created"))
        if at is None or not is_agent_comment(c):
            continue
        body = c.get("body") or ""
        if parsed := parse_review(body):
            hold = parsed[0] == VERDICT_KO["fix"] and AUTO_HOLD_NOTE in body
            t.reviews.append(ReviewComment(at, parsed[0], parsed[1], hold))
        elif (doc := parse_doc_review(body)) is not None:
            t.docs.append(ReviewComment(at, "", doc))
        else:
            continue
        t.labels.update({cid: label for cid, label, _ in table_rows(body)})
    t.reviews.sort(key=lambda r: r.at)
    t.docs.sort(key=lambda r: r.at)
    runs = (j.prop_get(key) or {}).get("runs")
    t.runs = mark_reruns([r for r in (parse_run(e) for e in (runs if isinstance(runs, list) else [])) if r])
    return t


def build_jql(project: str, since: dt.date) -> str:
    """보고에 필요한 기간을 덮는 ticket 검색 조건.

    JQL의 날짜는 API 계정의 시간대로 해석되므로 한국 시간 since 첫날의 변경이 빠지지 않게 하루 앞선 날부터 찾는다.

    Args:
        project: Jira 프로젝트 키.
        since: 읽을 기간의 첫날(한국 시간).

    Returns:
        검색 조건.
    """
    day = since - dt.timedelta(days=1)
    return f'project = {project} AND issuetype in ({", ".join(TARGET_TYPES)}) AND updated >= "{day.isoformat()}"'


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


def _be(value: str) -> str:
    """값 문구 뒤에 "임"을 붙인다. 값이 "기록 없음"이면 그대로 둔다.

    Args:
        value: _pct 같은 함수가 만든 값 문구.

    Returns:
        문장 끝 문구.
    """
    return value if value == "기록 없음" else f"{value}임"


def _pct(a: int, b: int, unit: str = "건") -> str:
    return f"{a}/{b}{unit}, {a / b * 100:.1f}%" if b else "기록 없음"


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


class Report:
    """정기 보고 하나의 계산 결과와 본문 조립.

    본문은 줄마다 핵심 하나를 개조식으로 적고, 설명은 하위 줄("◦")로 내린다.
    """

    def __init__(self, tickets: list[Ticket], month: str, lead: str, jql: str):
        self.tickets, self.month, self.lead, self.jql = tickets, month, lead, jql
        self.cur = months_span(month, month)
        self.prev_month = add_months(month, -1)
        self.prev = months_span(self.prev_month, self.prev_month)
        self.labels: dict[str, str] = {}
        for t in tickets:
            self.labels.update(t.labels)
        self.cur_usage, self.prev_usage = usage(tickets, self.cur), usage(tickets, self.prev)
        self.high = usage_judgement(self.cur_usage, self.prev_usage)
        self.explained: set[str] = set()

    def explain(self, term: str, depth: int = 1) -> list[str]:
        """TERMS의 말을 처음 나올 때만 하위 줄로 풀어 적는다.

        Args:
            term: TERMS의 키.
            depth: 들여쓰기 단계.

        Returns:
            처음이면 설명 줄 하나, 이미 설명했으면 빈 목록.
        """
        if term in self.explained:
            return []
        self.explained.add(term)
        return [_sub(TERMS[term], depth)]

    def label(self, cid: str) -> str:
        """검사 ID를 검사 표의 "검사 이름 (ID)"로 적는다. 읽은 comment에 없으면 ID만 적는다.

        Args:
            cid: 검사 ID.

        Returns:
            검사 칸 문구.
        """
        return _esc(self.labels.get(cid, cid))

    def top_fails(self, sp: Span, n: int) -> list[str]:
        """기간의 검사 ID별 ❌ 상위 n개 줄. 월간 묶음에서 먼저 불리므로 ❌의 뜻은 월간에서만 적는다.

        Args:
            sp: 기간.
            n: 개수.

        Returns:
            Slack 본문 줄.
        """
        reviews = len(comments_in(self.tickets, sp, docs=False))
        docs = len(comments_in(self.tickets, sp)) - reviews
        top = sorted(fail_counts(self.tickets, sp).items(), key=_rank)[:n]
        mark = "❌" if "❌" in self.explained else "❌(보완 필요)"
        self.explained.add("❌")
        basis = _sub(f"대상: 검수 comment {reviews}건, 문서화 리뷰 comment {docs}건")
        if not top:
            return [f"• 검사 ID별 {mark} 상위 {n}개는 기록 없음", basis]
        lines = [f"• 검사 ID별 {mark} 상위 {n}개는 아래와 같음", basis, _sub("값: 검사 표의 ❌ 행 수")]
        return lines + [f"{INDENT}{i}. {self.label(cid)}: ❌ {c}회" for i, (cid, c) in enumerate(top, 1)]

    def monthly(self) -> list[str]:
        """월간 묶음 줄.

        Returns:
            Slack 본문 줄.
        """
        return ["*월간*", *self.team_metrics(), "", *self.usage_lines(), "", *self.candidate_lines()]

    def team_metrics(self) -> list[str]:
        """월간 팀 지표 줄.

        Returns:
            Slack 본문 줄.
        """
        sp = self.cur
        done = sum(1 for t in self.tickets if done_in(t, sp))
        fb = feedback_minutes(self.tickets, sp)
        lines = [
            f"팀 지표({span_text(sp)})",
            f"• done ticket 수는 {done}개임",
            _sub("기준: 이 기간에 done으로 전환된 ticket 수"),
            f"• 첫 검수 통과율은 {_be(_pct(*pass_count(first_reviews(self.tickets, sp))))}",
            _sub("분모: 이 기간에 작성된 ticket별 첫 검수 comment"),
            _sub("분자: 그중 검수 결과가 통과인 comment"),
            *self.top_fails(sp, TOP_MONTH),
            f"• stop 사유 기록률은 {_be(_pct(*r4_count(self.tickets, sp)))}",
            _sub("기준: 이 기간 검수 comment의 R4 행 중 만족 비율"),
            _sub("해당 없음인 R4 행은 뺌"),
        ]
        if fb:
            lines += [
                f"• 피드백 대기 시간은 중앙값 {_fmt(statistics.median(fb))}분임",
                _sub(f"표본: request 전환 {len(fb)}건"),
                *self.explain("request 전환"),
                _sub(f"범위: {_fmt(min(fb))}–{_fmt(max(fb))}분"),
                _sub("측정: request 전환부터 다음 첫 검수 comment까지"),
            ]
        else:
            lines += [
                "• 피드백 대기 시간은 기록 없음",
                _sub("request 전환 뒤 검수 comment가 달린 경우가 0건임"),
                *self.explain("request 전환"),
            ]
        return lines

    def usage_lines(self) -> list[str]:
        """월간 묶음의 Claude 사용량 줄.

        Returns:
            Slack 본문 줄.
        """
        cur, prev = self.cur_usage, self.prev_usage
        flag, word, basis = self.high
        lines = [
            f"Claude 사용량({span_text(self.cur)})",
            "• 대상: jira-doc 기록의 실행 기록(runs)",
            _sub("달은 실행 날짜로 나눔"),
            "• API 환산 비용: claude CLI가 API 단가로 계산한 값",
            _sub("실제 청구액이 아님"),
        ]
        if not cur.total_runs:
            lines.append("• 이번 달 실행 기록 없음")
        else:
            lines.append(f"• API 환산 비용 합계는 {_usd(cur.total_cost)}임")
            lines.append(_sub(f"실행 {cur.total_runs}건"))
            if cur.missing_cost:
                lines.append(_sub(f"API 환산 비용 기록이 없는 실행 {cur.missing_cost}건은 0 USD로 셈"))
        lines.append(_sub(f"전월: 실행 {prev.total_runs}건, {_usd(prev.total_cost)}"))
        lines += [f"• 사용량 많음 판정: {word}", *(_sub(b) for b in basis)]
        lines.append(_sub(f"기준: API 환산 비용이 전월의 {HIGH_RATIO}배 이상"))
        lines.append(_sub(f"기준: 전월 검수 실행이 {HIGH_MIN_PREV_REVIEWS}건 이상"))
        lines.append("• 실행 종류별 네 갈래의 값은 아래와 같음")
        for b, ko in BRANCHES.items():
            lines.append(
                _sub(f"{ko}: {cur.runs[b]}건, {_usd(cur.cost[b])}(전월 {prev.runs[b]}건, {_usd(prev.cost[b])})")
            )
            lines += [_sub(d, 2) for d in BRANCH_DESC[b]]
        lines.append("• 이번 달 토큰 수는 아래와 같음")
        lines += [_sub(f"{ko}: {cur.tokens[k]:,}") for k, ko in TOKEN_KO.items()]
        if flag:
            lines += self.cause_lines()
        return lines

    def cause_lines(self) -> list[str]:
        """사용량 많음일 때 API 환산 비용이 늘어난 갈래와 원인 추정 줄.

        Returns:
            Slack 본문 줄.
        """
        cur, prev = self.cur_usage, self.prev_usage
        up = sorted(
            ((b, cur.cost[b] - prev.cost[b], cur.runs[b] - prev.runs[b]) for b in BRANCHES),
            key=lambda x: -x[1],
        )
        up = [x for x in up if x[1] > 0]
        lines = ["• 전월보다 API 환산 비용이 늘어난 갈래는 아래와 같음"]
        lines += [_sub(f"{BRANCHES[b]}: {dc:+.2f} USD, 실행 {dn:+d}건") for b, dc, dn in up]
        lines += [f"• {BRANCH_CAUSE[b]}" for b, _, _ in up]
        done = [sum(1 for t in self.tickets if done_in(t, sp)) for sp in (self.cur, self.prev)]
        rej = [sum(rejections(t, sp) for t in self.tickets) for sp in (self.cur, self.prev)]
        lines.append(f"• 참고: done ticket {done[0]}개(전월 {done[1]}개)")
        lines.append(f"• 참고: 반려 {rej[0]}회(전월 {rej[1]}회)")
        return lines + self.explain("반려")

    def candidate_lines(self) -> list[str]:
        """구두 피드백 대상 후보와 지난달 후보의 개선 여부 줄.

        Returns:
            Slack 본문 줄.
        """
        cands = candidates(self.tickets, self.cur)
        lines = [
            f"구두 피드백 대상 후보({self.month}, 담당자별)",
            f"• 조건 A: 한 ticket의 반려가 {CANDIDATE_MIN}회 이상",
            *self.explain("반려"),
            f"• 조건 B: 같은 검사 ID의 ❌가 {CANDIDATE_MIN}회 이상",
            _sub("검수 comment와 문서화 리뷰 comment를 합쳐 셈"),
            "• 조건 A 또는 B를 만족하면 후보임",
        ]
        for c in cands.values():
            lines.append(f"• {_esc(c.name)}")
            lines += [_sub(f"조건 A: {key} 반려 {n}회") for key, n in c.rejected]
            lines += [_sub(f"조건 B: {self.label(cid)} ❌ {n}회") for cid, n in c.failed]
        if not cands:
            rej = sum(rejections(t, self.cur) for t in self.tickets)
            n = len(comments_in(self.tickets, self.cur, docs=False))
            lines.append("• 기록 없음" if not rej and not n else "• 후보 없음")
            lines.append(_sub(f"이 기간 반려 {rej}회, 검수 comment {n}건"))
        lines += ["", f"지난달 후보의 개선 여부({self.prev_month} 후보)"]
        lines.append("• 지난달 후보는 같은 규칙으로 다시 계산함")
        follow = followup(candidates(self.tickets, self.prev), self.tickets, self.cur, self.prev)
        for name, res, detail in follow:
            lines += [f"• {_esc(name)}: {res}", _sub(FOLLOW_DESC[res]), *(_sub(d) for d in detail)]
        if not follow:
            lines.append("• 지난달 후보 없음")
        lines += ["• 실제 구두 피드백 여부는 반영하지 않음", "• 결과는 후보로 알린 뒤의 변화로 해석함"]
        return lines

    def quarterly(self) -> list[str]:
        """분기 묶음 줄.

        Returns:
            Slack 본문 줄.
        """
        y, m = map(int, self.month.split("-"))
        sp = months_span(quarter_first(self.month), self.month)
        lines = [
            f"*분기({y} Q{(m - 1) // 3 + 1})*",
            f"• 기간: {span_text(sp)}",
            "",
            "KR(상위 목표 ticket)별 하위 Task",
            "• 대상: 이 분기에 done으로 전환된 Task",
        ]
        rows = kr_rows(self.tickets, sp)
        for r in rows:
            lines += [f"• {r.key} {_esc(r.name)}".rstrip(), _sub(f"done {r.done}개")]
            rate = _pct(r.achieved, r.expected, "개") if r.expected else "기록 없음"
            lines.append(_sub(f"결과 산출물 달성률: {rate}"))
            if r.no_line:
                lines.append(_sub(f"달성 요약 줄이 없는 Task {r.no_line}개는 뺌"))
        if not rows:
            lines.append("• 기록 없음(done으로 전환된 Task 0개)")
        lines += [
            "• 달성률은 description의 달성 요약 줄을 합산함",
            _sub('달성 요약 줄: "예상 산출물 n개 중 m개 달성"'),
            _sub("계산: m의 합 / n의 합"),
            "",
            "검사 ID별 ❌",
            *self.top_fails(sp, TOP_QUARTER),
        ]
        a = agreement_summary(agreement_cases(self.tickets, self.lead), sp)
        lines += [
            "",
            "판정 일치율",
            "• 대상: 이 분기에 작성된 검수 comment",
            f"• 분기값은 {_be(_pct(a['match'], a['judged']))}",
            f"• 최근 {RECENT_N}건 값은 {_be(_pct(a['recent_match'], a['recent_n']))}",
            _sub(f"대상: 분기 끝까지 판정할 수 있는 최근 최대 {RECENT_N}건"),
            _sub("이번 실행에서 읽은 ticket 안에서만 셈"),
            f"• 검토 요청 비율은 {_be(_pct(a['escalate'], a['total']))}",
            _sub("분자: 검수 결과가 검토 요청인 검수 comment"),
            _sub("분모: 이 분기 검수 comment 전체"),
            _sub("검토 요청: CI agent가 팀장 판단을 요청한 검수 결과"),
            "• 판정 기준은 아래와 같음",
            _sub("검수 comment 뒤 ready-to-done에서 나간 첫 전환을 확인함"),
            _sub("팀장 계정의 전환만 셈"),
            _sub("일치: 통과→done, 보류→In Progress"),
            _sub("불일치: 통과→In Progress, 보류→done"),
        ]
        if not self.lead:
            lines.append(_sub("팀장 accountId가 없어 모든 전환을 팀장 외 전환으로 셈"))
        lines += ["• 일치율에서 뺀 검수 comment는 아래와 같음", _sub(f"{ESCALATE}: {a['escalate']}건")]
        for k, n in a["excluded"].items():
            lines += [_sub(f"{k}: {n}건"), _sub(EXCLUDED_DESC[k], 2)]
        return lines

    def yearly(self) -> list[str]:
        """연간 묶음 줄. 첫 실행 기록이 있는 달 전의 사용량은 "기록 없음"으로 적는다.

        검사 ID별 만족률은 검사 ID마다 연간 값을 적고, 연간 ❌ 횟수 상위 TOP_YEAR_MONTHLY개 검사 ID에만 월별 값을
        하위 줄로 붙인다. 같은 횟수면 검사 ID별 ❌ 상위의 정렬 규칙(_rank)을 따른다.

        Returns:
            Slack 본문 줄.
        """
        y = self.month[:4]
        months = [f"{y}-{m:02d}" for m in range(1, 13)]
        firsts = [r.at for t in self.tickets for r in t.runs]
        start = min(firsts).astimezone(KST) if firsts else None
        lines = [f"*연간({y})*", "", "월별 Claude 사용량", "• 값: 실행 날짜 기준 API 환산 비용"]
        if start:
            lines += [f"• 첫 실행 기록은 {start.date().isoformat()}임", _sub("그 전 달은 기록 없음으로 적음")]
        else:
            lines.append("• 읽은 범위에 실행 기록이 없음")
        for mo in months:
            if start is None or mo < start.strftime("%Y-%m"):
                lines.append(f"• {mo}: 기록 없음")
                continue
            u = usage(self.tickets, months_span(mo, mo))
            lines.append(f"• {mo}: {_usd(u.total_cost)}, 실행 {u.total_runs}건")
            lines += [_sub(f"{ko} {_usd(u.cost[b])}") for b, ko in BRANCHES.items()]
        lines += ["", "월별 첫 검수 통과율", "• 기준: 그 달에 작성된 ticket별 첫 검수 comment"]
        lines += [f"• {mo}: {_pct(*pass_count(first_reviews(self.tickets, months_span(mo, mo))))}" for mo in months]
        sp = months_span(months[0], months[-1])
        sat = check_satisfaction(self.tickets, sp)
        lines += [
            "",
            "검사 ID별 만족률",
            f"• 기간: {span_text(sp)}",
            "• 대상: 검수 comment와 문서화 리뷰 comment",
            "• 해당 없음인 행은 뺌",
        ]
        fails = fail_counts(self.tickets, sp)
        top = [cid for cid, _ in sorted(fails.items(), key=_rank)[:TOP_YEAR_MONTHLY]]
        if top:
            lines += [
                f"• 월별 값은 연간 ❌ 횟수 상위 {TOP_YEAR_MONTHLY}개 검사 ID에만 적음",
                _sub("같은 횟수면 검사 ID 순"),
            ]
        monthly = {mo: check_satisfaction(self.tickets, months_span(mo, mo)) for mo in months}
        ranked = sorted(sat.items(), key=lambda x: (x[1][0] / x[1][1], x[0]))
        for cid, (ok, n) in ranked:
            lines.append(f"• {self.label(cid)}: {_pct(ok, n)}")
            if cid in top:
                lines.append(_sub(f"연간 ❌ {fails[cid]}회"))
                for mo in months:
                    val = monthly[mo].get(cid)
                    lines.append(_sub(f"{mo}: {_pct(*val) if val else '기록 없음'}"))
        if not ranked:
            lines.append("• 기록 없음(판정된 행 0건)")
        return lines

    def body(self) -> str:
        """Slack 본문.

        Returns:
            mrkdwn 문자열.
        """
        lines = [
            f"[{self.month} jira-doc 정기 보고]",
            f"• 읽은 범위: Jira 검색 조건(JQL)으로 찾은 ticket {len(self.tickets)}개",
            _sub(f"JQL: `{self.jql}`"),
            "• 시각은 한국 시간 기준임",
            "• 기간은 양 끝 날짜를 포함함",
        ]
        if len(self.tickets) >= SEARCH_LIMIT:
            lines += [f"• 검색 결과가 상한 {SEARCH_LIMIT}개에 닿음", _sub("빠진 ticket이 있을 수 있음")]
        lines += ["", *self.monthly()]
        if int(self.month[5:]) % 3 == 0:
            lines += ["", *self.quarterly()]
        return "\n".join(lines)

    def messages(self) -> list[str]:
        """팀장 채널로 보낼 Slack 메시지 목록.

        첫 메시지는 월간 묶음과 분기 묶음이다. 연간 묶음이 붙는 달(12월)에는 연간 묶음을 두 번째 메시지로 나눈다.
        한 메시지가 지나치게 길어지지 않게 하려는 것이다.

        Returns:
            mrkdwn 문자열 목록.
        """
        out = [self.body()]
        if self.month.endswith("-12"):
            head = [
                f"[{self.month[:4]} jira-doc 연간 보고]",
                "• 시각은 한국 시간 기준임",
                "• 기간은 양 끝 날짜를 포함함",
            ]
            out.append("\n".join([*head, "", *self.yearly()]))
        return out

    def summary_lines(self) -> list[str]:
        """Summary 탭에 남길 팀 합계 줄. 담당자 이름, 담당자별 수치, 구두 피드백 대상 후보는 넣지 않는다.

        Returns:
            markdown 줄.
        """
        cur, sp = self.cur_usage, self.cur
        fb = feedback_minutes(self.tickets, sp)
        lines = [
            f"### jira-doc 정기 보고 {self.month} (팀 합계)",
            "",
            f"- Claude 사용량: 실행 {cur.total_runs}건, API 환산 비용 {_usd(cur.total_cost)}",
            f"- 사용량 많음 판정: {self.high[1]}({'; '.join(self.high[2])})",
            *(f"- {ko}: 실행 {cur.runs[b]}건, {_usd(cur.cost[b])}" for b, ko in BRANCHES.items()),
            f"- done ticket 수: {sum(1 for t in self.tickets if done_in(t, sp))}개",
            f"- 첫 검수 통과율: {_pct(*pass_count(first_reviews(self.tickets, sp)))}",
            f"- stop 사유 기록률: {_pct(*r4_count(self.tickets, sp))}",
            f"- 피드백 대기 시간 중앙값: {_fmt(statistics.median(fb)) + '분' if fb else '기록 없음'}",
            f"- 피드백 대기 시간 표본: request 전환 {len(fb)}건",
        ]
        if int(self.month[5:]) % 3 == 0:
            qsp = months_span(quarter_first(self.month), self.month)
            a = agreement_summary(agreement_cases(self.tickets, self.lead), qsp)
            lines.append(f"- 분기 판정 일치율: {_pct(a['match'], a['judged'])}")
        lines += ["- 담당자별 수치와 구두 피드백 대상 후보는 팀장 채널 메시지에만 적음", ""]
        return lines


def run_report(j: JiraReader, month: str, lead: str, summ: SummaryLike) -> list[str]:
    """정기 보고 메시지를 계산해 Slack 본문을 만들고 Summary 탭에 팀 합계를 남긴다.

    읽는 기간은 전월 첫날부터이고, 분기 묶음이 붙는 달(3, 6, 9, 12월)은 분기 첫날부터, 연간 묶음이 붙는 달(12월)은
    그해 1월 1일부터다. 프로젝트 키는 환경 변수 JIRA_PROJECT_KEY에서 읽는다.

    Args:
        j: Jira 읽기 객체(scripts/jira_api.py의 Jira).
        month: 보고 대상 달(YYYY-MM).
        lead: 팀장 Jira accountId.
        summ: Summary 탭 기록 객체(scripts/apply.py의 Summary). extra에 팀 합계 줄을 추가한다.

    Returns:
        Slack 메시지 목록(mrkdwn). 12월 보고는 연간 묶음을 두 번째 메시지로 나눈다(Report.messages).
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
    report = Report(collect(j, jql), month, lead, jql)
    summ.extra += report.summary_lines()
    return report.messages()
