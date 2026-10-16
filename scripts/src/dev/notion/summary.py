"""Notion 현황 지표(N1–N9)의 summary.md 본문 조립."""

from __future__ import annotations

import datetime as dt
from collections import Counter

from src.common.text import rate
from src.dev.notion.page import STATUS_ORDER, PageResult, month
from src.dev.notion.template import (
    EVIDENCE_BLOCKS,
    EVIDENCE_MENTIONS,
    KIND_NONE,
    SECTION_LABELS,
    TEMPLATE_HEADINGS,
)


def _judged(results: list[PageResult], attr: str) -> tuple[int, int]:
    """판정 값이 있는 페이지 중 만족한 페이지 수와 판정 값이 있는 페이지 수."""
    values = [getattr(r, attr) for r in results if getattr(r, attr) is not None]
    return sum(values), len(values)


def _metric(title: str, label: str, a: int, b: int, method: str, extra: list[str] | None = None) -> list[str]:
    """지표 절 하나. 비율(a/b), 표본 수 n, 계산 방법을 적는다."""
    return [f"## {title}", "", f"- {label}은 {rate(a, b)}, n={b}임", f"  - 계산 방법: {method}", *(extra or []), ""]


def _n2(results: list[PageResult]) -> list[str]:
    """N2 구역별 작성률."""
    n = len(results)
    lines = [
        "## N2 구역별 작성률",
        "",
        f"- 대상 페이지 전체(n={n}) 중 구역마다 작성한 페이지의 비율은 아래와 같음",
        "  - 계산 방법: 구역 안에 작성한 내용이 있는 블록이 1개 이상 있으면 작성으로 셈",
        "  - 안내 문구만 있는 블록, 빈 블록(텍스트 없는 paragraph·목록 항목·체크박스 등), 템플릿 예시 항목, 구분선·목차·"
        "상위 경로 블록은 작성한 내용으로 세지 않음",
        "  - 안내 문구 뒤에 글자를 이어 적었거나 안내 문구 아래에 하위 블록을 적었으면 작성으로 셈",
        "  - 다른 블록을 담는 블록(열, 표 등)은 하위 블록에 작성한 내용이 있을 때만 셈. 토글은 제목이나 하위 블록에 "
        "작성한 내용이 있으면 셈",
        "  - 구역 안에 템플릿에 없는 heading이 있으면 heading 문구는 세지 않고 그 아래 블록으로 판정함",
        "  - 템플릿 구역 heading을 찾지 못한 페이지(템플릿 종류 none)는 모든 구역을 미작성으로 셈",
    ]
    for key, label in SECTION_LABELS.items():
        lines.append(f"- {label}: {rate(sum(r.written[key] for r in results), n)}")
    lines.append(f"- 세 구역 모두: {rate(sum(r.all_written() for r in results), n)}")
    return lines + [""]


def _n9(results: list[PageResult]) -> list[str]:
    """N9 월별 task 수."""
    lines = [
        "## N9 월별 task 수",
        "",
        "- 최초 생성 월(한국 시간)별 대상 페이지 수, Done 수, Done 비중은 아래 표와 같음",
        "  - 계산 방법: Done 비중은 그 달 Done 수 / 그 달 대상 페이지 수임",
        "",
        "| 최초 생성 월 | 대상 페이지 수 | Done 수 | Done 비중(Done 수/대상 페이지 수) |",
        "|---|---|---|---|",
    ]
    by_month: dict[str, list[PageResult]] = {}
    for r in results:
        by_month.setdefault(month(r.page.created), []).append(r)
    for m, rs in sorted(by_month.items()):
        done = sum(r.done for r in rs)
        lines.append(f"| {m} | {len(rs)} | {done} | {rate(done, len(rs))} |")
    return lines + [""]


def _count_line(counter: Counter, order: list[str]) -> str:
    """'이름 n개' 목록 문구. order에 있는 이름을 먼저 적고 나머지는 이름순으로 적는다."""
    names = [x for x in order if x in counter] + sorted(x for x in counter if x not in order)
    return ", ".join(f"{x or '(빈 값)'} {counter[x]}개" for x in names) or "없음"


