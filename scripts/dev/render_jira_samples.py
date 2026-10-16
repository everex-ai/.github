#!/usr/bin/env python3
"""jira-doc이 결과로 산출하는 문서 중 코드가 조립하는 부분의 샘플을 만든다. Jira, Slack에 쓰지 않는다.

    python scripts/dev/render_jira_samples.py --out reports/result-doc-style/after

scripts/src/jira/issue.py의 apply_issue, slack_weekly와 scripts/src/jira/slack.py의 token_expiry_text를 가짜 Jira 객체와 샘플 입력으로 실행하고,
Jira에 쓰려던 comment와 필드 값, Slack에 보내려던 본문을 파일로 저장한다.
샘플 입력의 문구는 prompts/types/task/deliverables.md, prompts/types/task/review.md, prompts/types/bug/review.md,
prompts/types/issue/review.md의 출력 예시에서 가져온다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # src 패키지를 불러오려면 scripts/가 import 경로에 있어야 함
from src.dev.samples.render import render  # noqa: E402


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="샘플을 저장할 폴더")
    a = ap.parse_args()
    for p in render(Path(a.out)):
        print(p)


if __name__ == "__main__":
    main()
