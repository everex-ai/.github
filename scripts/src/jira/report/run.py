"""정기 보고 진입 함수 run_report. Jira와 PR을 읽어 Report를 만들고 Slack 메시지 목록을 돌려준다."""

from __future__ import annotations

import datetime as dt
import os
import sys
from collections.abc import Callable

from src.jira.report import fetch
from src.jira.report.fetch import PR_LIMIT, attach_updates, build_jql, collect, fetch_subtasks, pr_times
from src.jira.report.message import Report
from src.jira.report.model import JiraReader, SummaryLike
from src.jira.report.period import add_months, months_span, quarter_first


def run_report(
    j: JiraReader,
    month: str,
    lead: str,
    summ: SummaryLike,
    pr_search: Callable[[str, dt.date, dt.date], list[dict] | None] | None = None,
) -> list[str]:
    """정기 보고 메시지를 계산해 묶음별 Slack 메시지를 만들고 Summary 탭에 팀 합계를 남긴다.

    읽는 기간은 전월 첫날부터이고, 분기 묶음이 붙는 달(3, 6, 9, 12월)은 전 분기 첫날부터, 연간 묶음이 붙는 달(12월)은
    전년 1월 1일부터다. 분기, 연간 묶음의 Claude 활용 요약 절이 직전 기간(전 분기, 전년)의 사용량을 읽기 때문이다.
    프로젝트 키는 환경 변수 JIRA_PROJECT_KEY, GitHub organization은 GH_ORG에서 읽는다.
    업데이트 판정은 월간 묶음에서만 쓰므로 sub-task와 PR은 대상 달에 만들어진 것만 한 번씩 검색한다.
    PR 검색 결과가 PR_LIMIT건에 닿으면 월간 메시지에 그 사실을 적는다.
    PR 검색 기간은 build_jql처럼 하루 앞선 날부터이고, 기간 판정은 시각으로 다시 한다.

    Args:
        j: Jira 읽기 객체(scripts/src/jira/client.py의 Jira).
        month: 보고 대상 달(YYYY-MM).
        lead: 팀장 Jira accountId.
        summ: Summary 탭 기록 객체(scripts/src/common/github.py의 Summary). extra에 팀 합계 줄을 추가한다.
        pr_search: PR 검색 함수(search_prs와 같은 인자와 반환값). None이면 search_prs를 쓴다.

    Returns:
        Slack 메시지 목록(mrkdwn). 월간, 분기, 연간 묶음 순이다(Report.messages).
    """
    project = os.environ.get("JIRA_PROJECT_KEY", "")
    if not project:
        sys.exit("report 모드에는 JIRA_PROJECT_KEY가 필요하다")
    first = add_months(month, -1)
    if int(month[5:]) % 3 == 0:
        first = min(first, add_months(quarter_first(month), -3))
    if month.endswith("-12"):
        first = f"{int(month[:4]) - 1:04d}-01"
    jql = build_jql(project, months_span(first, first)[0].date())
    tickets = collect(j, jql)
    cur = months_span(month, month)
    since, until = cur[0].date(), (cur[1] - dt.timedelta(days=1)).date()
    prs = (pr_search or fetch.search_prs)(os.environ.get("GH_ORG", ""), since - dt.timedelta(days=1), until)
    attach_updates(tickets, fetch_subtasks(j, project, since, cur[1].date()), pr_times(prs or [], project))
    report = Report(tickets, month, lead, pr_ok=prs is not None, pr_capped=prs is not None and len(prs) >= PR_LIMIT)
    summ.extra += report.summary_lines()
    return report.messages()
