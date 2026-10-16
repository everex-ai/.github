"""정기 보고의 달, 분기 기간 계산(한국 시간 기준 [시작, 끝) 구간)."""

from __future__ import annotations

import datetime as dt

from src.constant.time import KST
from src.jira.report.model import Span


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
