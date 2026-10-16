"""scripts/dev/jira_metrics.py와 scripts/src/dev/jira_metrics/(문서화 Agent 지표 집계)의 파싱과 계산.

검수 comment는 scripts/src/jira/comment.py의 review_comment와 agent_comment로 만들어 실제 형식과 같게 한다.
Jira는 읽기 메서드(search, changelog, comments)만 가진 가짜 객체로 대신하고 실제 API를 호출하지 않는다.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함(test_render.py 참고)
LEAD = {"accountId": "acc-lead", "displayName": "팀장", "accountType": "atlassian"}
ASSIGNEE = {"accountId": "acc-assignee", "displayName": "담당자", "accountType": "atlassian"}
LINK = "[comment 2026-09-01|https://example.atlassian.net/browse/INNO-1?focusedCommentId=1]"
FIRST_CHECKS = [  # 첫 검수(보류): 보완 필요 T2, R3, R4
    {"id": "T1", "result": "pass", "detail": "예상 산출물 2개"},
    {"id": "T2", "result": "fail", "detail": f"진행 배경이 비어 있음. 참고 {LINK}"},
    {"id": "R3", "result": "fail", "detail": "문장형 서술"},
    {"id": "R4", "result": "fail", "detail": "stop 사유 comment 없음"},
    {"id": "A2", "result": "n/a", "detail": "완료 기준 변경 없음"},
]
LAST_CHECKS = [  # 마지막 검수(통과): T2, R4는 만족으로 바뀌고 R3는 그대로 보완 필요
    {"id": "T1", "result": "pass", "detail": "예상 산출물 2개"},
    {"id": "T2", "result": "pass", "detail": f"진행 배경 있음 {LINK}"},
    {"id": "R3", "result": "fail", "detail": "문장형 서술"},
    {"id": "R4", "result": "pass", "detail": "stop 사유 comment 있음"},
    {"id": "A2", "result": "n/a", "detail": "완료 기준 변경 없음"},
]
BUG_CHECKS = [
    {"id": "B1", "result": "pass", "detail": "현황 있음"},
    {"id": "B3", "result": "fail", "detail": "근거 링크 없음"},
    {"id": "R4", "result": "n/a", "detail": "stop 없음"},
]


@pytest.fixture(scope="module")
def jm():
    """scripts/dev/jira_metrics.py 모듈."""
    name = "jira_metrics"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "dev" / "jira_metrics.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


@pytest.fixture(scope="module")
def ja():
    """scripts/src/jira/comment.py (모듈 이름 src.jira.comment)."""
    return importlib.import_module("src.jira.comment")


@pytest.fixture(scope="module")
def jm_analyze():
    """scripts/src/dev/jira_metrics/analyze.py (모듈 이름 src.dev.jira_metrics.analyze). task_record, build_jql을 가진다."""
    return importlib.import_module("src.dev.jira_metrics.analyze")


@pytest.fixture(scope="module")
def rp_parse():
    """scripts/src/jira/report/parse.py (모듈 이름 src.jira.report.parse). parse_review를 가진다."""
    return importlib.import_module("src.jira.report.parse")


def ts(at: str) -> str:
    """'MM-DDTHH:MM'을 Jira 시각 문자열로 바꾼다."""
    return f"2026-{at}:00.000+0900"


def status(at: str, src: str, dst: str, author: dict = LEAD) -> dict:
    return {"created": ts(at), "author": author, "items": [{"field": "status", "fromString": src, "toString": dst}]}


def comment(at: str, body: str, author: dict = LEAD) -> dict:
    return {"id": at, "created": ts(at), "author": author, "body": body}


def review_body(ja, v: str, checks: list[dict], note: str = "상태를 바꾸지 않고 팀장 확인을 요청함") -> str:
    """apply_issue가 게시하는 꼴의 검수 comment(표식, 판정 줄, 검사 표, 담당자 멘션)."""
    text = ja.review_comment(v, checks, [], [], [], "결과 요약 한 줄", [], [note])
    return ja.agent_comment(text + "\n\n[~accountid:acc-assignee]")


class FakeJira:
    """jira_metrics가 부르는 Jira 읽기 메서드만 흉내 낸다. 쓰기 메서드를 부르면 AttributeError가 난다."""

    def __init__(self, issues: list[dict], changelogs: dict[str, list[dict]], comments: dict[str, list[dict]]):
        self.issues, self.changelogs, self.comment_map = issues, changelogs, comments
        self.calls: list[tuple[str, str]] = []

    def search(self, jql: str, fields: list[str], limit: int = 500) -> list[dict]:
        self.calls.append(("search", jql))
        return self.issues

    def changelog(self, key: str) -> list[dict]:
        self.calls.append(("changelog", key))
        return self.changelogs.get(key, [])

    def comments(self, key: str) -> list[dict]:
        self.calls.append(("comments", key))
        return self.comment_map.get(key, [])


@pytest.fixture()
def fake(ja):
    """task 4개.

    INNO-1(Task): 보류 -> 재요청 -> 실패 알림 -> 통과 -> done. 문서화 리뷰 comment, "정정:" comment가 있음.
    INNO-2(Task): 기간 안 request 전환 뒤 검수 comment 없이 ready-to-done에 머무는 중.
    INNO-3(Task): request 전환이 기간 전이라 집계 대상이 아님.
    INNO-4(Bug): 검수 comment 하나(검토 요청) 뒤 In Progress로 되돌려짐.
    """
    doc_review = ja.agent_comment(
        ja.doc_review_comment([{"id": "T4", "result": "fail", "detail": "배경 부족"}], [], "acc-lead")
    )
    failure = ja.agent_comment("자동 검수 실행 실패: 판정 파일 없음\n팀장 확인 필요\n[~accountid:acc-lead]")
    issues = [
        {"key": k, "fields": {"issuetype": {"name": t}}}
        for k, t in (("INNO-1", "Task"), ("INNO-2", "Task"), ("INNO-3", "Task"), ("INNO-4", "Bug"))
    ]
    changelogs = {
        "INNO-1": [
            status("09-01T10:00", "To Do", "In Progress"),
            status("09-02T10:00", "In Progress", "ready-to-done"),
            status("09-02T10:30", "ready-to-done", "In Progress"),  # 보류: apply.py가 comment보다 먼저 되돌림
            status("09-04T09:00", "In Progress", "Ready-To-Done"),  # 대소문자 무시
            status("09-05T09:30", "ready-to-done", "Done"),
        ],
        "INNO-2": [status("09-10T10:00", "In Progress", "ready-to-done")],
        "INNO-3": [
            status("08-20T10:00", "In Progress", "ready-to-done"),
            status("08-21T10:00", "ready-to-done", "Done"),
        ],
        "INNO-4": [
            status("09-15T09:00", "In Progress", "ready-to-done"),
            status("09-16T09:00", "ready-to-done", "In Progress"),
        ],
    }
    comments = {
        "INNO-1": [
            comment("09-02T10:31", review_body(ja, "fix", FIRST_CHECKS)),
            comment("09-02T10:32", doc_review),
            comment("09-03T09:00", "정정: 결과 2번의 링크를 PR #45로 바꿔 주세요", ASSIGNEE),
            comment("09-03T09:10", "검수 결과: *통과*\n사람이 옮겨 적은 메모", ASSIGNEE),
            comment("09-04T09:10", failure),
            comment("09-04T09:30", review_body(ja, "pass", LAST_CHECKS)),
        ],
        "INNO-3": [comment("08-20T10:30", review_body(ja, "pass", LAST_CHECKS))],
        "INNO-4": [comment("09-15T09:45", review_body(ja, "escalate", BUG_CHECKS))],
    }
    return FakeJira(issues, changelogs, comments)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def section(text: str, heading: str) -> str:
    """summary.md에서 heading으로 시작하는 절의 본문(다음 "## " 절 전까지)."""
    body = text.split(f"\n{heading}", 1)[1]
    return body.split("\n## ", 1)[0]


def run(jm, fake, tmp_path, *extra: str) -> Path:
    out = tmp_path / "out"
    args = jm.parse_args(
        ["--since", "2026-09-01", "--until", "2026-09-30", "--out", str(out), "--lead", "acc-lead", *extra]
    )
    jm.run(fake, args)
    return out


def test_parse_review_reads_verdict_and_check_table(rp_parse, ja):
    verdict, results = rp_parse.parse_review(review_body(ja, "fix", FIRST_CHECKS))
    assert verdict == "보류"
    assert results == {"T1": "pass", "T2": "fail", "R3": "fail", "R4": "fail", "A2": "n/a"}
    assert rp_parse.parse_review(review_body(ja, "escalate", BUG_CHECKS))[0] == "검토 요청"


def test_doc_review_and_failure_comments_are_not_reviews(jm_analyze, rp_parse, ja):
    doc_review = ja.agent_comment(ja.doc_review_comment([{"id": "T4", "result": "pass", "detail": "x"}], [], ""))
    failure = ja.agent_comment("자동 검수 실행 실패: 판정 파일 없음\n팀장 확인 필요")
    assert rp_parse.parse_review(doc_review) is None
    assert rp_parse.parse_review(failure) is None
    rec = jm_analyze.task_record(
        "INNO-9",
        "Task",
        [],
        [
            comment("09-02T10:00", doc_review),
            comment("09-02T10:01", failure),
            comment("09-02T10:02", "검수 결과: *통과*", ASSIGNEE),  # 사람 comment는 판정 줄이 있어도 세지 않음
            comment("09-02T10:03", review_body(ja, "pass", LAST_CHECKS)),
        ],
    )
    assert [r.verdict for r in rec.reviews] == ["통과"]
    assert len(rec.failures) == 1
    assert rec.corrections == []


def test_correction_comments_counted_from_humans_only(jm_analyze, ja):
    rec = jm_analyze.task_record(
        "INNO-9",
        "Task",
        [],
        [
            comment("09-02T10:00", "정정: 결과 1번 수정 필요", ASSIGNEE),
            comment("09-02T10:01", "  정정: 결과 2번 수정 필요", ASSIGNEE),
            comment("09-02T10:02", "결과 3번 정정: 수정 필요", ASSIGNEE),
            comment("09-02T10:03", ja.agent_comment("정정: agent comment는 세지 않음")),
        ],
    )
    assert len(rec.corrections) == 2


def test_build_jql(jm, jm_analyze):
    since = jm.dt.date(2026, 9, 1)  # JQL 날짜는 API 계정의 시간대로 해석되므로 하루 앞선 날부터 찾음
    assert jm_analyze.build_jql("project = INNO", since) == '(project = INNO) AND updated >= "2026-08-31"'
    assert jm_analyze.build_jql("project = INNO ORDER BY created", since) == (
        '(project = INNO) AND updated >= "2026-08-31" ORDER BY created'
    )
    assert jm_analyze.build_jql("project = INNO", None) == "project = INNO"


def test_tasks_csv_hold_rerequest_pass(jm, fake, tmp_path):
    out = run(jm, fake, tmp_path)
    assert fake.calls[0] == ("search", '(project = INNO) AND updated >= "2026-08-31"')
    rows = {r["key"]: r for r in read_csv(out / "tasks.csv")}
    assert set(rows) == {"INNO-1", "INNO-2", "INNO-4"}  # INNO-3은 request 전환이 기간 전
    t1 = rows["INNO-1"]
    assert t1["request_count"] == "2"
    assert t1["review_count"] == "2"  # 문서화 리뷰 comment, 실패 알림 comment, 사람 comment는 세지 않음
    assert t1["first_request_at"] == "2026-09-02T10:00:00+09:00"
    assert t1["first_review_at"] == "2026-09-02T10:31:00+09:00"
    assert t1["feedback_minutes"] == "31.0"
    assert t1["feedback_minutes_each"] == "31.0;30.0"  # 재요청(09:00)부터 통과 검수 comment(09:30)까지 30분
    assert t1["ready_hours_each"] == "0.5;24.5"  # 10:00-10:30, 09-04 09:00-09-05 09:30
    assert t1["ready_hours"] == "25.0"
    assert (t1["first_verdict"], t1["last_verdict"]) == ("보류", "통과")
    assert t1["first_fail_ids"] == "T2;R3;R4"
    assert t1["last_fail_ids"] == "R3"
    assert t1["resolved_fail_ids"] == "T2;R4"
    assert t1["correction_comments"] == "1"
    assert t1["failure_alerts"] == "1"
    assert t1["after_last_verdict"] == "Done"
    t2 = rows["INNO-2"]
    assert (t2["review_count"], t2["feedback_minutes"], t2["ready_hours"]) == ("0", "", "")  # 아직 머무는 중
    t4 = rows["INNO-4"]
    assert t4["first_fail_ids"] == t4["last_fail_ids"] == "B3"
    assert t4["resolved_fail_ids"] == ""  # 검수 comment가 하나뿐
    assert t4["after_last_verdict"] == "In Progress"
    assert "assignee" not in t1


def test_reviews_csv(jm, fake, tmp_path):
    out = run(jm, fake, tmp_path)
    rows = read_csv(out / "reviews.csv")
    assert [(r["key"], r["verdict"]) for r in rows] == [("INNO-1", "보류"), ("INNO-1", "통과"), ("INNO-4", "검토 요청")]
    assert rows[0] == {
        "key": "INNO-1",
        "created_at": "2026-09-02T10:31:00+09:00",
        "verdict": "보류",
        "fail_ids": "T2;R3;R4",
        "pass_ids": "T1",
        "na_ids": "A2",
    }


def test_summary_metrics(jm, fake, tmp_path):
    out = run(jm, fake, tmp_path)
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "리뷰 범위는 66.7% (2/3)임" in text  # J1
    assert "조치율은 50.0% (2/4)임" in text  # J3: 분모 INNO-1 3개 + INNO-4 1개, 분자 T2, R4
    assert "검수 comment가 하나뿐인 task 1개" in text
    j4 = section(text, "## J4 ")
    assert "request 전환부터 검수 comment까지는 중앙값 31분, 범위 30–45분, n=3임" in j4
    assert "기간 안 request 전환마다 그 뒤이면서 다음 request 전환 전인 첫 검수 comment까지 잰 값임" in j4
    assert "다음 request 전환 전까지 검수 comment가 없는 request 전환은 뺌" in j4
    assert "ready-to-done" not in j4  # ready-to-done 체류 시간은 J6 진행 추적으로 옮김
    assert "| 2026-W36 | 0.0% (0/1) |" in text  # J5: INNO-1 첫 검수 보류
    assert "| 진행 배경 작성 (T2) | 0.0% (0/1) |" in text
    j6 = section(text, "## J6 ")
    assert "ready-to-done 상태에 머문 시간은 중앙값 24시간, 범위 0.5–24.5시간, n=3임" in j6
    assert "stop 사유 comment (R4) 만족 비율은 50.0% (1/2)임" in j6  # 해당 없음 제외
    assert "영업일 기준 5일 이상 활동 없음 (A1)" in j6
    assert "주간 점검 메시지(Slack, Actions Summary 탭)" in j6
    j7 = section(text, "## J7 ")
    # INNO-1 보류(10:31): 다음 검수 comment 전 ready-to-done에서 나간 전환 없음(10:30 되돌림은 comment 전)
    # INNO-1 통과(09-04 09:30): 팀장이 done 전환 -> 일치. INNO-4 검토 요청 -> 제외
    assert "판정 일치율은 100.0% (1/1)임" in j7
    assert "검토 요청 1건, 자동 보류 0건, 팀장 외 전환 0건, 전환 없음 1건, 기타 전환 0건" in j7
    assert "검토 요청 비율은 33.3% (1/3)임" in j7
    assert '"정정:" comment가 달린 task 비율은 33.3% (1/3)임' in text  # J8
    assert "실패 알림 comment 비율은 25.0% (1/4)임" in text
    assert "## J2 " not in text and "time-study" not in text
    assert "~" not in text
    assert "평균" not in text
    assert "acc-assignee" not in text and "담당자 이름" not in text
    assert "사용자" not in text


def test_feedback_skips_request_without_review(jm, ja, tmp_path):
    """첫 request 전환 뒤 검수 comment 없이 재요청된 task: 첫 전환은 feedback_minutes와 J4에서 빠짐."""
    fake = FakeJira(
        [{"key": "INNO-5", "fields": {"issuetype": {"name": "Task"}}}],
        {
            "INNO-5": [
                status("09-20T10:00", "In Progress", "ready-to-done"),
                status("09-20T11:00", "ready-to-done", "In Progress"),  # 검수 comment 없이 되돌려짐
                status("09-21T10:00", "In Progress", "ready-to-done"),
            ]
        },
        {"INNO-5": [comment("09-21T10:15", review_body(ja, "pass", LAST_CHECKS))]},
    )
    out = run(jm, fake, tmp_path)
    row = read_csv(out / "tasks.csv")[0]
    assert row["first_review_at"] == "2026-09-21T10:15:00+09:00"
    assert row["feedback_minutes_each"] == ";15.0"  # request 전환마다 한 칸. 첫 전환은 빈 값
    assert row["feedback_minutes"] == ""  # feedback_minutes_each의 첫 값과 같음
    j4 = section((out / "summary.md").read_text(encoding="utf-8"), "## J4 ")
    assert "request 전환부터 검수 comment까지는 중앙값 15분, 범위 15–15분, n=1임" in j4


def test_period_uses_kst_dates_and_includes_until(jm, tmp_path):
    cases = {  # task 키: (request 전환 시각, 집계 대상 여부). 기간은 --since 2026-09-01 --until 2026-09-30
        "INNO-11": ("2026-08-31T23:59:00.000+0900", False),
        "INNO-12": ("2026-09-01T00:00:00.000+0900", True),
        "INNO-13": ("2026-08-31T15:00:00.000+0000", True),  # 한국 시간 09-01 00:00
        "INNO-14": ("2026-09-30T23:59:00.000+0900", True),
        "INNO-15": ("2026-10-01T00:00:00.000+0900", False),
        "INNO-16": ("2026-09-30T15:00:00.000+0000", False),  # 한국 시간 10-01 00:00
    }
    item = {"field": "status", "fromString": "In Progress", "toString": "ready-to-done"}
    fake = FakeJira(
        [{"key": k, "fields": {"issuetype": {"name": "Task"}}} for k in cases],
        {k: [{"created": at, "items": [item]}] for k, (at, _) in cases.items()},
        {},
    )
    out = run(jm, fake, tmp_path)
    assert {r["key"] for r in read_csv(out / "tasks.csv")} == {k for k, (_, ok) in cases.items() if ok}


def test_j7_uses_report_definition(jm, ja, tmp_path):
    """J7은 scripts/src/jira/report/metrics.py의 judge를 사용함: 자동 보류 제외, 팀장 외 전환 제외, 팀장 전환만 셈."""
    item = {"issuetype": {"name": "Task"}}
    fake = FakeJira(
        [{"key": k, "fields": item} for k in ("INNO-6", "INNO-7", "INNO-8")],
        {
            "INNO-6": [
                status("09-10T10:00", "In Progress", "ready-to-done"),
                status("09-10T10:30", "ready-to-done", "In Progress"),  # 자동 보류 전환(comment 전)
            ],
            "INNO-7": [
                status("09-11T10:00", "In Progress", "ready-to-done", ASSIGNEE),
                status("09-11T11:00", "ready-to-done", "In Progress", ASSIGNEE),  # 담당자가 되돌림
            ],
            "INNO-8": [
                status("09-12T10:00", "In Progress", "ready-to-done", ASSIGNEE),
                status("09-12T11:00", "ready-to-done", "In Progress"),  # 팀장이 보류에 맞춰 되돌림
            ],
        },
        {
            "INNO-6": [comment("09-10T10:31", review_body(ja, "fix", FIRST_CHECKS, "상태를 in-progress로 되돌림"))],
            "INNO-7": [comment("09-11T10:30", review_body(ja, "pass", LAST_CHECKS))],
            "INNO-8": [comment("09-12T10:30", review_body(ja, "fix", FIRST_CHECKS))],
        },
    )
    j7 = section((run(jm, fake, tmp_path) / "summary.md").read_text(encoding="utf-8"), "## J7 ")
    assert "판정 일치율은 100.0% (1/1)임" in j7
    assert "검토 요청 0건, 자동 보류 1건, 팀장 외 전환 1건, 전환 없음 0건, 기타 전환 0건" in j7
    assert "--lead" not in j7
