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


FORMAL_ENDINGS = ("습니다", "주세요")

EXPECTED = [{"id": "1", "text": "재현 테스트 추가"}, {"id": "2", "text": "오류율 1% 미만"}]
ITEMS = [
    {"id": "1", "done": True, "result": "재현 테스트 3건 추가", "evidence": "[PR #42|https://example.com/42]"},
    {"id": "2", "done": False, "result": "수치만 있음", "why": "", "reason": "평가 리포트 링크 comment 필요"},
]
EXTRA = [{"result": "대시보드 추가", "why": "", "evidence": "[PR #45|https://example.com/45]"}]


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
        "escalate", checks, EXPECTED, ITEMS, EXTRA, "* 요약", ["평가 리포트 링크 comment 필요"], ["A2로 보류"]
    )
    assert text.startswith("검수 결과: *보류*\n* A2로 보류\n\n예상 산출물 2개 중 1개 달성, 초과 달성 1건")
    assert text.index("*결과 요약*") < text.index("*요청*") < text.index("|| 검사 || 결과 || 내용 ||")


def test_doc_review_comment_explains_modes(ja):
    t8 = ja.check_t8(EXPECTED, ITEMS, EXTRA)
    text = ja.doc_review_comment("INNO-1", "pass", "fix", [t8], [], "acc-lead")
    assert text.startswith("문서화 리뷰 (팀장 확인용): INNO-1\n관찰 모드: ")
    assert (
        "/ 게이트 모드(문서화 리뷰에 보완 필요 항목이 있으면 통과를 보완 요청으로 내리는 모드)였다면: *보완 요청*"
        in text
    )


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
    assert stale["check"] == "A1" and stale["detail"] == "마지막 활동 2026-09-02 뒤 19영업일 경과(토요일과 일요일 제외)"
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
    assert "검수 결과: *보류*" in body
    assert "예상 산출물 번호(1, 2)와 CI agent 출력 번호(1)가 다름. CI agent 출력 오류로 간주해 보류" in body
    assert (
        "* 예상 산출물과 결과 산출물의 1:1 대응 (T3) 결과에 따라 보류로 내림: 예상 산출물과 결과 산출물의 번호가 맞지 않음"
        in body
    )


def test_check_table_uses_same_labels_for_all_checks(ja):
    checks = [{"id": "T1", "result": "pass", "detail": "a"}, {"id": "T5", "result": "fail", "detail": "b"}]
    checks.append({"id": "A2", "result": "n/a", "detail": "c"})
    table = ja.check_table(checks)
    assert "| 예상 산출물 작성 (T1) | ✅ 만족 | a |" in table
    assert "| 예상 산출물 분할 단위 (T5) | ❌ 보완 필요 | b |" in table
    assert "| 완료 기준의 사후 변경 (A2) | ➖ 해당 없음 | c |" in table
    assert "통과" not in table and "실패" not in table


def test_verdict_change_notes_start_with_check_name(render, tmp_path):
    files = {p.name: p.read_text(encoding="utf-8") for p in render.render(tmp_path)}
    bug = files["review-comment-bug-escalate.wiki"].splitlines()
    assert bug[2].startswith("* 완료 기준의 사후 변경 (A2) 결과에 따라 보류로 내림: 마지막 request 전환(")
    assert "실패" not in files["review-comment-bug-escalate.wiki"]  # 검사 표와 같은 이름(보완 필요)을 사용함
    gate = files["review-comment-task-gate.wiki"]
    assert (
        "* 문서화 리뷰에 보완 필요 항목이 있어 통과를 보완 요청으로 내림: " not in gate
    )  # 판정이 이미 보완 요청이라 바뀌지 않음
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
        "* 문서화 리뷰에 보완 필요 항목이 있어 통과를 보완 요청으로 내림: 예상 산출물 분할 단위 (T5), 진행 기록 comment (T6)"
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
