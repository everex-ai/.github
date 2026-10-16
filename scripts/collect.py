#!/usr/bin/env python3
"""1단계: Jira와 GitHub에서 입력을 모아 ctx/ 아래에 파일로 저장한다.

- review 모드: --issue 하나만 대상
- digest 모드: --issue가 필요하다. 그 task 하나만 대상
- alerts 모드: 주간 점검용. Done/Deleted가 아닌 모든 최상위 task의 요약만 ctx/_scan/에 만든다 (Claude 단계를 쓰지 않는다)
환경 변수: JIRA_*, JIRA_PROJECT_KEY, JIRA_TLDR_FIELD_ID, GH_TOKEN, GH_ORG
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.jira import collect as jira_collect  # noqa: E402
from src.jira.client import Jira  # noqa: E402


def main() -> None:
    """인자를 읽어 모드에 맞게 ctx/ 아래에 수집 결과를 저장한다."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["review", "digest", "alerts"], required=True)
    ap.add_argument("--issue", default="")
    args = ap.parse_args()
    if args.mode != "alerts" and not args.issue.strip():  # ticket 키 없는 실행은 막는다. Jira를 부르기 전에 끝낸다
        sys.exit(f"{args.mode} 모드에는 --issue가 필요하다")

    j = Jira()
    site = j.site_url()
    org = os.environ.get("GH_ORG", "")
    project = os.environ.get("JIRA_PROJECT_KEY", "")
    if jira_collect.CTX.exists():
        shutil.rmtree(jira_collect.CTX)
    jira_collect.CTX.mkdir()

    if args.mode == "alerts":  # 주간 점검: 누락 검사용 요약만 모은다
        if not project:
            sys.exit("alerts 모드에는 JIRA_PROJECT_KEY가 필요하다")
        jira_collect.collect_scan(j, project, site)
        print(f"[collect] alerts: 점검 대상 {len(list((jira_collect.CTX / '_scan').glob('*.json')))}건")
        return
    jira_collect.collect_issue(j, args.issue.strip(), site, org, args.mode)


if __name__ == "__main__":
    main()
