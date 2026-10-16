"""결과 문서 샘플 생성. Jira, Slack에 쓰지 않는다.

src/jira/issue.py의 apply_issue, slack_weekly와 src/jira/slack.py의 token_expiry_text를 가짜 Jira 객체와 샘플 입력으로
실행하고, Jira에 쓰려던 comment와 필드 값, Slack에 보내려던 본문을 파일로 저장한다.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import types
from pathlib import Path
from unittest import mock

from src.common import github
from src.dev.samples.data import (
    BUG_CHANGELOG,
    BUG_CHECKS,
    BUG_DESC,
    BUG_DOC_CHECKS,
    BUG_DOC_DESC,
    BUG_DOC_SUMMARY,
    BUG_FEEDBACK,
    ISSUE_CHECKS,
    ISSUE_DESC,
    ISSUE_FEEDBACK,
    ISSUE_SUMMARY,
    LATE_REASON,
    PASS_ITEMS,
    PASS_SUMMARY,
    REQUEST_ITEMS,
    REQUEST_SUMMARY,
    STOP_CHANGES,
    TASK_CHECKS,
    TASK_DESC,
    TASK_EXTRA,
    TASK_FEEDBACK,
    TASK_ITEMS,
    TASK_REQUESTS,
    TASK_SUMMARY,
    TASK_TITLE,
)
from src.dev.samples.fake_jira import (
    ASSIGNEE,
    LEAD,
    SITE,
    FakeJira,
    agent_section,
    comment_url,
    write_issue,
    write_json,
    write_out,
)
from src.jira import issue, slack
from src.jira.precheck import history, run, template

FIXED_NOW = dt.datetime(2026, 9, 29, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))

# scripts/src/jira/precheck/의 검사 함수 묶음. precheck_checks와 테스트가 jp로 받는다
PRECHECK = types.SimpleNamespace(
    expected_items=template.expected_items,
    check_task_template=template.check_task_template,
    check_bug_template=template.check_bug_template,
    check_issue_template=template.check_issue_template,
    stop_events=history.stop_events,
    check_r4=history.check_r4,
    check_a2=history.check_a2,
    doc_facts=history.doc_facts,
    run_issue_dir=run.run_issue_dir,
    run_scan=run.run_scan,
)


class FixedDatetime(dt.datetime):
    """now()가 항상 FIXED_NOW를 돌려주는 datetime. 샘플의 날짜를 고정한다."""

    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> FixedDatetime:
        """고정 시각을 돌려준다.

        Args:
            tz: 시간대. 주면 그 시간대로 바꾼다.

        Returns:
            고정 시각.
        """
        t = cls.fromtimestamp(FIXED_NOW.timestamp(), FIXED_NOW.tzinfo)
        return t.astimezone(tz) if tz else t.replace(tzinfo=None)


def precheck_checks(jp: types.SimpleNamespace, itype: str, stops: list[dict] | None = None) -> dict:
    """샘플 task의 스크립트 검사 결과를 scripts/src/jira/precheck/의 검사 함수로 계산한다.

    Issue는 완료 기준 구역이 없어 A2를 계산하지 않는다(scripts/src/jira/precheck/run.py의 run_issue_dir과 같음).

    Args:
        jp: PRECHECK(precheck 검사 함수 묶음).
        itype: Task, Bug 또는 Issue.
        stops: precheck.stop_events의 결과. 없으면 stop 이력이 없는 것으로 R4를 계산한다.

    Returns:
        precheck.json의 checks.
    """
    if itype == "Task":
        checks = jp.check_task_template(TASK_DESC)
        checks.pop("expected")
        checks["A2"] = jp.check_a2([], "예상 산출물")
    elif itype == "Bug":
        checks = jp.check_bug_template(BUG_DESC, 2)
        checks["A2"] = jp.check_a2(BUG_CHANGELOG, "개선", "개선(To-Be)")
    else:
        checks = jp.check_issue_template(ISSUE_DESC)
    checks["R4"] = jp.check_r4(stops or [])
    return checks


def task_verdict(key: str, mode: str, verdict: str | None) -> dict:
    """샘플 Task의 verdict.json을 만든다.

    Args:
        key: task 키.
        mode: review 또는 digest.
        verdict: 판정 값. 정리 모드는 None.

    Returns:
        verdict.json 내용.
    """
    v = {
        "issueKey": key,
        "mode": mode,
        "verdict": verdict,
        "checks": TASK_CHECKS if mode == "review" else [],
        "items": TASK_ITEMS,
        "extra": TASK_EXTRA,
        "requests": TASK_REQUESTS,
    }
    if mode == "review":
        v["feedback"] = TASK_FEEDBACK
    return v


def run_issue(work: Path, key: str, mode: str, gate: bool) -> FakeJira:
    """apply_issue를 가짜 Jira로 실행한다.

    Args:
        work: ctx, out 폴더가 있는 작업 폴더.
        key: task 키.
        mode: review 또는 digest.
        gate: 게이트 모드 여부.

    Returns:
        쓰려던 값을 모은 FakeJira.
    """
    j = FakeJira({key: TASK_DESC})
    with contextlib.redirect_stdout(io.StringIO()):
        issue.apply_issue(
            j, work / "ctx" / key, work / "out", mode, gate, LEAD, github.Summary(str(work / "summary.md"))
        )
    return j


def render(out_dir: Path) -> list[Path]:
    """샘플을 만들어 out_dir에 저장한다.

    Args:
        out_dir: 샘플을 저장할 폴더.

    Returns:
        저장한 파일 목록.
    """
    jp = PRECHECK
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    sent: list[str] = []

    def save(name: str, text: str) -> None:
        p = out_dir / name
        p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        saved.append(p)

    # 시각, 실행 로그 주소, Slack 전송을 고정한다. 끝나면 모듈과 환경 변수를 원래대로 되돌린다
    with contextlib.ExitStack() as stack:
        work = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        ctx, out = work / "ctx", work / "out"
        users = work / "slack-users.json"
        slack_ids = {ASSIGNEE: "U000SAMPLE", LEAD: "U000LEAD"}
        write_json(users, slack_ids)
        fake_dt = types.SimpleNamespace(**{k: getattr(dt, k) for k in dir(dt) if not k.startswith("_")})
        fake_dt.datetime = FixedDatetime
        run_env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "everex-ai/.github",
            "GITHUB_RUN_ID": "123456",
            # 비워 두어 load_slack_users가 secret 대신 아래에서 바꾼 SLACK_USERS(임시 파일)를 읽게 한다
            "SLACK_USERS_JSON": "",
        }
        stack.enter_context(mock.patch.dict(os.environ, run_env))
        stack.enter_context(mock.patch.object(issue, "now_stamp", lambda: FIXED_NOW.strftime("%Y-%m-%d %H:%M")))
        stack.enter_context(mock.patch.object(issue, "dt", fake_dt))
        stack.enter_context(mock.patch.object(slack, "SLACK_USERS", users))
        stack.enter_context(mock.patch.object(slack, "slack_post", lambda text: sent.append(text) or "전송 완료"))

        # 검수 모드, 관찰 모드: 결과 산출물 구역, 검수 comment, 문서화 리뷰 comment, 보류 Slack 알림
        # (미달성 2번의 why가 "근거 부족으로 확인 불가"라 보류. 사유 없는 stop이 있어 요청 끝에 R4 요청 문장이 붙음)
        expected, task_checks = jp.expected_items(TASK_DESC), precheck_checks(jp, "Task")
        hold_stops = jp.stop_events(STOP_CHANGES, [], FIXED_NOW)
        hold_checks = precheck_checks(jp, "Task", hold_stops)
        write_issue(ctx, "INNO-17", "Task", expected, hold_checks, summary=TASK_TITLE, stops=hold_stops)
        write_out(out, "INNO-17", task_verdict("INNO-17", "review", "fix"), TASK_SUMMARY)
        j = run_issue(work, "INNO-17", "review", gate=False)
        save("deliverables-review.wiki", agent_section(j.fields["INNO-17"]["description"]))
        save("review-comment-task-observe.wiki", j.comments[0][1])
        save("doc-review-comment.wiki", j.comments[1][1])
        save("slack-review-hold.txt", sent[-1])

        # 검토 요청 Slack 알림: 미달성 항목마다 담당자가 남긴 사유가 있음
        write_issue(ctx, "INNO-21", "Task", expected, task_checks, summary=TASK_TITLE)
        reasons = [it["reason"] for it in REQUEST_ITEMS if it.get("reason")]
        request = task_verdict("INNO-21", "review", "escalate")
        request |= {"items": REQUEST_ITEMS, "extra": [], "requests": reasons}
        write_out(out, "INNO-21", request, REQUEST_SUMMARY)
        run_issue(work, "INNO-21", "review", gate=False)
        save("slack-review-request.txt", sent[-1])

        # 통과 검수 comment와 Slack 알림: 예상 산출물이 모두 달성이고, stop 사유를 늦게 남겨 R4가 "늦게 기록"임
        pass_stops = jp.stop_events(STOP_CHANGES, [LATE_REASON], FIXED_NOW)
        pass_checks = precheck_checks(jp, "Task", pass_stops)
        write_issue(ctx, "INNO-22", "Task", expected, pass_checks, summary=TASK_TITLE, stops=pass_stops)
        passed = task_verdict("INNO-22", "review", "pass") | {"items": PASS_ITEMS, "extra": [], "requests": []}
        write_out(out, "INNO-22", passed, PASS_SUMMARY)
        j = run_issue(work, "INNO-22", "review", gate=False)
        save("review-comment-task-pass.wiki", j.comments[0][1])
        save("slack-review-pass.txt", sent[-1])

        # 검수 모드, 게이트 모드: 문서화 리뷰가 검수 comment에 들어간다
        j = run_issue(work, "INNO-17", "review", gate=True)
        save("review-comment-task-gate.wiki", j.comments[0][1])

        # 정리 모드(F3): 사유가 기록에 없으면 사유 줄을 생략한다
        write_out(out, "INNO-17", task_verdict("INNO-17", "digest", None))
        j = run_issue(work, "INNO-17", "digest", gate=False)
        save("deliverables-digest.wiki", agent_section(j.fields["INNO-17"]["description"]))

        # Bug 검수: A2 실패로 보류
        write_issue(ctx, "INNO-30", "Bug", [], precheck_checks(jp, "Bug"))
        bug = {"issueKey": "INNO-30", "mode": "review", "verdict": "fix", "checks": BUG_CHECKS}
        bug |= {"items": [], "extra": [], "requests": ["해결 뒤 To-Be 동작을 확인한 comment 필요"]}
        write_out(
            out,
            "INNO-30",
            bug,
            "* 원인은 입력 해상도 검사 누락, 해결은 검사 추가로 2026-09-18 병합됨"
            "([PR #51 해상도 검사 추가|https://github.com/everex-ai/repo/pull/51])\n"
            "* 해결 뒤 To-Be 동작을 확인한 사람 comment 없음",
        )
        j = FakeJira({"INNO-30": BUG_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            issue.apply_issue(j, ctx / "INNO-30", out, "review", False, LEAD, github.Summary(str(work / "summary.md")))
        save("review-comment-bug-escalate.wiki", j.comments[0][1])

        # Bug 문서화 리뷰(관찰 모드): 검수 결과는 통과 그대로이고, B5–B7은 팀장용 문서화 리뷰 comment에만 들어감
        bug_doc_checks = jp.check_bug_template(BUG_DOC_DESC, 1)
        bug_doc_checks |= {"A2": jp.check_a2([], "개선", "개선(To-Be)"), "R4": jp.check_r4([])}
        write_issue(ctx, "INNO-33", "Bug", [], bug_doc_checks)
        bug_doc = {"issueKey": "INNO-33", "mode": "review", "verdict": "pass", "checks": BUG_DOC_CHECKS}
        bug_doc |= {"items": [], "extra": [], "requests": [], "feedback": BUG_FEEDBACK}
        write_out(out, "INNO-33", bug_doc, BUG_DOC_SUMMARY)
        j = FakeJira({"INNO-33": BUG_DOC_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            issue.apply_issue(j, ctx / "INNO-33", out, "review", False, LEAD, github.Summary(str(work / "summary.md")))
        save("review-comment-bug-pass.wiki", j.comments[0][1])
        save("doc-review-comment-bug.wiki", j.comments[1][1])

        # Issue 검수(관찰 모드): I3 보완 필요로 보류. 검사 표에 A2 행이 없고, I4와 I5는 문서화 리뷰 comment에만 들어감
        write_issue(ctx, "INNO-35", "Issue", [], precheck_checks(jp, "Issue"))
        issue_v = {"issueKey": "INNO-35", "mode": "review", "verdict": "fix", "checks": ISSUE_CHECKS}
        issue_v |= {
            "items": [],
            "extra": [],
            "requests": ["이관해서 만든 task 키 comment 필요"],
            "feedback": ISSUE_FEEDBACK,
        }
        write_out(out, "INNO-35", issue_v, ISSUE_SUMMARY)
        j = FakeJira({"INNO-35": ISSUE_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            issue.apply_issue(j, ctx / "INNO-35", out, "review", False, LEAD, github.Summary(str(work / "summary.md")))
        save("review-comment-issue-fix.wiki", j.comments[0][1])
        save("doc-review-comment-issue.wiki", j.comments[1][1])

        # 실패 알림(Slack): 판정 없음 기본값. Jira에는 comment를 남기지 않는다
        write_issue(
            ctx, "INNO-31", "Task", jp.expected_items(TASK_DESC), precheck_checks(jp, "Task"), summary=TASK_TITLE
        )
        # save_output.py가 구조화 출력이 없을 때 만드는 기본값 verdict.json(판정 없음)과 원인 파일
        default = task_verdict("INNO-31", "review", "no_verdict")
        default["items"] = [{"id": e["id"], "done": None, "result": ""} for e in jp.expected_items(TASK_DESC)]
        write_out(out, "INNO-31", default)
        (out / "INNO-31" / "_agent_error.txt").write_text(
            "Claude 문서화 단계 종료 종류(subtype): error_max_turns\n", encoding="utf-8"
        )
        j = FakeJira({"INNO-31": TASK_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            failure = issue.apply_issue(
                j, ctx / "INNO-31", out, "review", False, LEAD, github.Summary(str(work / "summary.md"))
            )
        save("failure-slack.txt", slack.failure_slack_text("review", [failure], slack_ids, LEAD))

        # 실패 알림(Slack): verdict.json이 형식에 맞지 않음(문서화 리뷰가 아닌 검사 ID로 feedback을 씀)
        bad = task_verdict("INNO-32", "review", "fix")
        bad["feedback"] = [{"id": "T8", "points": ["사유 누락"]}]
        write_issue(
            ctx, "INNO-32", "Task", jp.expected_items(TASK_DESC), precheck_checks(jp, "Task"), summary=TASK_TITLE
        )
        write_out(out, "INNO-32", bad)
        j = FakeJira({"INNO-32": TASK_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            failure = issue.apply_issue(
                j, ctx / "INNO-32", out, "review", False, LEAD, github.Summary(str(work / "summary.md"))
            )
        save("failure-slack-invalid.txt", slack.failure_slack_text("review", [failure], slack_ids, LEAD))

        # 누락 알림(정리 모드)과 주간 점검(Slack): precheck.run_scan이 만든 알림을 쓴다
        scan = ctx / "_scan"
        write_json(
            scan / "INNO-40.json",
            {
                "key": "INNO-40",
                "type": "Task",
                "status": "Backlog",
                "summary": "라벨 재수집",
                "url": f"{SITE}/browse/INNO-40",
                "created": "2026-09-01T10:00:00.000+0900",
                "assignee": {"accountId": ASSIGNEE, "displayName": "담당자A"},
                "humanComments": [],
                "statusChanges": [
                    {"created": "2026-09-02T10:00:00.000+0900", "field": "status", "to": "In Progress"},
                    {"created": "2026-09-22T10:00:00.000+0900", "field": "status", "to": "Backlog"},
                ],
            },
        )
        write_json(
            scan / "INNO-41.json",
            {
                "key": "INNO-41",
                "type": "Task",
                "status": "In Progress",
                "summary": "모델 경량화",
                "url": f"{SITE}/browse/INNO-41",
                "created": "2026-09-01T10:00:00.000+0900",
                "assignee": {"accountId": "acc-other", "displayName": "담당자B"},
                "humanComments": [{"created": "2026-09-15T10:00:00.000+0900", "url": comment_url("INNO-41", 1)}],
                "statusChanges": [{"created": "2026-09-02T10:00:00.000+0900", "field": "status", "to": "In Progress"}],
            },
        )
        # A3(ready-to-done 영업일 기준 3일 이상 대기): 팀장 멘션은 주간 점검 Slack에만 나가고 누락 알림 comment에는 없음
        write_json(
            scan / "INNO-42.json",
            {
                "key": "INNO-42",
                "type": "Task",
                "status": "ready-to-done",
                "summary": "추론 지연 개선",
                "url": f"{SITE}/browse/INNO-42",
                "created": "2026-09-01T10:00:00.000+0900",
                "assignee": {"accountId": ASSIGNEE, "displayName": "담당자A"},
                "humanComments": [],
                "statusChanges": [
                    {"created": "2026-09-02T10:00:00.000+0900", "field": "status", "to": "In Progress"},
                    {"created": "2026-09-23T14:00:00.000+0900", "field": "status", "to": "ready-to-done"},
                ],
            },
        )
        for p in sorted(scan.glob("INNO-*.json")):
            if not p.name.endswith(".alerts.json"):
                with contextlib.redirect_stdout(io.StringIO()):
                    jp.run_scan(p, FIXED_NOW)
        weekly_summ = github.Summary(str(work / "weekly-summary.md"))
        with contextlib.redirect_stdout(io.StringIO()):
            issue.slack_weekly(ctx, weekly_summ, LEAD)
        weekly_summ.flush()
        save("weekly-slack.txt", sent[-1])  # 검수 알림도 sent에 쌓이므로 마지막에 보낸 주간 점검 본문을 쓴다
        # Actions Summary 탭에 남기는 주간 점검 사본. Slack 멤버 ID 대신 Jira 표시 이름이 들어간다
        save("weekly-summary.md", (work / "weekly-summary.md").read_text(encoding="utf-8"))

        # 토큰(인증 키) 만료 알림(팀장 채널): 30일 이하 1건, 만료됨 1건, 30일 초과 1건(본문에서 빠짐)
        token_expiry = json.dumps(
            {"JIRA_API_TOKEN": "2026-10-20", "CLAUDE_CODE_OAUTH_TOKEN": "2026-09-20", "ORG_READ_TOKEN": "2027-09-15"}
        )
        save("token-expiry-slack.txt", slack.token_expiry_text(token_expiry, FIXED_NOW.date())[0])
    return saved
