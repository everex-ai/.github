#!/usr/bin/env python3
"""2단계: 사람의 판단이 필요 없는 검사를 계산한다.

- ctx/<KEY>/            -> ctx/<KEY>/precheck.json   (Claude가 인용하는 검사 결과와 예상 산출물 목록)
- ctx/_scan/<KEY>.json  -> ctx/_scan/<KEY>.alerts.json (주간 점검 알림. apply.py가 Slack으로 보냄)
검사 항목의 정의는 prompts/review.md와 prompts/types/<work type>/review.md의 검사 항목 표와 같다(A1, A3는 docs/jira-doc-agent/README.md의 "스크립트가 계산하는 값" 절).
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.jira.precheck.run import run_issue_dir, run_scan  # noqa: E402


def main() -> None:
    """첫 인자(기본 ctx)의 폴더에서 ticket별 검사와 주간 점검 알림을 계산한다."""
    ctx = Path(sys.argv[1] if len(sys.argv) > 1 else "ctx")
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    for d in sorted(ctx.glob("[A-Z]*-[0-9]*/")):
        if (d / "issue.json").exists():
            run_issue_dir(d, now)
    for p in sorted((ctx / "_scan").glob("*.json")) if (ctx / "_scan").exists() else []:
        if not p.name.endswith(".alerts.json"):
            run_scan(p, now)


if __name__ == "__main__":
    main()
