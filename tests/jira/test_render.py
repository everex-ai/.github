"""jira-doc이 결과로 산출하는 문서 중 scripts/apply.py, scripts/src/jira/가 조립하는 부분의 형식.

scripts/apply.py는 scripts/review/의 같은 이름 파일과 섞이지 않게 모듈 이름 jira_apply로 불러온다
(tests/review/는 scripts/review/를 sys.path에 넣고 apply, precheck로 불러온다).
fixture를 conftest.py에 두지 않는 이유: tests/review/test_precheck.py가 `from conftest import ...`로
tests/review/conftest.py를 불러오므로, tests/jira/conftest.py가 있으면 모듈 이름 conftest가 겹친다.
그래서 scripts/src를 불러오기 위한 sys.path 설정도 이 파일에 둔다(맨 뒤에 넣어 scripts/review/보다 뒤에 둔다).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함


@pytest.fixture(scope="module")
def render():
    """scripts/src/dev/samples/render.py (모듈 이름 src.dev.samples.render)."""
    return importlib.import_module("src.dev.samples.render")


@pytest.fixture(scope="module")
def ja():
    """scripts/apply.py (모듈 이름 jira_apply). main을 실행하는 테스트가 사용한다."""
    name = "jira_apply"
    if name not in sys.modules:
        saved = list(sys.path)  # apply.py가 scripts/를 sys.path 맨 앞에 넣으므로 불러온 뒤 되돌린다
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "apply.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved
    return sys.modules[name]


@pytest.fixture(scope="module")
def jp(render):
    """scripts/src/jira/precheck/의 검사 함수 묶음."""
    return render.PRECHECK


@pytest.fixture(scope="module")
def jc():
    """scripts/src/jira/collect.py (모듈 이름 src.jira.collect). scripts/review/collect.py와 섞이지 않는다."""
    return importlib.import_module("src.jira.collect")


@pytest.fixture(scope="module")
def jw():
    """scripts/src/jira/wiki.py (모듈 이름 src.jira.wiki)."""
    return importlib.import_module("src.jira.wiki")


@pytest.fixture(scope="module")
def jrn():
    """scripts/src/jira/runs.py (모듈 이름 src.jira.runs)."""
    return importlib.import_module("src.jira.runs")


@pytest.fixture(scope="module")
def ji():
    """scripts/src/jira/issue.py (모듈 이름 src.jira.issue)."""
    return importlib.import_module("src.jira.issue")


@pytest.fixture(scope="module")
def jrr():
    """scripts/src/jira/report/run.py (모듈 이름 src.jira.report.run). report 모드가 지연 import하는 run_report를 가진다."""
    return importlib.import_module("src.jira.report.run")


@pytest.fixture(scope="module")
def jsl():
    """scripts/src/jira/slack.py (모듈 이름 src.jira.slack)."""
    return importlib.import_module("src.jira.slack")


@pytest.fixture(scope="module")
def jcm():
    """scripts/src/jira/comment.py (모듈 이름 src.jira.comment)."""
    return importlib.import_module("src.jira.comment")


@pytest.fixture(scope="module")
def jv():
    """scripts/src/jira/verdict.py (모듈 이름 src.jira.verdict)."""
    return importlib.import_module("src.jira.verdict")


@pytest.fixture(autouse=True)
def slack(jsl, monkeypatch, tmp_path_factory):
    """apply_issue가 Slack에 보내려던 본문을 모은다. 실제 Slack에는 보내지 않는다."""
    sent: list[str] = []
    users = tmp_path_factory.mktemp("slack") / "slack-users.json"
    users.write_text(json.dumps({"acc-assignee": "U-ASSIGNEE", "acc-lead": "U-LEAD"}), encoding="utf-8")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_USERS_JSON", raising=False)  # 대응표를 secret이 아니라 아래 임시 파일에서 읽게 한다
    monkeypatch.setattr(jsl, "SLACK_USERS", users)
    monkeypatch.setattr(jsl, "slack_post", lambda text: sent.append(text) or "전송 완료")
    return sent


FORMAL_ENDINGS = ("습니다", "주세요")

EXPECTED = [{"id": "1", "text": "재현 테스트 추가"}, {"id": "2", "text": "오류율 1% 미만"}]
ITEMS = [
    {"id": "1", "done": True, "result": "재현 테스트 3건 추가", "evidence": "[PR #42|https://example.com/42]"},
    {"id": "2", "done": False, "result": "수치만 있음", "why": "", "reason": "평가 리포트 링크 comment 필요"},
]
EXTRA = [{"result": "대시보드 추가", "why": "", "evidence": "[PR #45|https://example.com/45]"}]
REASONED = {**ITEMS[1], "why": "라벨 재수집이 다음 분기로 밀림([comment 2026-09-15|https://example.com/c/1])"}
NOT_A_REASON = {**ITEMS[1], "why": "근거 부족으로 확인 불가"}
R3_FAIL = [{"id": "R3", "result": "fail"}]  # 담당자가 고칠 수 있는 검사(OWNER_FIXABLE_CHECKS)가 아님
HOLD_TO_REQUEST = "미달성 항목마다 담당자가 남긴 사유가 있어 보류에서 검토 요청으로 바꿈"
PASS_TO_REQUEST = "미달성 항목마다 담당자가 남긴 사유가 있어 통과에서 검토 요청으로 바꿈"
TO_HOLD = "미달성 항목이 있어 통과에서 보류로 바꿈"
ISSUE_URL = "https://example.atlassian.net/browse/INNO-17"


def run_review(
    render,
    ja,
    work,
    verdict,
    items,
    status="ready-to-done",
    lead="acc-lead",
    tldr=None,
    mode="review",
    j=None,
    changelog=None,
    usage=None,
    agent_slack=None,
    checks=None,
):
    """INNO-17(Task)을 가짜 Jira로 실행하고 (FakeJira, Summary)를 돌려준다. 기본은 검수 모드.

    changelog를 주면 ctx/INNO-17/changelog.json에, usage를 주면 out/_usage.json에 쓴다.
    agent_slack은 verdict.json의 slack이다. 주지 않으면 빈 목록이다. checks는 precheck.json의 checks다.
    """
    ctx, out = work / "ctx", work / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, checks or {}, status=status)
    if changelog is not None:
        render.write_json(ctx / "INNO-17" / "changelog.json", changelog)
    if usage is not None:
        render.write_json(out / "_usage.json", usage)
    v = render.task_verdict("INNO-17", mode, verdict) | {"items": items, "extra": [], "slack": agent_slack or []}
    render.write_out(out, "INNO-17", v, "* 요약")
    if tldr is not None:
        (out / "INNO-17" / "tldr.wiki").write_text(tldr, encoding="utf-8")
    if j is None:
        j = render.FakeJira({"INNO-17": ""})
    summ = ja.Summary(str(work / "summary.md"))
    ja.apply_issue(j, ctx / "INNO-17", out, mode, False, lead, summ)
    return j, summ


def test_deliverables_request_line(jcm):
    body = jcm.deliverables_wiki(EXPECTED, ITEMS, EXTRA)
    assert body.startswith("예상 산출물 2개 중 1개 달성, 초과 달성 1건\n")  # 달성 요약을 맨 앞에 적음
    assert "#* 요청: 평가 리포트 링크 comment 필요" in body
    assert "필요한 것" not in body
    # 달성 항목에는 요청 줄이 없다
    assert body.split("# *오류율 1% 미만*")[0].count("#* 요청:") == 0


def test_deliverables_reason_lines_by_mode(jcm):
    review = jcm.deliverables_wiki(EXPECTED, ITEMS, EXTRA, final=True)
    assert "#* 미달성 사유: 사유 미기재" in review and "** 추가 사유: 사유 미기재" in review
    digest = jcm.deliverables_wiki(EXPECTED, ITEMS, EXTRA, final=False)  # F3 정리: 사유가 기록에 없으면 사유 줄 생략
    assert "미달성 사유" not in digest and "추가 사유" not in digest
    items = [ITEMS[0], {**ITEMS[1], "why": "라벨 재수집이 다음 분기로 밀림"}]
    assert "#* 미달성 사유: 라벨 재수집이 다음 분기로 밀림" in jcm.deliverables_wiki(EXPECTED, items, [], final=False)


def test_t8_names_items_and_request(jv):
    t8 = jv.check_t8(EXPECTED, ITEMS, EXTRA)
    assert t8["result"] == "fail"
    assert t8["detail"] == "사유 없는 미달성 항목 2번, 사유 없는 초과 달성 1건('대시보드 추가')"
    assert (
        t8["request"]
        == "미달성 항목 2번의 달성 근거 또는 미달성 사유, 초과 달성 '대시보드 추가'의 추가 사유 comment 필요"
    )
    feedback = [{"id": "T5", "points": [], "request": "3번 항목의 완료 기준 추가 필요"}]
    checks = [t8, {"id": "T5", "result": "fail"}, {"id": "T6", "result": "pass"}]
    assert jv.doc_requests(checks, feedback) == [t8["request"], "3번 항목의 완료 기준 추가 필요"]


def test_review_comment_puts_verdict_reasons_first(jcm):
    checks = [{"id": "A2", "result": "fail", "detail": "완료 기준 변경"}]
    text = jcm.review_comment(
        "escalate", checks, EXPECTED, ITEMS, EXTRA, "* 요약", ["평가 리포트 링크 comment 필요"], ["A2로 검토 요청"]
    )
    assert text.startswith("검수 결과: *검토 요청*\n* A2로 검토 요청\n\n예상 산출물 2개 중 1개 달성, 초과 달성 1건")
    assert text.index("*결과 요약*") < text.index("*요청*") < text.index("|| 검사 || 결과 || 내용 ||")


def test_doc_review_comment_first_line(jv, jcm):
    t8 = jv.check_t8(EXPECTED, ITEMS, EXTRA)
    text = jcm.doc_review_comment([t8], [], "acc-lead")
    assert text.startswith("문서화 리뷰 결과(관찰 모드): *보완 필요*\n\n|| 검사 || 결과 || 내용 ||")
    assert "*담당자에게 보낼 요청 후보*\n# 미달성 항목 2번의 달성 근거 또는 미달성 사유" in text
    assert text.endswith("\n\n[~accountid:acc-lead]")
    ok = jcm.doc_review_comment([{"id": "T4", "result": "pass", "detail": "a"}], [], "")
    assert ok.startswith("문서화 리뷰 결과(관찰 모드): *통과*\n\n")
    assert "요청 후보" not in ok and "accountid" not in ok
    assert jcm.doc_review_md("INNO-1", [t8], [])[0] == "### 문서화 리뷰 INNO-1(관찰 모드): 보완 필요"


@pytest.mark.parametrize(
    ("v", "itype", "items", "checks", "gate", "doc_fails", "want"),
    [
        ("pass", "Task", [ITEMS[0]], [], False, [], ("pass", None)),
        ("fix", "Task", [ITEMS[0], REASONED], [], False, [], ("escalate", HOLD_TO_REQUEST)),
        ("pass", "Task", [ITEMS[0], REASONED], [], False, [], ("escalate", PASS_TO_REQUEST)),
        ("fix", "Task", [ITEMS[0], REASONED], R3_FAIL, False, [], ("escalate", HOLD_TO_REQUEST)),
        ("fix", "Task", [ITEMS[0], REASONED], [], False, ["T5"], ("escalate", HOLD_TO_REQUEST)),
        ("fix", "Task", ITEMS, [], False, [], ("fix", None)),
        ("fix", "Task", [ITEMS[0], NOT_A_REASON], [], False, [], ("fix", None)),
        ("fix", "Task", [ITEMS[0], REASONED], [{"id": "T1", "result": "fail"}], False, [], ("fix", None)),
        ("fix", "Task", [ITEMS[0], REASONED], [], True, ["T5"], ("fix", None)),
        ("pass", "Task", ITEMS, [], False, [], ("fix", TO_HOLD)),
        ("pass", "Task", [ITEMS[0], NOT_A_REASON], [], False, [], ("fix", TO_HOLD)),
        ("pass", "Task", [ITEMS[0], REASONED], [{"id": "T1", "result": "fail"}], False, [], ("fix", TO_HOLD)),
        ("escalate", "Task", ITEMS, [], False, [], ("escalate", None)),
        ("fix", "Bug", [REASONED], [], False, [], ("fix", None)),
    ],
    ids=[
        "모두 달성이면 통과 유지",
        "사유 모두 있으면 보류를 검토 요청으로",
        "사유 모두 있으면 통과를 검토 요청으로",
        "담당자 검사가 아닌 R3 실패는 무시",
        "관찰 모드의 문서화 리뷰 보완 필요는 무시",
        "사유 없으면 보류 유지",
        "근거 부족으로 확인 불가는 사유가 아님",
        "담당자 검사 T1 실패면 보류 유지",
        "게이트 모드의 문서화 리뷰 보완 필요면 보류 유지",
        "통과인데 사유 없는 미달성이면 보류",
        "통과인데 근거 부족이면 보류",
        "통과이고 사유 모두 있어도 담당자 검사 실패면 보류",
        "검토 요청 유지",
        "Task가 아니면 그대로",
    ],
)
def test_recheck_verdict(jv, v, itype, items, checks, gate, doc_fails, want):
    assert jv.recheck_verdict(v, itype, items, checks, gate, doc_fails) == want


def test_hold_does_not_transition_and_mentions_lead_only(render, ja, tmp_path, slack):
    j, summ = run_review(render, ja, tmp_path, "fix", ITEMS, status="Ready-to-Done")
    assert "검수 결과: *보류*\n* 상태를 바꾸지 않고 팀장 확인을 요청함\n" in j.comments[0][1]
    assert slack[0] == (
        f"<@U-LEAD> [보류] <{ISSUE_URL}|INNO-17>\n"
        "• 예상 산출물 2개 중 1개 달성\n"
        f"<{ISSUE_URL}?focusedCommentId=10200|검수 comment>"
    )
    assert not any(e in slack[0] for e in FORMAL_ENDINGS)
    assert "Slack 알림 전송(팀장)" in summ.rows[0]


def test_hold_mentions_lead_by_name_when_not_in_slack_users(render, ja, tmp_path, slack):
    run_review(render, ja, tmp_path, "fix", ITEMS, lead="acc-unknown")
    assert slack[0].startswith("팀장 [보류] ")


def test_state_note_says_status_is_not_changed(ja, ji):
    assert ji.STATE_NOTE == "상태를 바꾸지 않고 팀장 확인을 요청함"
    assert not hasattr(ja, "review_transition") and not hasattr(ja, "IN_PROGRESS")


def test_request_keeps_status_and_mentions_lead(render, ja, tmp_path, slack):
    j, summ = run_review(render, ja, tmp_path, "fix", [ITEMS[0], REASONED])
    assert f"검수 결과: *검토 요청*\n* {HOLD_TO_REQUEST}\n* 상태를 바꾸지 않고 팀장 확인을 요청함\n" in j.comments[0][1]
    assert slack[0] == (
        f"<@U-LEAD> [검토 요청] <{ISSUE_URL}|INNO-17>\n"
        "• 미달성 1건, 담당자 사유 있음\n"
        "• 예상 산출물 2개 중 1개 달성\n"
        f"<{ISSUE_URL}?focusedCommentId=10200|검수 comment>"
    )
    assert not any(e in slack[0] for e in FORMAL_ENDINGS)
    assert "Slack 알림 전송(팀장)" in summ.rows[0]
    run_review(render, ja, tmp_path / "no-lead-id", "fix", [ITEMS[0], REASONED], lead="acc-unknown")
    assert slack[1].startswith("팀장 [검토 요청] ")


def test_pass_asks_lead_to_move_done(render, ja, tmp_path, slack):
    j, _ = run_review(render, ja, tmp_path, "pass", [ITEMS[0], {**ITEMS[1], "done": True}])
    assert "검수 결과: *통과*\n* 상태를 바꾸지 않고 팀장 확인을 요청함\n" in j.comments[0][1]
    assert slack[0] == (
        f"<@U-LEAD> [통과] <{ISSUE_URL}|INNO-17>\n"
        "• 예상 산출물 2개 중 2개 달성\n"
        f"<{ISSUE_URL}?focusedCommentId=10200|검수 comment>"
    )
    assert not any(e in slack[0] for e in FORMAL_ENDINGS)


def test_slack_failure_stays_in_summary(render, ja, jsl, tmp_path, monkeypatch):
    monkeypatch.setattr(jsl, "slack_post", lambda text: "전송 실패: HTTP Error 500")
    _, summ = run_review(render, ja, tmp_path, "fix", ITEMS)
    assert "Slack 보류 알림: 전송 실패: HTTP Error 500" in summ.rows[0]


SLACK_USERS_FORMAT_WARNING = "SLACK_USERS_JSON 형식이 틀려 Slack 멘션 없이 이름으로 보냄"
SLACK_USERS_EMPTY_WARNING = "Slack 멘션 대응표가 비어 멘션 없이 이름으로 보냄"


def write_weekly_scan(ctx):
    """ctx/_scan/에 주간 점검 알림 하나(INNO-40, 담당자 이름 담당자A, stop 사유 미기재)를 쓴다."""
    scan = ctx / "_scan"
    scan.mkdir(parents=True)
    alerts = {
        "key": "INNO-40",
        "summary": "라벨 재수집",
        "url": "https://example.atlassian.net/browse/INNO-40",
        "assignee": {"accountId": "acc-assignee", "displayName": "담당자A"},
        "alerts": [{"check": "R4", "code": "HOLD_REASON_MISSING:2026-09-08", "detail": "stop 사유 comment 필요"}],
    }
    (scan / "INNO-40.alerts.json").write_text(json.dumps(alerts, ensure_ascii=False), encoding="utf-8")


def run_weekly(ja, work, lead="", extra=()):
    """주간 점검을 실행하고 Actions Summary 파일에 쓴 글을 돌려준다.

    extra는 INNO-40 밖에 ctx/_scan/에 더 쓸 알림 파일 내용(*.alerts.json) 목록이고, lead는 팀장 accountId다.
    """
    write_weekly_scan(work / "ctx")
    for a in extra:
        (work / "ctx" / "_scan" / f"{a['key']}.alerts.json").write_text(json.dumps(a, ensure_ascii=False), "utf-8")
    summ = ja.Summary(str(work / "summary.md"))
    ja.slack_weekly(work / "ctx", summ, lead)
    summ.flush()
    return (work / "summary.md").read_text(encoding="utf-8")


def test_slack_users_env_comes_before_file(render, ja, tmp_path, monkeypatch, slack, capsys):
    monkeypatch.setenv("SLACK_USERS_JSON", json.dumps({"acc-assignee": "U-ENV", "acc-lead": "U-ENV-LEAD"}))
    assert ja.load_slack_users() == ({"acc-assignee": "U-ENV", "acc-lead": "U-ENV-LEAD"}, None)
    _, summ = run_review(render, ja, tmp_path, "fix", ITEMS)
    assert slack[0].startswith("<@U-ENV-LEAD> [보류] ")
    summ.flush()
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    # secret의 Slack 멤버 ID는 Slack 본문에만 들어가고 print, Summary 행, Summary 파일에는 나가지 않음
    assert "U-ENV" not in capsys.readouterr().out + "".join(summ.rows) + summary


@pytest.mark.parametrize("value", ["", "  \n", None], ids=["빈 문자열", "공백만", "설정 안 됨"])
def test_slack_users_empty_env_uses_file(render, ja, tmp_path, monkeypatch, slack, value):
    if value is None:
        monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
    else:
        monkeypatch.setenv("SLACK_USERS_JSON", value)
    assert ja.load_slack_users() == ({"acc-assignee": "U-ASSIGNEE", "acc-lead": "U-LEAD"}, None)
    run_review(render, ja, tmp_path, "fix", ITEMS)
    assert slack[0].startswith("<@U-LEAD> [보류] ")


@pytest.mark.parametrize(
    "value", ['{"acc-assignee": "U-SECRET"', '["U-SECRET"]'], ids=["JSON 형식 오류", "객체가 아님"]
)
def test_slack_users_invalid_env_sends_names_and_warns(render, ja, tmp_path, monkeypatch, slack, capsys, value):
    monkeypatch.setenv("SLACK_USERS_JSON", value)
    assert ja.load_slack_users() == ({}, SLACK_USERS_FORMAT_WARNING)
    # 검수 모드: 파일에 담당자 ID가 있어도 사용하지 않고, 담당자 이름 자리(이름이 없으면 "담당자 미지정")로 보냄
    _, summ = run_review(render, ja, tmp_path / "review", "fix", ITEMS)
    assert slack[0].startswith("팀장 [보류] ")
    assert SLACK_USERS_FORMAT_WARNING in summ.rows[0]
    # 주간 점검: Slack 본문에 멘션이 없고 담당자 이름이 있으며, 경고가 Summary에 남음
    summary = run_weekly(ja, tmp_path / "weekly")
    assert "<@" not in slack[-1] and "> 담당자A · " in slack[-1]
    assert SLACK_USERS_FORMAT_WARNING in summary
    assert "U-SECRET" not in summary + "".join(summ.rows) + capsys.readouterr().out  # secret 값을 출력하지 않음


def test_weekly_summary_copy_has_names_not_mentions(ja, tmp_path, monkeypatch, slack, capsys):
    monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
    summary = run_weekly(ja, tmp_path)
    head = slack[-1].splitlines()[0]
    assert head.endswith("전달사항] <@U-ASSIGNEE>")  # Slack 머리줄의 멘션 목록
    assert "<@U" in slack[-1] and "> <@U-ASSIGNEE> · " in slack[-1]
    copy = summary.split("### 주간 점검 (Slack 전송 완료)\n\n```\n")[1].split("\n```")[0]
    assert copy.splitlines()[0] == head.split("] ")[0] + "]"  # Summary 사본의 머리줄에는 멘션 목록이 없음
    assert "<@" not in summary and "> 담당자A · " in copy
    # 파일 대응표의 Slack 멤버 ID는 print와 Summary 파일(행과 사본)에 나가지 않음
    assert "U-ASSIGNEE" not in capsys.readouterr().out + summary


STALE_ALERTS = {
    "key": "INNO-41",
    "summary": "모델 경량화",
    "url": "https://example.atlassian.net/browse/INNO-41",
    "assignee": {"accountId": "acc-assignee", "displayName": "담당자A"},
    "alerts": [
        {
            "check": "A1",
            "code": "STALE:2026-09-15",
            "detail": "활동 없이 10영업일",
            "message": "진행 상황 comment 필요",
        }
    ],
}
READY_WAIT_DETAIL = "대기 4영업일"
READY_WAIT_ALERTS = {
    "key": "INNO-42",
    "summary": "추론 지연 개선",
    "url": "https://example.atlassian.net/browse/INNO-42",
    "assignee": {"accountId": "acc-assignee", "displayName": "담당자A"},
    "alerts": [
        {"check": "A3", "code": "READY_WAIT:2026-09-23", "detail": READY_WAIT_DETAIL, "message": "팀장 조치 필요"}
    ],
}


def test_weekly_message_sections_have_actions_and_a3_mentions_lead(ja, tmp_path, monkeypatch, slack):
    monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
    summary = run_weekly(ja, tmp_path, lead="acc-lead", extra=[STALE_ALERTS, READY_WAIT_ALERTS])
    lines = slack[-1].splitlines()
    assert lines[0].endswith("전달사항] <@U-ASSIGNEE> <@U-LEAD>")  # 팀장 멘션이 머리줄 멘션 목록에 들어감
    assert (
        "아래 task마다" not in slack[-1] and "아래 ticket마다" not in slack[-1]
    )  # 전체 조치 문구 줄 대신 절 제목마다 조치 문구를 붙임
    assert "*stop 사유 미기재* 1건" in lines  # R4는 절 제목이 할 일을 알려 주므로 조치 문구가 없음
    assert not any(line.startswith("*stop 사유 미기재*") and " — " in line for line in lines)
    assert "*영업일 기준 5일 이상 활동 없음* 1건 — 업데이트 요망" in lines
    assert "*ready-to-done 영업일 기준 3일 이상 대기* 1건 — 팀장: done 또는 In Progress 전환" in lines
    # ticket 줄: 제목에 링크, URL 줄과 "담당" 머리말 없음. A3 항목은 담당자가 Jira 표시 이름(멘션 아님), 팀장은 멘션
    link42 = "<https://example.atlassian.net/browse/INNO-42|INNO-42 추론 지연 개선>"
    link41 = "<https://example.atlassian.net/browse/INNO-41|INNO-41 모델 경량화>"
    assert f"    • {link42} 담당자A, 팀장 <@U-LEAD> · {READY_WAIT_DETAIL}" in lines
    assert f"    • {link41} <@U-ASSIGNEE> · " + STALE_ALERTS["alerts"][0]["detail"] in lines
    assert not any(line.lstrip().startswith("https://") for line in lines)  # URL 줄 없음
    assert "담당 " not in slack[-1]
    assert "U-LEAD" not in summary  # Actions Summary 사본에는 팀장의 Slack 멤버 ID도 나가지 않음
    assert f"    • {link42} 담당자A, 팀장(Slack 멘션 없음) · {READY_WAIT_DETAIL}" in summary


@pytest.mark.parametrize(
    ("lead", "want_text", "want_mentions"),
    [
        ("", "팀장 미지정", []),
        ("acc-unknown", "팀장(Slack 멘션 없음)", []),
        ("acc-lead", "팀장 <@U-LEAD>", ["<@U-LEAD>"]),
    ],
    ids=["lead 없음", "대응표에 없음", "대응표에 있음"],
)
def test_weekly_a3_lead_display(jsl, tmp_path, lead, want_text, want_mentions):
    (tmp_path / "INNO-42.alerts.json").write_text(json.dumps(READY_WAIT_ALERTS, ensure_ascii=False), "utf-8")
    lines, mentions = jsl.weekly_lines(tmp_path, {"acc-assignee": "U-ASSIGNEE", "acc-lead": "U-LEAD"}, lead)
    link = "<https://example.atlassian.net/browse/INNO-42|INNO-42 추론 지연 개선>"
    assert f"    • {link} 담당자A, {want_text} · {READY_WAIT_DETAIL}" in lines
    assert mentions == want_mentions  # A3 항목만 있으면 담당자는 멘션 목록에 들어가지 않음


@pytest.mark.parametrize(
    ("env", "file_table"),
    [("{}", "fixture"), ('{"_설명": "설명만 있음"}', "fixture"), ("", {"_설명": "설명만 있음"}), (None, None)],
    ids=["secret이 빈 객체", "secret에 설명만 있음", "secret이 비고 파일에 설명만 있음", "secret과 파일이 모두 없음"],
)
def test_slack_users_empty_table_warns(render, ja, jsl, tmp_path, monkeypatch, slack, env, file_table):
    if env is None:
        monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
    else:
        monkeypatch.setenv("SLACK_USERS_JSON", env)
    if file_table != "fixture":  # "fixture"는 fixture slack의 임시 파일(항목 2개)을 그대로 둠
        users = tmp_path / "slack-users.json"
        if file_table is not None:
            users.write_text(json.dumps(file_table, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(jsl, "SLACK_USERS", users)
    assert ja.load_slack_users() == ({}, SLACK_USERS_EMPTY_WARNING)
    _, summ = run_review(render, ja, tmp_path / "review", "fix", ITEMS)
    assert slack[0].startswith("팀장 [보류] ")
    assert SLACK_USERS_EMPTY_WARNING in summ.rows[0]
    summary = run_weekly(ja, tmp_path / "weekly")
    assert "<@" not in slack[-1] and "> 담당자A · " in slack[-1]
    assert SLACK_USERS_EMPTY_WARNING in summary


@pytest.mark.parametrize("source", ["env", "file"])
def test_slack_users_skips_description_and_non_string_values(ja, jsl, tmp_path, monkeypatch, source):
    table = {"_설명": "Jira accountId -> Slack 멤버 ID", "acc-assignee": "U-ASSIGNEE", "acc-num": 7, "acc-list": ["U1"]}
    table["acc-null"] = None
    if source == "env":
        monkeypatch.setenv("SLACK_USERS_JSON", json.dumps(table, ensure_ascii=False))
    else:
        monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
        (tmp_path / "slack-users.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(jsl, "SLACK_USERS", tmp_path / "slack-users.json")
    assert ja.load_slack_users() == ({"acc-assignee": "U-ASSIGNEE"}, None)


@pytest.mark.parametrize("prefix", ["검수 결과: ", "판정: "], ids=["새 접두어", "이전 접두어"])
def test_tldr_verdict_follows_changed_result(render, ja, tmp_path, prefix):
    tldr = f"h2. Summary\n* 결과\n* {{color:#6b778c}}상태: ready-to-done, {prefix}보류, 갱신: 2026-09-29 09:00{{color}}"
    j, _ = run_review(render, ja, tmp_path, "fix", [ITEMS[0], REASONED], tldr=tldr)
    field = j.fields["INNO-17"][render.FakeJira.tldr_field]
    assert f"{prefix}검토 요청, 갱신" in field and f"{prefix}보류" not in field


def test_wiki_to_slack(jsl):
    text = "{color:#6b778c}근거: [PR #42|https://example.com/42], [comment|https://e.com/c?focusedCommentId=1]{color}"
    assert (
        jsl.wiki_to_slack(text) == "근거: <https://example.com/42|PR #42>, <https://e.com/c?focusedCommentId=1|comment>"
    )
    assert jsl.wiki_to_slack("[~accountid:acc-lead] 확인 필요") == "[~accountid:acc-lead] 확인 필요"
    assert jsl.wiki_to_slack("지연 < 50ms & [PR|https://e.com/?a=1&b=2]") == (
        "지연 &lt; 50ms &amp; <https://e.com/?a=1&amp;b=2|PR>"
    )


def test_slack_text_escapes_title(jsl):
    text = jsl.review_slack_text("fix", "INNO-1", "지연 <50ms & 경량화", "https://e.com/INNO-1", "담당자", [], "")
    assert text == "담당자 [보류] <https://e.com/INNO-1|INNO-1 지연 &lt;50ms &amp; 경량화>"


def test_slack_post_reports_timeout(jsl, monkeypatch):
    monkeypatch.undo()  # autouse fixture slack이 바꾼 slack_post를 원래 함수로 되돌린다
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.com/x")

    def timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(jsl.urllib.request, "urlopen", timeout)
    assert jsl.slack_post("본문") == "전송 실패: timed out"


def test_slack_post_invalid_url_does_not_raise_or_leak(jsl, monkeypatch):
    """webhook 값이 URL 형식이 아니면 예외 없이 실패 문구를 돌려준다. public 로그에 남지 않게 값은 적지 않는다."""
    monkeypatch.undo()  # autouse fixture slack이 바꾼 slack_post를 원래 함수로 되돌린다
    monkeypatch.setenv("SLACK_LEAD_WEBHOOK_URL", "<지금 SLACK_WEBHOOK_URL과 같은 URL>")
    result = jsl.slack_post("본문", "SLACK_LEAD_WEBHOOK_URL")
    assert result == "전송 실패: SLACK_LEAD_WEBHOOK_URL 값이 URL 형식이 아님"


def test_comment_failure_keeps_state_note_and_slack(render, ja, tmp_path, slack):
    j = render.FakeJira({"INNO-17": ""})

    def fail(key, body):
        raise ja.JiraError("POST /issue/INNO-17/comment -> 500: boom")

    j.add_comment = fail
    _, summ = run_review(render, ja, tmp_path, "fix", ITEMS, j=j)
    assert "상태를 바꾸지 않고 팀장 확인을 요청함" in summ.rows[0]
    assert "검수 comment 등록 실패: POST /issue/INNO-17/comment -> 500: boom" in summ.rows[0]
    assert slack[0].startswith("<@U-LEAD> [보류] ")
    assert "검수 comment" not in slack[0]


def test_no_comment_id_means_no_evidence_line(render, ja, tmp_path, slack):
    j = render.FakeJira({"INNO-17": ""})
    j.add_comment = lambda key, body: j.comments.append((key, body))  # 반환값 없음
    run_review(render, ja, tmp_path, "fix", ITEMS, j=j)
    assert "검수 comment" not in slack[0]


def test_digest_mode_has_no_transition_or_slack(render, ja, tmp_path, slack):
    tldr = "h2. Summary\n* 결과\n* {color:#6b778c}상태: In Progress, 갱신: 2026-09-29 09:00{color}"
    j, _ = run_review(render, ja, tmp_path, None, ITEMS, tldr=tldr, mode="digest")
    assert slack == [] and j.comments == []
    assert j.fields["INNO-17"][render.FakeJira.tldr_field] == tldr


def test_jira_has_no_transition_to(ja):
    assert not hasattr(ja.Jira, "transition_to")  # 자동 보류 전환을 없앴으므로 전환 함수도 없음


def test_failure_slack_text(ja, monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    failures = [{"key": "INNO-17", "title": "A & B", "url": ISSUE_URL, "reason": "CI agent 판정 없음. 원인: x"}]
    users = {"acc-lead": "U-LEAD"}
    assert ja.failure_slack_text("review", failures, users, "acc-lead") == (
        "[jira-doc 실패] 검수 모드 ticket 1건 처리 실패 <@U-LEAD>\n"
        f"• <{ISSUE_URL}|INNO-17 A &amp; B>: CI agent 판정 없음. 원인: x\n"
        "GitHub Actions 실행: <https://github.com/org/repo/actions/runs/1|실행 로그>"
    )
    monkeypatch.delenv("GITHUB_RUN_ID")
    no_url = [{"key": "INNO-9", "title": "제목", "url": "", "reason": "Jira 오류: x"}]
    assert ja.failure_slack_text("digest", no_url, {}, "acc-lead") == (
        "[jira-doc 실패] 정리 모드 ticket 1건 처리 실패 팀장(Slack 멘션 없음)\n• INNO-9 제목: Jira 오류: x"
    )


def test_validate_verdict_message_names_doc_review_checks(jv):
    v = {"issueKey": "INNO-1", "mode": "review", "verdict": "pass", "checks": [], "items": [], "extra": []}
    v |= {"requests": [], "feedback": [{"id": "T8", "points": []}]}  # T8은 apply.py가 계산하므로 feedback에 쓸 수 없음
    err = jv.validate_verdict(v, "review", "INNO-1")
    assert (
        "진행 배경 충실도 (T4), 예상 산출물 분할 단위 (T5), 진행 기록 comment (T6), sub-task 기록 (T7), "
        "진행 배경부터 결과 산출물까지의 연결 (T9), 문제(As-Is) 재현 정보 (B5), 원인 분석 기록 (B6), 해결 확인 기록 (B7), "
        "이슈 내용 충실도 (I4), 논의와 결정 기록 (I5) 중 하나"
    ) in err
    assert "~" not in err


@pytest.mark.parametrize("cid", ["B5", "B6", "B7", "I4", "I5"])
def test_validate_verdict_accepts_bug_and_issue_feedback(jv, cid):
    v = {"issueKey": "INNO-1", "mode": "review", "verdict": "pass", "checks": [], "items": [], "extra": []}
    v |= {"requests": [], "feedback": [{"id": cid, "points": ["a"], "request": "b 필요"}]}
    assert jv.validate_verdict(v, "review", "INNO-1") is None


def test_alert_messages(jp, tmp_path):
    now = dt.datetime(2026, 9, 29, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    scan = {
        "key": "INNO-40",
        "type": "Task",
        "status": "Backlog",
        "created": "2026-09-01T10:00:00.000+0900",
        "humanComments": [],
        "statusChanges": [{"created": "2026-09-22T10:00:00.000+0900", "field": "status", "to": "Backlog"}],
    }
    p = tmp_path / "INNO-40.json"
    p.write_text(json.dumps(scan), encoding="utf-8")
    jp.run_scan(p, now)
    alerts = json.loads((tmp_path / "INNO-40.alerts.json").read_text(encoding="utf-8"))["alerts"]
    assert [a["check"] for a in alerts] == ["R4"]
    assert alerts[0]["message"].endswith("comment 필요")


def test_rendered_samples_have_no_formal_endings(render, tmp_path):
    files = render.render(tmp_path)
    assert {p.name for p in files} >= {
        "deliverables-review.wiki",
        "deliverables-digest.wiki",
        "review-comment-task-observe.wiki",
        "doc-review-comment.wiki",
        "failure-slack.txt",
        "failure-slack-invalid.txt",
        "token-expiry-slack.txt",
        "weekly-slack.txt",
        "slack-review-hold.txt",
        "slack-review-request.txt",
        "slack-review-pass.txt",
        "review-comment-bug-pass.wiki",
        "doc-review-comment-bug.wiki",
        "review-comment-issue-fix.wiki",
        "doc-review-comment-issue.wiki",
    }
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert not any(e in text for e in FORMAL_ENDINGS), p.name
        assert "~" not in text.replace("[~accountid:", ""), p.name


def test_render_restores_module_state(render, jsl, ji, tmp_path):
    before = (ji.now_stamp, ji.dt, jsl.slack_post, jsl.SLACK_USERS)
    render.render(tmp_path)
    assert (ji.now_stamp, ji.dt, jsl.slack_post, jsl.SLACK_USERS) == before


def test_sources_have_no_formal_endings():
    paths = [
        ROOT / "scripts" / "apply.py",
        ROOT / "scripts" / "precheck.py",
        *(ROOT / "scripts" / "src").glob("**/*.py"),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not any(e in text for e in FORMAL_ENDINGS), path.name


def test_deliverables_summary_counts_expected_ids_only(jcm):
    items = [*ITEMS, {"id": "9", "done": True, "result": "번호 밖 결과"}]
    assert jcm.deliverables_summary(EXPECTED, items, []) == "예상 산출물 2개 중 1개 달성"


def test_short_name_strips_wiki_link_and_truncates(jw):
    assert jw.short_name("[PR #46 오류 로그|https://example.com/46] 추가") == "PR #46 오류 로그 추가"
    assert jw.short_name("가" * 50) == "가" * 40 + "…"


def test_a2_detail(jp):
    changelog = [
        {"field": "status", "to": "Ready-to-Done", "created": "2026-09-19T17:40:00.000+0900"},
        {
            "field": "description",
            "from": "h2. 개선\n* a",
            "to": "h2. 개선\n* b",
            "created": "2026-09-20T10:12:00.000+0900",
        },
    ]
    a2 = jp.check_a2(changelog, "개선")
    assert a2["result"] == "fail"
    assert a2["detail"].startswith("2026-09-20 10:12에 '개선' 구역이 바뀜 (마지막 request 전환(")
    assert a2["detail"].endswith(") 2026-09-19 17:40 이후)")
    assert jp.check_a2([], "개선")["detail"].startswith("request 전환(")


BUG_INFO = {
    "발생 기기/서비스": "포즈 추정 앱",
    "발생 일자": "2026-09-15",
    "발생 장비": "Galaxy S24",
    "발생 계정": "qa01",
}
PROBLEM, TOBE = "결과 파일 export가 오류로 실패함", "export가 오류 없이 끝남"
LINES_NOTE = "(안내문과 빈 줄을 뺀 줄 수)"


def bug_desc(info: dict[str, str], problem: str, tobe: str) -> str:
    """Bug의 description field 구성(기본 정보, 문제(As-Is), 개선(To-Be), 첨부 자료(필수))으로 description을 만든다.

    빈 값은 템플릿처럼 "발생 일자: "로 남긴다.
    """
    lines = ["h2. 기본 정보", *(f"* {k}: {v}" for k, v in info.items()), ""]
    lines += ["h2. 문제(As-Is)", f"* {problem}", "", "h2. 개선(To-Be)", f"* {tobe}", ""]
    lines += ["h2. 첨부 자료(필수)", "* (사진 및 영상 첨부)"]
    return "\n".join(lines)


def test_bug_template_filled_passes(jp):
    checks = jp.check_bug_template(bug_desc(BUG_INFO, PROBLEM, TOBE), 1)
    assert checks["B1"] == {
        "result": "pass",
        "detail": f"기본 정보 항목 4개(발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정) 모두 채워짐, "
        f"문제(As-Is) 내용 1줄{LINES_NOTE}, 첨부 파일 1개",
    }
    assert checks["B2"] == {"result": "pass", "detail": f"개선(To-Be) 내용 1줄{LINES_NOTE}"}


@pytest.mark.parametrize(
    ("desc", "attachments", "want_in_detail"),
    [
        (bug_desc(BUG_INFO | {"발생 장비": ""}, PROBLEM, TOBE), 1, "기본 정보 중 비어 있는 항목 1개(발생 장비),"),
        (bug_desc(BUG_INFO, "(오류 동작에 대한 설명)", TOBE), 1, f"문제(As-Is) 내용 0줄{LINES_NOTE}"),
        (bug_desc(BUG_INFO, PROBLEM, TOBE), 0, "첨부 파일 0개"),
        (
            "h2. 현황\n* 발생 기기/서비스: 포즈 추정 앱\n* 발생 일자: 2026-09-15\n* 발생 장비: Galaxy S24\n"
            "* 발생 계정: qa01\n* 발생 내용: export 실패\n\nh2. 개선\n* export가 오류 없이 끝남",
            1,
            "기본 정보 중 비어 있는 항목 4개(발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정),",
        ),
    ],
    ids=["기본 정보 항목 비어 있음", "문제(As-Is) 안내문만 있음", "첨부 없음", "이전 description field 구성"],
)
def test_bug_template_b1_fails(jp, desc, attachments, want_in_detail):
    b1 = jp.check_bug_template(desc, attachments)["B1"]
    assert b1["result"] == "fail"
    assert want_in_detail in b1["detail"]


def test_bug_template_b2_fails_with_placeholder_only(jp):
    b2 = jp.check_bug_template(bug_desc(BUG_INFO, PROBLEM, "(정상 동작에 대한 설명)"), 1)["B2"]
    assert b2 == {"result": "fail", "detail": f"개선(To-Be) 내용 0줄{LINES_NOTE}"}


def test_bug_sample_passes_template_and_fails_a2(render, jp):
    """INNO-30 샘플(scripts/src/dev/samples/data.py)은 B1, B2를 통과하고, 개선(To-Be)이 바뀌어 A2가 fail이다."""
    checks = render.precheck_checks(jp, "Bug")
    assert (checks["B1"]["result"], checks["B2"]["result"], checks["A2"]["result"]) == ("pass", "pass", "fail")
    assert "에 '개선(To-Be)' 구역이 바뀜" in checks["A2"]["detail"]


def test_bug_run_issue_dir_names_tobe_section_in_a2(render, jp, tmp_path):
    """운영 경로(run_issue_dir)의 Bug A2 내용 칸도 구역 이름을 "개선(To-Be)"로 적는다."""
    d = tmp_path / "INNO-30"
    d.mkdir()
    issue = {"key": "INNO-30", "type": "Bug", "status": "Ready-to-Done", "attachments": [{}, {}]}
    for name, data in [("issue.json", issue), ("comments.json", []), ("changelog.json", render.BUG_CHANGELOG)]:
        (d / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (d / "description.wiki").write_text(render.BUG_DESC, encoding="utf-8")
    jp.run_issue_dir(d, render.FIXED_NOW)
    checks = json.loads((d / "precheck.json").read_text(encoding="utf-8"))["checks"]
    assert checks["B1"]["result"] == "pass"
    assert "에 '개선(To-Be)' 구역이 바뀜" in checks["A2"]["detail"]


@pytest.mark.parametrize(
    "changed",
    [
        bug_desc(BUG_INFO | {"발생 장비": "Galaxy S25"}, PROBLEM, TOBE),
        bug_desc(BUG_INFO, "결과 파일 export가 멈춤", TOBE),
    ],
    ids=["기본 정보 값", "문제(As-Is) 내용"],
)
def test_bug_human_input_hash_covers_new_sections(jc, changed):
    """정리 모드가 Bug를 건너뛸지 정하는 해시가 기본 정보와 문제(As-Is)의 변경을 반영한다."""
    base = jc.human_input_hash("Bug", bug_desc(BUG_INFO, PROBLEM, TOBE), [], [], [], [])
    assert jc.human_input_hash("Bug", changed, [], [], [], []) != base


def test_stale_alert_message_and_stop_detail(jp, tmp_path):
    now = dt.datetime(2026, 9, 29, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    scans = {
        "INNO-40": {
            "status": "Backlog",
            "statusChanges": [{"created": "2026-09-22T10:00:00.000+0900", "to": "Backlog"}],
        },
        "INNO-41": {
            "status": "In Progress",
            "statusChanges": [{"created": "2026-09-02T10:00:00.000+0900", "to": "In Progress"}],
        },
    }
    alerts = {}
    for key, extra in scans.items():
        p = tmp_path / f"{key}.json"
        scan = {"key": key, "type": "Task", "created": "2026-09-01T10:00:00.000+0900", "humanComments": [], **extra}
        p.write_text(json.dumps(scan), encoding="utf-8")
        jp.run_scan(p, now)
        alerts[key] = json.loads((tmp_path / f"{key}.alerts.json").read_text(encoding="utf-8"))["alerts"]
    assert alerts["INNO-40"][0]["detail"] == "stop 뒤 6일"
    stale = alerts["INNO-41"][0]
    assert stale["check"] == "A1" and stale["detail"] == "활동 없이 19영업일"
    assert stale["message"].count(". ") == 1 and stale["message"].endswith("필요")


def test_weekly_message_elapsed_phrases_and_no_url_link(jp, jsl, tmp_path):
    """run_scan이 만든 알림으로 만든 주간 점검 메시지의 경과 문구 3종. URL이 없으면 링크 없이 "키 제목"을 적는다."""
    now = dt.datetime(2026, 9, 29, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    scans = {
        "INNO-40": (
            "Backlog",
            [("2026-09-22T10:00:00.000+0900", "Backlog")],
            "https://example.atlassian.net/browse/INNO-40",
        ),
        "INNO-41": ("In Progress", [("2026-09-02T10:00:00.000+0900", "In Progress")], None),
        "INNO-42": (
            "ready-to-done",
            [("2026-09-24T15:00:00.000+0900", "ready-to-done")],
            "https://example.atlassian.net/browse/INNO-42",
        ),
    }
    for key, (status, changes, url) in scans.items():
        scan = {
            "key": key,
            "type": "Task",
            "status": status,
            "summary": "제목",
            "url": url,
            "created": "2026-09-01T10:00:00.000+0900",
            "assignee": {"accountId": "acc-assignee", "displayName": "담당자A"},
            "humanComments": [],
            "statusChanges": [{"created": c, "field": "status", "to": to} for c, to in changes],
        }
        (tmp_path / f"{key}.json").write_text(json.dumps(scan), encoding="utf-8")
        jp.run_scan(tmp_path / f"{key}.json", now)
    lines, _ = jsl.weekly_lines(tmp_path, {})
    assert lines == [
        "",
        "*stop 사유 미기재* 1건",
        "    • <https://example.atlassian.net/browse/INNO-40|INNO-40 제목> 담당자A · stop 뒤 6일",
        "",
        "*영업일 기준 5일 이상 활동 없음* 1건 — 업데이트 요망",
        "    • INNO-41 제목 담당자A · 활동 없이 19영업일",
        "",
        "*ready-to-done 영업일 기준 3일 이상 대기* 1건 — 팀장: done 또는 In Progress 전환",
        "    • <https://example.atlassian.net/browse/INNO-42|INNO-42 제목> 담당자A, 팀장 미지정 · 대기 3영업일",
    ]


def scan_ready_wait(jp, tmp_path, status, changes):
    """INNO-42의 주간 점검 데이터로 run_scan을 2026-09-29(화) 09:00에 실행하고 알림 목록을 돌려준다."""
    now = dt.datetime(2026, 9, 29, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    scan = {
        "key": "INNO-42",
        "type": "Task",
        "status": status,
        "created": "2026-09-01T10:00:00.000+0900",
        "humanComments": [],
        "statusChanges": [{"created": created, "field": "status", "to": to} for created, to in changes],
    }
    p = tmp_path / "INNO-42.json"
    p.write_text(json.dumps(scan), encoding="utf-8")
    jp.run_scan(p, now)
    return json.loads((tmp_path / "INNO-42.alerts.json").read_text(encoding="utf-8"))["alerts"]


def test_ready_wait_alert_after_3_business_days(jp, tmp_path):
    # 예전 진입(2026-09-10) 뒤 In Progress로 돌아갔다가 2026-09-24(목)에 다시 진입. 마지막 진입부터 셈
    changes = [
        ("2026-09-10T10:00:00.000+0900", "ready-to-done"),
        ("2026-09-11T10:00:00.000+0900", "In Progress"),
        ("2026-09-24T15:00:00.000+0900", "ready-to-done"),
    ]
    alerts = scan_ready_wait(jp, tmp_path, "ready-to-done", changes)
    assert [(a["code"], a["check"]) for a in alerts] == [("READY_WAIT:2026-09-24", "A3")]
    assert alerts[0]["detail"] == "대기 3영업일"
    assert alerts[0]["message"].endswith("팀장의 done 전환 또는 In Progress 전환 필요")


def test_ready_wait_no_alert_after_2_business_days(jp, tmp_path):
    # 2026-09-25(금) 진입 뒤 월, 화 2영업일
    assert scan_ready_wait(jp, tmp_path, "ready-to-done", [("2026-09-25T10:00:00.000+0900", "ready-to-done")]) == []


def test_ready_wait_no_alert_after_reentry_2_business_days_ago(jp, tmp_path):
    # 예전 진입(2026-09-10)부터는 3영업일이 넘지만, 마지막 진입(2026-09-25(금))부터는 2영업일
    changes = [
        ("2026-09-10T10:00:00.000+0900", "ready-to-done"),
        ("2026-09-11T10:00:00.000+0900", "In Progress"),
        ("2026-09-25T10:00:00.000+0900", "ready-to-done"),
    ]
    assert scan_ready_wait(jp, tmp_path, "ready-to-done", changes) == []


def test_ready_wait_no_alert_outside_ready_to_done(jp, tmp_path):
    changes = [("2026-09-24T15:00:00.000+0900", "ready-to-done"), ("2026-09-25T10:00:00.000+0900", "Done")]
    assert scan_ready_wait(jp, tmp_path, "Done", changes) == []


def test_t3_mismatch_detail(render, ja, tmp_path, slack):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    v = render.task_verdict("INNO-17", "review", "pass")
    v["items"] = [ITEMS[0]]
    render.write_out(out, "INNO-17", v, "* 요약")
    j = render.FakeJira({"INNO-17": ""})
    ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "", ja.Summary(str(tmp_path / "summary.md")))
    body = j.comments[0][1]
    assert "검수 결과: *검토 요청*" in body
    assert "예상 산출물 번호(1, 2)와 CI agent 출력 번호(1)가 다름. CI agent 출력 오류로 간주해 검토 요청" in body
    assert (
        "* 예상 산출물과 결과 산출물의 1:1 대응 (T3) 결과에 따라 통과에서 검토 요청으로 바꿈: "
        "예상 산출물과 결과 산출물의 번호가 맞지 않음"
    ) in body
    assert "\n• 예상 산출물 번호가 CI agent 출력과 다름\n" in slack[0]  # Slack 요점에는 짧은 꼴


def test_check_table_uses_same_labels_for_all_checks(jcm):
    checks = [{"id": "T1", "result": "pass", "detail": "a"}, {"id": "T5", "result": "fail", "detail": "b"}]
    checks.append({"id": "A2", "result": "n/a", "detail": "c"})
    table = jcm.check_table(checks)
    assert "| 예상 산출물 작성 (T1) | ✅ 만족 | a |" in table
    assert "| 예상 산출물 분할 단위 (T5) | ❌ 보완 필요 | b |" in table
    assert "| 완료 기준의 사후 변경 (A2) | ➖ 해당 없음 | c |" in table
    assert "통과" not in table and "실패" not in table


def test_check_table_keeps_wiki_links_in_cells(jcm):
    link = "[stop 사유 comment 2026-09-25|https://e.com/c?focusedCommentId=1]"
    checks = [{"id": "R4", "result": "pass", "detail": f"a|b {link} c|d"}, {"id": "T5", "result": "fail"}]
    feedback = [{"id": "T5", "points": ["[PR #1|https://e.com/1] 링크 밖 x|y", "둘째 줄"]}]
    table = jcm.check_table(checks, feedback)
    assert f"| stop 사유 comment (R4) | ✅ 만족 | a/b {link} c/d |" in table  # 링크 안의 "|"는 그대로 둠
    assert "| 예상 산출물 분할 단위 (T5) | ❌ 보완 필요 | [PR #1|https://e.com/1] 링크 밖 x/y \\\\ 둘째 줄 |" in table


def test_verdict_change_notes_start_with_check_name(render, tmp_path):
    files = {p.name: p.read_text(encoding="utf-8") for p in render.render(tmp_path)}
    bug = files["review-comment-bug-escalate.wiki"].splitlines()
    assert bug[2].startswith(
        "* 완료 기준의 사후 변경 (A2) 결과에 따라 보류에서 검토 요청으로 바꿈: 마지막 request 전환("
    )
    assert "실패" not in files["review-comment-bug-escalate.wiki"]  # 검사 표와 같은 이름(보완 필요)을 사용함
    gate = files["review-comment-task-gate.wiki"]
    # 검수 결과가 이미 보류라 바뀌지 않음
    assert "* 문서화 리뷰에 보완 필요 항목이 있어 통과에서 보류로 바꿈: " not in gate
    assert "(T3)" not in files["review-comment-task-observe.wiki"].split("*검사 항목*")[0]


def test_gate_note_names_doc_review_checks(render, ja, tmp_path, slack):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    v = render.task_verdict("INNO-17", "review", "pass")
    v["items"] = [{**ITEMS[0]}, {**ITEMS[1], "done": True}]
    v["extra"] = []
    render.write_out(out, "INNO-17", v, "* 요약")
    j = render.FakeJira({"INNO-17": ""})
    ja.apply_issue(j, ctx / "INNO-17", out, "review", True, "", ja.Summary(str(tmp_path / "summary.md")))
    body = j.comments[0][1]
    assert (
        "* 문서화 리뷰에 보완 필요 항목이 있어 통과에서 보류로 바꿈: 예상 산출물 분할 단위 (T5), 진행 기록 comment (T6)"
        in body
    )
    assert body.index("(T5)") < body.index("*검사 항목*")
    assert "\n• 예상 산출물 분할 단위 보완 필요\n• 진행 기록 comment 보완 필요\n" in slack[0]


def run_without_output(render, ja, work, mode, missing, verdict="fix", blank=False):
    """INNO-17(Task, ready-to-done)을 가짜 Jira로 실행하되 CI agent 출력 파일 하나를 지운다.

    blank가 True이면 지우지 않고 공백만 남긴다. (FakeJira, Summary)를 돌려준다.
    """
    ctx, out = work / "ctx", work / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    v = render.task_verdict("INNO-17", mode, verdict if mode == "review" else None) | {"items": ITEMS, "extra": []}
    render.write_out(out, "INNO-17", v, "* 요약")
    if blank:
        (out / "INNO-17" / missing).write_text(" \n", encoding="utf-8")
    else:
        (out / "INNO-17" / missing).unlink()
    j = render.FakeJira({"INNO-17": ""})
    summ = ja.Summary(str(work / "summary.md"))
    ja.apply_issue(j, ctx / "INNO-17", out, mode, False, "acc-lead", summ)
    return j, summ


def test_tldr_missing_note_stays_out_of_comment(render, ja, tmp_path):
    """검수 모드에서 tldr.wiki가 없으면 실패로 처리하므로, 검수 comment에 넣지 않던 "TL;DR 출력 없음" 메모도 나가지 않는다."""
    j, summ = run_without_output(render, ja, tmp_path, "review", "tldr.wiki")
    assert all("TL;DR 출력 없음" not in body for _, body in j.comments)
    assert "TL;DR 출력 없음" not in summ.rows[0]
    assert "CI agent 출력에 TL;DR 본문 없음 또는 비어 있음" in summ.rows[0]


def test_t8_counts_not_a_reason_as_missing_reason(jv):
    t8 = jv.check_t8(EXPECTED, [ITEMS[0], NOT_A_REASON], [])
    assert t8["result"] == "fail"
    assert t8["detail"] == "사유 없는 미달성 항목 2번"
    assert t8["request"] == "미달성 항목 2번의 달성 근거 또는 미달성 사유 comment 필요"


def test_t8_passes_with_owner_reason(jv):
    t8 = jv.check_t8(EXPECTED, [ITEMS[0], REASONED], [])
    assert t8["result"] == "pass"
    assert "request" not in t8


def test_doc_review_comment_lists_t9_after_t8(jv, jcm, ji):
    t8 = jv.check_t8(EXPECTED, [ITEMS[0], REASONED], [])
    t9 = {"id": "T9", "result": "pass", "detail": "진행 배경, 예상 산출물, 결과 산출물이 이어짐"}
    text = jcm.doc_review_comment([t9, t8], [], "")  # 넘긴 순서와 달리 표는 T8 다음에 T9를 보인다
    assert "| 진행 배경부터 결과 산출물까지의 연결 (T9) | ✅ 만족 |" in text
    assert text.index("초과 달성·미달성 사유 (T8)") < text.index("진행 배경부터 결과 산출물까지의 연결 (T9)")
    assert ji.DOC_CHECKS["Task"][-2:] == ("T8", "T9")


def test_validate_verdict_accepts_t9_feedback_and_reports_missing_t9(render, ja, jv, tmp_path):
    v = {"issueKey": "INNO-1", "mode": "review", "verdict": "pass", "checks": [], "items": [], "extra": []}
    v |= {"requests": [], "feedback": [{"id": "T9", "points": ["a"], "request": "b 필요"}]}
    assert jv.validate_verdict(v, "review", "INNO-1") is None
    no_t9 = render.task_verdict("INNO-17", "review", "fix") | {"items": ITEMS, "extra": []}
    no_t9["checks"] = [c for c in no_t9["checks"] if c["id"] != "T9"]
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    render.write_out(out, "INNO-17", no_t9, "* 요약")
    summ = ja.Summary(str(tmp_path / "summary.md"))
    ja.apply_issue(render.FakeJira({"INNO-17": ""}), ctx / "INNO-17", out, "review", False, "acc-lead", summ)
    assert "문서화 리뷰 항목 누락: 진행 배경부터 결과 산출물까지의 연결 (T9)" in summ.rows[0]


def assert_only_failed_run(j):
    """apply_issue가 실패한 실행에서 property에 쓴 것이 verdict "failed"인 runs 항목 하나뿐인지 확인한다."""
    [(key, prop)] = j.props_set
    assert key == "INNO-17" and set(prop) == {"runs"}
    [run] = prop["runs"]
    assert run["verdict"] == "failed" and run["mode"] == "review"


@pytest.mark.parametrize("blank", [False, True], ids=["파일 없음", "빈 파일"])
@pytest.mark.parametrize(
    ("missing", "label"),
    [("tldr.wiki", "TL;DR 본문"), ("comment.wiki", "검수 comment의 결과 요약")],
)
def test_review_without_output_file_leaves_no_jira_comment(render, ja, tmp_path, slack, missing, label, blank):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    j, summ = run_without_output(render, ja, tmp_path, "review", missing, blank=blank)
    assert j.comments == []  # 검수 comment, 문서화 리뷰 comment, 실패 알림 comment 모두 Jira에 남기지 않음
    assert j.fields == {}  # 상태 전환, TL;DR와 결과 산출물 구역 갱신 없음
    assert_only_failed_run(j)  # 실패해도 jira-doc 기록에는 runs만 남김(판정과 입력 해시는 남기지 않음)
    assert slack == []  # Slack 검수 알림도 없음. 실패는 main이 한 건으로 알림
    assert summ.rows[0].startswith("| INNO-17 | Task | review | 실패 |")
    assert f"CI agent 출력에 {label} 없음 또는 비어 있음" in summ.rows[0]
    assert "추정:" not in summ.rows[0]
    summ2 = ja.Summary(str(tmp_path / "summary2.md"))
    failure = ja.apply_issue(render.FakeJira({}), ctx / "INNO-17", out, "review", False, "acc-lead", summ2)
    assert failure == {
        "key": "INNO-17",
        "title": "",
        "url": ISSUE_URL,
        "reason": f"CI agent 출력에 {label} 없음 또는 비어 있음",
    }


def test_digest_without_tldr_keeps_note_in_summary_only(render, ja, tmp_path, slack):
    j, summ = run_without_output(render, ja, tmp_path, "digest", "tldr.wiki")
    assert j.comments == [] and slack == []
    assert "TL;DR 출력 없음" in summ.rows[0]
    assert "결과 산출물" in j.fields["INNO-17"]["description"]  # 정리 모드는 결과 산출물 구역을 그대로 갱신함


def test_failure_reasons(render, ja, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    failures = {}
    for key, content in (("INNO-1", None), ("INNO-2", "{not json")):
        render.write_issue(ctx, key, "Task", EXPECTED, {})
        (out / key).mkdir(parents=True)
        if content is not None:
            (out / key / "verdict.json").write_text(content, encoding="utf-8")
        j = render.FakeJira({})
        failures[key] = ja.apply_issue(j, ctx / key, out, "review", False, "", ja.Summary(str(tmp_path / "summary.md")))
        assert j.comments == []
    assert (
        "판정을 저장한 파일(verdict.json) 없음. 추정: CI agent 실행이 실패했거나 끝나지 않음"
        in failures["INNO-1"]["reason"]
    )
    assert "판정을 저장한 파일(verdict.json)을 JSON으로 읽을 수 없음" in failures["INNO-2"]["reason"]


def test_agent_error_cause_is_appended_to_every_failure(render, ja, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-1", "Task", EXPECTED, {})
    (out / "INNO-1").mkdir(parents=True)
    (out / "INNO-1" / "_agent_error.txt").write_text(
        "Claude 문서화 단계 종료 종류(subtype): error_max_turns\n", encoding="utf-8"
    )
    summ = ja.Summary(str(tmp_path / "summary.md"))
    failure = ja.apply_issue(render.FakeJira({}), ctx / "INNO-1", out, "review", False, "", summ)
    assert failure["reason"].endswith(". 원인: Claude 문서화 단계 종료 종류(subtype): error_max_turns")
    assert failure["reason"] in summ.rows[0]


def test_no_verdict_default_fails_without_format_wrapper(render, ja, jv, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    default = render.task_verdict("INNO-17", "review", "no_verdict") | {
        "items": [{"id": "1", "done": None, "result": ""}]
    }
    render.write_out(out, "INNO-17", default, "* 요약")
    j = render.FakeJira({"INNO-17": ""})
    summ = ja.Summary(str(tmp_path / "summary.md"))
    failure = ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "acc-lead", summ)
    assert failure["reason"] == jv.NO_VERDICT_KO == "CI agent 판정 없음"
    assert j.comments == [] and j.fields == {}
    # items의 done만 null이어도(정리 모드 포함) 같은 이유로 실패함
    only_items = render.task_verdict("INNO-17", "digest", None) | {"items": [{"id": "1", "done": None, "result": ""}]}
    assert jv.validate_verdict(only_items, "digest", "INNO-17") == jv.NO_VERDICT_KO


def run_main(render, ja, monkeypatch, work, mode, keys, *, lead="acc-lead", jira=None):
    """ja.main을 가짜 Jira와 sys.argv로 실행하고 (FakeJira, 종료 코드, Summary 파일 내용)을 돌려준다. 정상 종료면 코드는 0."""
    j = jira or render.FakeJira({k: "" for k in keys})
    monkeypatch.setattr(ja, "Jira", lambda: j)
    monkeypatch.setenv("JIRA_LEAD_ACCOUNT_ID", lead)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "9")
    summary = work / "summary.md"
    argv = [
        "apply.py",
        "--ctx",
        str(work / "ctx"),
        "--out",
        str(work / "out"),
        "--mode",
        mode,
        "--summary",
        str(summary),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    code = 0
    try:
        ja.main()
    except SystemExit as e:
        code = e.code
    return j, code, summary.read_text(encoding="utf-8")


def test_main_exits_zero_without_failure(render, ja, monkeypatch, tmp_path, slack):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    v = render.task_verdict("INNO-17", "review", "pass") | {"items": [{**i, "done": True} for i in ITEMS], "extra": []}
    render.write_out(out, "INNO-17", v, "* 요약")
    _, code, _ = run_main(render, ja, monkeypatch, tmp_path, "review", ["INNO-17"])
    assert code == 0
    assert all(not t.startswith("[jira-doc 실패]") for t in slack)


def test_main_failure_sends_one_slack_and_exits_one(render, ja, monkeypatch, tmp_path, slack):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, summary="export 오류")
    render.write_out(out, "INNO-17", render.task_verdict("INNO-17", "review", "no_verdict"), "* 요약")
    (out / "INNO-17" / "_agent_error.txt").write_text(
        "Claude 문서화 단계 종료 종류(subtype): error_max_turns\n", encoding="utf-8"
    )
    j, code, summary = run_main(render, ja, monkeypatch, tmp_path, "review", ["INNO-17"])
    assert code == 1
    assert j.comments == []  # 검수 모드의 실패도 Jira에 comment를 남기지 않음
    [text] = slack  # 검수 알림 없이 실패 알림 한 건
    assert text.startswith("[jira-doc 실패] 검수 모드 ticket 1건 처리 실패 <@U-LEAD>\n")
    assert f"• <{ISSUE_URL}|INNO-17 export 오류>: " in text
    assert "원인: " in text and "GitHub Actions 실행: <https://github.com/org/repo/actions/runs/9|실행 로그>" in text
    assert "실패 알림 ticket 1건" in summary
    assert "U-LEAD" not in summary and "<@U-" not in summary  # Actions 요약에 Slack 멤버 ID가 나가지 않음


def test_main_digest_two_failures_in_one_slack(render, ja, monkeypatch, tmp_path, slack):
    ctx = tmp_path / "ctx"
    for key in ("INNO-1", "INNO-2"):
        render.write_issue(ctx, key, "Task", EXPECTED, {}, summary=f"제목 {key}")
    _, code, summary = run_main(render, ja, monkeypatch, tmp_path, "digest", ["INNO-1", "INNO-2"])
    assert code == 1
    [text] = slack
    assert text.startswith("[jira-doc 실패] 정리 모드 ticket 2건 처리 실패 <@U-LEAD>\n")
    assert "INNO-1 제목 INNO-1" in text and "INNO-2 제목 INNO-2" in text
    assert "U-LEAD" not in summary


def test_main_jira_error_is_a_failure(render, ja, monkeypatch, tmp_path, slack):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, summary="export 오류")
    v = render.task_verdict("INNO-17", "review", "pass") | {"items": [{**i, "done": True} for i in ITEMS], "extra": []}
    render.write_out(out, "INNO-17", v, "* 요약")
    j = render.FakeJira({"INNO-17": ""})

    def boom(key, fields):
        raise ja.JiraError("GET /issue/INNO-17 -> 500: boom")

    j.issue = boom
    _, code, _ = run_main(render, ja, monkeypatch, tmp_path, "review", ["INNO-17"], jira=j)
    assert code == 1
    [text] = slack
    assert "INNO-17 export 오류" in text and "Jira 오류: GET /issue/INNO-17 -&gt; 500: boom" in text


def test_t3_mismatch_when_already_review_request(render, ja, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    v = render.task_verdict("INNO-17", "review", "escalate")
    v["items"] = [ITEMS[0]]
    render.write_out(out, "INNO-17", v, "* 요약")
    j = render.FakeJira({"INNO-17": ""})
    ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "", ja.Summary(str(tmp_path / "summary.md")))
    body = j.comments[0][1]
    head = body.split("*검사 항목*")[0]
    assert "검수 결과: *검토 요청*" in head and "(T3) 결과에 따라" not in head
    assert "| 예상 산출물과 결과 산출물의 1:1 대응 (T3) | ❌ 보완 필요 |" in body


def test_tldr_label_not_found_is_reported(render, ja, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    v = render.task_verdict("INNO-17", "review", "pass")
    v["items"] = [ITEMS[0]]
    render.write_out(out, "INNO-17", v, "* 요약")
    (out / "INNO-17" / "tldr.wiki").write_text("h2. Summary\n* 상태: In Progress\n", encoding="utf-8")
    j = render.FakeJira({"INNO-17": ""})
    summ = ja.Summary(str(tmp_path / "summary.md"))
    ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "", summ)
    assert "TL;DR의 검수 결과 표시를 찾지 못해 바꾸지 않음" in summ.rows[0]
    assert all("TL;DR의 검수 결과 표시" not in body for _, body in j.comments)


# 늦게 남긴 stop 사유(R4). stop 2026-09-08 18:00, 다음 상태 변경 2026-09-10 09:30
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 9, 29, 9, 0, tzinfo=KST)
STOP_CHANGES = [
    {"created": "2026-09-08T18:00:00.000+0900", "field": "status", "to": "Backlog"},
    {"created": "2026-09-10T09:30:00.000+0900", "field": "status", "to": "In Progress"},
]
R4_REQUEST = (
    "stop(2026-09-08)의 사유를 'stop 사유:'로 시작하는 comment로 남긴 뒤 다시 "
    "request 전환(담당자가 완료를 요청해 ticket을 ready-to-done 상태로 보내는 Jira 전환) 필요"
)
LATE_URL = f"{ISSUE_URL}?focusedCommentId=1"  # 늦은 사유 comment(2026-09-25)의 링크
LATE_NOTE = "늦게 기록: stop 2026-09-08 뒤 다음 상태 변경 이후에 남긴"
R4_LATE_DETAIL = f"stop 1회 모두 사유 comment 있음. {LATE_NOTE} [stop 사유 comment 2026-09-25|{LATE_URL}]"


def human_comment(created, body, cid):
    return {"created": created, "url": f"{ISSUE_URL}?focusedCommentId={cid}", "body": body}


def test_stop_events_comment_before_next_change_is_reason(jp):
    c = human_comment("2026-09-09T10:00:00.000+0900", "데이터 대기", 1)
    [st] = jp.stop_events(STOP_CHANGES, [c], NOW)
    assert st["hasReason"] is True and st["late"] is False
    assert st["reasonUrl"] == c["url"] and "lateReasonAt" not in st


def test_stop_events_marked_comment_after_next_change_is_late_reason(jp):
    plain = human_comment("2026-09-20T10:00:00.000+0900", "진행 상황 공유", 1)
    marked = human_comment("2026-09-25T11:00:00.000+0900", "  STOP 사유: 고객사 데이터 대기", 2)
    later = human_comment("2026-09-26T11:00:00.000+0900", "stop 사유: 다시 적음", 3)
    [st] = jp.stop_events(STOP_CHANGES, [later, plain, marked], NOW)  # 본문 앞 공백과 대소문자는 무시함
    assert st["hasReason"] is True and st["late"] is True
    assert st["lateReasonAt"] == marked["created"] and st["reasonUrl"] == marked["url"]


def test_stop_events_reason_before_next_change_wins_over_marked_comment(jp):
    on_time = human_comment("2026-09-09T10:00:00.000+0900", "데이터 대기", 1)
    marked = human_comment("2026-09-25T11:00:00.000+0900", "stop 사유: 고객사 데이터 대기", 2)
    [st] = jp.stop_events(STOP_CHANGES, [marked, on_time], NOW)
    assert st["hasReason"] is True and st["late"] is False
    assert st["reasonUrl"] == on_time["url"] and "lateReasonAt" not in st


def test_stop_events_plain_comment_after_next_change_is_not_reason(jp):
    c = human_comment("2026-09-25T11:00:00.000+0900", "사유: 고객사 데이터 대기", 1)
    [st] = jp.stop_events(STOP_CHANGES, [c], NOW)
    assert st["hasReason"] is False and st["late"] is False and st["reasonUrl"] == ""


@pytest.mark.parametrize(
    "comment",
    [
        {"created": "2026-09-25T11:00:00.000+0900", "url": LATE_URL},
        {"created": "2026-09-25T11:00:00.000+0900", "url": LATE_URL, "body": None},
        {"created": None, "url": LATE_URL, "body": "stop 사유: 고객사 데이터 대기"},
    ],
    ids=["body 키 없음", "body가 None", "created가 None"],
)
def test_stop_events_comment_without_body_or_created_is_not_late_reason(jp, comment):
    [st] = jp.stop_events(STOP_CHANGES, [comment], NOW)
    assert st["hasReason"] is False and st["late"] is False


def test_stop_events_marked_comment_before_stop_is_not_reason(jp):
    c = human_comment("2026-09-05T11:00:00.000+0900", "stop 사유: 미리 적음", 1)
    [st] = jp.stop_events(STOP_CHANGES, [c], NOW)
    assert st["hasReason"] is False and st["late"] is False


def test_check_r4_late_reason_passes_with_both_dates(jp):
    marked = human_comment("2026-09-25T11:00:00.000+0900", "stop 사유: 고객사 데이터 대기", 1)
    r4 = jp.check_r4(jp.stop_events(STOP_CHANGES, [marked], NOW))
    assert r4 == {"result": "pass", "detail": R4_LATE_DETAIL, "late": True}
    on_time = human_comment("2026-09-09T10:00:00.000+0900", "데이터 대기", 2)
    assert jp.check_r4(jp.stop_events(STOP_CHANGES, [on_time], NOW)) == {
        "result": "pass",
        "detail": "stop 1회 모두 사유 comment 있음",
    }
    # 링크가 없는 comment 하나가 사유 없는 stop 두 개를 함께 인정함. 여러 개는 "; "로 이음
    changes = [
        *STOP_CHANGES,
        {"created": "2026-09-20T18:00:00.000+0900", "field": "status", "to": "Backlog"},
        {"created": "2026-09-22T09:00:00.000+0900", "field": "status", "to": "In Progress"},
    ]
    no_url = {**marked, "url": ""}
    assert jp.check_r4(jp.stop_events(changes, [no_url], NOW))["detail"] == (
        "stop 2회 모두 사유 comment 있음. 늦게 기록: stop 2026-09-08 뒤 다음 상태 변경 이후에 남긴 "
        "stop 사유 comment 2026-09-25; stop 2026-09-20 뒤 다음 상태 변경 이후에 남긴 stop 사유 comment 2026-09-25"
    )


@pytest.mark.parametrize(
    ("late_reason", "want_codes"),
    [(False, ["HOLD_REASON_MISSING:2026-09-08"]), (True, [])],
    ids=["늦은 사유가 없으면 R4 항목", "늦은 사유가 있으면 R4 항목 없음"],
)
def test_run_scan_skips_r4_alert_for_late_reason(jsl, jp, tmp_path, late_reason, want_codes):
    # 예전 stop(2026-09-08)은 다음 상태 변경까지 사유가 없고, 지금 stop(2026-09-20)은 사유가 있음
    marked = human_comment("2026-09-15T11:00:00.000+0900", "stop 사유: 고객사 데이터 대기", 1)
    current = human_comment("2026-09-21T10:00:00.000+0900", "장비 수리 대기", 2)
    changes = [*STOP_CHANGES, {"created": "2026-09-20T18:00:00.000+0900", "field": "status", "to": "Backlog"}]
    scan = {
        "key": "INNO-40",
        "type": "Task",
        "status": "Backlog",
        "created": "2026-09-01T10:00:00.000+0900",
        "humanComments": [marked, current] if late_reason else [current],
        "statusChanges": changes,
    }
    (tmp_path / "INNO-40.json").write_text(json.dumps(scan), encoding="utf-8")
    jp.run_scan(tmp_path / "INNO-40.json", NOW)
    alerts = json.loads((tmp_path / "INNO-40.alerts.json").read_text(encoding="utf-8"))["alerts"]
    assert [a["code"] for a in alerts] == want_codes
    lines, _ = jsl.weekly_lines(tmp_path, {})  # 주간 점검 메시지의 R4 항목
    assert any(line.startswith("*stop 사유 미기재*") for line in lines) == bool(want_codes)
    if want_codes:  # 항목 줄은 묶음 제목 아래로 들여 씀(검수 알림과 같은 공백 4칸)
        assert any(line.startswith(f"{jsl.SLACK_INDENT}• INNO-40 ") for line in lines)


class ScanJira:
    """collect_scan이 부르는 Jira 메서드(search, changelog, prop_get)만 흉내 낸다."""

    def __init__(self, comments):
        self.comments = comments

    def search(self, jql, fields):
        f = {"summary": "라벨 재수집", "status": {"name": "Backlog"}, "issuetype": {"name": "Task"}}
        return [{"key": "INNO-40", "fields": f | {"comment": {"comments": self.comments}}}]

    def changelog(self, key):
        return []

    def prop_get(self, key):
        return {}


def test_collect_scan_keeps_first_100_chars_of_human_comment_body(jc, tmp_path, monkeypatch):
    monkeypatch.setattr(jc, "CTX", tmp_path)
    long_body = "stop 사유: " + "가" * 150
    comments = [
        {
            "id": "1",
            "created": "2026-09-25T11:00:00.000+0900",
            "author": {"accountType": "atlassian"},
            "body": long_body,
        },
        {"id": "2", "created": "2026-09-25T12:00:00.000+0900", "author": {"accountType": "atlassian"}, "body": "짧음"},
        {"id": "3", "created": "2026-09-25T13:00:00.000+0900", "author": {"accountType": "app"}, "body": "안내"},
    ]
    jc.collect_scan(ScanJira(comments), "INNO", "https://example.atlassian.net")
    scan = json.loads((tmp_path / "_scan" / "INNO-40.json").read_text(encoding="utf-8"))
    assert [c["body"] for c in scan["humanComments"]] == [long_body[:100], "짧음"]
    assert len(scan["humanComments"][0]["body"]) == 100


def run_r4_fail(render, ja, jp, work, verdict, requests, mode="review", a2=None):
    """사유 없는 stop(2026-09-08)이 있는 INNO-17을 실행하고 (FakeJira, Summary)를 돌려준다."""
    ctx, out = work / "ctx", work / "out"
    stops = jp.stop_events(STOP_CHANGES, [], NOW)
    checks = {"R4": jp.check_r4(stops)} | ({"A2": a2} if a2 else {})
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, checks, stops=stops)
    v = render.task_verdict("INNO-17", mode, verdict)
    v |= {"items": [ITEMS[0], {**ITEMS[1], "done": True}], "extra": [], "requests": requests, "slack": []}
    render.write_out(out, "INNO-17", v, "* 요약")
    j = render.FakeJira({"INNO-17": ""})
    summ = ja.Summary(str(work / "summary.md"))
    ja.apply_issue(j, ctx / "INNO-17", out, mode, False, "acc-lead", summ)
    return j, summ


@pytest.mark.parametrize(
    "given",
    [["평가 리포트 링크 comment 필요"], [R4_REQUEST, "평가 리포트 링크 comment 필요"]],
    ids=["CI agent가 적지 않으면 끝에 넣음", "CI agent가 이미 적었으면 넣지 않음"],
)
def test_r4_fail_adds_request_once_to_hold_comment_not_slack(render, ja, jp, tmp_path, slack, given):
    j, _ = run_r4_fail(render, ja, jp, tmp_path, "pass", given)
    assert j.comments[0][1].count(R4_REQUEST) == 1  # 검수 comment의 요청에는 한 번만 들어감
    # Slack 메시지에는 요청 목록이 없고, R4 실패는 "stop 사유 comment 보완 필요" 한 줄로만 나옴
    assert slack[0] == (
        f"<@U-LEAD> [보류] <{ISSUE_URL}|INNO-17>\n"
        "• stop 사유 comment 보완 필요\n"
        "• 예상 산출물 2개 중 2개 달성\n"
        f"<{ISSUE_URL}?focusedCommentId=10200|검수 comment>"
    )
    assert R4_REQUEST not in slack[0] and "(R4)" not in slack[0]


def test_r4_request_not_added_when_a2_makes_review_request(render, ja, jp, tmp_path, slack):
    a2 = {"result": "fail", "detail": "2026-09-26 10:00에 '예상 산출물' 구역이 바뀜"}
    j, summ = run_r4_fail(render, ja, jp, tmp_path, "pass", [], a2=a2)
    body = j.comments[0][1]
    assert body.startswith("[ai-doc-agent]\n검수 결과: *검토 요청*\n")
    assert R4_REQUEST not in body and "*요청*" not in body  # 검수 comment의 요청
    assert "requests" not in summ.rows[0]  # requests가 비어 있음
    assert "| stop 사유 comment (R4) | ❌ 보완 필요 |" in body
    assert R4_REQUEST not in slack[0]
    assert slack[0].splitlines()[1] == "• request 전환 뒤 완료 기준 바뀜"


def test_r4_request_not_added_in_digest_mode(render, ja, jp, tmp_path, slack):
    j, summ = run_r4_fail(render, ja, jp, tmp_path, None, [], mode="digest")
    assert j.comments == [] and slack == []
    assert "requests" not in summ.rows[0]  # requests가 비어 있음
    assert all(R4_REQUEST not in str(v) for v in j.fields["INNO-17"].values())


@pytest.mark.parametrize(
    ("v", "itype", "items", "changes", "want"),
    [
        ("pass", "Task", [ITEMS[0], {**ITEMS[1], "done": True}], [], ["예상 산출물 2개 중 2개 달성"]),
        ("escalate", "Task", [ITEMS[0], REASONED], ["미달성 1건, 담당자 사유 있음"], None),
        ("escalate", "Bug", [], [], []),
    ],
    ids=["통과", "검토 요청", "예상 산출물이 없는 work type"],
)
def test_slack_reasons_show_late_r4_once(jsl, v, itype, items, changes, want):
    late = [{"id": "R4", "result": "pass", "detail": R4_LATE_DETAIL, "late": True}]
    base = jsl.review_slack_reasons(v, itype, EXPECTED, items, [], [], changes, [])
    got = jsl.review_slack_reasons(v, itype, EXPECTED, items, [], late, changes, [])
    if want is not None:
        assert base == want
    assert got == [*base, "stop 사유 뒤늦게 작성"] and got.count("stop 사유 뒤늦게 작성") == 1
    assert "(R4)" not in "".join(got) and "늦게 기록" not in "".join(got)
    on_time = [{"id": "R4", "result": "pass", "detail": "stop 1회 모두 사유 comment 있음"}]
    assert jsl.review_slack_reasons(v, itype, EXPECTED, items, [], on_time, changes, []) == base


def test_slack_reasons_order_dedupe_and_limit(jsl):
    late = [{"id": "R4", "result": "pass", "detail": R4_LATE_DETAIL, "late": True}]
    changes = ["request 전환 뒤 완료 기준 바뀜", "예상 산출물 작성 보완 필요"]
    agent = ["원인 기록 없음", "예상 산출물 작성 보완 필요", "첨부 로그 파일 없음", "해결 확인 없음"]
    # 변경 이유, 달성 요약, verdict.slack(같은 문구는 한 번만), 늦은 stop 사유 순서이고 모두 4줄 이내
    # 늦은 stop 사유는 CI agent가 적지 않으므로 자리를 남긴다
    got = jsl.review_slack_reasons("fix", "Task", EXPECTED, ITEMS, [], late, changes, agent)
    assert got == [
        "request 전환 뒤 완료 기준 바뀜",
        "예상 산출물 작성 보완 필요",
        "예상 산출물 2개 중 1개 달성",
        "stop 사유 뒤늦게 작성",
    ]
    got = jsl.review_slack_reasons("pass", "Task", EXPECTED, ITEMS, [], late, [], agent)
    assert got == [
        "예상 산출물 2개 중 1개 달성",
        "원인 기록 없음",
        "예상 산출물 작성 보완 필요",
        "stop 사유 뒤늦게 작성",
    ]
    got = jsl.review_slack_reasons("pass", "Task", EXPECTED, ITEMS, [], [], [], agent)
    assert got == ["예상 산출물 2개 중 1개 달성", "원인 기록 없음", "예상 산출물 작성 보완 필요", "첨부 로그 파일 없음"]
    # CI agent가 지시를 어기고 늦은 stop 사유를 적어도 한 번만 나옴
    got = jsl.review_slack_reasons("pass", "Bug", [], [], [], late, [], ["stop 사유 뒤늦게 작성", "x"])
    assert got == ["x", "stop 사유 뒤늦게 작성"]


def test_slack_reasons_keep_only_link_title_and_escape(jsl):
    agent = [
        "원인은 [PR #42 해상도 검사|https://e.com/pull/42?a=1&b=2]에 있음",
        "{color:#6b778c}지연 < 50ms & 낮음{color}",
        "  ",
        7,
    ]
    got = jsl.review_slack_reasons("fix", "Bug", [], [], [], [], [], agent)
    assert got == ["원인은 PR #42 해상도 검사에 있음", "지연 &lt; 50ms &amp; 낮음"]


def test_slack_reasons_hold_without_agent_points_names_failed_owner_checks(jsl):
    t1 = {"id": "T1", "result": "fail", "detail": "예상 산출물 구역에 항목 없음"}
    r3 = {"id": "R3", "result": "fail", "detail": "서술형"}  # 담당자가 고칠 수 있는 검사가 아님
    ok = {"id": "T2", "result": "pass", "detail": "있음"}
    assert jsl.review_slack_reasons("fix", "Bug", [], [], [], [t1, r3, ok], [], []) == ["예상 산출물 작성 보완 필요"]
    assert jsl.review_slack_reasons("fix", "Bug", [], [], [], [t1], [], ["첨부 로그 파일 없음"]) == [
        "첨부 로그 파일 없음"
    ]
    for v in ("pass", "escalate"):  # 통과와 검토 요청에는 대체 줄이 없음
        assert jsl.review_slack_reasons(v, "Bug", [], [], [], [t1], [], []) == []
    # 검수 결과를 바꾼 이유와 같은 문구는 한 번만
    got = jsl.review_slack_reasons("fix", "Bug", [], [], [], [t1], ["예상 산출물 작성 보완 필요"], [])
    assert got == ["예상 산출물 작성 보완 필요"]


def test_hold_reason_for_r4_is_a_sentence(jp, jsl):
    stops = jp.stop_events(
        [
            {"created": "2026-10-02T09:00:00.000+0900", "field": "status", "to": "Backlog"},
            {"created": "2026-10-02T10:00:00.000+0900", "field": "status", "to": "In Progress"},
        ],
        [],
        dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))),
    )
    r4 = {"id": "R4", **jp.check_r4(stops)}
    want = "stop 전환(In Progress → Backlog)에 대한 사유를 담당자가 comment에 남기지 않음(stop 시점: 2026-10-02)"
    assert r4["result"] == "fail" and r4["detail"] == want
    t1 = {"id": "T1", "result": "fail", "detail": "예상 산출물 구역에 항목 없음"}
    got = jsl.review_slack_reasons("fix", "Task", EXPECTED, ITEMS[:1], [], [t1, r4], [], [])
    assert got == ["예상 산출물 2개 중 1개 달성", "예상 산출물 작성 보완 필요", "stop 사유 comment 보완 필요"]


def test_slack_text_shape(jsl):
    url, comment = "https://e.com/INNO-1", "https://e.com/INNO-1?focusedCommentId=1"
    assert jsl.review_slack_text("escalate", "INNO-1", "제목", url, "<@U1>", ["가 없음", "나 함"], comment) == (
        f"<@U1> [검토 요청] <{url}|INNO-1 제목>\n• 가 없음\n• 나 함\n<{comment}|검수 comment>"
    )
    assert jsl.review_slack_text("pass", "INNO-1", "제목", "", "팀장", [], "") == "팀장 [통과] INNO-1 제목"  # 링크 없음


@pytest.mark.parametrize(
    ("verdict", "items"),
    [("fix", ITEMS), ("pass", [ITEMS[0], {**ITEMS[1], "done": True}]), ("fix", [ITEMS[0], REASONED])],
    ids=["보류", "통과", "검토 요청"],
)
def test_slack_review_has_no_removed_parts(render, ja, tmp_path, slack, verdict, items):
    run_review(render, ja, tmp_path, verdict, items, agent_slack=["첨부 로그 파일 없음"])
    text = slack[0]
    for removed in ("검수 결과:", "상태 변경 없음", "직접 전환 필요", "\n요청\n", "근거:", " 이유\n", "    •"):
        assert removed not in text
    assert text.splitlines()[0].startswith("<@U-LEAD> [") and text.splitlines()[1].startswith("• ")
    assert "• 첨부 로그 파일 없음" in text and text.endswith("|검수 comment>")
    assert len(text.splitlines()) <= 6  # 첫 줄, 요점 4줄, 검수 comment 줄


def test_slack_review_hold_without_agent_points_names_failed_check(render, ja, tmp_path, slack):
    checks = {"T1": {"result": "fail", "detail": "예상 산출물 구역에 항목 없음"}}
    run_review(render, ja, tmp_path, "fix", ITEMS, checks=checks)
    assert slack[0].splitlines()[1:3] == ["• 예상 산출물 2개 중 1개 달성", "• 예상 산출물 작성 보완 필요"]


def test_slack_review_after_script_check_hold_names_checks(render, ja, tmp_path, slack):
    checks = {"T1": {"result": "fail", "detail": "a"}, "T2": {"result": "fail", "detail": "b"}}
    run_review(render, ja, tmp_path, "pass", [ITEMS[0], {**ITEMS[1], "done": True}], checks=checks)
    assert slack[0].splitlines()[1:4] == [
        "• 예상 산출물 작성 보완 필요",
        "• 진행 배경 작성 보완 필요",
        "• 예상 산출물 2개 중 2개 달성",
    ]


def test_slack_review_unreasoned_miss_after_pass_says_count(render, ja, tmp_path, slack):
    run_review(render, ja, tmp_path, "pass", ITEMS)
    assert slack[0].startswith("<@U-LEAD> [보류] ")
    assert slack[0].splitlines()[1] == "• 예상 산출물 미달성 1건"


# Bug, Issue 문서화 리뷰(Bug B5–B7, Issue I4–I5)
BUG_PRE = {
    "B1": {"result": "pass", "detail": "기본 정보 채워짐"},
    "B2": {"result": "pass", "detail": "개선(To-Be) 있음"},
    "A2": {"result": "n/a", "detail": "request 전환 이력 없음"},
    "R4": {"result": "n/a", "detail": "stop 이력 없음"},
}
BUG_AGENT_CHECKS = [
    {"id": "B3", "result": "pass", "detail": "원인과 해결에 PR 링크 있음"},
    {"id": "B4", "result": "pass", "detail": "확인 comment 있음"},
    {"id": "B5", "result": "fail", "detail": "재현 정보 부족"},
    {"id": "B6", "result": "pass", "detail": "원인 분석 기록 있음"},
    {"id": "B7", "result": "pass", "detail": "해결 확인 기록 있음"},
    {"id": "R2", "result": "pass", "detail": "링크 있음"},
]
BUG_FEEDBACK = [{"id": "B5", "points": ["재현 절차 없음"], "request": "문제(As-Is)에 재현 절차 작성 필요"}]
ISSUE_PRE = {
    "I1": {"result": "pass", "detail": "유형 선택됨"},
    "I2": {"result": "pass", "detail": "이슈 내용 2줄"},
    "R4": {"result": "n/a", "detail": "stop 이력 없음"},
}
ISSUE_AGENT_CHECKS = [
    {"id": "I3", "result": "pass", "detail": "결정 comment 링크 있음"},
    {"id": "I4", "result": "pass", "detail": "제기 이유 있음"},
    {"id": "I5", "result": "pass", "detail": "결정 이유 있음"},
    {"id": "R2", "result": "pass", "detail": "링크 있음"},
]


def run_type_review(render, ja, work, itype, pre_checks, checks, feedback, gate=False, j=None):
    """Bug 또는 Issue task(INNO-50)를 CI agent 판정 통과로 검수 모드 실행하고 FakeJira를 돌려준다."""
    ctx, out = work / "ctx", work / "out"
    render.write_issue(ctx, "INNO-50", itype, [], pre_checks)
    v = {"issueKey": "INNO-50", "mode": "review", "verdict": "pass", "checks": checks}
    v |= {"items": [], "extra": [], "requests": [], "feedback": feedback}
    render.write_out(out, "INNO-50", v, "* 요약")
    if j is None:
        j = render.FakeJira({"INNO-50": ""})
    ja.apply_issue(j, ctx / "INNO-50", out, "review", gate, "acc-lead", ja.Summary(str(work / "summary.md")))
    return j


def test_check_table_puts_bug_and_issue_doc_review_after_template_checks(jcm):
    ids = ["I5", "B7", "I3", "B5", "B4", "I4", "B6"]
    table = jcm.check_table([{"id": cid, "result": "pass", "detail": cid} for cid in ids])
    labels = [row.split(" | ")[0].removeprefix("| ") for row in table.splitlines()[1:]]
    assert labels == [
        "To-Be 동작 확인 comment (B4)",
        "문제(As-Is) 재현 정보 (B5)",
        "원인 분석 기록 (B6)",
        "해결 확인 기록 (B7)",
        "대응 결과의 근거 (I3)",
        "이슈 내용 충실도 (I4)",
        "논의와 결정 기록 (I5)",
    ]


def test_bug_observe_doc_review_goes_to_lead_comment_only(render, ja, tmp_path):
    j = run_type_review(render, ja, tmp_path, "Bug", BUG_PRE, BUG_AGENT_CHECKS, BUG_FEEDBACK)
    assert len(j.comments) == 2
    review, doc = j.comments[0][1], j.comments[1][1]
    assert "검수 결과: *통과*" in review  # 관찰 모드는 문서화 리뷰로 검수 결과를 바꾸지 않음
    assert "문서화 리뷰 결과(관찰 모드): *보완 필요*" in doc
    assert "| 문제(As-Is) 재현 정보 (B5) | ❌ 보완 필요 | 재현 절차 없음 |" in doc
    assert "| 원인 분석 기록 (B6) | ✅ 만족 | 원인 분석 기록 있음 |" in doc
    assert "| 해결 확인 기록 (B7) | ✅ 만족 | 해결 확인 기록 있음 |" in doc
    assert "# 문제(As-Is)에 재현 절차 작성 필요" in doc
    assert all(f"({cid})" not in review for cid in ("B5", "B6", "B7"))
    assert "| To-Be 동작 확인 comment (B4) | ✅ 만족 |" in review


@pytest.mark.parametrize(("failed", "want"), [("I4", "보류"), ("I5", "보류"), (None, "통과")])
def test_issue_gate_doc_review_fail_turns_pass_into_hold(render, ja, jv, tmp_path, failed, want):
    checks = [{**c, "result": "fail"} if c["id"] == failed else c for c in ISSUE_AGENT_CHECKS]
    j = run_type_review(render, ja, tmp_path, "Issue", ISSUE_PRE, checks, [], gate=True)
    assert len(j.comments) == 1  # 게이트 모드는 팀장용 문서화 리뷰 comment를 따로 남기지 않음
    body = j.comments[0][1]
    assert f"검수 결과: *{want}*" in body
    if failed:
        note = f"* 문서화 리뷰에 보완 필요 항목이 있어 통과에서 보류로 바꿈: {jv.check_label(failed)}\n"
        assert note in body
        assert f"| {jv.check_label(failed)} | ❌ 보완 필요 |" in body


@pytest.mark.parametrize(
    ("itype", "pre", "checks"),
    [("Bug", BUG_PRE, BUG_AGENT_CHECKS), ("Issue", ISSUE_PRE, ISSUE_AGENT_CHECKS)],
)
def test_bug_and_issue_review_do_not_compute_t8(render, ja, tmp_path, itype, pre, checks):
    j = run_type_review(render, ja, tmp_path, itype, pre, checks, [])
    assert len(j.comments) == 2  # 검수 comment와 문서화 리뷰 comment
    assert all("(T8)" not in body for _, body in j.comments)


@pytest.mark.parametrize(
    ("kind", "want"),
    [
        ("* -제안-\n* 이슈\n* 기타\n", "pass"),
        ("-제안-\n이슈\n-기타(: 일정 공유)-\n", "pass"),
        ("* 제안\n* 이슈\n* 기타(: )\n", "unknown"),
        ("* 제안\n----\n* 기타\n", "unknown"),
        ("* [ ] 제안\n* [ ] 이슈\n", "fail"),
        ("- -제안-\n- 이슈\n", "pass"),
        ("* [x] 제안\n* [ ] 이슈\n", "pass"),
    ],
    ids=["취소선 글머리표", "취소선 줄", "선택 없음", "가로줄", "빈 체크박스", "취소선 대시 글머리표", "체크 글자"],
)
def test_issue_type_counts_struck_item_as_selected(jp, kind, want):
    # Jira 체크 목록에서 선택한 항목은 API v2 wiki markup에 취소선으로 나온다(INNO-36 검수, 2026-10-08)
    desc = f"h2. 이슈 유형\n\n{kind}\nh2. 이슈 내용\n\n* a\n* b\n"
    assert jp.check_issue_template(desc)["I1"]["result"] == want


def test_issue_run_issue_dir_has_no_a2(jp, tmp_path):
    d = tmp_path / "INNO-35"
    d.mkdir()
    issue = {"key": "INNO-35", "type": "Issue", "status": "Ready-to-Done"}
    changelog = [{"field": "status", "to": "Ready-to-Done", "created": "2026-09-19T17:40:00.000+0900"}]
    for name, data in [("issue.json", issue), ("comments.json", []), ("changelog.json", changelog)]:
        (d / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (d / "description.wiki").write_text("h2. 이슈 유형\n* [x] 제안\n\nh2. 이슈 내용\n* a\n* b\n", encoding="utf-8")
    jp.run_issue_dir(d, NOW)
    pre = json.loads((d / "precheck.json").read_text(encoding="utf-8"))
    assert set(pre["checks"]) == {"I1", "I2", "R4"}
    assert "docFacts" in pre


@pytest.mark.parametrize(
    ("itype", "desc", "lines"),
    [
        ("Task", "h2. 진행 배경\n* a\n\nh2. 예상 산출물\n* b\n* c\n", 1),
        ("Bug", bug_desc(BUG_INFO, PROBLEM, TOBE).replace(f"* {PROBLEM}\n", f"* {PROBLEM}\n* 매번 일어남\n"), 2),
        ("Issue", "h2. 이슈 유형\n* [x] 제안\n\nh2. 이슈 내용\n* a\n* b\n* c\n\nh2. 진행 배경\n* x\n", 3),
    ],
    ids=["Task는 진행 배경", "Bug는 문제(As-Is)", "Issue는 이슈 내용"],
)
def test_doc_facts_counts_background_by_work_type(jp, tmp_path, itype, desc, lines):
    assert jp.doc_facts(tmp_path, itype, desc, [], [], NOW)["background"]["lines"] == lines


def test_bug_observe_records_doc_fails(render, ja, tmp_path, monkeypatch):
    j = render.FakeJira({"INNO-50": ""})
    props: list[dict] = []
    monkeypatch.setattr(j, "prop_set", lambda key, prop: props.append(prop))
    run_type_review(render, ja, tmp_path, "Bug", BUG_PRE, BUG_AGENT_CHECKS, BUG_FEEDBACK, j=j)
    assert props[-1]["docFails"] == ["B5"]  # jira-doc 기록에 보완 필요인 문서화 리뷰 항목 ID만 남음


def test_bug_run_issue_dir_counts_problem_lines_as_background(jp, tmp_path):
    d = tmp_path / "INNO-30"
    d.mkdir()
    issue = {"key": "INNO-30", "type": "Bug", "status": "Ready-to-Done", "attachments": [{}]}
    for name, data in [("issue.json", issue), ("comments.json", []), ("changelog.json", [])]:
        (d / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    desc = bug_desc(BUG_INFO, PROBLEM, TOBE).replace(f"* {PROBLEM}\n", f"* {PROBLEM}\n* 매번 일어남\n")
    (d / "description.wiki").write_text(desc, encoding="utf-8")
    jp.run_issue_dir(d, NOW)
    pre = json.loads((d / "precheck.json").read_text(encoding="utf-8"))
    assert pre["docFacts"]["background"]["lines"] == 2  # 문제(As-Is) 2줄. 기본 정보 4줄, 개선(To-Be) 1줄과 다름


# ---------------------------------------------------------------------- 실행 기록(runs)
READY = {"field": "status", "from": "In Progress", "to": "ready-to-done"}
BACK = {"field": "status", "from": "ready-to-done", "to": "In Progress"}
USAGE = [
    {
        "retry": False,
        "costUsd": 0.25,
        "tokens": {"input": 10, "output": 20, "cacheWrite": 30, "cacheRead": 40},
        "models": ["claude-sonnet-5-5"],
    },
    {
        "retry": True,
        "costUsd": 0.5,
        "tokens": {"input": 1, "output": 2, "cacheWrite": 3, "cacheRead": 4},
        "models": ["claude-sonnet-5-5", "claude-haiku-5"],
    },
]


@pytest.fixture
def prop_jira(render):
    """property를 기억하는 가짜 Jira를 만드는 함수."""

    class PropJira(render.FakeJira):
        def __init__(self, props=None):
            super().__init__({"INNO-17": ""})
            self.props = props or {}

        def prop_get(self, key):
            return json.loads(json.dumps(self.props.get(key, {})))

        def prop_set(self, key, prop):
            super().prop_set(key, prop)
            self.props[key] = prop

    return PropJira


def test_review_run_is_recorded_with_usage_and_request_no(render, ja, tmp_path, prop_jira):
    j = prop_jira()
    changelog = [
        {**READY, "created": "2026-09-01"},
        {**BACK, "created": "2026-09-02"},
        {**READY, "created": "2026-09-03"},
        {"field": "description", "to": "ready-to-done"},  # 상태 변경이 아니라 셈에서 뺌
    ]
    run_review(render, ja, tmp_path, "fix", ITEMS, j=j, changelog=changelog, usage=USAGE)
    [run] = j.props["INNO-17"]["runs"]
    assert run["mode"] == "review" and run["verdict"] == "fix" and run["requestNo"] == 2 and run["retry"] is True
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+09:00", run["at"])  # 한국 시간, 초 단위
    assert run["costUsd"] == pytest.approx(0.75)
    assert run["tokens"] == {"input": 11, "output": 22, "cacheWrite": 33, "cacheRead": 44}
    assert run["models"] == ["claude-sonnet-5-5", "claude-haiku-5"]
    assert "notified" not in j.props["INNO-17"]  # 누락 알림 기록 기본값은 더 만들지 않음


def test_digest_run_has_no_request_no_and_zero_tokens_without_usage_file(render, ja, tmp_path, prop_jira):
    j = prop_jira()
    run_review(render, ja, tmp_path, None, ITEMS, mode="digest", j=j, changelog=[READY])
    [run] = j.props["INNO-17"]["runs"]
    assert run["mode"] == "digest" and run["verdict"] == "digest" and run["requestNo"] is None
    assert run["retry"] is False and run["costUsd"] is None and run["models"] == []
    assert run["tokens"] == {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0}


def test_runs_keep_only_latest_100(render, ja, tmp_path, prop_jira):
    old = [{"at": f"run-{i}"} for i in range(100)]
    j = prop_jira({"INNO-17": {"runs": old}})
    run_review(render, ja, tmp_path, "pass", [ITEMS[0], {**ITEMS[1], "done": True}], j=j)
    runs = j.props["INNO-17"]["runs"]
    assert len(runs) == 100 and runs[0]["at"] == "run-1" and runs[-1]["verdict"] == "pass"


def test_failed_run_is_recorded_and_property_error_stays_in_summary(render, ja, tmp_path, prop_jira):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})  # out/이 없어 반영 실패
    j = prop_jira()
    summ = ja.Summary(str(tmp_path / "summary.md"))
    failure = ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "acc-lead", summ)
    assert failure["key"] == "INNO-17"
    [run] = j.props["INNO-17"]["runs"]
    assert run["verdict"] == "failed" and run["requestNo"] is None
    assert set(j.props["INNO-17"]) == {"runs"}

    def broken(key):
        raise ja.JiraError("GET property -> 500: boom")

    j2 = prop_jira()
    j2.prop_get = broken
    summ2 = ja.Summary(str(tmp_path / "summary2.md"))
    failure2 = ja.apply_issue(j2, ctx / "INNO-17", out, "review", False, "acc-lead", summ2)
    assert failure2["key"] == "INNO-17" and j2.props_set == []  # 흐름은 그대로 실패를 돌려줌
    assert any("실행 기록(runs) 저장 실패: GET property -> 500: boom" in line for line in summ2.extra)


# ---------------------------------------------------------------------- 토큰(인증 키) 만료 알림
TODAY = dt.date(2026, 10, 14)


def expiry_json(**days):
    """이름 -> 오늘(TODAY)부터 며칠 뒤인지로 TOKEN_EXPIRY JSON 문자열을 만든다."""
    return json.dumps(
        {n.replace("_", " ") if n == "F1_PAT" else n: str(TODAY + dt.timedelta(days=d)) for n, d in days.items()}
    )


def test_token_expiry_sends_only_30_days_or_less(jsl):
    raw = expiry_json(JIRA_API_TOKEN=30, ORG_READ_TOKEN=31, F1_PAT=0, CLAUDE_CODE_OAUTH_TOKEN=-3, OTHER_KEY=10)
    text, warning = jsl.token_expiry_text(raw, TODAY)
    assert warning == ""
    lines = text.splitlines()
    assert lines[0] == "[jira-doc 토큰(인증 키) 만료 알림]"
    assert lines[-1] == "팀장이 갱신 뒤 variable `TOKEN_EXPIRY`의 만료일도 고쳐야 함"
    assert "ORG_READ_TOKEN" not in text  # 31일 남음
    assert lines[1:] == [
        "• CLAUDE_CODE_OAUTH_TOKEN: 만료됨",
        "    ◦ 만료일 2026-10-11(2026-10-14 기준)",
        "    ◦ 갱신: 팀장이 `claude setup-token`으로 발급",
        "    ◦ 교체: 팀장이 secret CLAUDE_CODE_OAUTH_TOKEN",
        "• F1 PAT: 오늘 만료",
        "    ◦ 만료일 2026-10-14(2026-10-14 기준)",
        "    ◦ 갱신: 팀장이 GitHub Settings의 Personal access tokens 발급",
        "    ◦ 교체: 팀장이 Jira Automation F1 설정",
        "• OTHER_KEY: 남은 날 10일",
        "    ◦ 만료일 2026-10-24(2026-10-14 기준)",
        "    ◦ 갱신: 갱신할 곳 미등록",
        "• JIRA_API_TOKEN: 남은 날 30일",
        "    ◦ 만료일 2026-11-13(2026-10-14 기준)",
        "    ◦ 갱신: 팀장이 Atlassian 계정의 API 토큰 화면에서 발급",
        "    ◦ 교체: 팀장이 secret JIRA_API_TOKEN",
        "팀장이 갱신 뒤 variable `TOKEN_EXPIRY`의 만료일도 고쳐야 함",
    ]
    for line in lines:  # 결과 문서 규칙: 줄마다 50자 이내(백틱 안 코드와 목록 기호 제외)
        assert len(re.sub(r"`[^`]*`", "", line).strip(" •◦-")) <= 50, line


def test_token_expiry_bad_item_is_skipped_and_others_continue(jsl):
    raw = json.dumps({"JIRA_API_TOKEN": "2026-10-20", "ORG_READ_TOKEN": "내일", "F1 PAT": "2026-13-01"})
    text, warning = jsl.token_expiry_text(raw, TODAY)
    assert "JIRA_API_TOKEN: 남은 날 6일" in text and "ORG_READ_TOKEN" not in text
    assert (
        warning
        == "TOKEN_EXPIRY의 ORG_READ_TOKEN 날짜 형식(YYYY-MM-DD)이 틀려 건너뜀; TOKEN_EXPIRY의 F1 PAT 날짜 형식(YYYY-MM-DD)이 틀려 건너뜀"
    )


def test_token_expiry_sends_nothing_when_all_over_30_days(jsl):
    assert jsl.token_expiry_text(expiry_json(JIRA_API_TOKEN=31, ORG_READ_TOKEN=300), TODAY) == ("", "")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "  ",
        "{틀림",
        '["2026-11-01"]',
        '{"JIRA_API_TOKEN": "2026/11/01"}',
        '{"JIRA_API_TOKEN": "2026-13-01"}',
        '{"JIRA_API_TOKEN": 5}',
    ],
    ids=["없음", "공백", "JSON 오류", "객체가 아님", "날짜 형식 오류", "없는 달", "문자열이 아님"],
)
def test_token_expiry_bad_value_only_warns(jsl, raw):
    text, warning = jsl.token_expiry_text(raw, TODAY)
    assert text == "" and "TOKEN_EXPIRY" in warning


def test_slack_token_expiry_posts_to_lead_channel(ja, jsl, tmp_path, monkeypatch):
    posted = []
    monkeypatch.setattr(
        jsl, "slack_post", lambda text, env="SLACK_WEBHOOK_URL": posted.append((text, env)) or "전송 완료"
    )
    today = ja.dt.datetime.now(ja.KST).date()
    monkeypatch.setenv(
        "TOKEN_EXPIRY",
        json.dumps(
            {"JIRA_API_TOKEN": str(today + dt.timedelta(days=30)), "ORG_READ_TOKEN": str(today + dt.timedelta(days=31))}
        ),
    )
    summ = ja.Summary(str(tmp_path / "s.md"))
    ja.slack_token_expiry(summ)
    [(text, env)] = posted
    assert env == "SLACK_LEAD_WEBHOOK_URL" and "JIRA_API_TOKEN" in text and "ORG_READ_TOKEN" not in text
    assert "전송 완료" in summ.rows[0]
    monkeypatch.setenv("TOKEN_EXPIRY", json.dumps({"JIRA_API_TOKEN": str(today + dt.timedelta(days=31))}))
    ja.slack_token_expiry(summ)
    assert len(posted) == 1 and len(summ.rows) == 1  # 30일 넘게 남으면 보내지도 기록하지도 않음


@pytest.mark.parametrize("raw", [None, "{틀림"], ids=["TOKEN_EXPIRY 없음", "JSON 오류"])
def test_slack_token_expiry_warns_without_sending(ja, jsl, tmp_path, monkeypatch, raw):
    posted = []
    monkeypatch.setattr(
        jsl, "slack_post", lambda text, env="SLACK_WEBHOOK_URL": posted.append((text, env)) or "전송 완료"
    )
    if raw is None:
        monkeypatch.delenv("TOKEN_EXPIRY", raising=False)
    else:
        monkeypatch.setenv("TOKEN_EXPIRY", raw)
    summ = ja.Summary(str(tmp_path / "s.md"))
    ja.slack_token_expiry(summ)
    assert posted == [] and len(summ.rows) == 1 and "| 경고 |" in summ.rows[0] and "TOKEN_EXPIRY" in summ.rows[0]


# ---------------------------------------------------------------------- 팀장 채널 전송과 report 모드
def test_slack_post_skips_when_webhook_env_missing(jsl, monkeypatch):
    monkeypatch.undo()  # autouse fixture slack이 바꾼 slack_post를 원래 함수로 되돌린다
    monkeypatch.delenv("SLACK_LEAD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert jsl.slack_post("본문", "SLACK_LEAD_WEBHOOK_URL") == "SLACK_LEAD_WEBHOOK_URL 없음. 전송 건너뜀"
    assert jsl.slack_post("본문") == "SLACK_WEBHOOK_URL 없음. 전송 건너뜀"


def test_slack_post_uses_given_webhook_env(jsl, monkeypatch):
    monkeypatch.undo()
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("SLACK_LEAD_WEBHOOK_URL", "https://hooks.example.com/lead")
    urls = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(jsl.urllib.request, "urlopen", lambda req, timeout: urls.append(req.full_url) or Resp())
    assert jsl.slack_post("본문", "SLACK_LEAD_WEBHOOK_URL") == "전송 완료"
    assert urls == ["https://hooks.example.com/lead"]


def test_last_month(ja):
    assert ja.last_month(dt.date(2026, 10, 14)) == "2026-09"
    assert ja.last_month(dt.date(2027, 1, 1)) == "2026-12"


def test_report_mode_sends_run_report_text_to_lead_channel(ja, jsl, jrr, tmp_path, monkeypatch):
    calls, posted = [], []

    def fake_run_report(j, month, lead, summ):
        calls.append((j, month, lead, summ))
        return ["정기 보고 본문"]

    monkeypatch.setattr(jrr, "run_report", fake_run_report)
    monkeypatch.setattr(ja, "Jira", lambda: "가짜 Jira")
    monkeypatch.setattr(
        jsl, "slack_post", lambda text, env="SLACK_WEBHOOK_URL": posted.append((text, env)) or "전송 완료"
    )
    monkeypatch.setenv("JIRA_LEAD_ACCOUNT_ID", "acc-lead")
    summary = tmp_path / "summary.md"
    monkeypatch.setattr(sys, "argv", ["apply.py", "--mode", "report", "--month", "2026-09", "--summary", str(summary)])
    ja.main()
    [(j, month, lead, summ)] = calls
    assert (j, month, lead) == ("가짜 Jira", "2026-09", "acc-lead") and isinstance(summ, ja.Summary)
    assert posted == [("정기 보고 본문", "SLACK_LEAD_WEBHOOK_URL")]
    text = summary.read_text(encoding="utf-8")
    assert "| - | - | report | 전송 완료 | 2026-09 정기 보고 1/1 |" in text


def test_report_mode_sends_each_message(ja, jsl, jrr, tmp_path, monkeypatch):
    """run_report가 메시지 목록을 돌려주면(12월 보고의 월간, 분기, 연간 세 메시지) 메시지마다 팀장 채널로 보낸다."""
    posted = []
    monkeypatch.setattr(jrr, "run_report", lambda j, month, lead, summ: ["월간", "분기", "연간"])
    monkeypatch.setattr(ja, "Jira", lambda: None)
    monkeypatch.setattr(
        jsl, "slack_post", lambda text, env="SLACK_WEBHOOK_URL": posted.append((text, env)) or "전송 완료"
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setattr(sys, "argv", ["apply.py", "--mode", "report", "--month", "2026-12", "--summary", str(summary)])
    ja.main()
    assert posted == [(t, "SLACK_LEAD_WEBHOOK_URL") for t in ("월간", "분기", "연간")]
    text = summary.read_text(encoding="utf-8")
    assert all(f"2026-12 정기 보고 {i}/3" in text for i in (1, 2, 3))


def test_report_mode_defaults_to_last_month(ja, jsl, jrr, tmp_path, monkeypatch):
    months = []
    monkeypatch.setattr(jrr, "run_report", lambda j, month, lead, summ: months.append(month) or ["본문"])
    monkeypatch.setattr(ja, "Jira", lambda: None)
    monkeypatch.setattr(jsl, "slack_post", lambda text, env="SLACK_WEBHOOK_URL": "전송 완료")
    monkeypatch.setattr(sys, "argv", ["apply.py", "--mode", "report", "--summary", str(tmp_path / "s.md")])
    ja.main()
    assert months == [ja.last_month(ja.dt.datetime.now(ja.KST).date())]


# ---------------------------------------------------------------------- runs 크기와 Jira 오류 경로
def test_runs_drop_oldest_when_property_exceeds_30000_bytes(jrn):
    big = {"at": "x", "note": "가" * 1000}  # UTF-8 3000바이트
    prop = {"runs": [dict(big, n=i) for i in range(30)]}
    jrn.add_run(prop, {"at": "new", "verdict": "pass"})
    assert len(json.dumps(prop, ensure_ascii=False).encode("utf-8")) <= 30000
    assert prop["runs"][-1] == {"at": "new", "verdict": "pass"} and 1 < len(prop["runs"]) < 11
    assert prop["runs"][0]["n"] == 30 - (len(prop["runs"]) - 1)  # 오래된 것부터 지워짐
    prop = {"runs": [{"note": "가" * 20000}]}
    jrn.add_run(prop, {"note": "나" * 20000})
    assert len(prop["runs"]) == 1 and prop["runs"][0]["note"].startswith("나")


def test_run_cost_is_rounded_to_4_decimals(render, ji, tmp_path):
    render.write_json(tmp_path / "out" / "_usage.json", [{"costUsd": 0.123456, "tokens": {}, "models": []}])
    entry = ji.run_entry(tmp_path / "ctx" / "INNO-17", tmp_path / "out", "digest", "digest")
    assert entry["costUsd"] == 0.1235


def test_jira_error_in_apply_issue_is_recorded_as_failed_run(render, ja, tmp_path, prop_jira, monkeypatch, capsys):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, summary="제목")
    v = render.task_verdict("INNO-17", "review", "pass") | {
        "items": [ITEMS[0], {**ITEMS[1], "done": True}],
        "extra": [],
    }
    render.write_out(out, "INNO-17", v, "* 요약")
    j = prop_jira()

    def broken(key, body):
        raise ja.JiraError("POST comment -> 500: boom")

    j.set_fields = broken  # 반영 중간(결과 산출물 구역 갱신)에서 Jira 오류가 나게 한다
    monkeypatch.setattr(ja, "Jira", lambda: j)
    monkeypatch.setattr(
        sys,
        "argv",
        ["apply.py", "--mode", "review", "--ctx", str(ctx), "--out", str(out), "--summary", str(tmp_path / "s.md")],
    )
    with pytest.raises(SystemExit) as e:
        ja.main()
    assert e.value.code == 1
    [run] = j.props["INNO-17"]["runs"]
    assert run["verdict"] == "failed" and run["mode"] == "review" and set(j.props["INNO-17"]) == {"runs"}
    assert "Jira 오류: POST comment -> 500: boom" in (tmp_path / "s.md").read_text(encoding="utf-8")


def test_jira_error_while_recording_failed_run_stays_in_summary(render, ja, tmp_path, prop_jira, monkeypatch):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, summary="제목")
    v = render.task_verdict("INNO-17", "review", "pass") | {
        "items": [ITEMS[0], {**ITEMS[1], "done": True}],
        "extra": [],
    }
    render.write_out(out, "INNO-17", v, "* 요약")
    j = prop_jira()

    def broken(*args, **kwargs):
        raise ja.JiraError("boom")

    j.set_fields = broken
    j.prop_get = broken
    monkeypatch.setattr(ja, "Jira", lambda: j)
    monkeypatch.setattr(
        sys,
        "argv",
        ["apply.py", "--mode", "review", "--ctx", str(ctx), "--out", str(out), "--summary", str(tmp_path / "s.md")],
    )
    with pytest.raises(SystemExit) as e:
        ja.main()
    assert e.value.code == 1  # 기록 실패가 흐름을 막지 않고 실패로 끝남
    assert "INNO-17: 실행 기록(runs) 저장 실패: boom" in (tmp_path / "s.md").read_text(encoding="utf-8")


class DecisionJira:
    """decision 모드 테스트용 가짜 Jira. 지금 상태, changelog, comment, jira-doc 기록을 받는다."""

    def __init__(self, status, changes, comments=(), prop=None, lead_name="Kai"):
        self.status, self.changes, self._comments, self.prop = status, changes, list(comments), prop or {}
        self.lead_name = lead_name

    def user_name(self, account_id):
        return self.lead_name if account_id else ""

    def site_url(self):
        return "https://example.atlassian.net"

    def issue(self, key, fields):
        return {
            "fields": {
                "summary": "제목",
                "status": {"name": self.status},
                "issuetype": {"name": "Task"},
                "assignee": {"accountId": "acc-assignee", "displayName": "담당자A"},
            }
        }

    def changelog(self, key):
        return [
            {
                "created": at,
                "author": {"accountId": who},
                "items": [{"field": "status", "fromString": src, "toString": dst}],
            }
            for at, src, dst, who in self.changes
        ]

    def comments(self, key):
        return self._comments

    def prop_get(self, key):
        return dict(self.prop)

    def prop_set(self, key, value):
        self.prop = dict(value)


REVIEW_COMMENT = {"id": "10300", "body": "[ai-doc-agent]\n검수 결과: *보류*\n* 상태를 바꾸지 않고 팀장 확인을 요청함"}


def run_decision(ja, tmp_path, fake):
    summ = ja.Summary(str(tmp_path / "summary.md"))
    ja.apply_decision(fake, "INNO-17", "acc-lead", summ)
    return summ


def test_decision_hold_notifies_assignee(ja, tmp_path, slack):
    """보류 전환이면 담당자에게 상태 변경 결과만 보낸다."""
    fake = DecisionJira(
        "In Progress",
        [("2026-10-15T10:00:00.000+0900", "ready-to-done", "In Progress", "acc-lead")],
        [{"id": "1", "body": "사람 comment"}, REVIEW_COMMENT],
        {"lastVerdict": "fix"},
    )
    summ = run_decision(ja, tmp_path, fake)
    assert slack == [
        "<@U-ASSIGNEE> [Kai 확인: 보류] <https://example.atlassian.net/browse/INNO-17|INNO-17 제목>\n• In Progress로 이동"
    ]
    assert "팀장 확인 결과 보류, 담당자에게 Slack 알림" in summ.rows[0]


def test_decision_pass_notifies_assignee(ja, tmp_path, slack):
    fake = DecisionJira("Done", [("2026-10-15T10:00:00.000+0900", "ready-to-done", "Done", "acc-lead")])
    run_decision(ja, tmp_path, fake)
    assert slack == [
        "<@U-ASSIGNEE> [Kai 확인: 통과] <https://example.atlassian.net/browse/INNO-17|INNO-17 제목>\n• Done으로 이동"
    ]


def test_decision_message_uses_lead_name_from_jira(ja, tmp_path, slack):
    """대괄호 안의 팀장 이름은 가짜 Jira가 돌려준 표시 이름이다."""
    fake = DecisionJira(
        "Done", [("2026-10-15T10:00:00.000+0900", "ready-to-done", "Done", "acc-lead")], lead_name="Liam"
    )
    run_decision(ja, tmp_path, fake)
    assert slack[0].splitlines()[0].startswith("<@U-ASSIGNEE> [Liam 확인: 통과] ")


def test_decision_message_without_lead_name_uses_default(ja, tmp_path, slack):
    """팀장 이름을 읽지 못하면(빈 문자열) 대괄호 안에 "팀장"을 적는다."""
    fake = DecisionJira(
        "In Progress", [("2026-10-15T10:00:00.000+0900", "ready-to-done", "In Progress", "acc-lead")], lead_name=""
    )
    run_decision(ja, tmp_path, fake)
    assert slack[0].splitlines()[0].startswith("<@U-ASSIGNEE> [팀장 확인: 보류] ")


def test_decision_message_without_url_has_no_link(jsl):
    """URL이 없으면 링크 없이 "키 제목"을 적는다. 제목의 &, <, >는 escape한다."""
    text = jsl.decision_slack_text("INNO-17", "a <b> & c", "", "담당자A", "통과", "Done", "Kai")
    assert text == "담당자A [Kai 확인: 통과] INNO-17 a &lt;b&gt; &amp; c\n• Done으로 이동"
    text = jsl.decision_slack_text("INNO-17", "t", "", "담당자A", "보류", "In Progress", "A&B <C>")
    assert text == "담당자A [A&amp;B &lt;C&gt; 확인: 보류] INNO-17 t\n• In Progress로 이동"


@pytest.mark.parametrize(
    "status, changes, why",
    [
        (
            "In Progress",
            [("2026-10-15T10:00:00.000+0900", "ready-to-done", "In Progress", "acc-assignee")],
            "팀장이 한 전환이 아니라 알리지 않음",
        ),
        (
            "To Do",
            [("2026-10-15T10:00:00.000+0900", "ready-to-done", "To Do", "acc-lead")],
            "지금 상태가 To Do라 알리지 않음",
        ),
        (
            "In Progress",
            [("2026-10-15T10:00:00.000+0900", "Backlog", "In Progress", "acc-lead")],
            "ready-to-done에서 지금 상태로 온 전환이 없어 알리지 않음",
        ),
        (
            "In Progress",
            [
                ("2026-10-14T10:00:00.000+0900", "ready-to-done", "Done", "acc-lead"),
                ("2026-10-15T10:00:00.000+0900", "Done", "In Progress", "acc-lead"),
            ],
            "ready-to-done에서 지금 상태로 온 전환이 없어 알리지 않음",
        ),
    ],
    ids=["담당자 전환", "다른 도착 상태", "ready-to-done 전환 없음", "마지막 전환과 지금 상태가 다름"],
)
def test_decision_skips(ja, tmp_path, slack, status, changes, why):
    summ = run_decision(ja, tmp_path, DecisionJira(status, changes))
    assert slack == []
    assert "건너뜀" in summ.rows[0] and why in summ.rows[0]


def test_decision_mode_requires_issue(ja, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["apply.py", "--mode", "decision", "--summary", str(tmp_path / "s.md")])
    with pytest.raises(SystemExit, match="decision 모드에는 --issue가 필요하다"):
        ja.main()


def test_decision_same_transition_is_sent_once(ja, tmp_path, slack):
    """같은 전환으로 decision이 두 번 실행되면 두 번째는 보내지 않는다(jira-doc 기록의 lastDecisionAt)."""
    fake = DecisionJira("Done", [("2026-10-15T10:00:00.000+0900", "ready-to-done", "Done", "acc-lead")])
    run_decision(ja, tmp_path, fake)
    assert fake.prop["lastDecisionAt"] == "2026-10-15T10:00:00.000+0900"
    summ = run_decision(ja, tmp_path, fake)
    assert len(slack) == 1
    assert "이미 알린 전환이라 다시 알리지 않음" in summ.rows[0]


# ---------------------------------------------------------------------- 모든 실행 모드의 실패 알림
@pytest.mark.parametrize(
    "mode, mode_ko",
    [
        ("review", "검수 모드"),
        ("digest", "정리 모드"),
        ("alerts", "주간 점검 모드"),
        ("report", "보고 모드"),
        ("decision", "확인 결과 알림 모드"),
    ],
)
def test_failure_slack_text_names_every_mode(ja, mode, mode_ko):
    failures = [{"key": "INNO-9", "title": "제목", "url": "", "reason": "Jira 오류: x"}]
    first = ja.failure_slack_text(mode, failures, {}, "").splitlines()[0]
    assert first == f"[jira-doc 실패] {mode_ko} ticket 1건 처리 실패 팀장 미지정"


@pytest.fixture
def run_url_env(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "5")


def test_run_failure_slack_text_with_error_and_issue(jsl, run_url_env):
    error = {"step": "1. 입력 수집", "error": "JiraError: <401> & 거부"}
    assert jsl.run_failure_slack_text("review", "INNO-17", error, {"acc-lead": "U-LEAD"}, "acc-lead") == (
        "[jira-doc 실패] 검수 모드 실행 실패 <@U-LEAD>\n"
        "• ticket: INNO-17\n"
        "• 단계: 1. 입력 수집\n"
        "• 원인: JiraError: &lt;401&gt; &amp; 거부\n"
        "GitHub Actions 실행: <https://github.com/org/repo/actions/runs/5|실행 로그>"
    )


def test_run_failure_slack_text_without_error_or_issue(jsl, monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert jsl.run_failure_slack_text("alerts", "", None, {}, "") == (
        "[jira-doc 실패] 주간 점검 모드 실행 실패 팀장 미지정\n• 원인: 기록 없음. 실행 로그 확인 필요"
    )
    text = jsl.run_failure_slack_text("report", "", None, {}, "acc-lead")
    assert text.splitlines()[0] == "[jira-doc 실패] 보고 모드 실행 실패 팀장(Slack 멘션 없음)"
    assert "ticket:" not in text
    # 모드가 빈 문자열이면 Summary 비고와 같게 "?"를 적고 공백이 두 칸 생기지 않음
    assert jsl.run_failure_slack_text("", "", None, {}, "").splitlines()[0] == "[jira-doc 실패] ? 실행 실패 팀장 미지정"


def run_failure_main(ja, monkeypatch, work, *, failed_mode="report", issue=""):
    """Jira 환경 변수 없이 apply.py main을 failure 모드로 실행하고 Summary 파일 내용을 돌려준다."""

    def forbidden():
        raise AssertionError("failure 모드는 Jira를 만들면 안 됨")

    for name in ("JIRA_CLOUD_ID", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ja, "Jira", forbidden)
    monkeypatch.setenv("JIRA_LEAD_ACCOUNT_ID", "acc-lead")
    summary = work / "summary.md"
    argv = ["apply.py", "--ctx", str(work / "ctx"), "--out", str(work / "out"), "--mode", "failure"]
    argv += ["--failed-mode", failed_mode, "--issue", issue, "--summary", str(summary)]
    monkeypatch.setattr(sys, "argv", argv)
    ja.main()
    return summary.read_text(encoding="utf-8")


def test_failure_mode_sends_run_error_to_integration_channel(ja, monkeypatch, tmp_path, slack):
    out = tmp_path / "out"
    out.mkdir()
    error = {"step": "4. 반영", "error": "JiraError: boom"}
    (out / "_run_error.json").write_text(json.dumps(error, ensure_ascii=False), encoding="utf-8")
    summary = run_failure_main(ja, monkeypatch, tmp_path)
    [text] = slack
    assert text.startswith("[jira-doc 실패] 보고 모드 실행 실패 <@U-LEAD>\n")
    assert "• 단계: 4. 반영" in text and "• 원인: JiraError: boom" in text
    assert "| - | - | failure | 전송 완료 |" in summary
    assert "<@" not in summary and "U-LEAD" not in summary  # Summary 사본에는 Slack 멤버 ID가 나가지 않음
    assert "팀장(Slack 멘션 없음)" in summary and "JiraError: boom" in summary


def test_failure_mode_without_run_error_says_no_record(ja, monkeypatch, tmp_path, slack):
    run_failure_main(ja, monkeypatch, tmp_path, failed_mode="digest", issue="INNO-17")
    [text] = slack
    assert "• ticket: INNO-17" in text and "• 원인: 기록 없음. 실행 로그 확인 필요" in text


def test_failure_mode_skips_when_ticket_failure_already_notified(ja, monkeypatch, tmp_path, slack):
    out = tmp_path / "out"
    out.mkdir()
    (out / "_failure_notified").touch()
    summary = run_failure_main(ja, monkeypatch, tmp_path, failed_mode="review", issue="INNO-17")
    assert slack == []
    assert "| - | - | failure | 건너뜀 | ticket 실패 알림을 이미 보냄 |" in summary


def test_report_mode_error_leaves_run_error_and_reraises(ja, jrr, monkeypatch, tmp_path):
    def broken(j, month, lead, summ):
        raise ja.JiraError("boom")

    monkeypatch.setattr(jrr, "run_report", broken)
    monkeypatch.setattr(ja, "Jira", lambda: None)
    out = tmp_path / "out"
    argv = [
        "apply.py",
        "--mode",
        "report",
        "--month",
        "2026-09",
        "--out",
        str(out),
        "--summary",
        str(tmp_path / "s.md"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(ja.JiraError, match="boom"):
        ja.main()
    error = json.loads((out / "_run_error.json").read_text(encoding="utf-8"))
    assert error == {"step": "4. 반영", "error": "JiraError: boom"}


def test_decision_mode_without_issue_leaves_no_run_error(ja, monkeypatch, tmp_path):
    out = tmp_path / "out"
    argv = ["apply.py", "--mode", "decision", "--out", str(out), "--summary", str(tmp_path / "s.md")]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit):
        ja.main()
    assert not out.exists()  # 인자 검사의 SystemExit은 실패 기록을 남기지 않음


def test_main_ticket_failure_marks_failure_notified(render, ja, monkeypatch, tmp_path, slack):
    """ticket 실패 알림을 보내면 5단계가 같은 실패를 다시 알리지 않도록 out/_failure_notified를 만든다."""
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, summary="export 오류")
    render.write_out(out, "INNO-17", render.task_verdict("INNO-17", "review", "no_verdict"), "* 요약")
    _, code, _ = run_main(render, ja, monkeypatch, tmp_path, "review", ["INNO-17"])
    assert code == 1 and len(slack) == 1
    assert (out / "_failure_notified").exists()


def test_main_ticket_failure_not_marked_when_slack_send_fails(render, ja, jsl, monkeypatch, tmp_path):
    """전송에 실패하면 표시 파일을 만들지 않아 5단계가 다시 알린다."""
    monkeypatch.setattr(jsl, "slack_post", lambda text: "전송 실패: x")
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, summary="export 오류")
    render.write_out(out, "INNO-17", render.task_verdict("INNO-17", "review", "no_verdict"), "* 요약")
    _, code, summary = run_main(render, ja, monkeypatch, tmp_path, "review", ["INNO-17"])
    assert code == 1 and "전송 실패: x" in summary
    assert not (out / "_failure_notified").exists()


def test_main_without_failure_leaves_no_failure_notified(render, ja, monkeypatch, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, summary="제목")
    v = render.task_verdict("INNO-17", "review", "pass") | {"items": [{**i, "done": True} for i in ITEMS], "extra": []}
    render.write_out(out, "INNO-17", v, "* 요약")
    _, code, _ = run_main(render, ja, monkeypatch, tmp_path, "review", ["INNO-17"])
    assert code == 0
    assert not (out / "_failure_notified").exists() and not (out / "_run_error.json").exists()


def test_decision_with_empty_lead_warns_without_sending(ja, tmp_path, slack):
    """팀장 accountId가 비면 전환한 사람을 확인할 수 없어 보내지 않고 Summary에 "경고"로 남긴다."""
    fake = DecisionJira("Done", [("2026-10-15T10:00:00.000+0900", "ready-to-done", "Done", "acc-lead")])

    def forbidden(key):
        raise AssertionError("lead가 비면 changelog를 읽으면 안 됨")

    fake.changelog = forbidden
    summ = ja.Summary(str(tmp_path / "summary.md"))
    ja.apply_decision(fake, "INNO-17", "", summ)
    assert slack == [] and fake.prop == {}
    assert summ.rows == [
        "| INNO-17 | Task | decision | 경고 | 팀장 accountId(JIRA_LEAD_ACCOUNT_ID)가 비어 알리지 않음 |"
    ]


# ---------------------------------------------------------------------- 시나리오 L1 테스트(docs/jira-doc-agent/test-scenarios.md)
@pytest.mark.parametrize("cid", ["T1", "T2"], ids=["TK-3 예상 산출물 비어 있음(T1)", "TK-1 진행 배경 비어 있음(T2)"])
def test_task_pass_with_failed_t1_or_t2_turns_into_hold(render, ja, jv, tmp_path, slack, cid):
    """CI agent 판정이 통과여도 스크립트 검사 T1이나 T2가 보완 필요이면 보류가 된다."""
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    checks = {cid: {"result": "fail", "detail": "작성된 내용 없음"}}
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, checks)
    v = render.task_verdict("INNO-17", "review", "pass")
    v |= {"items": [{**i, "done": True} for i in ITEMS], "extra": [], "requests": []}
    render.write_out(out, "INNO-17", v, "* 요약")
    j = render.FakeJira({"INNO-17": ""})
    ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "acc-lead", ja.Summary(str(tmp_path / "summary.md")))
    body = j.comments[0][1]
    assert "검수 결과: *보류*" in body
    assert f"* 스크립트 검사에 보완 필요 항목이 있어 통과에서 보류로 바꿈: {jv.check_label(cid)}\n" in body
    assert slack[0].startswith("<@U-LEAD> [보류] ")


@pytest.mark.parametrize(
    ("desc", "attachments", "cid", "detail"),
    [
        (bug_desc(BUG_INFO | {"발생 장비": ""}, PROBLEM, TOBE), 1, "B1", "기본 정보 중 비어 있는 항목 1개(발생 장비)"),
        (bug_desc(BUG_INFO, PROBLEM, TOBE), 0, "B1", "첨부 파일 0개"),
        (bug_desc(BUG_INFO, PROBLEM, "(정상 동작에 대한 설명)"), 1, "B2", "개선(To-Be) 내용 0줄"),
    ],
    ids=["BG-1 기본 정보 일부 비어 있음(B1)", "BG-2 첨부 파일 없음(B1)", "BG-4 개선(To-Be) 비어 있음(B2)"],
)
def test_bug_pass_with_failed_b1_or_b2_turns_into_hold(render, ja, jp, jv, tmp_path, desc, attachments, cid, detail):
    """Bug에서 CI agent 판정이 통과여도 스크립트 검사 B1이나 B2가 보완 필요이면 보류가 된다."""
    pre = BUG_PRE | jp.check_bug_template(desc, attachments)
    assert pre[cid]["result"] == "fail"
    j = run_type_review(render, ja, tmp_path, "Bug", pre, BUG_AGENT_CHECKS, BUG_FEEDBACK)
    body = j.comments[0][1]
    assert "검수 결과: *보류*" in body
    assert f"* 스크립트 검사에 보완 필요 항목이 있어 통과에서 보류로 바꿈: {jv.check_label(cid)}\n" in body
    assert detail in body


@pytest.mark.parametrize(
    ("content", "want"),
    [
        ("* " + "가" * 39, "fail"),
        ("* " + "가" * 40, "pass"),
        ("* 제기 이유\n* 결정할 것", "pass"),
    ],
    ids=["IS-2 1줄 39자", "IS-2 1줄 40자", "IS-2 2줄"],
)
def test_issue_content_i2_boundary_is_40_chars_for_one_line(jp, content, want):
    """이슈 내용이 1줄이면 40자부터 I2를 통과하고, 2줄 이상이면 길이와 관계없이 통과한다."""
    desc = f"h2. 이슈 유형\n\n* [x] 제안\n\nh2. 이슈 내용\n\n{content}\n"
    assert jp.check_issue_template(desc)["I2"]["result"] == want


@pytest.mark.parametrize(
    ("verdict", "items", "tail"),
    [
        ("escalate", ITEMS, "팀장 확인 필요: [~accountid:acc-lead]"),
        ("pass", [{**i, "done": True} for i in ITEMS], "[~accountid:acc-assignee]"),
        ("fix", ITEMS, "[~accountid:acc-assignee]"),
    ],
    ids=["검토 요청은 팀장", "통과는 담당자", "보류는 담당자"],
)
def test_review_comment_ends_with_mention_by_verdict(render, ja, tmp_path, verdict, items, tail):
    """AL-1: 검수 comment는 검토 요청이면 팀장, 통과와 보류이면 담당자 멘션으로 끝난다."""
    j, _ = run_review(render, ja, tmp_path, verdict, items)
    body = j.comments[0][1]
    assert body.endswith(f"\n\n{tail}")
    others = {"[~accountid:acc-lead]", "[~accountid:acc-assignee]"} - {tail.split(": ")[-1]}
    assert not any(m in body for m in others)


def test_sanitize_mentions_keeps_only_allowed_accounts(jcm):
    """AL-1: sanitize_mentions는 담당자와 팀장 외 accountId 멘션을 지운다."""
    text = "a [~accountid:acc-other] b [~accountid:acc-lead] c [~accountid:acc-assignee] d [~accountid:712020:xyz]"
    assert jcm.sanitize_mentions(text, {"acc-lead", "acc-assignee"}) == (
        "a  b [~accountid:acc-lead] c [~accountid:acc-assignee] d "
    )


def test_decision_send_failure_does_not_save_last_decision_at(ja, jsl, tmp_path, monkeypatch, slack):
    """AL-4: 알림 전송이 실패하면 lastDecisionAt을 저장하지 않아 다음 실행에서 다시 보낸다."""
    fake = DecisionJira("Done", [("2026-10-15T10:00:00.000+0900", "ready-to-done", "Done", "acc-lead")])
    monkeypatch.setattr(jsl, "slack_post", lambda text: "전송 실패: x")
    summ = run_decision(ja, tmp_path, fake)
    assert "lastDecisionAt" not in fake.prop
    assert "전송 실패: x" in summ.rows[0]
    monkeypatch.setattr(jsl, "slack_post", lambda text: slack.append(text) or "전송 완료")
    run_decision(ja, tmp_path, fake)
    assert len(slack) == 1 and fake.prop["lastDecisionAt"] == "2026-10-15T10:00:00.000+0900"


def test_failure_slack_text_marks_unassigned_lead_in_first_line(jsl):
    """AL-5: 팀장 accountId가 비면 실패 알림 첫 줄에 "팀장 미지정"을 적는다."""
    failures = [{"key": "INNO-9", "title": "제목", "url": "", "reason": "Jira 오류: x"}]
    first = jsl.failure_slack_text("review", failures, {"acc-lead": "U-LEAD"}, "").splitlines()[0]
    assert first == "[jira-doc 실패] 검수 모드 ticket 1건 처리 실패 팀장 미지정"


def scan_alert_checks(jp, tmp_path, status, changes, created="2026-09-28T08:00:00.000+0900"):
    """INNO-43의 주간 점검 데이터로 run_scan을 NOW(2026-09-29 화 09:00)에 실행하고 알림의 검사 ID 목록을 돌려준다."""
    scan = {
        "key": "INNO-43",
        "type": "Task",
        "status": status,
        "created": created,
        "humanComments": [],
        "statusChanges": [{"created": at, "field": "status", "to": to} for at, to in changes],
    }
    p = tmp_path / "INNO-43.json"
    p.write_text(json.dumps(scan), encoding="utf-8")
    jp.run_scan(p, NOW)
    return [a["check"] for a in json.loads((tmp_path / "INNO-43.alerts.json").read_text(encoding="utf-8"))["alerts"]]


@pytest.mark.parametrize(
    ("status", "stop_at", "want"),
    [
        ("Backlog", "2026-09-28T10:00:00.000+0900", []),
        ("Backlog", "2026-09-28T09:00:00.000+0900", ["R4"]),
        ("In Progress", "2026-09-25T10:00:00.000+0900", []),
    ],
    ids=["Backlog에서 23시간은 알림 없음", "Backlog에서 24시간은 R4 알림", "In Progress이면 알림 없음"],
)
def test_scan_r4_boundary_is_24_hours_in_backlog(jp, tmp_path, status, stop_at, want):
    """AL-6: R4는 지금 상태가 Backlog이고 사유 없는 stop 뒤 24시간 이상일 때만 알린다."""
    assert scan_alert_checks(jp, tmp_path, status, [(stop_at, "Backlog")]) == want


@pytest.mark.parametrize(
    ("started", "want"),
    [("2026-09-23T10:00:00.000+0900", []), ("2026-09-22T10:00:00.000+0900", ["A1"])],
    ids=["마지막 활동 뒤 4영업일은 알림 없음", "마지막 활동 뒤 5영업일은 A1 알림"],
)
def test_scan_a1_boundary_is_5_business_days(jp, tmp_path, started, want):
    """AL-6: A1은 In Progress에서 마지막 활동 뒤 5영업일 이상일 때 알린다."""
    changes = [(started, "In Progress")]
    # ticket 생성 시각도 활동으로 세므로, 활동 시각이 In Progress 전환이 되게 생성 시각을 앞에 둔다
    assert scan_alert_checks(jp, tmp_path, "In Progress", changes, created="2026-09-01T10:00:00.000+0900") == want


def test_weekly_lines_limit_items_per_section_to_15(ja, tmp_path, slack):
    """AL-6: 같은 검사 알림이 16건이면 15건만 적고 "외 1건" 줄을 적는다."""
    extra = [
        {
            "key": f"INNO-{n}",
            "summary": "라벨 재수집",
            "url": f"https://example.atlassian.net/browse/INNO-{n}",
            "assignee": {"accountId": "acc-assignee", "displayName": "담당자A"},
            "alerts": [{"check": "R4", "code": "HOLD_REASON_MISSING:2026-09-08", "detail": "stop 사유 comment 필요"}],
        }
        for n in range(41, 56)
    ]
    run_weekly(ja, tmp_path, "acc-lead", extra)
    lines = slack[-1].splitlines()
    assert any("16건" in line for line in lines)
    assert sum(" · stop 사유 comment 필요" in line for line in lines) == 15
    assert lines[-1] == "    • 외 1건" and sum("외 1건" in line for line in lines) == 1


def test_weekly_without_alerts_sends_no_issue_message(ja, tmp_path, slack):
    """AL-6: 알림 파일이 없으면 주간 메시지에 "점검 결과 이상 없음"을 보낸다."""
    summ = ja.Summary(str(tmp_path / "summary.md"))
    ja.slack_weekly(tmp_path / "ctx", summ, "acc-lead")
    assert len(slack) == 1 and slack[0].splitlines()[1:] == ["점검 결과 이상 없음"]
    assert "알림 없음" in summ.rows[0]
