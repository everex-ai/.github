#!/usr/bin/env python3
"""4단계: Claude의 출력(out/<KEY>/)을 검증해 Jira에 반영한다. Jira에 쓰는 유일한 단계.

alerts 모드(주간 점검)는 Jira에 쓰지 않고 ctx/_scan/*.alerts.json을 Slack 한 건으로 보낸다. 같은 모드에서 환경 변수 TOKEN_EXPIRY의
만료일이 30일 이하로 남은 토큰(인증 키)이 있으면 팀장 채널로 Slack 한 건을 더 보낸다.
report 모드는 scripts/src/jira/report/run.py의 run_report가 만든 정기 보고 메시지를 팀장 채널로 보낸다.
decision 모드는 팀장이 ready-to-done ticket을 done(통과) 또는 In Progress(보류)로 전환했을 때 담당자에게 Slack 한 건을 보낸다.
failure 모드(5단계)는 앞 스텝이 실패했을 때만 실행하며, out/_run_error.json의 실패 원인을 연동 채널 Slack 한 건으로 알린다.
검수·정리 모드에서 ticket 실패 알림을 이미 보냈으면(out/_failure_notified) 다시 보내지 않는다.

반영 순서: 구역 교체(결과 산출물) -> TL;DR -> comment(검수 모드) -> property -> Slack 알림(검수 모드)
- out/<KEY>/ 파일은 CI agent가 아니라 scripts/save_output.py가 CI agent의 구조화 출력(JSON)으로 만든다. 구조화 출력에 없는 Task는
  판정 없음 기본값(verdict "no_verdict", items의 done null)으로 채워져 있다.
- verdict.json이 없거나 형식이 틀리거나 판정 없음 기본값이면 반영하지 않고 실패로 기록한다. 검수 모드에서 verdict.json은 있으나
  tldr.wiki나 comment.wiki가 없거나 비어 있어도 같은 실패로 처리한다. 실패한 Task에는 Jira comment를 남기지 않는다.
- 실패한 Task는 모아서 Slack 한 건(팀장 멘션)으로 알리고, 실패가 하나라도 있으면 종료 코드 1로 끝난다(Actions 실행 상태가 failure).
- 결과 산출물 구역과 검수 comment의 검사 표는 Claude의 verdict.json(items, extra, checks)을 받아 이 스크립트가
  정해진 형식으로 조립한다. Claude가 쓰는 자유 서술은 comment.wiki(결과 요약)와 tldr.wiki뿐이다.
- 검수 결과는 통과(pass), 검토 요청(escalate), 보류(fix)다. 괄호 안은 verdict.json의 코드값이다.
- T3: verdict.items의 번호가 precheck의 예상 산출물 번호(1..n)와 1:1이 아니면 검수 결과를 검토 요청으로 바꾼다.
- 문서화 리뷰 항목(Task T4–T9, Bug B5–B7, Issue I4–I5. 검수 모드): 관찰 모드는 검수 결과를 바꾸지 않고 팀장용 comment와 Actions Summary로 공유하고,
  게이트 모드는 미달이면 통과를 보류로 바꾼다. T8(초과 달성·미달성 사유)은 이 스크립트가 계산한다.
- Task 검수는 미달성 항목마다 담당자가 남긴 사유가 있으면 검토 요청으로, 통과인데 미달성 항목이 있으면 보류로 바꾼다.
- 상태 처리(검수 모드): 어떤 검수 결과에서도 Jira 상태를 바꾸지 않고 팀장이 확인 뒤 직접 전환한다. 게이트 모드는
  문서화 리뷰를 검수 결과에 넣을지만 정하고 전환과 관계없다.
- Slack 알림(검수 모드): 검수 결과와 관계없이 팀장에게 검수 결과, 이유, 검수 comment 링크를 보낸다. 담당자는 통과·보류 검수
  comment의 멘션으로 검수 결과를 바로 받고, 팀장이 전환하면 decision 모드의 팀장 확인 결과 알림으로 상태 변경을 받는다.
- 실행 기록: 실행마다 jira-doc 기록(issue property ai-doc-agent)의 runs에 항목 하나를 추가한다(최근 100건). 반영 성공과 실패 모두 기록한다.
- comment의 멘션은 담당자와 팀장 두 사람으로 제한한다.
- 정리 모드(F3)도 Task의 결과 산출물 구역을 다시 쓴다. comment와 판정은 없고, 사유가 기록에 없으면 "사유 미기재"를 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.common.github import FAILURE_NOTIFIED_FILE, RUN_ERROR_FILE, Summary, write_run_error  # noqa: E402
from src.common.io import load_json  # noqa: E402
from src.constant.slack import LEAD_WEBHOOK_ENV  # noqa: E402
from src.constant.time import KST  # noqa: E402
from src.jira import slack  # noqa: E402
from src.jira.client import Jira, JiraError  # noqa: E402
from src.jira.issue import (  # noqa: E402
    apply_decision,
    apply_issue,
    last_month,
    record_failed_run,
    slack_token_expiry,
    slack_weekly,
)
from src.jira.slack import failure_slack_text, load_slack_users, run_failure_slack_text  # noqa: E402


def failure_notice(out: Path, failed_mode: str, issue: str, lead: str, summ: Summary) -> None:
    """앞 스텝이 실패했을 때 연동 채널로 실패 알림을 보낸다(failure 모드). Jira에 접근하지 않는다.

    out/_failure_notified가 있으면 ticket 실패 알림을 이미 보낸 것이라 보내지 않고 Summary 탭에 "건너뜀"을 남긴다.
    없으면 out/_run_error.json(없으면 기록 없음)으로 본문을 만들어 연동 채널(SLACK_WEBHOOK_URL)로 보낸다.
    Summary에는 빈 대응표로 다시 만든 본문을 남겨 Slack 멤버 ID가 public repo에 보이지 않게 한다.

    Args:
        out: 실패 기록 폴더(out/).
        failed_mode: 실패한 실행 모드.
        issue: 대상 ticket 키. 없으면 빈 문자열.
        lead: 팀장 accountId.
        summ: Actions 요약.
    """
    if (out / FAILURE_NOTIFIED_FILE).exists():
        summ.row("-", "-", "failure", "건너뜀", "ticket 실패 알림을 이미 보냄")
        return
    error = load_json(out / RUN_ERROR_FILE, None)
    error = error if isinstance(error, dict) else None
    users, warning = load_slack_users()
    result = slack.slack_post(run_failure_slack_text(failed_mode, issue, error, users, lead))
    note = f"{failed_mode or '?'} 실행 실패 알림"
    summ.row("-", "-", "failure", result, f"{note}; {warning}" if warning else note)
    summ.extra += [
        f"### 실행 실패 알림 (Slack {result})",
        "",
        "```",
        run_failure_slack_text(failed_mode, issue, error, {}, lead),
        "```",
        "",
    ]


def main() -> None:
    """명령줄 인자의 실행 모드(review, digest, alerts, report, decision, failure)대로 반영하거나 알린다.

    failure 모드를 뺀 모드에서 예외가 나면 out/_run_error.json에 원인을 남기고 예외를 다시 올린다.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", default="ctx")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mode", choices=["review", "digest", "alerts", "report", "decision", "failure"], required=True)
    ap.add_argument("--issue", default="", help="decision 모드의 대상 ticket 키. failure 모드는 실패한 ticket 키")
    ap.add_argument("--failed-mode", default="", help="failure 모드에서 실패한 실행 모드")
    ap.add_argument("--month", default="", help="report 모드의 보고 대상 달(YYYY-MM). 비우면 한국 시간 기준 지난달")
    ap.add_argument("--gate", default="false")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    args = ap.parse_args()

    ctx, out = Path(args.ctx), Path(args.out)
    gate = str(args.gate).lower() == "true"
    lead = os.environ.get("JIRA_LEAD_ACCOUNT_ID", "")
    summ = Summary(args.summary)

    if args.mode == "failure":  # 5단계: 앞 스텝 실패 알림. Jira에 접근하지 않는다
        failure_notice(out, args.failed_mode, args.issue.strip(), lead, summ)
        summ.flush()
        return

    try:
        if args.mode == "alerts":  # 주간 점검: Jira에 쓰지 않고 Slack으로만 보고
            slack_weekly(ctx, summ, lead)
            slack_token_expiry(summ)
            summ.flush()
            return

        if args.mode == "decision":  # 팀장 확인 결과 알림: ctx를 읽지 않고 Jira에서 그 ticket만 읽는다
            if not args.issue.strip():
                sys.exit("decision 모드에는 --issue가 필요하다")
            apply_decision(Jira(), args.issue.strip(), lead, summ)
            summ.flush()
            return

        if args.mode == "report":  # 정기 보고: ctx를 읽지 않고 run_report가 Jira 기록으로 본문을 만든다
            month = args.month or last_month(dt.datetime.now(KST).date())
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                sys.exit(f"--month는 YYYY-MM 형식이어야 한다: {month}")
            from src.jira.report.run import run_report

            texts = run_report(Jira(), month, lead, summ)  # 12월 보고는 월간, 분기, 연간 세 메시지다
            for i, text in enumerate(texts, 1):
                summ.row(
                    "-", "-", "report", slack.slack_post(text, LEAD_WEBHOOK_ENV), f"{month} 정기 보고 {i}/{len(texts)}"
                )
            summ.flush()
            return

        j = Jira()

        failures: list[dict] = []  # 반영하지 못한 Task. 끝에서 Slack 한 건으로 알린다
        for d in sorted(ctx.glob("[A-Z]*-[0-9]*/")):
            if (d / "issue.json").exists():
                try:
                    failure = apply_issue(j, d, out, args.mode, gate, lead, summ)
                except JiraError as e:
                    reason = f"Jira 오류: {str(e)[:160]}"
                    summ.row(d.name, "?", args.mode, "실패", reason)
                    record_failed_run(j, d, out, args.mode, summ)
                    issue = load_json(d / "issue.json", {})
                    failure = {
                        "key": d.name,
                        "title": issue.get("summary") or "",
                        "url": issue.get("url") or "",
                        "reason": reason,
                    }
                if failure:
                    failures.append(failure)

        failed = out / "_failed.txt"
        if failed.exists():
            summ.extra.append("### Claude 단계가 처리하지 못한 ticket")
            summ.extra.append("```\n" + failed.read_text(encoding="utf-8") + "\n```")
        # Actions Summary는 GitHub에 로그인한 누구나 볼 수 있어, Summary에는 빈 대응표로 다시 만든 본문을 남긴다
        if failures:
            users, warning = load_slack_users()
            result = slack.slack_post(failure_slack_text(args.mode, failures, users, lead))
            if result == "전송 완료":  # 전송에 실패하면 표시하지 않아 5단계가 다시 알린다
                out.mkdir(parents=True, exist_ok=True)
                (out / FAILURE_NOTIFIED_FILE).touch()  # 5단계가 같은 실패를 다시 알리지 않게 표시한다
            note = f"실패 알림 ticket {len(failures)}건"
            summ.row("-", "-", args.mode, result, f"{note}; {warning}" if warning else note)
            summ.extra += [
                f"### 실패 알림 (Slack {result})",
                "",
                "```",
                failure_slack_text(args.mode, failures, {}, lead),
                "```",
                "",
            ]
        summ.flush()
        if failures:
            sys.exit(1)

    except Exception as e:  # 5단계 실패 알림이 읽도록 원인을 남기고 다시 올린다. SystemExit은 잡지 않는다
        write_run_error(out, "4. 반영", e)
        raise


if __name__ == "__main__":
    main()
