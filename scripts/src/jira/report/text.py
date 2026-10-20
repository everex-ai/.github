"""정기 보고 Slack 본문의 숫자와 문자열 서식, 줄 감싸기, Claude 사용 ticket 줄."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable

from src.common.text import fmt
from src.constant.slack import SLACK_INDENT
from src.jira.report.model import BRANCHES, Span, Ticket
from src.jira.report.period import _in

LINE_MAX = 50  # 줄 하나의 글자 수 상한(목록 기호와 들여쓰기 제외)
NAME_MAX = 12  # 팀원별 줄에 적는 담당자 이름 글자 수 상한. 넘으면 자르고 "…"를 붙인다
TOP_TICKETS = 3  # Claude 활용 요약 절에 적는 Claude 사용 ticket 수
TITLE_MAX = 20  # Claude 사용 ticket 줄에 적는 제목 글자 수 상한. 넘으면 자르고 "…"를 붙인다


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
    return f"{fmt(minutes)}분" if minutes < 60 else f"{fmt(minutes / 60)}시간"


def _median(values: list[float]) -> str:
    """중앙값과 표본 수 "중앙값 30분(5건)". 표본이 없으면 "-"."""
    return f"중앙값 {_dur(statistics.median(values))}({len(values)}건)" if values else "-"


def _seconds(minutes: list[float]) -> str:
    """자동 검수 소요 시간 값 "평균 390초(120–900초, 24건)". 초는 반올림한 정수다. 기록이 없으면 "-"."""
    if not minutes:
        return "-"
    secs = [m * 60 for m in minutes]
    return f"평균 {round(statistics.mean(secs))}초({round(min(secs))}–{round(max(secs))}초, {len(secs)}건)"


def _count(n: int) -> str:
    """토큰 수. 1000 미만은 그대로, 그 이상은 천, 만, 억 단위로 소수 한 자리까지 적는다(예: 1.3천, 23.5만)."""
    for unit, size in (("억", 10**8), ("만", 10**4), ("천", 10**3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def _usd(x: float) -> str:
    return f"{x:.2f} USD"


def _sub(text: str, depth: int = 1) -> str:
    """Slack 본문의 절 아래 줄. depth 단계만큼 SLACK_INDENT로 들여 쓰고 1단계는 "◦", 2단계 이상은 "‣"를 붙인다."""
    return f"{SLACK_INDENT * depth}{'◦' if depth == 1 else '‣'} {text}"


def _sub2(text: str) -> str:
    """Slack 본문의 절 아래 2단계 줄(_sub의 depth 2)."""
    return _sub(text, 2)


def _esc(text: str) -> str:
    """Slack이 제어 문자로 읽는 &, <, >를 바꾼다."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(prefix: str, items: list[str], limit: int = LINE_MAX) -> list[str]:
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


def _capped_wrap(items: list[str], max_lines: int, prefix: str = "") -> list[str]:
    """_wrap으로 나눈 줄이 max_lines를 넘으면 뒤 값부터 "외 n종"으로 줄인다.

    Args:
        items: 이어 적을 값.
        max_lines: 줄 수 상한.
        prefix: 첫 줄 앞 문구.

    Returns:
        줄 목록. items가 비었으면 빈 목록.
    """
    for rest in range(len(items) + 1):
        shown = items[: len(items) - rest] + ([f"외 {rest}종"] if rest else [])
        lines = _wrap(prefix, shown)
        if len(lines) <= max_lines:
            return lines
    return [f"{prefix}외 {len(items)}종"]


def _name(name: str) -> str:
    """담당자 표시 이름. NAME_MAX자를 넘으면 자르고 "…"를 붙인다. Slack 제어 문자는 바꾼다."""
    return _esc(name if len(name) <= NAME_MAX else name[:NAME_MAX] + "…")


def _clip(text: str, room: int) -> str:
    """text 앞 TITLE_MAX자 이내를 Slack 제어 문자를 바꿔 room자 이내로 적는다. 자르면 "…"를 붙인다.

    Args:
        text: 제목.
        room: 글자 수 상한("…" 포함).

    Returns:
        표시 문구. room 안에 한 글자도 넣을 수 없으면 빈 문자열.
    """
    for n in range(min(len(text), TITLE_MAX), 0, -1):
        shown = _esc(text[:n]) + ("…" if n < len(text) else "")
        if len(shown) <= room:
            return shown
    return ""


