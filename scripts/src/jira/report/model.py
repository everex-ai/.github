"""정기 보고가 쓰는 Protocol과 dataclass, 여러 모듈이 같이 쓰는 기간 type과 갈래 이름."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

BRANCHES = {"first": "첫 검수", "cycle": "반려 cycle 재검수", "retry": "재실행·실패", "digest": "정리 모드"}
TOKEN_KO = {"input": "input", "output": "output", "cacheWrite": "cache 쓰기", "cacheRead": "cache 읽기"}
Span = tuple[dt.datetime, dt.datetime]  # 기간 [시작, 끝)


class JiraReader(Protocol):
    """정기 보고에 필요한 Jira 읽기 메서드. scripts/src/jira/client.py의 Jira, 테스트의 가짜 객체가 이 모양을 따른다."""

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
    """Summary 탭 기록 객체(scripts/src/common/github.py의 Summary). 이 모듈은 extra만 사용한다."""

    extra: list[str]

    def row(self, key: str, itype: str, mode: str, result: str, note: str = "") -> None:
        """실행 결과 표에 행 하나를 추가한다."""
        ...


class Judged(Protocol):
    """판정 일치율을 계산할 검수 comment. ReviewComment와 scripts/src/dev/jira_metrics/model.py의 Review가 이 모양을 따른다."""

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
    title: str = ""  # 제목(summary)
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
    idle: Counter = field(default_factory=Counter)  # 업데이트 없는 기존 ticket의 대상 기간 끝 상태 -> 수
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
