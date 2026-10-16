#!/usr/bin/env python3
"""문서화 Agent(jira-doc)의 도입 효과 지표(J1, J3–J8)를 Jira 기록에서 집계한다. Jira에 쓰지 않는다.

    python scripts/dev/jira_metrics.py --since 2026-09-01 --until 2026-09-30 --out reports/agent-impact/jira-doc/jira

- 지표 정의는 docs/jira-doc-agent/metrics.md에 있다. J2(환산 리뷰 시간)는 지표에서 뺐다.
- Jira 읽기는 scripts/src/jira/client.py의 Jira.search, Jira.changelog, Jira.comments만 호출한다.
  환경 변수 JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN이 필요하다. J7의 팀장 계정은 --lead(기본값 JIRA_LEAD_ACCOUNT_ID)다.
- 검수 comment 파싱(parse_review)과 상태 이력 처리(status_changes)는 scripts/src/jira/report/parse.py에 있고,
  J7 판정 일치율도 scripts/src/jira/report/metrics.py의 judge를 사용한다.
- 검사 이름과 표시 문구(check_label, CHECK_ORDER, RESULT_KO)는 scripts/src/jira/verdict.py와
  scripts/src/constant/check.py에서 가져온다.
- 기간(--since, --until)은 한국 시간 날짜이고, 기간 안에 request 전환이 있는 task를 집계한다.
- 출력: <out>/tasks.csv(task별), <out>/reviews.csv(검수 comment별), <out>/summary.md(지표 요약). 담당자는 넣지 않는다.
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
from src.dev.jira_metrics.run import run  # noqa: E402
from src.jira import client as jira_client  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """명령줄 인자를 읽는다.

    Args:
        argv: 인자 목록. 없으면 sys.argv를 읽는다.

    Returns:
        인자 값.
    """
    ap = argparse.ArgumentParser(description="문서화 Agent(jira-doc)의 지표(J1, J3–J8)를 Jira 기록에서 집계한다.")
    ap.add_argument("--jql", default="project = INNO", help="대상 task 검색 조건(JQL)")
    ap.add_argument("--since", type=dt.date.fromisoformat, help="기간 시작 날짜 YYYY-MM-DD(한국 시간)")
    ap.add_argument("--until", type=dt.date.fromisoformat, help="기간 끝 날짜 YYYY-MM-DD(한국 시간, 그날 포함)")
    ap.add_argument("--out", type=Path, required=True, help="출력 폴더")
    ap.add_argument(
        "--lead",
        default=os.environ.get("JIRA_LEAD_ACCOUNT_ID", ""),
        help="팀장 Jira accountId. J7 판정 일치율에서 이 계정의 전환만 센다(기본값: 환경 변수 JIRA_LEAD_ACCOUNT_ID)",
    )
    return ap.parse_args(argv)


def main() -> None:
    """Jira 환경 변수로 읽기 객체를 만들고 집계를 실행한다."""
    args = parse_args()
    try:
        jira = jira_client.Jira()
    except KeyError as e:
        sys.exit(f"[jira_metrics] 환경 변수 {e.args[0]} 필요 (JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN)")
    run(jira, args)


if __name__ == "__main__":
    main()
