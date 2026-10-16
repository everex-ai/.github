"""기간 날짜를 한국 시간 경계로 바꾸고 시각이 기간 안인지 확인하는 함수."""

from __future__ import annotations

import datetime as dt

from src.constant.time import KST


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
