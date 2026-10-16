"""jira_metrics의 Jira 기록 읽기, task별 지표 계산, 검색 조건 조립."""

from __future__ import annotations

import datetime as dt
import re

from src.common.period import in_period
from src.constant import jira as jira_const
from src.constant.jira import SEARCH_LIMIT
from src.dev.jira_metrics.model import ID_SEP, JiraReader, Review, TaskRecord, TaskResult, iso
from src.jira import wiki as jira_wiki
from src.jira.report import fetch as report_fetch
from src.jira.report import metrics as report_metrics
from src.jira.report import parse as report_parse
from src.jira.report.parse import is_status, parse_review, parse_ts, status_changes

# 결과 산출물 구역과 TL;DR의 정정 요청 comment의 시작(scripts/src/constant/check.py의 SECTION_NOTE)
CORRECTION_PREFIX = "정정:"
SEARCH_FIELDS = ["issuetype"]
FAILURE_PREFIX = "자동 검수 실행 실패:"  # 구조화 출력 전환 전 apply.py가 Jira에 남기던 실패 알림 comment의 첫 문구


def minutes(d: dt.timedelta) -> float:
    """시간 간격을 분으로 바꾼다.

    Args:
        d: 시간 간격.

    Returns:
        분.
    """
    return d.total_seconds() / 60


def _strip_mark(body: str) -> str:
    """agent comment 본문에서 첫 줄의 표식([ai-doc-agent])을 뗀다."""
    text = body.lstrip()
    if text.startswith(jira_const.AGENT_MARK):
        text = text[len(jira_const.AGENT_MARK) :].lstrip()
    return text


def task_record(key: str, issue_type: str, changelog: list[dict], comments: list[dict]) -> TaskRecord:
    """task 하나의 changelog와 comment를 집계에 필요한 기록으로 바꾼다.

    agent comment(scripts/src/jira/wiki.py의 is_agent_comment) 중 판정 줄이 있으면 검수 comment로, 실패 알림 문구로
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
        if not jira_wiki.is_agent_comment(c):
            if body.lstrip().startswith(CORRECTION_PREFIX):
                rec.corrections.append(at)
            continue
        text = _strip_mark(body)
        if text.startswith(FAILURE_PREFIX):
            rec.failures.append(at)
        elif parsed := parse_review(text):
            hold = parsed[0] == report_parse.VERDICT_KO["fix"] and report_fetch.AUTO_HOLD_NOTE in text
            rec.reviews.append(Review(key, at, *parsed, auto_hold=hold))
    rec.reviews.sort(key=lambda r: r.at)
    rec.failures.sort()
    rec.corrections.sort()
    return rec


def collect(jira: JiraReader, jql: str) -> list[TaskRecord]:
    """JQL로 찾은 task마다 changelog와 comment를 읽어 기록을 만든다. Jira 읽기 메서드만 호출한다.

    Args:
        jira: Jira 읽기 객체(scripts/src/jira/client.py의 Jira 또는 테스트의 가짜 객체).
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


def analyze_task(
    rec: TaskRecord, start: dt.datetime | None, end: dt.datetime | None, lead: str = ""
) -> TaskResult | None:
    """task 하나의 지표 값을 계산한다. 기간 안에 request 전환이 없으면 집계 대상이 아니다.

    검수 comment와 실패 알림 comment는 기간 안 첫 request 전환 뒤에 달린 것만 센다.
    피드백 대기 시간은 기간 안 request 전환마다, 그 뒤이면서 다음 request 전환 전인 첫 검수 comment까지 잰다.
    그런 검수 comment가 없는 request 전환은 None으로 둔다.
    ready-to-done 체류 시간은 기간 안 request 전환마다 다음 status 변경까지 재고, 다음 변경이 없으면 뺀다.
    판정 일치율 결과 값은 scripts/src/jira/report/metrics.py의 judge로 task의 모든 검수 comment에 대해 정한 뒤 집계 대상 검수
    comment의 값만 남긴다. 다음 검수 comment가 기간 밖이어도 그 전까지의 전환을 보기 위함이다.

    Args:
        rec: task 기록.
        start: 기간 시작 시각.
        end: 기간 끝 시각(포함하지 않음).
        lead: 팀장 Jira accountId. 이 계정의 전환만 판정 일치율에 센다.

    Returns:
        계산 결과. 집계 대상이 아니면 None.
    """
    all_requests = [c.at for c in rec.changes if is_status(c.dst, jira_const.READY_STATUS)]
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
        if is_status(c.dst, jira_const.READY_STATUS) and in_period(c.at, start, end) and i + 1 < len(rec.changes):
            stays.append(minutes(rec.changes[i + 1].at - c.at) / 60)
    after = next((c.dst for c in rec.changes if c.at > reviews[-1].at), "") if reviews else ""
    outcomes = report_metrics.judge(rec.reviews, rec.changes, lead)  # rec.reviews는 시각 순이라 reviews는 그 끝부분이다
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
