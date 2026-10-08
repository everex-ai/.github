"""jira-doc이 결과로 산출하는 문서 중 scripts/apply.py, scripts/precheck.py가 조립하는 부분의 형식.

scripts/apply.py, scripts/precheck.py는 scripts/review/의 같은 이름 파일과 섞이지 않게 모듈 이름 jira_apply,
jira_precheck로 불러온다(tests/review/는 scripts/review/를 sys.path에 넣고 apply, precheck로 불러온다).
fixture를 conftest.py에 두지 않는 이유: tests/review/test_precheck.py가 `from conftest import ...`로
tests/review/conftest.py를 불러오므로, tests/jira/conftest.py가 있으면 모듈 이름 conftest가 겹친다.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def render():
    """scripts/dev/render_jira_samples.py 모듈."""
    name = "render_jira_samples"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "dev" / "render_jira_samples.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


@pytest.fixture(scope="module")
def ja(render):
    """scripts/apply.py (모듈 이름 jira_apply)."""
    return render.load_modules()[0]


@pytest.fixture(scope="module")
def jp(render):
    """scripts/precheck.py (모듈 이름 jira_precheck)."""
    return render.load_modules()[1]


@pytest.fixture(scope="module")
def jc(render):
    """scripts/collect.py (모듈 이름 jira_collect). tests/review/가 불러오는 scripts/review/collect.py와 섞이지 않게 한다."""
    return render.load_module("jira_collect", ROOT / "scripts" / "collect.py")


@pytest.fixture(autouse=True)
def slack(ja, monkeypatch, tmp_path_factory):
    """apply_issue가 Slack에 보내려던 본문을 모은다. 실제 Slack에는 보내지 않는다."""
    sent: list[str] = []
    users = tmp_path_factory.mktemp("slack") / "slack-users.json"
    users.write_text(json.dumps({"acc-assignee": "U-ASSIGNEE", "acc-lead": "U-LEAD"}), encoding="utf-8")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_USERS_JSON", raising=False)  # 대응표를 secret이 아니라 아래 임시 파일에서 읽게 한다
    monkeypatch.setattr(ja, "SLACK_USERS", users)
    monkeypatch.setattr(ja, "slack_post", lambda text: sent.append(text) or "전송 완료")
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
    render, ja, work, verdict, items, status="ready-to-done", lead="acc-lead", tldr=None, mode="review", j=None
):
    """INNO-17(Task)을 가짜 Jira로 실행하고 (FakeJira, Summary)를 돌려준다. 기본은 검수 모드."""
    ctx, out = work / "ctx", work / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {}, status=status)
    v = render.task_verdict("INNO-17", mode, verdict) | {"items": items, "extra": []}
    render.write_out(out, "INNO-17", v, "* 요약")
    if tldr is not None:
        (out / "INNO-17" / "tldr.wiki").write_text(tldr, encoding="utf-8")
    if j is None:
        j = render.FakeJira({"INNO-17": ""})
    summ = ja.Summary(str(work / "summary.md"))
    ja.apply_issue(j, ctx / "INNO-17", out, mode, False, lead, summ)
    return j, summ


def test_deliverables_request_line(ja):
    body = ja.deliverables_wiki(EXPECTED, ITEMS, EXTRA)
    assert body.startswith("예상 산출물 2개 중 1개 달성, 초과 달성 1건\n")  # 달성 요약을 맨 앞에 적음
    assert "#* 요청: 평가 리포트 링크 comment 필요" in body
    assert "필요한 것" not in body
    # 달성 항목에는 요청 줄이 없다
    assert body.split("# *오류율 1% 미만*")[0].count("#* 요청:") == 0


def test_deliverables_reason_lines_by_mode(ja):
    review = ja.deliverables_wiki(EXPECTED, ITEMS, EXTRA, final=True)
    assert "#* 미달성 사유: 사유 미기재" in review and "** 추가 사유: 사유 미기재" in review
    digest = ja.deliverables_wiki(EXPECTED, ITEMS, EXTRA, final=False)  # F3 정리: 사유가 기록에 없으면 사유 줄 생략
    assert "미달성 사유" not in digest and "추가 사유" not in digest
    items = [ITEMS[0], {**ITEMS[1], "why": "라벨 재수집이 다음 분기로 밀림"}]
    assert "#* 미달성 사유: 라벨 재수집이 다음 분기로 밀림" in ja.deliverables_wiki(EXPECTED, items, [], final=False)


def test_t8_names_items_and_request(ja):
    t8 = ja.check_t8(EXPECTED, ITEMS, EXTRA)
    assert t8["result"] == "fail"
    assert t8["detail"] == "사유 없는 미달성 항목 2번, 사유 없는 초과 달성 1건('대시보드 추가')"
    assert t8["request"] == "미달성 항목 2번의 미달성 사유와 초과 달성 '대시보드 추가'의 추가 사유 comment 필요"
    feedback = [{"id": "T5", "points": [], "request": "3번 항목의 완료 기준 추가 필요"}]
    checks = [t8, {"id": "T5", "result": "fail"}, {"id": "T6", "result": "pass"}]
    assert ja.doc_requests(checks, feedback) == [t8["request"], "3번 항목의 완료 기준 추가 필요"]


def test_review_comment_puts_verdict_reasons_first(ja):
    checks = [{"id": "A2", "result": "fail", "detail": "완료 기준 변경"}]
    text = ja.review_comment(
        "escalate", checks, EXPECTED, ITEMS, EXTRA, "* 요약", ["평가 리포트 링크 comment 필요"], ["A2로 검토 요청"]
    )
    assert text.startswith("검수 결과: *검토 요청*\n* A2로 검토 요청\n\n예상 산출물 2개 중 1개 달성, 초과 달성 1건")
    assert text.index("*결과 요약*") < text.index("*요청*") < text.index("|| 검사 || 결과 || 내용 ||")


def test_doc_review_comment_first_line(ja):
    t8 = ja.check_t8(EXPECTED, ITEMS, EXTRA)
    text = ja.doc_review_comment([t8], [], "acc-lead")
    assert text.startswith("문서화 리뷰 결과(관찰 모드): *보완 필요*\n\n|| 검사 || 결과 || 내용 ||")
    assert "*담당자에게 보낼 요청 후보*\n# 미달성 항목 2번의 미달성 사유" in text
    assert text.endswith("\n\n[~accountid:acc-lead]")
    ok = ja.doc_review_comment([{"id": "T4", "result": "pass", "detail": "a"}], [], "")
    assert ok.startswith("문서화 리뷰 결과(관찰 모드): *통과*\n\n")
    assert "요청 후보" not in ok and "accountid" not in ok
    assert ja.doc_review_md("INNO-1", [t8], [])[0] == "### 문서화 리뷰 INNO-1(관찰 모드): 보완 필요"


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
def test_recheck_verdict(ja, v, itype, items, checks, gate, doc_fails, want):
    assert ja.recheck_verdict(v, itype, items, checks, gate, doc_fails) == want


def test_hold_moves_ready_task_back_and_mentions_assignee(render, ja, tmp_path, slack):
    j, summ = run_review(render, ja, tmp_path, "fix", ITEMS, status="Ready-to-Done")
    assert j.transitioned_to == [("INNO-17", "In Progress")]
    assert "검수 결과: *보류*\n* 상태를 in-progress로 되돌림\n" in j.comments[0][1]
    assert slack[0].startswith(
        "<@U-ASSIGNEE> [INNO-17] 검수 결과: 보류. 상태를 in-progress로 되돌림\n"
        f'<{ISSUE_URL}|INNO-17>\n보류 이유\n    • 사유 없는 미달성: 예상 산출물 2번 "오류율 1% 미만"\n요청\n    1. '
    )
    assert slack[0].endswith(f"\n근거: <{ISSUE_URL}?focusedCommentId=10200|검수 comment>")
    assert not any(e in slack[0] for e in FORMAL_ENDINGS)
    assert "Slack 알림 전송(담당자)" in summ.rows[0]


def test_hold_keeps_status_outside_ready(render, ja, tmp_path, slack):
    j, summ = run_review(render, ja, tmp_path, "fix", ITEMS, status="In Progress")
    assert j.transitioned_to == []
    assert "* 지금 상태가 In Progress라 in-progress로 되돌리지 않음\n" in j.comments[0][1]
    assert "지금 상태가 In Progress라 in-progress로 되돌리지 않음" in summ.rows[0]
    assert slack[0].startswith("<@U-ASSIGNEE> [INNO-17] 검수 결과: 보류. 지금 상태 In Progress 유지\n")


def test_request_keeps_status_and_mentions_lead(render, ja, tmp_path, slack):
    j, summ = run_review(render, ja, tmp_path, "fix", [ITEMS[0], REASONED])
    assert j.transitioned_to == []
    assert f"검수 결과: *검토 요청*\n* {HOLD_TO_REQUEST}\n* 상태를 바꾸지 않고 팀장 확인을 요청함\n" in j.comments[0][1]
    assert slack[0].startswith("<@U-LEAD> [INNO-17] 검수 결과: 검토 요청. 상태 변경 없음\n")
    assert (
        '\n    • 예상 산출물 2번 "오류율 1% 미만": 라벨 재수집이 다음 분기로 밀림(<https://example.com/c/1|comment 2026-09-15>)\n'
        f"    • {HOLD_TO_REQUEST}\n팀장이 task를 확인한 뒤 in-progress 또는 done으로 직접 전환 필요\n"
    ) in slack[0]
    assert "\n요청\n" not in slack[0]
    assert not any(e in slack[0] for e in FORMAL_ENDINGS)
    assert "Slack 알림 전송(팀장)" in summ.rows[0]
    run_review(render, ja, tmp_path / "no-lead-id", "fix", [ITEMS[0], REASONED], lead="acc-unknown")
    assert slack[1].startswith("팀장 [INNO-17] 검수 결과: 검토 요청. ")


def test_pass_asks_lead_to_move_done(render, ja, tmp_path, slack):
    j, _ = run_review(render, ja, tmp_path, "pass", [ITEMS[0], {**ITEMS[1], "done": True}])
    assert j.transitioned_to == []
    assert "검수 결과: *통과*\n* 상태를 바꾸지 않고 팀장 확인을 요청함\n" in j.comments[0][1]
    assert slack[0].startswith(
        "<@U-LEAD> [INNO-17] 검수 결과: 통과. 상태 변경 없음\n"
        f"<{ISSUE_URL}|INNO-17>\n통과 이유\n    • 예상 산출물 2개 중 2개 달성\n    • 요약\n"
        "팀장이 task를 확인한 뒤 done으로 직접 전환 필요\n"
    )
    assert not any(e in slack[0] for e in FORMAL_ENDINGS)


def test_slack_failure_stays_in_summary(render, ja, tmp_path, monkeypatch):
    monkeypatch.setattr(ja, "slack_post", lambda text: "전송 실패: HTTP Error 500")
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


def run_weekly(ja, work):
    """주간 점검을 실행하고 Actions Summary 파일에 쓴 글을 돌려준다."""
    write_weekly_scan(work / "ctx")
    summ = ja.Summary(str(work / "summary.md"))
    ja.slack_weekly(work / "ctx", summ)
    summ.flush()
    return (work / "summary.md").read_text(encoding="utf-8")


def test_slack_users_env_comes_before_file(render, ja, tmp_path, monkeypatch, slack, capsys):
    monkeypatch.setenv("SLACK_USERS_JSON", json.dumps({"acc-assignee": "U-ENV", "acc-lead": "U-ENV-LEAD"}))
    assert ja.load_slack_users() == ({"acc-assignee": "U-ENV", "acc-lead": "U-ENV-LEAD"}, None)
    _, summ = run_review(render, ja, tmp_path, "fix", ITEMS)
    assert slack[0].startswith("<@U-ENV> [INNO-17] 검수 결과: 보류. ")
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
    assert slack[0].startswith("<@U-ASSIGNEE> [INNO-17] 검수 결과: 보류. ")


@pytest.mark.parametrize(
    "value", ['{"acc-assignee": "U-SECRET"', '["U-SECRET"]'], ids=["JSON 형식 오류", "객체가 아님"]
)
def test_slack_users_invalid_env_sends_names_and_warns(render, ja, tmp_path, monkeypatch, slack, capsys, value):
    monkeypatch.setenv("SLACK_USERS_JSON", value)
    assert ja.load_slack_users() == ({}, SLACK_USERS_FORMAT_WARNING)
    # 검수 모드: 파일에 담당자 ID가 있어도 사용하지 않고, 담당자 이름 자리(이름이 없으면 "담당자 미지정")로 보냄
    _, summ = run_review(render, ja, tmp_path / "review", "fix", ITEMS)
    assert slack[0].startswith("담당자 미지정 [INNO-17] 검수 결과: 보류. ")
    assert SLACK_USERS_FORMAT_WARNING in summ.rows[0]
    # 주간 점검: Slack 본문에 멘션이 없고 담당자 이름이 있으며, 경고가 Summary에 남음
    summary = run_weekly(ja, tmp_path / "weekly")
    assert "<@" not in slack[-1] and "담당 담당자A, " in slack[-1]
    assert SLACK_USERS_FORMAT_WARNING in summary
    assert "U-SECRET" not in summary + "".join(summ.rows) + capsys.readouterr().out  # secret 값을 출력하지 않음


def test_weekly_summary_copy_has_names_not_mentions(ja, tmp_path, monkeypatch, slack, capsys):
    monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
    summary = run_weekly(ja, tmp_path)
    head = slack[-1].splitlines()[0]
    assert head.endswith("전달사항] <@U-ASSIGNEE>")  # Slack 머리줄의 멘션 목록
    assert "<@U" in slack[-1] and "담당 <@U-ASSIGNEE>, " in slack[-1]
    copy = summary.split("### 주간 점검 (Slack 전송 완료)\n\n```\n")[1].split("\n```")[0]
    assert copy.splitlines()[0] == head.split("] ")[0] + "]"  # Summary 사본의 머리줄에는 멘션 목록이 없음
    assert "<@" not in summary and "담당 담당자A, " in copy
    # 파일 대응표의 Slack 멤버 ID는 print와 Summary 파일(행과 사본)에 나가지 않음
    assert "U-ASSIGNEE" not in capsys.readouterr().out + summary


@pytest.mark.parametrize(
    ("env", "file_table"),
    [("{}", "fixture"), ('{"_설명": "설명만 있음"}', "fixture"), ("", {"_설명": "설명만 있음"}), (None, None)],
    ids=["secret이 빈 객체", "secret에 설명만 있음", "secret이 비고 파일에 설명만 있음", "secret과 파일이 모두 없음"],
)
def test_slack_users_empty_table_warns(render, ja, tmp_path, monkeypatch, slack, env, file_table):
    if env is None:
        monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
    else:
        monkeypatch.setenv("SLACK_USERS_JSON", env)
    if file_table != "fixture":  # "fixture"는 fixture slack의 임시 파일(항목 2개)을 그대로 둠
        users = tmp_path / "slack-users.json"
        if file_table is not None:
            users.write_text(json.dumps(file_table, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ja, "SLACK_USERS", users)
    assert ja.load_slack_users() == ({}, SLACK_USERS_EMPTY_WARNING)
    _, summ = run_review(render, ja, tmp_path / "review", "fix", ITEMS)
    assert slack[0].startswith("담당자 미지정 [INNO-17] 검수 결과: 보류. ")
    assert SLACK_USERS_EMPTY_WARNING in summ.rows[0]
    summary = run_weekly(ja, tmp_path / "weekly")
    assert "<@" not in slack[-1] and "담당 담당자A, " in slack[-1]
    assert SLACK_USERS_EMPTY_WARNING in summary


@pytest.mark.parametrize("source", ["env", "file"])
def test_slack_users_skips_description_and_non_string_values(ja, tmp_path, monkeypatch, source):
    table = {"_설명": "Jira accountId -> Slack 멤버 ID", "acc-assignee": "U-ASSIGNEE", "acc-num": 7, "acc-list": ["U1"]}
    table["acc-null"] = None
    if source == "env":
        monkeypatch.setenv("SLACK_USERS_JSON", json.dumps(table, ensure_ascii=False))
    else:
        monkeypatch.delenv("SLACK_USERS_JSON", raising=False)
        (tmp_path / "slack-users.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ja, "SLACK_USERS", tmp_path / "slack-users.json")
    assert ja.load_slack_users() == ({"acc-assignee": "U-ASSIGNEE"}, None)


@pytest.mark.parametrize("prefix", ["검수 결과: ", "판정: "], ids=["새 접두어", "이전 접두어"])
def test_tldr_verdict_follows_changed_result(render, ja, tmp_path, prefix):
    tldr = f"h2. Summary\n* 결과\n* {{color:#6b778c}}상태: ready-to-done, {prefix}보류, 갱신: 2026-09-29 09:00{{color}}"
    j, _ = run_review(render, ja, tmp_path, "fix", [ITEMS[0], REASONED], tldr=tldr)
    field = j.fields["INNO-17"][render.FakeJira.tldr_field]
    assert f"{prefix}검토 요청, 갱신" in field and f"{prefix}보류" not in field


def test_wiki_to_slack(ja):
    text = "{color:#6b778c}근거: [PR #42|https://example.com/42], [comment|https://e.com/c?focusedCommentId=1]{color}"
    assert (
        ja.wiki_to_slack(text) == "근거: <https://example.com/42|PR #42>, <https://e.com/c?focusedCommentId=1|comment>"
    )
    assert ja.wiki_to_slack("[~accountid:acc-lead] 확인 필요") == "[~accountid:acc-lead] 확인 필요"
    assert ja.wiki_to_slack("지연 < 50ms & [PR|https://e.com/?a=1&b=2]") == (
        "지연 &lt; 50ms &amp; <https://e.com/?a=1&amp;b=2|PR>"
    )


def test_slack_text_escapes_title_and_requests(ja):
    text = ja.review_slack_text(
        "fix", "INNO-1", "지연 <50ms & 경량화", "https://e.com/INNO-1", "담당자", "", [], ["a > b 확인 필요"], ""
    )
    assert "<https://e.com/INNO-1|INNO-1 지연 &lt;50ms &amp; 경량화>" in text
    assert "\n    1. a &gt; b 확인 필요" in text


def test_slack_post_reports_timeout(ja, monkeypatch):
    monkeypatch.undo()  # autouse fixture slack이 바꾼 slack_post를 원래 함수로 되돌린다
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example.com/x")

    def timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(ja.urllib.request, "urlopen", timeout)
    assert ja.slack_post("본문") == "전송 실패: timed out"


def test_transition_failure_is_reported_and_slack_still_sent(render, ja, tmp_path, slack):
    j = render.FakeJira({"INNO-17": ""})

    def fail(key, status):
        raise ja.JiraError("'In Progress'로 가는 전환이 INNO-17에 없음")

    j.transition_to = fail
    _, summ = run_review(render, ja, tmp_path, "fix", ITEMS, j=j)
    note = "in-progress 전환 실패: 'In Progress'로 가는 전환이 INNO-17에 없음"
    assert f"* {note}\n" in j.comments[0][1]
    assert note in summ.rows[0]
    assert slack[0].startswith("<@U-ASSIGNEE> [INNO-17] 검수 결과: 보류. in-progress 전환 실패\n")


def test_comment_failure_after_transition_keeps_state_note_and_slack(render, ja, tmp_path, slack):
    j = render.FakeJira({"INNO-17": ""})

    def fail(key, body):
        raise ja.JiraError("POST /issue/INNO-17/comment -> 500: boom")

    j.add_comment = fail
    _, summ = run_review(render, ja, tmp_path, "fix", ITEMS, j=j)
    assert j.transitioned_to == [("INNO-17", "In Progress")]
    assert "상태를 in-progress로 되돌림" in summ.rows[0]
    assert "검수 comment 등록 실패: POST /issue/INNO-17/comment -> 500: boom" in summ.rows[0]
    assert slack[0].startswith("<@U-ASSIGNEE> [INNO-17] 검수 결과: 보류. 상태를 in-progress로 되돌림\n")
    assert "근거:" not in slack[0]


def test_no_comment_id_means_no_evidence_line(render, ja, tmp_path, slack):
    j = render.FakeJira({"INNO-17": ""})
    j.add_comment = lambda key, body: j.comments.append((key, body))  # 반환값 없음
    run_review(render, ja, tmp_path, "fix", ITEMS, j=j)
    assert "근거:" not in slack[0]


def test_digest_mode_has_no_transition_or_slack(render, ja, tmp_path, slack):
    tldr = "h2. Summary\n* 결과\n* {color:#6b778c}상태: In Progress, 갱신: 2026-09-29 09:00{color}"
    j, _ = run_review(render, ja, tmp_path, None, ITEMS, tldr=tldr, mode="digest")
    assert j.transitioned_to == [] and slack == [] and j.comments == []
    assert j.fields["INNO-17"][render.FakeJira.tldr_field] == tldr


def test_jira_transition_to_matches_target_status(ja):
    jr = object.__new__(ja.Jira)
    calls = []
    jr.transitions = lambda key: [
        {"id": "5", "name": "In Progress", "to": {"name": "Done"}},
        {"id": "11", "name": "reopen", "to": {"name": "IN PROGRESS"}},
        {"id": "12", "name": "start", "to": {"name": "In Progress"}},
    ]
    jr._req = lambda method, path, params=None, body=None, ok404=False: calls.append((method, path, body))
    jr.transition_to("INNO-1", "In Progress")
    assert calls == [("POST", "/issue/INNO-1/transitions", {"transition": {"id": "11"}})]
    with pytest.raises(ja.JiraError, match="'Backlog'로 가는 전환이 INNO-1에 없음"):
        jr.transition_to("INNO-1", "Backlog")


def test_failure_comment(ja, monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    assert ja.failure_comment("verdict.json 없음", "acc-lead") == (
        "자동 검수 실행 실패: verdict.json 없음\n팀장 확인 필요\n"
        "근거: [GitHub Actions 실행 로그|https://github.com/org/repo/actions/runs/1]\n[~accountid:acc-lead]"
    )
    monkeypatch.delenv("GITHUB_RUN_ID")
    assert ja.failure_comment("x", "") == "자동 검수 실행 실패: x\n팀장 확인 필요"


def test_validate_verdict_message_names_doc_review_checks(ja):
    v = {"issueKey": "INNO-1", "mode": "review", "verdict": "pass", "checks": [], "items": [], "extra": []}
    v |= {"requests": [], "feedback": [{"id": "T8", "points": []}]}
    err = ja.validate_verdict(v, "review", "INNO-1")
    assert (
        "진행 배경 충실도 (T4), 예상 산출물 분할 단위 (T5), 진행 기록 comment (T6), sub-task 기록 (T7) 중 하나" in err
    )
    assert "~" not in err


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
        "failure-comment.wiki",
        "missing-alert-comment.wiki",
        "weekly-slack.txt",
        "slack-review-hold.txt",
        "slack-review-request.txt",
        "slack-review-pass.txt",
    }
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert not any(e in text for e in FORMAL_ENDINGS), p.name
        assert "~" not in text.replace("[~accountid:", ""), p.name


def test_render_restores_module_state(render, ja, tmp_path):
    before = (ja.now_stamp, ja.dt, ja.slack_post, ja.SLACK_USERS)
    render.render(tmp_path)
    assert (ja.now_stamp, ja.dt, ja.slack_post, ja.SLACK_USERS) == before


def test_sources_have_no_formal_endings():
    for rel in ("scripts/apply.py", "scripts/precheck.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not any(e in text for e in FORMAL_ENDINGS), rel


def test_deliverables_summary_counts_expected_ids_only(ja):
    items = [*ITEMS, {"id": "9", "done": True, "result": "번호 밖 결과"}]
    assert ja.deliverables_summary(EXPECTED, items, []) == "예상 산출물 2개 중 1개 달성"


def test_short_name_strips_wiki_link_and_truncates(ja):
    assert ja.short_name("[PR #46 오류 로그|https://example.com/46] 추가") == "PR #46 오류 로그 추가"
    assert ja.short_name("가" * 50) == "가" * 40 + "…"


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
    a2 = jp.check_a2("Bug", changelog, "개선")
    assert a2["result"] == "fail"
    assert a2["detail"].startswith("2026-09-20 10:12에 '개선' 구역이 바뀜 (마지막 request 전환(")
    assert a2["detail"].endswith(") 2026-09-19 17:40 이후)")
    assert jp.check_a2("Bug", [], "개선")["detail"].startswith("request 전환(")


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
    """INNO-30 샘플(scripts/dev/render_jira_samples.py)은 B1, B2를 통과하고, 개선(To-Be)이 바뀌어 A2가 fail이다."""
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
    assert alerts["INNO-40"][0]["detail"] == "2026-09-22 stop 뒤 6일 경과(지난 시간을 24시간 단위로 셈, 나머지 버림)"
    stale = alerts["INNO-41"][0]
    assert (
        stale["check"] == "A1"
        and stale["detail"] == "마지막 활동 2026-09-02 뒤 영업일 기준 19일 경과(토요일과 일요일 제외)"
    )
    assert stale["message"].count(". ") == 1 and stale["message"].endswith("필요")


def test_t3_mismatch_detail(render, ja, tmp_path):
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


def test_check_table_uses_same_labels_for_all_checks(ja):
    checks = [{"id": "T1", "result": "pass", "detail": "a"}, {"id": "T5", "result": "fail", "detail": "b"}]
    checks.append({"id": "A2", "result": "n/a", "detail": "c"})
    table = ja.check_table(checks)
    assert "| 예상 산출물 작성 (T1) | ✅ 만족 | a |" in table
    assert "| 예상 산출물 분할 단위 (T5) | ❌ 보완 필요 | b |" in table
    assert "| 완료 기준의 사후 변경 (A2) | ➖ 해당 없음 | c |" in table
    assert "통과" not in table and "실패" not in table


def test_check_table_keeps_wiki_links_in_cells(ja):
    link = "[stop 사유 comment 2026-09-25|https://e.com/c?focusedCommentId=1]"
    checks = [{"id": "R4", "result": "pass", "detail": f"a|b {link} c|d"}, {"id": "T5", "result": "fail"}]
    feedback = [{"id": "T5", "points": ["[PR #1|https://e.com/1] 링크 밖 x|y", "둘째 줄"]}]
    table = ja.check_table(checks, feedback)
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


def test_gate_note_names_doc_review_checks(render, ja, tmp_path):
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


def test_tldr_missing_note_stays_out_of_comment(render, ja, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    render.write_issue(ctx, "INNO-17", "Task", EXPECTED, {})
    render.write_out(out, "INNO-17", render.task_verdict("INNO-17", "review", "fix"), "* 요약")
    (out / "INNO-17" / "tldr.wiki").unlink()
    j = render.FakeJira({"INNO-17": ""})
    summ = ja.Summary(str(tmp_path / "summary.md"))
    ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "", summ)
    assert all("TL;DR 출력 없음" not in body for _, body in j.comments)
    assert "TL;DR 출력 없음" in summ.rows[0]


def test_failure_comment_reasons(render, ja, tmp_path):
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    bodies = {}
    for key, content in (("INNO-1", None), ("INNO-2", "{not json")):
        render.write_issue(ctx, key, "Task", EXPECTED, {})
        (out / key).mkdir(parents=True)
        if content is not None:
            (out / key / "verdict.json").write_text(content, encoding="utf-8")
        j = render.FakeJira({})
        ja.apply_issue(j, ctx / key, out, "review", False, "", ja.Summary(str(tmp_path / "summary.md")))
        bodies[key] = j.comments[0][1]
    assert "판정 파일(verdict.json) 없음. 추정: CI agent 실행이 실패했거나 끝나지 않음" in bodies["INNO-1"]
    assert "판정 파일(verdict.json)을 JSON으로 읽을 수 없음" in bodies["INNO-2"]


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
    "request 전환(담당자가 완료를 요청해 task를 ready-to-done 상태로 보내는 Jira 전환) 필요"
)
LATE_URL = f"{ISSUE_URL}?focusedCommentId=1"  # 늦은 사유 comment(2026-09-25)의 링크
LATE_NOTE = "늦게 기록: stop 2026-09-08 뒤 다음 상태 변경 이후에 남긴"
R4_LATE_DETAIL = f"stop 1회 모두 사유 comment 있음. {LATE_NOTE} [stop 사유 comment 2026-09-25|{LATE_URL}]"
# Slack 알림의 이유 묶음에 들어가는 줄. wiki 링크는 Slack 링크로 바뀜
R4_LATE_LINE = (
    f"stop 사유 comment (R4): stop 1회 모두 사유 comment 있음. {LATE_NOTE} <{LATE_URL}|stop 사유 comment 2026-09-25>"
)
ESCALATE_REASON = (
    '예상 산출물 2번 "오류율 1% 미만": 라벨 재수집이 다음 분기로 밀림(<https://example.com/c/1|comment 2026-09-15>)'
)


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
def test_run_scan_skips_r4_alert_for_late_reason(ja, jp, tmp_path, late_reason, want_codes):
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
    lines, _ = ja.weekly_lines(tmp_path, {})  # 주간 점검 메시지의 R4 항목
    assert any(line.startswith("*stop 사유 미기재*") for line in lines) == bool(want_codes)
    if want_codes:  # 항목 줄은 묶음 제목 아래로 들여 씀(검수 알림과 같은 공백 4칸)
        assert any(line.startswith(f"{ja.SLACK_INDENT}• INNO-40 ") for line in lines)


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
    v |= {"items": [ITEMS[0], {**ITEMS[1], "done": True}], "extra": [], "requests": requests}
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
def test_r4_fail_adds_request_once_to_hold_slack(render, ja, jp, tmp_path, slack, given):
    j, _ = run_r4_fail(render, ja, jp, tmp_path, "pass", given)
    assert slack[0].startswith("<@U-ASSIGNEE> [INNO-17] 검수 결과: 보류. ")
    assert slack[0].count(R4_REQUEST) == 1
    requests = slack[0].split("\n요청\n")[1].split("\n근거:")[0].splitlines()
    want = given if R4_REQUEST in given else [*given, R4_REQUEST]
    assert requests == [f"    {i}. {r}" for i, r in enumerate(want, 1)]
    assert j.comments[0][1].count(R4_REQUEST) == 1  # 검수 comment의 요청에도 한 번만 들어감
    r4_reason = "stop 전환(In Progress → Backlog)에 대한 사유를 담당자가 comment에 남기지 않음(stop 시점: 2026-09-08)"
    assert "\n보류 이유\n" in slack[0] and f"\n    • {r4_reason}\n" in slack[0]


def test_r4_request_not_added_when_a2_makes_review_request(render, ja, jp, tmp_path, slack):
    a2 = {"result": "fail", "detail": "2026-09-26 10:00에 '예상 산출물' 구역이 바뀜"}
    j, summ = run_r4_fail(render, ja, jp, tmp_path, "pass", [], a2=a2)
    body = j.comments[0][1]
    assert body.startswith("[ai-doc-agent]\n검수 결과: *검토 요청*\n")
    assert R4_REQUEST not in body and "*요청*" not in body  # 검수 comment의 요청
    assert "requests" not in summ.rows[0]  # requests가 비어 있음
    assert "| stop 사유 comment (R4) | ❌ 보완 필요 |" in body
    assert R4_REQUEST not in slack[0]


def test_r4_request_not_added_in_digest_mode(render, ja, jp, tmp_path, slack):
    j, summ = run_r4_fail(render, ja, jp, tmp_path, None, [], mode="digest")
    assert j.comments == [] and slack == []
    assert "requests" not in summ.rows[0]  # requests가 비어 있음
    assert all(R4_REQUEST not in str(v) for v in j.fields["INNO-17"].values())


@pytest.mark.parametrize(
    ("v", "itype", "items", "changes", "want"),
    [
        ("pass", "Task", [ITEMS[0], {**ITEMS[1], "done": True}], [], ["예상 산출물 2개 중 2개 달성", "요약"]),
        ("escalate", "Task", [ITEMS[0], REASONED], [HOLD_TO_REQUEST], [ESCALATE_REASON, HOLD_TO_REQUEST]),
        ("escalate", "Bug", [], [], ["요약"]),
    ],
    ids=["통과", "검토 요청", "이유가 비면 결과 요약 줄 뒤에 붙임"],
)
def test_slack_reasons_show_late_r4(ja, v, itype, items, changes, want):
    late = [{"id": "R4", "result": "pass", "detail": R4_LATE_DETAIL, "late": True}]
    base = ja.review_slack_reasons(v, itype, EXPECTED, items, [], [], changes, "* 요약")
    got = ja.review_slack_reasons(v, itype, EXPECTED, items, [], late, changes, "* 요약")
    assert base == want and got == [*want, R4_LATE_LINE]
    on_time = [{"id": "R4", "result": "pass", "detail": "stop 1회 모두 사유 comment 있음"}]
    assert ja.review_slack_reasons(v, itype, EXPECTED, items, [], on_time, changes, "* 요약") == base


def test_slack_reasons_hold_omits_late_r4(ja):
    late = [{"id": "R4", "result": "pass", "detail": R4_LATE_DETAIL, "late": True}]
    got = ja.review_slack_reasons("fix", "Task", EXPECTED, ITEMS, [], late, [], "* 요약")
    assert got == ['사유 없는 미달성: 예상 산출물 2번 "오류율 1% 미만"']


def test_hold_reason_for_r4_is_a_sentence(ja, jp):
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
    got = ja.review_slack_reasons("fix", "Task", EXPECTED, ITEMS[:1], [], [t1, r4], [], "* 요약")
    assert got == [f"{ja.check_label('T1')}: 예상 산출물 구역에 항목 없음", want]
