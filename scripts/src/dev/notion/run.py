"""Notion 현황 지표 집계 실행. 페이지를 읽어 pages.csv와 summary.md를 쓴다."""

from __future__ import annotations

import argparse
import http.client
import sys
from pathlib import Path
from typing import Any

from src.common.io import write_csv
from src.common.period import period_bounds
from src.dev.notion.client import CommentPermissionError, NotionReader
from src.dev.notion.page import PAGE_COLUMNS, PageResult, error_row, norm_id, page_info, process_page, select_pages
from src.dev.notion.summary import render_summary


def run(client: NotionReader, args: argparse.Namespace) -> list[PageResult]:
    """Notion 페이지를 읽어 지표를 계산하고 pages.csv, summary.md를 쓴다.

    페이지 하나가 실패하면 pages.csv의 error 열에 적고 다음 페이지로 넘어간다. comment 조회가 HTTP 403이면
    CommentPermissionError를 그대로 올려 바로 멈춘다.

    Args:
        client: Notion 읽기 객체.
        args: parse_args의 결과.

    Returns:
        집계한 페이지(error가 없는 대상 페이지)의 판정 결과.
    """
    start, end = period_bounds(args.since, args.until)
    assignees = {norm_id(x) for x in args.assignees}
    pages = client.query_pages(args.data_source)
    matched = select_pages([page_info(p) for p in pages], assignees, start, end)
    targets = matched if args.limit is None else matched[: args.limit]
    results: list[PageResult] = []
    rows: list[dict[str, Any]] = []
    for i, info in enumerate(targets, 1):
        try:
            result = process_page(client, info, args.page_comments_only)
        except CommentPermissionError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError, http.client.HTTPException) as e:
            print(f"[notion_baseline] 페이지 {info.id} 실패: {e}", file=sys.stderr)
            rows.append(error_row(info, f"{type(e).__name__}: {e}"))
            continue
        results.append(result)
        rows.append(result.row())
        if i % 10 == 0 or i == len(targets):
            print(f"[notion_baseline] 페이지 {i}/{len(targets)} 읽음", file=sys.stderr)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "pages.csv", PAGE_COLUMNS, rows)
    summary = render_summary(
        results,
        fetched=len(pages),
        matched=len(matched),
        errors=len(rows) - len(results),
        assignees=len(assignees),
        since=args.since,
        until=args.until,
        page_only=args.page_comments_only,
        limit=args.limit,
    )
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print(f"[notion_baseline] 대상 페이지 {len(rows)}개 중 {len(results)}개 집계: {out}/pages.csv, summary.md")
    return results
