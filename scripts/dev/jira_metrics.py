#!/usr/bin/env python3
"""문서화 Agent(jira-doc)의 도입 효과 지표(J1, J3–J8)를 Jira 기록에서 집계한다. Jira에 쓰지 않는다.

    python scripts/dev/jira_metrics.py --since 2026-09-01 --until 2026-09-30 --out reports/agent-impact/jira-doc/jira

- 지표 정의는 docs/jira-doc-agent/metrics.md에 있다. J2(환산 리뷰 시간)는 지표에서 뺐다.
- Jira 읽기는 scripts/jira_api.py의 Jira.search, Jira.changelog, Jira.comments만 호출한다.
  환경 변수 JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN이 필요하다. J7의 팀장 계정은 --lead(기본값 JIRA_LEAD_ACCOUNT_ID)다.
- 검수 comment 파싱(parse_review)과 상태 이력 처리(status_changes)는 scripts/report.py에 있고, 모듈 이름 jira_report로
  불러와 사용한다. J7 판정 일치율도 report.py의 judge를 사용한다.
- 검사 이름과 표시 문구(check_label, CHECK_ORDER, RESULT_KO)는 scripts/apply.py에서 가져온다. apply.py는
  scripts/review/apply.py와 섞이지 않게 모듈 이름 jira_apply로 불러온다(scripts/dev/render_jira_samples.py와 같은 방법).
- 기간(--since, --until)은 한국 시간 날짜이고, 기간 안에 request 전환이 있는 task를 집계한다.
- 출력: <out>/tasks.csv(task별), <out>/reviews.csv(검수 comment별), <out>/summary.md(지표 요약). 담당자는 넣지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import os
import re
import statistics
import sys
import types
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SCRIPTS = Path(__file__).resolve().parents[1]
KST = dt.timezone(dt.timedelta(hours=9))  # 기간 경계, ISO 주, 월을 정하는 시간대
DONE_STATUS = "done"  # end 전환의 도착 상태(docs/jira-doc-agent/README.md "용어" 절의 Jira 전환). 대소문자 무시로 비교
CORRECTION_PREFIX = "정정:"  # 결과 산출물 구역과 TL;DR의 정정 요청 comment의 시작(scripts/apply.py의 SECTION_NOTE)
SEARCH_FIELDS = ["issuetype"]
SEARCH_LIMIT = 5000  # Jira.search의 결과 상한. 닿으면 summary.md와 stderr에 알린다
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


def load_module(name: str, path: Path) -> types.ModuleType:
    """스크립트 파일을 지정한 모듈 이름으로 불러온다.

    scripts/apply.py와 scripts/review/apply.py처럼 파일 이름이 같은 스크립트가 섞이지 않게 하려고,
    불러오는 동안 스크립트가 바꾼 sys.path를 원래대로 되돌린다. 이미 불러온 이름이면 그 모듈을 돌려준다.

    Args:
        name: sys.modules에 등록할 모듈 이름.
        path: 스크립트 파일 경로.

    Returns:
        불러온 모듈.
    """
    if name in sys.modules:
        return sys.modules[name]
    saved = list(sys.path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        sys.path[:] = saved
    return mod


JA = load_module("jira_apply", SCRIPTS / "apply.py")
JAPI = load_module("jira_api", SCRIPTS / "jira_api.py")  # apply.py가 불러온 jira_api 모듈을 그대로 받는다
JR = load_module("jira_report", SCRIPTS / "report.py")  # 검수 comment 파싱, 상태 이력, 판정 일치율

VERDICT_PREFIX, VERDICT_SUFFIX = JR.VERDICT_PREFIX, JR.VERDICT_SUFFIX
PASS_VERDICT = JR.VERDICT_KO["pass"]
StatusChange = JR.StatusChange
parse_ts = JR.parse_ts
is_status = JR.is_status
parse_review = JR.parse_review
status_changes = JR.status_changes
FAILURE_PREFIX = "자동 검수 실행 실패:"  # 구조화 출력 전환 전 apply.py가 Jira에 남기던 실패 알림 comment의 첫 문구


class JiraReader(Protocol):
    """집계에 필요한 Jira 읽기 메서드. scripts/jira_api.py의 Jira, 테스트의 가짜 객체가 이 모양을 따른다."""

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
    auto_hold: bool = False  # 보류이고 자동 보류 전환 문구(scripts/report.py의 AUTO_HOLD_NOTE)가 있음

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
    agreement: list[str] = field(default_factory=list)  # reviews마다 판정 일치율 결과 값(scripts/report.py의 judge)

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


def sort_ids(ids: Iterable[str]) -> list[str]:
    """검사 ID를 검사 표 순서(scripts/apply.py의 CHECK_ORDER)로 정렬한다.

    Args:
        ids: 검사 ID의 iterable.

    Returns:
        정렬한 검사 ID 목록.
    """
    return sorted(ids, key=lambda cid: (JA.CHECK_ORDER.get(cid, 99), cid))


def iso(t: dt.datetime) -> str:
    """CSV에 적는 시각. 한국 시간 ISO 형식.

    Args:
        t: 시각.

    Returns:
        예: 2026-09-02T10:31:00+09:00.
    """
    return t.astimezone(KST).isoformat(timespec="seconds")


def minutes(d: dt.timedelta) -> float:
    """시간 간격을 분으로 바꾼다.

    Args:
        d: 시간 간격.

    Returns:
        분.
    """
    return d.total_seconds() / 60


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


def _strip_mark(body: str) -> str:
    """agent comment 본문에서 첫 줄의 표식([ai-doc-agent])을 뗀다."""
    text = body.lstrip()
    if text.startswith(JAPI.AGENT_MARK):
        text = text[len(JAPI.AGENT_MARK) :].lstrip()
    return text


def task_record(key: str, issue_type: str, changelog: list[dict], comments: list[dict]) -> TaskRecord:
    """task 하나의 changelog와 comment를 집계에 필요한 기록으로 바꾼다.

    agent comment(scripts/jira_api.py의 is_agent_comment) 중 판정 줄이 있으면 검수 comment로, 실패 알림 문구로
    시작하면 실패 알림 comment로 센다. 문서화 리뷰 comment와 누락 알림 comment는 어디에도 세지 않는다.
    사람 comment 중 본문이 "정정:"으로 시작하는 것은 "정정:" comment로 센다.

    Args:
        key: task 키.
        issue_type: work type(Task, Bug, Issue).
        changelog: Jira.changelog의 결과.
        comments: Jira.comments의 결과.

    Returns:
        task 기록.
    """
    rec = TaskRecord(key, issue_type, status_changes(changelog))
    for c in comments:
        at = parse_ts(c.get("created"))
        if at is None:
            continue
        body = c.get("body") or ""
        if not JAPI.is_agent_comment(c):
            if body.lstrip().startswith(CORRECTION_PREFIX):
                rec.corrections.append(at)
            continue
        text = _strip_mark(body)
        if text.startswith(FAILURE_PREFIX):
            rec.failures.append(at)
        elif parsed := parse_review(text):
            hold = parsed[0] == JR.VERDICT_KO["fix"] and JR.AUTO_HOLD_NOTE in text
            rec.reviews.append(Review(key, at, *parsed, auto_hold=hold))
    rec.reviews.sort(key=lambda r: r.at)
    rec.failures.sort()
    rec.corrections.sort()
    return rec


def collect(jira: JiraReader, jql: str) -> list[TaskRecord]:
    """JQL로 찾은 task마다 changelog와 comment를 읽어 기록을 만든다. Jira 읽기 메서드만 호출한다.

    Args:
        jira: Jira 읽기 객체(scripts/jira_api.py의 Jira 또는 테스트의 가짜 객체).
        jql: 검색 조건.

    Returns:
        task 기록 목록.
    """
    records: list[TaskRecord] = []
    for issue in jira.search(jql, SEARCH_FIELDS, limit=SEARCH_LIMIT):
        key = issue["key"]
        issue_type = ((issue.get("fields") or {}).get("issuetype") or {}).get("name") or ""
        records.append(task_record(key, issue_type, jira.changelog(key), jira.comments(key)))
    return records


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


def analyze_task(
    rec: TaskRecord, start: dt.datetime | None, end: dt.datetime | None, lead: str = ""
) -> TaskResult | None:
    """task 하나의 지표 값을 계산한다. 기간 안에 request 전환이 없으면 집계 대상이 아니다.

    검수 comment와 실패 알림 comment는 기간 안 첫 request 전환 뒤에 달린 것만 센다.
    피드백 대기 시간은 기간 안 request 전환마다, 그 뒤이면서 다음 request 전환 전인 첫 검수 comment까지 잰다.
    그런 검수 comment가 없는 request 전환은 None으로 둔다.
    ready-to-done 체류 시간은 기간 안 request 전환마다 다음 status 변경까지 재고, 다음 변경이 없으면 뺀다.
    판정 일치율 결과 값은 scripts/report.py의 judge로 task의 모든 검수 comment에 대해 정한 뒤 집계 대상 검수
    comment의 값만 남긴다. 다음 검수 comment가 기간 밖이어도 그 전까지의 전환을 보기 위함이다.

    Args:
        rec: task 기록.
        start: 기간 시작 시각.
        end: 기간 끝 시각(포함하지 않음).
        lead: 팀장 Jira accountId. 이 계정의 전환만 판정 일치율에 센다.

    Returns:
        계산 결과. 집계 대상이 아니면 None.
    """
    all_requests = [c.at for c in rec.changes if is_status(c.dst, JR.READY_STATUS)]
    requests = [t for t in all_requests if in_period(t, start, end)]
    if not requests:
        return None
    reviews = [r for r in rec.reviews if r.at >= requests[0]]
    feedback: list[float | None] = []
    for req in requests:
        nxt = next((t for t in all_requests if t > req), None)
        hit = next((r for r in reviews if r.at >= req and (nxt is None or r.at < nxt)), None)
        feedback.append(minutes(hit.at - req) if hit else None)
    stays: list[float] = []
    for i, c in enumerate(rec.changes):
        if is_status(c.dst, JR.READY_STATUS) and in_period(c.at, start, end) and i + 1 < len(rec.changes):
            stays.append(minutes(rec.changes[i + 1].at - c.at) / 60)
    after = next((c.dst for c in rec.changes if c.at > reviews[-1].at), "") if reviews else ""
    outcomes = JR.judge(rec.reviews, rec.changes, lead)  # rec.reviews는 시각 순이라 reviews는 그 끝부분이다
    return TaskResult(
        key=rec.key,
        issue_type=rec.issue_type,
        requests=requests,
        reviews=reviews,
        failures=[t for t in rec.failures if t >= requests[0]],
        corrections=len(rec.corrections),
        feedback_minutes=feedback,
        ready_hours=stays,
        after_last_verdict=after,
        agreement=outcomes[len(rec.reviews) - len(reviews) :],
    )


def review_row(r: Review) -> dict[str, str]:
    """reviews.csv의 한 행.

    Args:
        r: 검수 comment.

    Returns:
        REVIEW_COLUMNS를 키로 하는 dict.
    """
    return {
        "key": r.key,
        "created_at": iso(r.at),
        "verdict": r.verdict,
        "fail_ids": ID_SEP.join(r.ids("fail")),
        "pass_ids": ID_SEP.join(r.ids("pass")),
        "na_ids": ID_SEP.join(r.ids("n/a")),
    }


def _fmt(x: float) -> str:
    """summary.md에 적는 숫자. 소수 첫째 자리까지."""
    return f"{x:.1f}".removesuffix(".0")


def _rate(a: int, b: int) -> str:
    """비율 문구. 예: 50.0% (2/4)."""
    return f"{a / b * 100:.1f}% ({a}/{b})" if b else "계산할 표본이 없음 (0/0)"


def _spread(values: list[float], unit: str) -> str:
    """중앙값, 범위(최소–최대), 표본 수 문구."""
    if not values:
        return "표본이 없음(n=0)"
    med, lo, hi = statistics.median(values), min(values), max(values)
    return f"중앙값 {_fmt(med)}{unit}, 범위 {_fmt(lo)}–{_fmt(hi)}{unit}, n={len(values)}"


def _j1(results: list[TaskResult]) -> list[str]:
    """J1 리뷰 범위."""
    covered = sum(1 for r in results if r.reviews)
    return [
        "## J1 리뷰 범위",
        "",
        f"- 리뷰 범위는 {_rate(covered, len(results))}임",
        "  - 계산 방법: 기간 안에 request 전환이 있는 task(분모) 중 기간 안 첫 request 전환 뒤에 검수 comment가 "
        "하나 이상 달린 task(분자)의 비율임",
        "",
    ]


def _j3(results: list[TaskResult]) -> list[str]:
    """J3 발견·조치."""
    reviewed = [r for r in results if r.reviews]
    single = [r for r in reviewed if len(r.reviews) == 1]
    found = sum(len(r.first_fail_ids()) for r in reviewed)
    resolved = sum(len(r.resolved_fail_ids()) for r in reviewed)
    fail, ok, na = JA.RESULT_KO["fail"], JA.RESULT_KO["pass"], JA.RESULT_KO["n/a"]
    return [
        "## J3 발견·조치",
        "",
        f"- 첫 검수 comment의 {fail} 검사 수(task당)는 {_spread([len(r.first_fail_ids()) for r in reviewed], '개')}임",
        "  - 대상은 검수 comment가 하나 이상인 task임",
        f"- 조치율은 {_rate(resolved, found)}임",
        f"  - 계산 방법: 첫 검수 comment에서 {fail}였던 검사 수(분모) 중 마지막 검수 comment에서 {ok}인 검사 수"
        "(분자)의 비율임",
        f"  - 검수 comment가 하나뿐인 task {len(single)}개는 첫 검수 comment와 마지막 검수 comment가 같아 분자에 "
        f"들어가지 않고, 그 task의 {fail} 검사 {sum(len(r.first_fail_ids()) for r in single)}개는 분모에 들어감",
        f"  - 마지막 검수 comment에서 {na}이거나 행이 없는 검사는 분자에 넣지 않음",
        "",
    ]


def _j4(results: list[TaskResult]) -> list[str]:
    """J4 피드백 대기 시간."""
    feedback = [x for r in results for x in r.feedback_minutes if x is not None]
    return [
        "## J4 피드백 대기 시간",
        "",
        f"- request 전환부터 검수 comment까지는 {_spread(feedback, '분')}임",
        "  - 계산 방법: 기간 안 request 전환마다 그 뒤이면서 다음 request 전환 전인 첫 검수 comment까지 잰 값임",
        "  - 다음 request 전환 전까지 검수 comment가 없는 request 전환은 뺌",
        "",
    ]


def _j5(results: list[TaskResult]) -> list[str]:
    """J5 문서 품질 추이."""
    firsts = [r.reviews[0] for r in results if r.reviews]
    weeks: dict[str, list[Review]] = {}
    for rv in firsts:
        year, week, _ = rv.at.astimezone(KST).isocalendar()
        weeks.setdefault(f"{year}-W{week:02d}", []).append(rv)
    ok, fail, na = JA.RESULT_KO["pass"], JA.RESULT_KO["fail"], JA.RESULT_KO["n/a"]
    lines = [
        "## J5 문서 품질 추이",
        "",
        "- ISO 주별 첫 검수 통과율은 아래 표와 같음",
        f"  - 계산 방법: task별 첫 검수 comment를 작성 시각(한국 시간)의 ISO 주로 묶고, 그중 검수 결과가 {PASS_VERDICT}인 "
        "비율임",
        "",
        "| ISO 주 | 첫 검수 통과율(통과 수/첫 검수 comment 수) |",
        "|---|---|",
    ]
    for week, rvs in sorted(weeks.items()):
        lines.append(f"| {week} | {_rate(sum(1 for rv in rvs if rv.verdict == PASS_VERDICT), len(rvs))} |")
    lines += [
        "",
        "- 검사별 첫 검수 만족률은 아래 표와 같음",
        f"  - 계산 방법: task별 첫 검수 comment의 검사 표에서 {ok} 행 수 / ({ok} 행 수 + {fail} 행 수)임. {na}은 뺌",
        "",
        "| 검사 | 첫 검수 만족률(만족 수/만족과 보완 필요 수) |",
        "|---|---|",
    ]
    for cid in sort_ids({cid for rv in firsts for cid in rv.results}):
        judged = [rv.results[cid] for rv in firsts if rv.results.get(cid) in ("pass", "fail")]
        if judged:
            lines.append(f"| {JA.check_label(cid)} | {_rate(judged.count('pass'), len(judged))} |")
    return lines + [""]


def _j6(results: list[TaskResult]) -> list[str]:
    """J6 진행 추적."""
    r4 = [rv.results["R4"] for t in results for rv in t.reviews if rv.results.get("R4") in ("pass", "fail")]
    stays = [x for r in results for x in r.ready_hours]
    ok, na = JA.RESULT_KO["pass"], JA.RESULT_KO["n/a"]
    return [
        "## J6 진행 추적",
        "",
        f"- ready-to-done 상태에 머문 시간은 {_spread(stays, '시간')}임",
        "  - 계산 방법: 기간 안 request 전환으로 ready-to-done에 들어간 시각부터 다음 status 변경까지 잰 값임",
        "  - 아직 ready-to-done에 머무는 경우는 뺌",
        f"- {JA.check_label('R4')} 만족 비율은 {_rate(r4.count('pass'), len(r4))}임",
        f"  - 계산 방법: 검수 comment의 R4 행 중 {na}을 뺀 행 수(분모) 중 {ok} 행 수(분자)의 비율임",
        f"- {JA.check_label('A1')}은 이 스크립트에서 세지 않음",
        "  - A1은 주간 점검 메시지(Slack, Actions Summary 탭)에서 확인함",
        "",
    ]


def _j7(results: list[TaskResult], lead: str) -> list[str]:
    """J7 판정 일치율. 정의는 scripts/report.py의 judge(Agent 판정과 팀장 전환 비교)와 같다."""
    counts = Counter(x for r in results for x in r.agreement)
    match, mismatch, escalate = counts[JR.MATCH], counts[JR.MISMATCH], counts[JR.ESCALATE]
    excluded = ", ".join(f"{k} {counts[k]}건" for k in (JR.AUTO_HOLD, JR.NOT_LEAD, JR.WAITING, JR.OTHER))
    lines = [
        "## J7 판정 일치율",
        "",
        f"- 판정 일치율은 {_rate(match, match + mismatch)}임",
        "  - 계산 방법: 검수 comment 뒤, 다음 검수 comment 전까지 ticket이 ready-to-done에서 나간 첫 전환을 봄",
        "  - 그 전환이 팀장 계정의 전환일 때만 셈",
        f"  - 일치: {PASS_VERDICT}→done, {JR.VERDICT_KO['fix']}→In Progress",
        f"  - 불일치: {PASS_VERDICT}→In Progress, {JR.VERDICT_KO['fix']}→done",
        f"  - 일치율에서 뺀 검수 comment: {JR.ESCALATE} {escalate}건, {excluded}",
        f"- 검토 요청 비율은 {_rate(escalate, sum(counts.values()))}임",
        "  - 계산 방법: 집계 대상 검수 comment 중 검수 결과가 검토 요청인 비율임",
    ]
    if not lead:
        lines.append("- `--lead`가 없어 모든 전환을 팀장 외 전환으로 셈")
    return lines + [""]


def _j8(results: list[TaskResult]) -> list[str]:
    """J8 오류 신호."""
    corrected = sum(1 for r in results if r.corrections)
    reviews = sum(len(r.reviews) for r in results)
    failures = sum(len(r.failures) for r in results)
    return [
        "## J8 오류 신호",
        "",
        f'- "{CORRECTION_PREFIX}" comment가 달린 task 비율은 {_rate(corrected, len(results))}임',
        f'  - 계산 방법: 집계 대상 task 중 사람 comment의 본문이 "{CORRECTION_PREFIX}"으로 시작하는 comment가 하나 이상인 '
        "task의 비율임. 작성 시각은 제한하지 않음",
        f"- 실패 알림 comment 비율은 {_rate(failures, reviews + failures)}임",
        f"  - 계산 방법: 검수 실행(검수 comment {reviews}건 + 실패 알림 comment {failures}건) 중 실패 알림 comment의 "
        "비율임",
        "",
    ]


def render_summary(
    results: list[TaskResult],
    searched: int,
    jql: str,
    since: dt.date | None,
    until: dt.date | None,
    lead: str = "",
) -> str:
    """summary.md 본문. 지표마다 무엇의 값인지, 표본 수, 계산 방법을 적고 개인별 수치는 넣지 않는다.

    Args:
        results: 집계 대상 task의 계산 결과.
        searched: JQL로 찾은 task 수.
        jql: 검색 조건.
        since: 기간 시작 날짜.
        until: 기간 끝 날짜.
        lead: 팀장 Jira accountId. J7에서 이 계정의 전환만 센다.

    Returns:
        markdown 본문.
    """
    period = f"{since.isoformat() if since else '제한 없음'}–{until.isoformat() if until else '제한 없음'}"
    lines = [
        "# 문서화 Agent(jira-doc) 지표 집계",
        "",
        f"- 집계 대상은 검색 조건(JQL) `{jql}`로 찾은 task {searched}개 중 기간 안에 request 전환이 있는 task "
        f"{len(results)}개임",
        "  - request 전환: 담당자가 완료를 요청해 task를 ready-to-done 상태로 보내는 Jira 전환",
        f"  - 기간: {period}(한국 시간 날짜, 양 끝 날짜 포함)",
        f"- 검수 comment는 `{JAPI.AGENT_MARK}` 표식이 있는 comment 중 `{VERDICT_PREFIX}…{VERDICT_SUFFIX}` 줄이 있는 "
        "comment임",
        "  - 문서화 리뷰 comment와 실패 알림 comment는 검수 comment로 세지 않음",
        "  - 검수 comment와 실패 알림 comment는 task별 기간 안 첫 request 전환 뒤에 달린 것만 셈",
        "- 수치는 팀 합계이고 개인별 수치는 넣지 않음",
        "  - 표본 수(n), 중앙값, 범위(최소–최대)를 적음",
    ]
    if searched >= SEARCH_LIMIT:
        lines.append(f"- 검색 결과가 상한 {SEARCH_LIMIT}개에 닿아 빠진 task가 있을 수 있음")
    lines.append("")
    for part in (_j1(results), _j3(results), _j4(results), _j5(results), _j6(results)):
        lines += part
    lines += _j7(results, lead) + _j8(results)
    return "\n".join(lines).rstrip() + "\n"


def build_jql(jql: str, since: dt.date | None) -> str:
    """검색 조건에 updated >= (since 전날) 조건을 붙인다. ORDER BY 절은 끝에 그대로 둔다.

    JQL의 날짜는 API 계정의 시간대로 해석되므로, 한국 시간 since 첫날 이른 시각에 바뀐 task가 빠지지 않게 하루
    앞선 날로 넓게 찾는다. 기간은 analyze_task에서 한국 시간 경계(in_period)로 다시 거른다.

    Args:
        jql: 실행할 때 준 검색 조건.
        since: 기간 시작 날짜. 없으면 jql을 그대로 돌려준다.

    Returns:
        검색 조건.
    """
    if since is None:
        return jql
    m = re.search(r"\s+order\s+by\s+", jql, re.I)
    where, order = (jql[: m.start()], jql[m.start() :]) if m else (jql, "")
    day = since - dt.timedelta(days=1)
    return f'({where.strip()}) AND updated >= "{day.isoformat()}"{order}'


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


def run(jira: JiraReader, args: argparse.Namespace) -> list[TaskResult]:
    """Jira 기록을 읽어 지표를 계산하고 tasks.csv, reviews.csv, summary.md를 쓴다.

    Args:
        jira: Jira 읽기 객체.
        args: parse_args의 결과.

    Returns:
        집계 대상 task의 계산 결과.
    """
    start, end = period_bounds(args.since, args.until)
    jql = build_jql(args.jql, args.since)
    records = collect(jira, jql)
    if len(records) >= SEARCH_LIMIT:
        print(f"[jira_metrics] 검색 결과가 상한 {SEARCH_LIMIT}개에 닿음", file=sys.stderr)
    results = [r for r in (analyze_task(rec, start, end, args.lead) for rec in records) if r]
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "tasks.csv", TASK_COLUMNS, [r.row() for r in results])
    write_csv(out / "reviews.csv", REVIEW_COLUMNS, [review_row(rv) for r in results for rv in r.reviews])
    summary = render_summary(results, len(records), jql, args.since, args.until, args.lead)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print(f"[jira_metrics] task {len(results)}개 집계: {out}/tasks.csv, reviews.csv, summary.md")
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """명령줄 인자를 읽는다.

    Args:
        argv: 인자 목록. 없으면 sys.argv를 읽는다.

    Returns:
        인자 값.
    """
    ap = argparse.ArgumentParser(description="문서화 Agent(jira-doc)의 지표(J1, J3–J8)를 Jira 기록에서 집계한다.")
    ap.add_argument("--jql", default="project = INNO", help="대상 task 검색 조건(JQL)")
    ap.add_argument("--since", type=dt.date.fromisoformat, help="기간 시작 날짜 YYYY-MM-DD(한국 시간)")
    ap.add_argument("--until", type=dt.date.fromisoformat, help="기간 끝 날짜 YYYY-MM-DD(한국 시간, 그날 포함)")
    ap.add_argument("--out", type=Path, required=True, help="출력 폴더")
    ap.add_argument(
        "--lead",
        default=os.environ.get("JIRA_LEAD_ACCOUNT_ID", ""),
        help="팀장 Jira accountId. J7 판정 일치율에서 이 계정의 전환만 센다(기본값: 환경 변수 JIRA_LEAD_ACCOUNT_ID)",
    )
    return ap.parse_args(argv)


def main() -> None:
    """Jira 환경 변수로 읽기 객체를 만들고 집계를 실행한다."""
    args = parse_args()
    try:
        jira = JAPI.Jira()
    except KeyError as e:
        sys.exit(f"[jira_metrics] 환경 변수 {e.args[0]} 필요 (JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN)")
    run(jira, args)


if __name__ == "__main__":
    main()
