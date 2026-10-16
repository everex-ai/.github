"""정기 보고 하나의 계산 결과와 묶음별 Slack 메시지를 만드는 Report 클래스.

실행 시각 기본값(`dt.datetime.now`)을 부르는 곳은 이 모듈의 Report.__init__뿐이라, 테스트가 이 모듈의 dt만 바꾸면 시각이 고정된다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable

from src.constant.time import KST
from src.jira.report import fetch
from src.jira.report.fetch import PR_LIMIT
from src.jira.report.metrics import (
    RECENT_N,
    _rank,
    _status_counts,
    agreement_cases,
    agreement_summary,
    candidates,
    check_satisfaction,
    fail_counts,
    feedback_minutes,
    first_reviews,
    followup,
    kr_rows,
    lead_confirm_minutes,
    lead_wait,
    max_request_count,
    member_rows,
    pass_count,
    r4_count,
    ticket_overview,
    usage,
    usage_judgement,
)
from src.jira.report.model import BRANCHES, TOKEN_KO, Candidate, Span, Ticket
from src.jira.report.period import add_months, months_span, quarter_first, span_text
from src.jira.report.text import (
    _capped_wrap,
    _count,
    _esc,
    _fit_members,
    _frac,
    _median,
    _name,
    _ratio,
    _seconds,
    _sub,
    _times,
    _usd,
    top_ticket_lines,
)

TOP_QUARTER = 5  # 분기 묶음의 검사 ID별 ❌ 상위 개수
TOP_YEAR = 3  # 연간 묶음에서 분기별 만족률을 적는 연간 ❌ 상위 검사 ID 수
MAX_LINES = 40  # 메시지 하나의 줄 수 상한. 넘으면 팀원별 줄을 "외 n명"으로 줄인다
KR_MAX = 5  # 분기 묶음에 적는 KR 행 수 상한. 넘으면 "외 n개"로 줄인다
CANDIDATE_IDS = 2  # 후보 표시에 적는 조건 B 검사 ID 수. 넘으면 "외 n"으로 줄인다
DEF_LINE = "지표 정의: docs/jira-doc-agent/alerts.md"  # 메시지 끝 줄
NEW_STATUS_LINES = 2  # 새 ticket, 업데이트 없음의 상태별 수 줄 수 상한. 넘으면 뒤 상태를 "외 n종"으로 줄인다


class Report:
    """정기 보고 하나의 계산 결과와 묶음별 메시지 조립.

    메시지마다 제목, 대상 기간, 팀장 업무, 팀 전체, 팀원별, Claude 활용 요약 절, 지표 정의 줄 순서로 적는다.
    메시지는 MAX_LINES줄 이내다. 넘으면 팀원별 줄을 줄인다.
    자동 검수 소요 시간(feedback_minutes)은 Claude 활용 요약 절의 첫 줄에 적는다.
    팀장 확인 대기는 대상 기간 끝과 실행 시각(now, 없으면 지금 시각) 중 이른 시각까지 센다.
    """

    def __init__(
        self,
        tickets: list[Ticket],
        month: str,
        lead: str,
        pr_ok: bool = True,
        pr_capped: bool = False,
        now: dt.datetime | None = None,
    ):
        self.tickets, self.month, self.lead, self.pr_ok, self.pr_capped = tickets, month, lead, pr_ok, pr_capped
        self.now = now or dt.datetime.now(KST)
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
        메시지가 MAX_LINES줄을 넘지 않는다. 월간 묶음의 최악 경우 팀원별 절 밖의 줄이 35줄이라 팀원별 절에 5줄이
        남는다(절 제목, 순서 줄, 담당자 한 명의 두 줄, "외 n명" 줄).
        35줄: 제목과 대상 기간 2줄, 팀장 업무 5줄, 팀 전체 13줄(업데이트 없음 2줄, PR 검색 1줄, 새 ticket 하위 2줄,
        검색 상한 1줄 포함), Claude 활용 요약 14줄(비용 기록 없음 1줄, Top3 7줄 포함), 지표 정의 1줄.

        Args:
            title: 제목 줄.
            sp: 대상 기간.
            sections: 팀장 업무, 팀 전체, Claude 활용 요약 절의 줄.
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
        idle_head = f"업데이트 없음 {ov.idle_total}개"
        idle = _capped_wrap(_status_counts(ov.idle), NEW_STATUS_LINES, f"{idle_head}: ") or [idle_head]
        lines = [
            f"• 기존 ticket {ov.existing}개",
            _sub(f"업데이트 {ov.updated}개(그중 done 전환 {ov.updated_done}개)"),
            *(_sub(x) for x in idle),
        ]
        if not self.pr_ok:
            lines.append("• PR 검색 실패: PR 추가는 업데이트에서 뺌")
        if self.pr_capped:
            lines.append(f"• PR 검색이 상한 {PR_LIMIT}건에 닿음")
        lines.append(f"• 새 ticket {ov.new_total}개")
        lines += [_sub(x) for x in _capped_wrap(_status_counts(ov.new), NEW_STATUS_LINES)]
        if len(self.tickets) >= fetch.SEARCH_LIMIT:
            lines.append(f"• 검색 결과 상한 {fetch.SEARCH_LIMIT}개에 닿음: 빠진 ticket 있을 수 있음")
        return lines

    def claude_lines(
        self, sp: Span, prev: Span, prev_name: str, monthly: bool = False, cost_sub: Iterable[str] = ()
    ) -> list[str]:
        """Claude 활용 요약 절. 월간, 분기, 연간 묶음이 함께 쓴다.

        자동 검수 소요 시간, API 환산 금액, 토큰, Claude 사용 ticket Top3 순으로 적는다.
        API 환산 금액의 증감은 (대상 기간 - 직전 기간) / 직전 기간 × 100을 정수로 반올림한 값이다.
        직전 기간 API 환산 비용이 0이면 "직전 기록 없음"으로 적는다.

        Args:
            sp: 대상 기간.
            prev: 직전 기간.
            prev_name: 직전 기간 이름(전월, 전 분기, 전년).
            monthly: 월간 묶음이면 True. 사용량 많음(self.high)이 True면 API 환산 금액 줄에 표시를 붙인다.
            cost_sub: API 환산 금액 줄 아래에 둘 하위 줄(연간 묶음의 분기별 줄).

        Returns:
            Slack 본문 줄.
        """
        cur, before = usage(self.tickets, sp), usage(self.tickets, prev)
        if round(before.total_cost, 6) <= 0:
            notes = ["직전 기록 없음"]
        else:
            notes = [f"{prev_name} 대비 {round((cur.total_cost - before.total_cost) / before.total_cost * 100):+d}%"]
        if monthly and self.high[0] is True:
            notes.append("사용량 많음")
        lines = [
            "*Claude 활용 요약*",
            f"• 자동 검수 소요 시간: {_seconds(feedback_minutes(self.tickets, sp))}",
            f"• API 환산 금액: {_usd(cur.total_cost)}({', '.join(notes)})",
        ]
        if cur.missing_cost:
            lines.append(_sub(f"비용 기록 없는 실행 {cur.missing_cost}건은 0 USD로 셈"))
        lines += cost_sub
        tk = cur.tokens
        lines += [
            f"• 토큰: input {_count(tk['input'])}, output {_count(tk['output'])}",
            _sub(f"cache 쓰기(지시·기록 저장) {_count(tk['cacheWrite'])}"),
            _sub(f"cache 읽기(저장분 재사용) {_count(tk['cacheRead'])}"),
            *top_ticket_lines(self.tickets, sp),
        ]
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
        wait_n, wait_days = lead_wait(self.tickets, min(sp[1], self.now))
        lead = [
            "*팀장 업무*",
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
        use = self.claude_lines(sp, self.prev, "전월", monthly=True)
        return self.assemble(title, sp, [lead, team, use], head, groups)

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
        first = quarter_first(self.month)
        use = self.claude_lines(sp, months_span(add_months(first, -3), add_months(first, -1)), "전 분기")
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

        prev_year = f"{int(self.year) - 1:04d}"
        prev = months_span(f"{prev_year}-01", f"{prev_year}-12")
        use = self.claude_lines(sp, prev, "전년", cost_sub=[_sub(f"분기별: {per_quarter(cost)}")])
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
        wait_n, wait_days = lead_wait(self.tickets, min(sp[1], self.now))
        idle = f"({', '.join(_status_counts(ov.idle))})" if ov.idle else ""
        lines = [
            f"### jira-doc 정기 보고 {self.month} (팀 합계)",
            "",
            f"- Claude 사용량: 실행 {cur.total_runs}건, API 환산 비용 {_usd(cur.total_cost)}",
            f"- 사용량 많음 판정: {self.high[1]}({'; '.join(self.high[2])})",
            *(f"- {ko}: 실행 {cur.runs[b]}건, {_usd(cur.cost[b])}" for b, ko in BRANCHES.items()),
            "- Claude 토큰: " + ", ".join(f"{ko} {cur.tokens[k]:,}" for k, ko in TOKEN_KO.items()),
            f"- 기존 ticket: {ov.existing}개(업데이트 {ov.updated}개, 그중 done 전환 {ov.updated_done}개)",
            f"- 업데이트 없는 기존 ticket: {ov.idle_total}개{idle}",
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
            f"- 자동 검수 소요 시간: {_seconds(fb)}",
            f"- 판정 일치율: {_ratio(a['match'], a['judged'])}",
            f"- 팀장 확인 대기: {wait_n}건(최장 {wait_days}영업일)",
            f"- 팀장 확인 시간: {_median(lead_confirm_minutes(self.tickets, sp, self.lead))}",
        ]
        if int(self.month[5:]) % 3 == 0:
            q = agreement_summary(self.cases, self.quarter)
            lines.append(f"- 분기 판정 일치율: {_ratio(q['match'], q['judged'])}")
        lines += ["- 담당자별 수치와 구두 피드백 대상 후보는 팀장 채널 메시지에만 적음", ""]
        return lines
