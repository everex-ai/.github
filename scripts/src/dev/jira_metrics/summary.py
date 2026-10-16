"""jira_metrics의 summary.md 본문 조립. 지표(J1, J3–J8)마다 절 하나를 만든다."""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter

from src.common.text import fmt, rate
from src.constant import check as check_const
from src.constant import jira as jira_const
from src.constant.jira import SEARCH_LIMIT
from src.constant.time import KST
from src.dev.jira_metrics.analyze import CORRECTION_PREFIX
from src.dev.jira_metrics.model import Review, TaskResult, sort_ids
from src.jira import verdict as jira_verdict
from src.jira.report import metrics as report_metrics
from src.jira.report import parse as report_parse

VERDICT_PREFIX, VERDICT_SUFFIX = report_parse.VERDICT_PREFIX, report_parse.VERDICT_SUFFIX
PASS_VERDICT = report_parse.VERDICT_KO["pass"]


def _spread(values: list[float], unit: str) -> str:
    """중앙값, 범위(최소–최대), 표본 수 문구."""
    if not values:
        return "표본이 없음(n=0)"
    med, lo, hi = statistics.median(values), min(values), max(values)
    return f"중앙값 {fmt(med)}{unit}, 범위 {fmt(lo)}–{fmt(hi)}{unit}, n={len(values)}"


def _j1(results: list[TaskResult]) -> list[str]:
    """J1 리뷰 범위."""
    covered = sum(1 for r in results if r.reviews)
    return [
        "## J1 리뷰 범위",
        "",
        f"- 리뷰 범위는 {rate(covered, len(results))}임",
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
    fail, ok, na = check_const.RESULT_KO["fail"], check_const.RESULT_KO["pass"], check_const.RESULT_KO["n/a"]
    return [
        "## J3 발견·조치",
        "",
        f"- 첫 검수 comment의 {fail} 검사 수(task당)는 {_spread([len(r.first_fail_ids()) for r in reviewed], '개')}임",
        "  - 대상은 검수 comment가 하나 이상인 task임",
        f"- 조치율은 {rate(resolved, found)}임",
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
    ok, fail, na = check_const.RESULT_KO["pass"], check_const.RESULT_KO["fail"], check_const.RESULT_KO["n/a"]
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
        lines.append(f"| {week} | {rate(sum(1 for rv in rvs if rv.verdict == PASS_VERDICT), len(rvs))} |")
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
            lines.append(f"| {jira_verdict.check_label(cid)} | {rate(judged.count('pass'), len(judged))} |")
    return lines + [""]


def _j6(results: list[TaskResult]) -> list[str]:
    """J6 진행 추적."""
    r4 = [rv.results["R4"] for t in results for rv in t.reviews if rv.results.get("R4") in ("pass", "fail")]
    stays = [x for r in results for x in r.ready_hours]
    ok, na = check_const.RESULT_KO["pass"], check_const.RESULT_KO["n/a"]
    return [
        "## J6 진행 추적",
        "",
        f"- ready-to-done 상태에 머문 시간은 {_spread(stays, '시간')}임",
        "  - 계산 방법: 기간 안 request 전환으로 ready-to-done에 들어간 시각부터 다음 status 변경까지 잰 값임",
        "  - 아직 ready-to-done에 머무는 경우는 뺌",
        f"- {jira_verdict.check_label('R4')} 만족 비율은 {rate(r4.count('pass'), len(r4))}임",
        f"  - 계산 방법: 검수 comment의 R4 행 중 {na}을 뺀 행 수(분모) 중 {ok} 행 수(분자)의 비율임",
        f"- {jira_verdict.check_label('A1')}은 이 스크립트에서 세지 않음",
        "  - A1은 주간 점검 메시지(Slack, Actions Summary 탭)에서 확인함",
        "",
    ]


def _j7(results: list[TaskResult], lead: str) -> list[str]:
    """J7 판정 일치율. 정의는 scripts/src/jira/report/metrics.py의 judge(Agent 판정과 팀장 전환 비교)와 같다."""
    counts = Counter(x for r in results for x in r.agreement)
    match, mismatch, escalate = (
        counts[report_metrics.MATCH],
        counts[report_metrics.MISMATCH],
        counts[report_metrics.ESCALATE],
    )
    excluded = ", ".join(
        f"{k} {counts[k]}건"
        for k in (report_metrics.AUTO_HOLD, report_metrics.NOT_LEAD, report_metrics.WAITING, report_metrics.OTHER)
    )
    lines = [
        "## J7 판정 일치율",
        "",
        f"- 판정 일치율은 {rate(match, match + mismatch)}임",
        "  - 계산 방법: 검수 comment 뒤, 다음 검수 comment 전까지 ticket이 ready-to-done에서 나간 첫 전환을 봄",
        "  - 그 전환이 팀장 계정의 전환일 때만 셈",
        f"  - 일치: {PASS_VERDICT}→done, {report_parse.VERDICT_KO['fix']}→In Progress",
        f"  - 불일치: {PASS_VERDICT}→In Progress, {report_parse.VERDICT_KO['fix']}→done",
        f"  - 일치율에서 뺀 검수 comment: {report_metrics.ESCALATE} {escalate}건, {excluded}",
        f"- 검토 요청 비율은 {rate(escalate, sum(counts.values()))}임",
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
        f'- "{CORRECTION_PREFIX}" comment가 달린 task 비율은 {rate(corrected, len(results))}임',
        f'  - 계산 방법: 집계 대상 task 중 사람 comment의 본문이 "{CORRECTION_PREFIX}"으로 시작하는 comment가 하나 이상인 '
        "task의 비율임. 작성 시각은 제한하지 않음",
        f"- 실패 알림 comment 비율은 {rate(failures, reviews + failures)}임",
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
        f"- 검수 comment는 `{jira_const.AGENT_MARK}` 표식이 있는 comment 중 `{VERDICT_PREFIX}…{VERDICT_SUFFIX}` 줄이 있는 "
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