def render_summary(
    results: list[PageResult],
    *,
    fetched: int,
    matched: int,
    errors: int,
    assignees: int,
    since: dt.date | None,
    until: dt.date | None,
    page_only: bool,
    limit: int | None,
) -> str:
    """summary.md 본문. 지표마다 비율(a/b), 표본 수 n, 계산 방법을 적고 개인별 값과 담당자는 넣지 않는다.

    Args:
        results: 집계한 페이지(error가 없는 대상 페이지)의 판정 결과.
        fetched: data source에서 조회한 페이지 수.
        matched: 대상 조건을 만족한 페이지 수(--limit 적용 전).
        errors: 조회나 판정이 실패해 집계에서 뺀 페이지 수.
        assignees: --assignees로 받은 담당자 수.
        since: 기간 시작 날짜.
        until: 기간 끝 날짜.
        page_only: comment를 페이지 ID로만 조회했으면 True.
        limit: --limit 값. 없으면 None.

    Returns:
        markdown 본문.
    """
    period = f"{since.isoformat() if since else '제한 없음'}–{until.isoformat() if until else '제한 없음'}"
    statuses = Counter(r.page.status for r in results)
    kinds = Counter(r.kind for r in results)
    scope = "페이지 ID로만 조회함" if page_only else "페이지 ID와 페이지 안 모든 블록 ID로 조회함"
    lines = [
        "# 문서화 Agent 도입 이전 현황 지표 집계(업무 공유 체계(Kanban)의 'Task 단위')",
        "",
        f"- 기간은 최초 생성 일자 {period}임(한국 시간 날짜, 양 끝 날짜 포함)",
        f"- 대상 조건은 기간 안에 만든 페이지 중 `--assignees`로 받은 담당자 {assignees}명 중 한 명 이상이 담당한 페이지임",
        "  - 제목이 템플릿 페이지(Engineering Template, Research Template과 그 변형 1개)와 같은 페이지는 뺌",
        f"- 대상 조건을 만족한 페이지는 {matched}개임(data source에서 조회한 페이지 {fetched}개 중)",
    ]
    if limit is not None:
        lines.append(f"  - `--limit {limit}`을 적용해 최초 생성 일자 순으로 앞의 {min(limit, matched)}개만 처리함")
    if errors:
        lines.append(f"  - Notion 조회나 판정이 실패해 집계에서 뺀 페이지는 {errors}개임(pages.csv의 error 열)")
    lines += [
        f"- 집계한 페이지는 {len(results)}개임",
        f"  - 상태별: {_count_line(statuses, STATUS_ORDER)}",
        f"  - 템플릿 종류별: {_count_line(kinds, [*TEMPLATE_HEADINGS, KIND_NONE])}",
        "  - 템플릿 종류는 최상위 heading 문구로 판정함. none은 템플릿 구역 heading을 찾지 못한 페이지임",
        f"- comment는 Done과 Holding인 페이지만 조회함(N1, N8에만 사용). 조회 방식은 {scope}",
        "  - Notion API는 해결되지 않은 comment만 돌려주므로 해결된 comment는 세지 않음",
        "- 수치는 팀 합계이고 개인별 값은 넣지 않음. 판정은 집계 시점의 페이지 상태로 함",
        "",
    ]
    a, b = _judged(results, "n1")
    lines += _metric(
        "N1 리뷰 흔적 비율",
        "리뷰 흔적 비율",
        a,
        b,
        "Done인 대상 페이지(분모) 중 comment 작성자에 그 페이지 담당자가 아닌 사람이 1명 이상 있는 페이지(분자)의 비율임",
    )
    lines += _n2(results)
    a, b = _judged(results, "n3")
    lines += _metric(
        "N3 1:1 대응률",
        "1:1 대응률",
        a,
        b,
        "Done인 대상 페이지(분모) 중 Task 완료 기준과 결과 구역의 최상위 목록 항목(텍스트가 있는 글머리 기호·번호·체크박스 "
        "항목) 수가 같고 1개 이상인 페이지(분자)의 비율임",
        ["  - 구역 안에 템플릿에 없는 heading이 있으면 그 아래 목록 항목도 최상위 목록 항목으로 셈"],
    )
    a, b = _judged(results, "n4")
    lines += _metric(
        "N4 ✅/❌ 표기율",
        "✅/❌ 표기율",
        a,
        b,
        "Done인 대상 페이지(분모) 중 결과 구역의 최상위 목록 항목이 1개 이상이고 모두 ✅ 또는 ❌를 포함하는 페이지(분자)의 "
        "비율임. 체크박스는 체크를 ✅, 미체크를 ❌로 셈",
    )
    a, b = _judged(results, "n5")
    lines += _metric(
        "N5 미달성 사유율",
        "미달성 사유율",
        a,
        b,
        "❌ 항목이 1개 이상인 Done 대상 페이지(분모) 중 ❌ 항목마다 작성한 내용(N2와 같은 판정)이 있는 하위 블록이 1개 "
        "이상인 페이지(분자)의 비율임",
        ["  - 추정: 내용이 있는 하위 블록이 있으면 사유가 있다고 간주하는 근사 규칙이라 실제 사유 기록과 다를 수 있음"],
    )
    a, b = _judged(results, "n6")
    lines += _metric(
        "N6 근거 링크율",
        "근거 링크율",
        a,
        b,
        "Done인 대상 페이지(분모) 중 결과 구역(하위 블록 포함)에 근거가 1개 이상 있는 페이지(분자)의 비율임",
        [
            f"  - 근거: 링크가 걸린 텍스트, {', '.join(EVIDENCE_MENTIONS.values())}, "
            f"{', '.join(EVIDENCE_BLOCKS.values())} 블록",
            "  - 날짜 mention과 사용자 mention은 근거로 세지 않음",
        ],
    )
    a, b = _judged(results, "n7")
    lines += _metric(
        "N7 문서 완성률",
        "문서 완성률",
        a,
        b,
        "Done인 대상 페이지(분모) 중 N2의 세 구역 작성, N3, N4, N6을 모두 만족하고, ❌ 항목이 있으면 N5도 만족하는 "
        "페이지(분자)의 비율임",
    )
    a, b = _judged(results, "n8")
    lines += _metric(
        "N8 Holding 사유 comment율",
        "Holding 사유 comment율",
        a,
        b,
        "Holding인 대상 페이지(분모) 중 comment가 1개 이상 있는 페이지(분자)의 비율임",
        ["  - 추정: comment가 있으면 사유가 있다고 간주하는 근사 규칙이라 실제 사유 기록과 다를 수 있음"],
    )
    lines += _n9(results)
    return "\n".join(lines).rstrip() + "\n"