def top_ticket_lines(tickets: Iterable[Ticket], sp: Span) -> list[str]:
    """Claude 사용 ticket Top3 줄. 기간 안 실행의 API 환산 비용 합이 큰 ticket TOP_TICKETS개를 적는다.

    기간 안 실행이 1건 이상인 ticket만 넣는다. 비용이 없는 실행은 0으로 더한다. 같은 비용이면 ticket 키 순
    (짧은 키, 사전 순)이다. ticket마다 두 줄이다.
    첫 줄: 순위, ticket 키, work type, 담당자, API 환산 비용 합.
    둘째 줄: 제목 앞 TITLE_MAX자와 사유(기간 안 실행을 BRANCHES 순서로 센 횟수, 0회 갈래는 뺌).
    둘째 줄이 LINE_MAX자를 넘으면 제목을 더 줄이고, 제목에 남길 자리가 4자 미만이면 사유만 적는다.
    사유만으로 LINE_MAX자를 넘으면 뒤 갈래부터 "외 n종"으로 줄인다.

    Args:
        tickets: ticket 기록.
        sp: 기간.

    Returns:
        Slack 본문 줄. 기간 안 실행이 없으면 "◦ Claude 사용 ticket Top3: -" 한 줄(1단계 들여쓰기).
    """
    rows: list[tuple[Ticket, float, Counter]] = []
    for t in tickets:
        runs = [r for r in t.runs if _in(r.at, sp)]
        if runs:
            rows.append((t, sum(r.cost or 0.0 for r in runs), Counter(r.branch for r in runs)))
    if not rows:
        return [_sub(f"Claude 사용 ticket Top{TOP_TICKETS}: -")]
    rows.sort(key=lambda x: (-round(x[1], 6), len(x[0].key), x[0].key))
    lines = [_sub(f"Claude 사용 ticket Top{TOP_TICKETS}")]
    for rank, (t, cost, branches) in enumerate(rows[:TOP_TICKETS], 1):
        who = _name(t.assignee_name) if t.assignee_name else "-"
        lines.append(_sub2(f"({rank}) {t.key} {t.itype or '-'} {who} {_usd(cost)}"))
        # 사유만으로 LINE_MAX자를 넘으면 뒤 갈래부터 "외 n종"으로 줄인다
        reason = _capped_wrap([f"{ko} {branches[b]}회" for b, ko in BRANCHES.items() if branches[b]], 1)[0]
        room = LINE_MAX - len(reason) - len(" | ")
        title = _clip(t.title, room) if room >= 4 else ""
        lines.append(_sub(f"{title} | {reason}" if title else reason, 3))
    return lines


def _fit_members(head: str, groups: list[list[str]], budget: int) -> list[str]:
    """팀원별 절 줄. 절이 budget 줄을 넘으면 앞 담당자부터 넣고 남은 담당자는 "◦ 외 n명"으로 줄인다.

    budget이 3줄 미만이면 절 제목과 요약 한 줄(두 줄)만 적는다.

    Args:
        head: "순서:" 줄의 지표 순서 문구.
        groups: 담당자마다의 줄(한 명당 두 줄 이내).
        budget: 절에 쓸 수 있는 줄 수.

    Returns:
        Slack 본문 줄.
    """
    if budget < 3:
        return ["• *팀원별*", _sub(f"담당자 {len(groups)}명: 줄 수 상한으로 생략" if groups else "기록 없음")]
    lines = ["• *팀원별*", _sub(f"순서: {head}")]
    if not groups:
        return [*lines, _sub("기록 없음")]
    if len(lines) + sum(map(len, groups)) <= budget:
        return lines + [x for g in groups for x in g]
    kept: list[str] = []
    n = 0
    for g in groups:
        if len(lines) + len(kept) + len(g) + 1 > budget:
            break
        kept += g
        n += 1
    return [*lines, *kept, _sub(f"외 {len(groups) - n}명")]
