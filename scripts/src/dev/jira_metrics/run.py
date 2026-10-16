"""jira_metrics의 집계 실행. Jira 기록을 읽어 tasks.csv, reviews.csv, summary.md를 쓴다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.common.io import write_csv
from src.common.period import period_bounds
from src.constant.jira import SEARCH_LIMIT
from src.dev.jira_metrics.analyze import analyze_task, build_jql, collect, review_row
from src.dev.jira_metrics.model import REVIEW_COLUMNS, TASK_COLUMNS, JiraReader, TaskResult
from src.dev.jira_metrics.summary import render_summary


def run(jira: JiraReader, args: argparse.Namespace) -> list[TaskResult]:
    """Jira 기록을 읽어 지표를 계산하고 tasks.csv, reviews.csv, summary.md를 쓴다.

    Args:
        jira: Jira 읽기 객체.
        args: parse_args의 결과.

    Returns:
        집계 대상 task의 계산 결과.
    """
    start, end = period_bounds(args.since, args.until)
    jql = build_jql(args.jql, args.since)
    records = collect(jira, jql)
    if len(records) >= SEARCH_LIMIT:
        print(f"[jira_metrics] 검색 결과가 상한 {SEARCH_LIMIT}개에 닿음", file=sys.stderr)
    results = [r for r in (analyze_task(rec, start, end, args.lead) for rec in records) if r]
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "tasks.csv", TASK_COLUMNS, [r.row() for r in results])
    write_csv(out / "reviews.csv", REVIEW_COLUMNS, [review_row(rv) for r in results for rv in r.reviews])
    summary = render_summary(results, len(records), jql, args.since, args.until, args.lead)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print(f"[jira_metrics] task {len(results)}개 집계: {out}/tasks.csv, reviews.csv, summary.md")
    return results
