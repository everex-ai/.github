"""jira_metrics의 데이터 클래스와 CSV 칸 값 함수. Jira 읽기 Protocol, 검수 comment, task 기록, 계산 결과를 담는다."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.constant import check as check_const
from src.constant.time import KST
from src.jira.report.model import StatusChange

ID_SEP = ";"  # CSV 칸 안에서 검사 ID와 값 목록을 나누는 문자
TASK_COLUMNS = [
    "key",
    "issue_type",
    "request_count",
    "review_count",
    "first_request_at",
    "first_review_at",
    "feedback_minutes",
    "ready_hours",
    "first_verdict",
    "last_verdict",
    "first_fail_ids",
    "last_fail_ids",
    "resolved_fail_ids",
    "correction_comments",
    "failure_alerts",
    "after_last_verdict",
    "feedback_minutes_each",
    "ready_hours_each",
]
REVIEW_COLUMNS = ["key", "created_at", "verdict", "fail_ids", "pass_ids", "na_ids"]


def sort_ids(ids: Iterable[str]) -> list[str]:
    """검사 ID를 검사 표 순서(scripts/src/constant/check.py의 CHECK_ORDER)로 정렬한다.

    Args:
        ids: 검사 ID의 iterable.

    Returns:
        정렬한 검사 ID 목록.
    """
    return sorted(ids, key=lambda cid: (check_const.CHECK_ORDER.get(cid, 99), cid))


def iso(t: dt.datetime) -> str:
    """CSV에 적는 시각. 한국 시간 ISO 형식.

    Args:
        t: 시각.

    Returns:
        예: 2026-09-02T10:31:00+09:00.
    """
    return t.astimezone(KST).isoformat(timespec="seconds")


def num(x: float) -> str:
    """CSV에 적는 숫자. 소수 둘째 자리에서 반올림한다.

    Args:
        x: 숫자.

    Returns:
        숫자 문자열.
    """
    return str(round(x, 2))


def num_or_blank(x: float | None) -> str:
    """CSV에 적는 숫자. 값이 없으면 빈 문자열.

    Args:
        x: 숫자 또는 None.

    Returns:
        숫자 문자열 또는 빈 문자열.
    """
    return "" if x is None else num(x)


class JiraReader(Protocol):
    """집계에 필요한 Jira 읽기 메서드. scripts/src/jira/client.py의 Jira, 테스트의 가짜 객체가 이 모양을 따른다."""

    def search(self, jql: str, fields: list[str], limit: int = 500) -> list[dict]:
        """JQL로 task를 찾는다."""
        ...

    def changelog(self, key: str) -> list[dict]:
        """task의 변경 이력(Jira 응답의 values)을 돌려준다."""
        ...

    def comments(self, key: str) -> list[dict]:
        """task의 comment 목록을 작성 순서로 돌려준다."""
        ...


@dataclass
class Review:
    """검수 comment 하나의 판정과 검사 표 결과."""

    key: str
    at: dt.datetime
    verdict: str
    results: dict[str, str]  # 검사 ID -> 결과 코드값(pass, fail, n/a)
    auto_hold: bool = False  # 보류이고 자동 보류 전환 문구(scripts/src/jira/report/fetch.py의 AUTO_HOLD_NOTE)가 있음

    def ids(self, code: str) -> list[str]:
        """결과가 code인 검사 ID를 검사 표 순서로 돌려준다.

        Args:
            code: 결과 코드값(pass, fail, n/a).

        Returns:
            검사 ID 목록.
        """
        return sort_ids(cid for cid, result in self.results.items() if result == code)


@dataclass
class TaskRecord:
    """Jira에서 읽은 task 하나의 기록. 집계 기간과 관계없이 모든 기록을 담는다."""

    key: str
    issue_type: str
    changes: list[StatusChange]
    reviews: list[Review] = field(default_factory=list)
    failures: list[dt.datetime] = field(default_factory=list)  # 실패 알림 comment 작성 시각
    corrections: list[dt.datetime] = field(default_factory=list)  # "정정:" comment 작성 시각


@dataclass
class TaskResult:
    """집계 대상 task 하나의 계산 결과. tasks.csv의 한 행이 된다."""

    key: str
    issue_type: str
    requests: list[dt.datetime]  # 기간 안 request 전환 시각
    reviews: list[Review]  # 기간 안 첫 request 전환 뒤의 검수 comment
    failures: list[dt.datetime]  # 기간 안 첫 request 전환 뒤의 실패 알림 comment
    corrections: int
    feedback_minutes: list[float | None]  # requests마다 다음 request 전환 전 첫 검수 comment까지(분). 없으면 None
    ready_hours: list[float]  # 기간 안 request 전환마다 ready-to-done에 머문 시간(시간). 머무는 중이면 뺀다
    after_last_verdict: str  # 마지막 검수 comment 뒤 첫 status 변경의 도착 상태
    agreement: list[str] = field(default_factory=list)  # reviews마다 판정 일치율 결과 값(report/metrics.py의 judge)

    def first_fail_ids(self) -> list[str]:
        """첫 검수 comment에서 보완 필요인 검사 ID 목록.

        Returns:
            검사 ID 목록. 검수 comment가 없으면 빈 목록.
        """
        return self.reviews[0].ids("fail") if self.reviews else []

    def resolved_fail_ids(self) -> list[str]:
        """첫 검수 comment에서 보완 필요였고 마지막 검수 comment에서 만족인 검사 ID 목록.

        검수 comment가 하나뿐이면 첫 검수와 마지막 검수가 같으므로 빈 목록이다.

        Returns:
            검사 ID 목록.
        """
        if len(self.reviews) < 2:
            return []
        last = self.reviews[-1].results
        return [cid for cid in self.first_fail_ids() if last.get(cid) == "pass"]

    def row(self) -> dict[str, Any]:
        """tasks.csv의 한 행.

        feedback_minutes_each는 기간 안 request 전환마다 하나씩, 그 뒤이면서 다음 request 전환 전인 첫 검수
        comment까지(분)를 적고, 그런 검수 comment가 없으면 빈 값으로 둔다. feedback_minutes는 그 첫 값(기간 안 첫
        request 전환의 값)이다. ready_hours는 ready_hours_each의 합이다.

        Returns:
            TASK_COLUMNS를 키로 하는 dict.
        """
        first = self.reviews[0] if self.reviews else None
        last = self.reviews[-1] if self.reviews else None
        return {
            "key": self.key,
            "issue_type": self.issue_type,
            "request_count": len(self.requests),
            "review_count": len(self.reviews),
            "first_request_at": iso(self.requests[0]),
            "first_review_at": iso(first.at) if first else "",
            "feedback_minutes": num_or_blank(self.feedback_minutes[0]),
            "ready_hours": num(sum(self.ready_hours)) if self.ready_hours else "",
            "first_verdict": first.verdict if first else "",
            "last_verdict": last.verdict if last else "",
            "first_fail_ids": ID_SEP.join(self.first_fail_ids()),
            "last_fail_ids": ID_SEP.join(last.ids("fail")) if last else "",
            "resolved_fail_ids": ID_SEP.join(self.resolved_fail_ids()),
            "correction_comments": self.corrections,
            "failure_alerts": len(self.failures),
            "after_last_verdict": self.after_last_verdict,
            "feedback_minutes_each": ID_SEP.join(num_or_blank(x) for x in self.feedback_minutes),
            "ready_hours_each": ID_SEP.join(num(x) for x in self.ready_hours),
        }
