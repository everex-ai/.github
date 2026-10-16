#!/usr/bin/env python3
"""업무 공유 체계(Kanban)의 'Task 단위' 페이지에서 문서화 Agent 도입 이전 현황 지표(N1–N9)를 집계한다. Notion에 쓰지 않는다.

    NOTION_TOKEN=... python scripts/dev/notion_baseline.py --data-source <data source ID> \
        --assignees <사용자 ID>,<사용자 ID> --since 2026-03-01 --until 2026-08-31 \
        --out reports/agent-impact/jira-doc/notion-baseline

- 지표 정의는 docs/jira-doc-agent/metrics.md에 있다.
- Notion REST API(Notion-Version 2026-03-11)를 표준 라이브러리 urllib로 읽기만 한다. data source의 페이지 목록,
  블록 하위 목록(has_children이면 재귀), comment 목록을 읽는다.
- HTTP 429, 5xx, 연결·응답 읽기 오류(OSError, http.client.HTTPException)면 기다렸다가 다시 시도한다. 429는 Retry-After만큼
  기다린다.
- API를 호출하는 NotionClient와 판정·집계 함수(page_info 이하)를 나눈다. run은 클라이언트 객체를 인자로 받으므로
  테스트는 가짜 객체를 넘긴다.
- 대상 페이지: 최초 생성 일자(created_time)가 기간(--since, --until, 한국 시간 날짜, 두 날짜 포함) 안이고, 담당자에
  --assignees 중 한 명 이상이 있는 페이지. 제목이 템플릿 페이지와 같으면 뺀다. --limit N이면 최초 생성 일자 순으로
  앞의 N개만 처리한다.
- comment는 Done과 Holding 페이지만 조회한다(N1, N8에만 사용). 기본은 페이지 ID와 페이지 안 모든 블록 ID로 조회하고,
  --page-comments-only면 페이지 ID로만 조회한다. comment 조회가 HTTP 403이면 권한 안내를 출력하고 바로 멈춘다.
- 페이지 하나의 조회나 판정이 실패하면 그 페이지의 error 열에 적고 다음 페이지로 넘어간다. error가 있는 페이지는
  summary.md 집계에서 뺀다(scripts/review/dev/pr_metrics.py의 error_row와 같은 방법).
- 출력: <out>/pages.csv(페이지별), <out>/summary.md(지표 요약). 담당자는 넣지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]

if str(SCRIPTS) not in sys.path:  # src 패키지를 불러오려면 scripts/가 import 경로에 있어야 함
    sys.path.append(str(SCRIPTS))
from src.dev.notion.client import NotionClient, NotionError  # noqa: E402
from src.dev.notion.run import run  # noqa: E402


def _id_list(s: str) -> list[str]:
    """쉼표로 구분한 ID 목록."""
    return [x.strip() for x in s.split(",") if x.strip()]


def _positive_int(s: str) -> int:
    """1 이상의 정수."""
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 함")
    return n


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """명령줄 인자를 읽는다.

    Args:
        argv: 인자 목록. 없으면 sys.argv를 읽는다.

    Returns:
        인자 값.
    """
    ap = argparse.ArgumentParser(
        description="'Task 단위' 페이지에서 문서화 Agent 도입 이전 현황 지표(N1–N9)를 집계한다."
    )
    ap.add_argument("--data-source", required=True, help="'Task 단위'의 data source ID")
    ap.add_argument("--assignees", type=_id_list, required=True, help="대상 담당자의 Notion 사용자 ID(쉼표로 구분)")
    ap.add_argument("--since", type=dt.date.fromisoformat, help="최초 생성 일자 시작 YYYY-MM-DD(한국 시간)")
    ap.add_argument("--until", type=dt.date.fromisoformat, help="최초 생성 일자 끝 YYYY-MM-DD(한국 시간, 그날 포함)")
    ap.add_argument("--out", type=Path, required=True, help="출력 폴더")
    ap.add_argument("--page-comments-only", action="store_true", help="comment를 페이지 ID로만 조회한다")
    ap.add_argument("--limit", type=_positive_int, help="대상 페이지 중 최초 생성 일자 순으로 앞의 N개만 처리한다")
    return ap.parse_args(argv)


def main() -> None:
    """환경 변수 NOTION_TOKEN으로 클라이언트를 만들고 집계를 실행한다."""
    args = parse_args()
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("[notion_baseline] 환경 변수 NOTION_TOKEN 필요(Notion integration token)", file=sys.stderr)
        sys.exit(1)
    try:
        run(NotionClient(token), args)
    except NotionError as e:
        sys.exit(f"[notion_baseline] {e}")


if __name__ == "__main__":
    main()
