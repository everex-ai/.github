"""정기 보고의 지표 계산(사용량, 반려, 검수 결과, 판정 일치율, 상태 이력, 팀원별 값)."""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from src.constant.jira import READY_STATUS
from src.constant.time import KST
from src.jira.report.model import (
    Candidate,
    Judged,
    KrRow,
    MemberRow,
    Overview,
    ReviewComment,
    Span,
    StatusChange,
    Ticket,
    Usage,
)
from src.jira.report.parse import VERDICT_KO, is_status
from src.jira.report.period import _in
from src.jira.report.status import CLOSED_STATUSES, DONE_STATUS, IN_PROGRESS, STATUS_ORDER
from src.jira.report.text import _pct, _usd

KR_TYPE = "KR"  # 분기 묶음에서 상위 ticket으로 묶는 work type
DELIVERABLES_RE = re.compile(
    r"예상 산출물 (\d+)개 중 (\d+)개 달성"
)  # scripts/src/jira/comment.py deliverables_summary의 달성 요약 줄
HIGH_RATIO, HIGH_MIN_PREV_REVIEWS = 1.5, 10  # 사용량 많음: 비용이 전월의 1.5배 이상이고 전월 검수 실행이 10건 이상
CANDIDATE_MIN = 2  # 구두 피드백 대상 후보: 반려 2회 이상 또는 같은 검사 ID ❌ 2회 이상
RECENT_N = 20  # 최근 판정 일치율의 표본 수
# 판정 일치율의 결과 값. MATCH, MISMATCH만 일치율에 들어간다
MATCH, MISMATCH, ESCALATE = "일치", "불일치", "검토 요청"
AUTO_HOLD, NOT_LEAD, WAITING, OTHER = "자동 보류", "팀장 외 전환", "전환 없음", "기타 전환"


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


def _status_name(name: str) -> str:
    """상태 이름을 STATUS_ORDER의 표기로 맞춘다. 목록에 없으면 그대로, 비었으면 "-"."""
    return next((s for s in STATUS_ORDER if is_status(name, s)), name.strip() or "-")


def _status_counts(counts: Counter) -> list[str]:
    """상태별 수 "상태 n" 목록. STATUS_ORDER 순이고 목록에 없는 상태는 뒤에 이름 순으로 둔다."""
    order = {s: i for i, s in enumerate(STATUS_ORDER)}
    return [f"{s} {counts[s]}" for s in sorted(counts, key=lambda s: (order.get(s, len(order)), s))]


def ticket_overview(tickets: Iterable[Ticket], sp: Span, month_start: dt.datetime) -> Overview:
    """대상 기간의 ticket 현황.

    기존 ticket: month_start 전에 만들어졌고(만든 시각이 없으면 전으로 봄) month_start의 상태(state_at)가 진행
    상태인 ticket. 업데이트(updated_in)가 없는 기존 ticket은 대상 기간 끝의 상태(state_at, _status_name의 표기)별로
    센다. 새 ticket: 대상 기간에 만들어진 ticket. 지금 상태별로 센다.

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
            ov.idle[_status_name(state_at(t, sp[1]))] += 1
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

    scripts/src/jira/precheck/history.py의 business_days_since와 같은 규칙이다(시작 날짜는 빼고 끝 날짜는 넣음).

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
        end: 세는 끝 시각. Report는 대상 기간 끝과 실행 시각 중 이른 시각을 넘긴다.

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
    전환은 뺀다. 자동 보류 전환도 뺀다. jira-doc이 팀장 계정으로 전환해 전환한 사람으로는 구분할 수 없다.
    기준: 그 진입 시각 이후, 다음 ready-to-done 진입 전(없으면 끝 없음)에 작성된 검수 comment 중 자동 보류
    (ReviewComment.auto_hold)가 있다. 자동 보류 전환을 하던 apply.py는 전환을 검수 comment보다 먼저 기록해
    comment가 전환 뒤에 있다. 자동 보류 뒤 ticket은 In Progress라 같은 진입 구간에 다른 전환은 생기지 않는다.

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
            if entry is None:
                continue
            nxt = next((p.at for p in t.changes[i + 1 :] if is_status(p.dst, READY_STATUS)), None)
            if not any(rv.auto_hold and rv.at >= entry and (nxt is None or rv.at < nxt) for rv in t.reviews):
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
