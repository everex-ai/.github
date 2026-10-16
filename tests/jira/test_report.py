"""scripts/src/jira/report/(정기 보고 메시지)의 계산과 Slack 본문.

검수 comment와 문서화 리뷰 comment는 scripts/src/jira/comment.py의 review_comment, doc_review_comment, agent_comment로 만들어
실제 형식과 같게 한다. Jira는 읽기 메서드(search, changelog, comments, prop_get)만 가진 가짜 객체로 대신하고
실제 API를 호출하지 않는다. PR 검색(gh search prs)은 가짜 함수로 바꿔 gh를 실행하지 않는다.
"""

from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEAD_ID = "acc-lead"
LEAD = {"accountId": LEAD_ID, "displayName": "팀장", "accountType": "atlassian"}
KIM = {"accountId": "acc-kim", "displayName": "김담당", "accountType": "atlassian"}
LEE = {"accountId": "acc-lee", "displayName": "이담당", "accountType": "atlassian"}
PARK = {"accountId": "acc-park", "displayName": "박담당", "accountType": "atlassian"}
KR23 = {"key": "INNO-23", "fields": {"summary": "문서화 품질 개선", "issuetype": {"name": "KR"}}}
KR33 = {"key": "INNO-33", "fields": {"summary": "검수 자동화", "issuetype": {"name": "KR"}}}
EPIC = {"key": "INNO-40", "fields": {"summary": "기타 묶음", "issuetype": {"name": "Epic"}}}
TOKENS = {"input": 100, "output": 50, "cacheWrite": 10, "cacheRead": 1000}
HOLD_NOTE = "상태를 in-progress로 되돌림"


@pytest.fixture(scope="module")
def ja():
    """scripts/src/jira/comment.py (모듈 이름 src.jira.comment). 검수 comment 조립 함수와 표시 문구를 가진다."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함(test_render.py 참고)
    return importlib.import_module("src.jira.comment")


@pytest.fixture(scope="module")
def rp_time(ja):
    """scripts/src/constant/time.py (모듈 이름 src.constant.time). 한국 시간 KST를 가진다."""
    return importlib.import_module("src.constant.time")


@pytest.fixture(scope="module")
def rp_model(ja):
    """scripts/src/jira/report/model.py (모듈 이름 src.jira.report.model)."""
    return importlib.import_module("src.jira.report.model")


@pytest.fixture(scope="module")
def rp_parse(ja):
    """scripts/src/jira/report/parse.py (모듈 이름 src.jira.report.parse)."""
    return importlib.import_module("src.jira.report.parse")


@pytest.fixture(scope="module")
def rp_period(ja):
    """scripts/src/jira/report/period.py (모듈 이름 src.jira.report.period)."""
    return importlib.import_module("src.jira.report.period")


@pytest.fixture(scope="module")
def rp_fetch(ja):
    """scripts/src/jira/report/fetch.py (모듈 이름 src.jira.report.fetch). search_prs, subprocess, SEARCH_LIMIT을 찾는 모듈."""
    return importlib.import_module("src.jira.report.fetch")


@pytest.fixture(scope="module")
def rp_metrics(ja):
    """scripts/src/jira/report/metrics.py (모듈 이름 src.jira.report.metrics)."""
    return importlib.import_module("src.jira.report.metrics")


@pytest.fixture(scope="module")
def rp_text(ja):
    """scripts/src/jira/report/text.py (모듈 이름 src.jira.report.text)."""
    return importlib.import_module("src.jira.report.text")


@pytest.fixture(scope="module")
def rp_message(ja):
    """scripts/src/jira/report/message.py (모듈 이름 src.jira.report.message). Report가 dt.datetime.now를 부르는 모듈."""
    return importlib.import_module("src.jira.report.message")


@pytest.fixture(scope="module")
def rp_run(ja):
    """scripts/src/jira/report/run.py (모듈 이름 src.jira.report.run)."""
    return importlib.import_module("src.jira.report.run")


@pytest.fixture(autouse=True)
def project(monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "INNO")
    monkeypatch.setenv("GH_ORG", "everex-ai")


@pytest.fixture(autouse=True)
def clock(rp_time, rp_message, monkeypatch):
    """Report의 실행 시각 기본값(dt.datetime.now)을 2027-01-01 09:00(한국 시간)으로 고정한다.

    매월 1일 cron 실행처럼 2026년 대상 달이 모두 끝난 뒤 시각이라 팀장 확인 대기는 대상 기간 끝까지 센다.
    message.py의 dt만 바꾸고 datetime 모듈 자체는 바꾸지 않는다.
    """
    real = rp_message.dt
    fixed = real.datetime(2027, 1, 1, 9, tzinfo=rp_time.KST)

    class Clock(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(rp_message, "dt", types.SimpleNamespace(**{**vars(real), "datetime": Clock}))


@pytest.fixture(autouse=True)
def no_gh(rp_fetch, monkeypatch):
    """run_report가 기본으로 부르는 search_prs를 PR 0건을 돌려주는 가짜로 바꾼다(gh를 실행하지 않음).

    바꾸기 전 search_prs를 돌려준다. search_prs 자체를 확인하는 테스트가 이 fixture 값으로 받는다.
    """
    real = rp_fetch.search_prs
    monkeypatch.setattr(rp_fetch, "search_prs", lambda org, since, until: [])
    return real


def ts(when: str) -> str:
    """'YYYY-MM-DDTHH:MM'(한국 시간)을 Jira 시각 문자열로 바꾼다."""
    return f"{when}:00.000+0900"


def change(when: str, src: str, dst: str, who: dict = LEAD) -> dict:
    return {"created": ts(when), "author": who, "items": [{"field": "status", "fromString": src, "toString": dst}]}


def desc_edit(when: str, who: dict = KIM) -> dict:
    """변경 이력의 description 항목."""
    return {"created": ts(when), "author": who, "items": [{"field": "description", "fromString": "a", "toString": "b"}]}


def human(when: str, who: dict = KIM) -> dict:
    """사람 comment(agent 표식 없음)."""
    return {"created": ts(when), "author": who, "body": "진행 상황 공유"}


def subtask(key: str, parent: str, when: str) -> dict:
    """sub-task 검색 결과 항목."""
    return {"key": key, "fields": {"parent": {"key": parent}, "created": ts(when)}}


def checks(**results: str) -> list[dict]:
    return [{"id": cid, "result": r, "detail": f"{cid} 내용"} for cid, r in results.items()]


def review(ja, when: str, v: str, rows: list[dict], note: str = "상태를 바꾸지 않고 팀장 확인을 요청함") -> dict:
    """apply_issue가 게시하는 꼴의 검수 comment."""
    body = ja.agent_comment(ja.review_comment(v, rows, [], [], [], "결과 요약", [], [note]))
    return {"created": ts(when), "author": LEAD, "body": body}


def doc(ja, when: str, rows: list[dict]) -> dict:
    """apply_issue가 게시하는 꼴의 문서화 리뷰 comment(관찰 모드)."""
    return {"created": ts(when), "author": LEAD, "body": ja.agent_comment(ja.doc_review_comment(rows, [], LEAD_ID))}


def run(when: str, no: int | None = 1, cost: float | None = 1.0, mode: str = "review", **kw) -> dict:
    """jira-doc 기록 runs 항목."""
    entry = {"at": f"{when}:00+09:00", "mode": mode, "verdict": kw.get("verdict", "pass"), "requestNo": no}
    entry |= {"retry": kw.get("retry", False), "costUsd": cost, "tokens": dict(TOKENS), "models": ["claude-opus-5-5"]}
    return entry


def ticket(
    itype="Task",
    who=None,
    parent=None,
    desc="",
    changelog=(),
    comments=(),
    runs=(),
    created=None,
    status=None,
    title="",
) -> dict:
    """가짜 Jira의 ticket 하나. created는 'YYYY-MM-DDTHH:MM'(한국 시간), status는 지금 상태 이름, title은 제목."""
    fields = {"issuetype": {"name": itype}, "assignee": who, "parent": parent, "description": desc, "summary": title}
    fields |= {"created": ts(created) if created else None, "status": {"name": status} if status else None}
    return {"fields": fields, "changelog": list(changelog), "comments": list(comments), "runs": list(runs)}


class FakeJira:
    """scripts/src/jira/report/가 부르는 Jira 읽기 메서드만 흉내 낸다. 쓰기 메서드와 issue를 부르면 AttributeError가 난다."""

    def __init__(self, tickets: dict[str, dict], subtasks: list[dict] = ()):
        self.tickets = tickets
        self.subtasks = list(subtasks)
        self.jql: list[str] = []
        self.searches: list[tuple[str, list[str]]] = []

    def search(self, jql: str, fields: list[str], limit: int = 500) -> list[dict]:
        """검색 결과에 요청한 필드만 담는다. ticket마다 Jira.issue를 부르지 않는지 확인하려고 issue는 두지 않는다.

        sub-task 검색(subTaskIssueTypes())이면 subtasks를 돌려준다.
        """
        self.jql.append(jql)
        self.searches.append((jql, fields))
        if "subTaskIssueTypes()" in jql:
            return self.subtasks
        return [{"key": k, "fields": {f: t["fields"].get(f) for f in fields}} for k, t in self.tickets.items()]

    def changelog(self, key: str) -> list[dict]:
        return self.tickets[key]["changelog"]

    def comments(self, key: str) -> list[dict]:
        return self.tickets[key]["comments"]

    def prop_get(self, key: str) -> dict:
        return {"lastVerdict": "pass", "runs": self.tickets[key]["runs"]}


class Summ:
    """scripts/src/common/github.py Summary의 extra와 row만 흉내 낸다."""

    def __init__(self):
        self.extra: list[str] = []
        self.rows: list[tuple] = []

    def row(self, key: str, itype: str, mode: str, result: str, note: str = "") -> None:
        self.rows.append((key, itype, mode, result, note))


MARK_RE = re.compile(r"^\s*(?:• |◦ |\d+\. )?")


def assert_line_length(body: str, limit: int = 50) -> None:
    """줄마다 글자 수(공백 포함)가 limit 이하. 목록 기호와 들여쓰기, 백틱 안 코드는 셈에서 뺀다."""
    long = [line for line in body.splitlines() if len(re.sub(r"`[^`]*`", "", MARK_RE.sub("", line, count=1))) > limit]
    assert long == []


def block(body: str, head: str, n: int) -> list[str]:
    """head 줄 바로 아래 n줄."""
    lines = body.splitlines()
    i = lines.index(head)
    return lines[i + 1 : i + 1 + n]


def load(rp_fetch, tickets: dict[str, dict]) -> list:
    return rp_fetch.collect(FakeJira(tickets), "jql")


def month(rp_period, m: str):
    return rp_period.months_span(m, m)


@pytest.fixture()
def sample(ja) -> dict[str, dict]:
    """2026년 10–12월 기록. 2026-12 보고에 월간, 분기, 연간 묶음이 모두 나온다.

    INNO-101(김담당, KR INNO-23): 12월 반려 2회(팀장 1회, 담당자 1회), T2 ❌ 2회 -> 12월 후보(조건 A, B).
    INNO-102(이담당, KR INNO-23): 12월 첫 검수 통과. 문서화 리뷰 T4, T6 ❌. 반영 실패 실행과 정리 모드 실행이 있음.
    INNO-103(박담당, Bug): 12월 검토 요청. 3-R 재실행.
    INNO-104(박담당, KR INNO-33): 10월 보류 뒤 팀장이 done 전환(불일치).
    INNO-106(이담당, KR INNO-33): 11월 R3 ❌ 2회 -> 11월 후보(조건 B). 12월에 R3 재발 없고 첫 검수 통과율이 오름.
    INNO-107(박담당, Issue): 11월 검수 실행 8건(전월 검수 실행 10건 조건을 채움).
    INNO-108(박담당, Task): 10월 자동 보류 전환이 있던 보류 검수 comment.

    12월 ticket 현황(기존 ticket 10개, 1일 0시에 done이나 Backlog인 INNO-104, 106, 114는 뺌):
    업데이트 7개(상태 변경 101, 102, 103, description 수정 110, 사람 comment 111, sub-task 112, PR 113),
    그중 done 전환 101, 102. 업데이트 없음 3개(팀장 확인 대기 109, 담당자 107, 108).
    새 ticket 2개(INNO-120 To Do, INNO-121 In Progress). sub-task와 PR은 SUBTASKS, PRS에 있다.
    """
    return {
        "INNO-101": ticket(
            created="2026-11-20T09:00",
            title="PoseNet-S 경량화로 엣지 기기 실시간 추론 달성",
            status="Done",
            who=KIM,
            parent=KR23,
            desc="예상 산출물 3개 중 3개 달성",
            changelog=[
                change("2026-12-01T09:00", "In Progress", "ready-to-done", KIM),
                change("2026-12-01T10:00", "ready-to-done", "In Progress"),
                change("2026-12-02T09:00", "In Progress", "ready-to-done", KIM),
                change("2026-12-02T11:00", "ready-to-done", "In Progress", KIM),
                change("2026-12-03T09:00", "In Progress", "ready-to-done", KIM),
                change("2026-12-03T12:00", "ready-to-done", "Done"),
            ],
            comments=[
                review(ja, "2026-12-01T09:20", "fix", checks(T2="fail", R4="pass")),
                doc(ja, "2026-12-01T09:21", checks(T4="fail", T5="pass")),
                review(ja, "2026-12-02T09:30", "fix", checks(T2="fail", R4="fail")),
                review(ja, "2026-12-03T09:40", "pass", checks(T2="pass", R4="pass")),
            ],
            runs=[
                run("2026-12-01T09:20", 1, 1.0, verdict="fix"),
                run("2026-12-02T09:30", 2, 1.2, verdict="fix"),
                run("2026-12-03T09:40", 3, 0.8),
            ],
        ),
        "INNO-102": ticket(
            created="2026-11-25T09:00",
            title="검색 결과 & 정렬 개선",
            status="Done",
            who=LEE,
            parent=KR23,
            desc="예상 산출물 2개 중 1개 달성",
            changelog=[
                change("2026-12-05T09:00", "In Progress", "ready-to-done", LEE),
                change("2026-12-05T15:00", "ready-to-done", "Done"),
            ],
            comments=[
                review(ja, "2026-12-05T09:10", "pass", checks(T1="pass", T2="pass", R4="n/a")),
                doc(ja, "2026-12-05T09:11", checks(T4="fail", T6="fail")),
            ],
            runs=[
                run("2026-12-05T09:05", 1, 0.3, verdict="failed"),
                run("2026-12-05T09:10", 1, 0.9),
                run("2026-12-06T10:00", None, 0.5, mode="digest", verdict="digest"),
            ],
        ),
        "INNO-103": ticket(
            created="2026-11-01T09:00",
            title="로그인 실패 버그",
            status="In Progress",
            itype="Bug",
            who=PARK,
            changelog=[
                change("2026-12-10T09:00", "In Progress", "ready-to-done", PARK),
                change("2026-12-10T11:00", "ready-to-done", "In Progress"),
            ],
            comments=[review(ja, "2026-12-10T09:45", "escalate", checks(B1="pass", B3="fail", R4="fail"))],
            runs=[run("2026-12-10T09:45", 1, 1.1, verdict="escalate", retry=True)],
        ),
        "INNO-104": ticket(
            created="2026-10-01T09:00",
            status="Done",
            who=PARK,
            parent=KR33,
            desc="예상 산출물 4개 중 2개 달성",
            changelog=[
                change("2026-10-14T09:00", "In Progress", "ready-to-done", PARK),
                change("2026-10-15T09:00", "ready-to-done", "Done"),
            ],
            comments=[review(ja, "2026-10-14T09:30", "fix", checks(T2="fail"))],
            runs=[run("2026-10-14T09:30", 1, 1.0, verdict="fix")],
        ),
        "INNO-106": ticket(
            created="2026-11-01T09:00",
            status="Done",
            who=LEE,
            parent=KR33,
            desc="예상 산출물 2개 중 2개 달성",
            changelog=[
                change("2026-11-10T09:00", "In Progress", "ready-to-done", LEE),
                change("2026-11-10T10:00", "ready-to-done", "In Progress"),
                change("2026-11-12T09:00", "In Progress", "ready-to-done", LEE),
                change("2026-11-12T10:00", "ready-to-done", "Done"),
            ],
            comments=[
                review(ja, "2026-11-10T09:20", "fix", checks(T2="pass", R3="fail")),
                review(ja, "2026-11-12T09:20", "pass", checks(T2="pass", R3="fail")),
            ],
            runs=[run("2026-11-10T09:20", 1, 1.0, verdict="fix"), run("2026-11-12T09:20", 2, 1.0)],
        ),
        "INNO-107": ticket(
            created="2026-11-01T09:00",
            status="In Progress",
            itype="Issue",
            who=PARK,
            runs=[run(f"2026-11-{d:02d}T10:00", d - 1, 0.15) for d in range(2, 10)],
        ),
        "INNO-108": ticket(
            created="2026-10-01T09:00",
            status="In Progress",
            who=PARK,
            changelog=[
                change("2026-10-20T09:00", "In Progress", "ready-to-done", PARK),
                change("2026-10-20T09:05", "ready-to-done", "In Progress"),  # 자동 보류 전환(comment 전)
            ],
            comments=[review(ja, "2026-10-20T09:06", "fix", checks(T1="fail"), HOLD_NOTE)],
            runs=[run("2026-10-20T09:06", 1, 0.7, verdict="fix")],
        ),
        "INNO-109": ticket(  # 11월 request 전환 뒤 12월 업데이트 없음 -> 팀장 확인 대기
            who=KIM,
            created="2026-10-01T09:00",
            status="ready-to-done",
            changelog=[change("2026-11-20T10:00", "In Progress", "ready-to-done", KIM)],
            comments=[human("2026-11-21T10:00")],  # 11월 사람 comment는 12월 업데이트가 아님
        ),
        "INNO-110": ticket(
            who=LEE, created="2026-11-03T09:00", status="In Progress", changelog=[desc_edit("2026-12-08T10:00")]
        ),
        "INNO-111": ticket(who=KIM, created="2026-11-03T09:00", status="To Do", comments=[human("2026-12-09T10:00")]),
        "INNO-112": ticket(who=PARK, created="2026-11-04T09:00", status="In Progress"),  # SUBTASKS의 INNO-130
        "INNO-113": ticket(who=LEE, created="2026-11-05T09:00", status="In Progress"),  # PRS의 PR
        "INNO-114": ticket(  # 1일 0시에 Backlog -> 기존 ticket 아님
            who=PARK,
            created="2026-10-01T09:00",
            status="Backlog",
            changelog=[change("2026-10-30T10:00", "In Progress", "Backlog", PARK)],
        ),
        "INNO-120": ticket(who=KIM, created="2026-12-14T09:00", status="To Do"),
        "INNO-121": ticket(who=LEE, created="2026-12-15T09:00", status="In Progress"),
    }


SUBTASKS = [subtask("INNO-130", "INNO-112", "2026-12-11T10:00")]
PRS = [
    {"title": "INNO-113 검색 기능 추가", "createdAt": "2026-12-12T01:00:00Z"},
    {"title": "INNO-1130 다른 ticket", "createdAt": "2026-12-12T01:00:00Z"},
]


def report(rp_run, sample: dict, m: str = "2026-12", prs=PRS, summ=None) -> list[str]:
    """sample과 SUBTASKS, PRS로 run_report를 실행한 메시지 목록. prs가 None이면 PR 검색 실패."""
    return rp_run.run_report(FakeJira(sample, SUBTASKS), m, LEAD_ID, summ or Summ(), pr_search=lambda o, a, b: prs)


def test_comment_format_matches_apply(rp_parse, rp_metrics, ja):
    """scripts/src/jira/report/parse.py가 따로 둔 문구가 comment.py와 같고, apply.py가 만든 comment를 읽음."""
    assert rp_parse.VERDICT_KO == {k: v for k, v in ja.VERDICT_KO.items() if k is not None}
    assert rp_parse.RESULT_KO == ja.RESULT_KO
    head = ja.review_comment("pass", [], [], [], [], "", [], []).splitlines()[0]
    assert head == f"{rp_parse.VERDICT_PREFIX}{ja.VERDICT_KO['pass']}{rp_parse.VERDICT_SUFFIX}"
    body = review(ja, "2026-12-01T09:00", "fix", checks(T2="fail", R4="pass", A2="n/a"))["body"]
    assert rp_parse.parse_review(body) == ("보류", {"T2": "fail", "R4": "pass", "A2": "n/a"})
    assert rp_parse.parse_doc_review(body) is None
    doc_body = doc(ja, "2026-12-01T09:00", checks(T4="fail", T5="pass"))["body"]
    assert rp_parse.parse_review(doc_body) is None
    assert rp_parse.parse_doc_review(doc_body) == {"T4": "fail", "T5": "pass"}
    assert rp_parse.table_rows(doc_body)[0] == ("T4", ja.check_label("T4"), "fail")
    line = ja.deliverables_summary([{"id": 1}, {"id": 2}], [{"id": 1, "done": True}], [{"result": "x"}])
    assert rp_metrics.DELIVERABLES_RE.search(line).groups() == ("2", "1")


def test_run_branch(rp_parse):
    cases = [
        ({"mode": "review", "verdict": "pass", "requestNo": 1, "retry": False}, "first"),
        ({"mode": "review", "verdict": "fix", "requestNo": None, "retry": False}, "first"),
        ({"mode": "review", "verdict": "pass", "requestNo": 2, "retry": False}, "cycle"),
        ({"mode": "review", "verdict": "escalate", "requestNo": 5, "retry": False}, "cycle"),
        ({"mode": "review", "verdict": "failed", "requestNo": 1, "retry": False}, "retry"),
        ({"mode": "review", "verdict": "pass", "requestNo": 3, "retry": True}, "retry"),
        ({"mode": "digest", "verdict": "digest", "requestNo": None, "retry": False}, "digest"),
        ({"mode": "digest", "verdict": "failed", "requestNo": None, "retry": False}, "retry"),
    ]
    assert [rp_parse.run_branch(e) for e, _ in cases] == [b for _, b in cases]


def test_usage_sums_by_kst_month(rp_period, rp_fetch, rp_metrics):
    tickets = load(
        rp_fetch,
        {
            "INNO-1": ticket(
                runs=[
                    run("2026-11-30T23:59", 1, 2.0),  # 11월
                    {**run("2026-12-01T00:00", 2, 1.0), "at": "2026-11-30T15:00:00+00:00"},  # 한국 시간 12-01 00:00
                    run("2026-12-02T10:00", None, None, mode="digest", verdict="digest"),  # 비용 없음
                    run("2026-12-03T10:00", 1, 0.5, verdict="failed"),
                    "깨진 항목",
                ]
            )
        },
    )
    u = rp_metrics.usage(tickets, month(rp_period, "2026-12"))
    assert dict(u.runs) == {"cycle": 1, "digest": 1, "retry": 1}
    assert u.cost == {"first": 0.0, "cycle": 1.0, "retry": 0.5, "digest": 0.0}
    assert (u.review_runs, u.missing_cost, u.total_runs) == (2, 1, 3)
    assert u.tokens == {"input": 300, "output": 150, "cacheWrite": 30, "cacheRead": 3000}
    assert rp_metrics.usage(tickets, month(rp_period, "2026-11")).cost["first"] == 2.0


def test_usage_judgement_boundaries(rp_model, rp_metrics):
    def u(cost: float, reviews: int):
        x = rp_model.Usage()
        x.cost["first"], x.review_runs = cost, reviews
        return x

    assert rp_metrics.usage_judgement(u(15.0, 0), u(10.0, 10)) == (
        True,
        "해당함",
        ["이번 달 15.00 USD ≥ 전월 × 1.5(15.00 USD)", "전월 검수 실행 10건 ≥ 10건"],
    )
    assert rp_metrics.usage_judgement(u(14.99, 0), u(10.0, 10))[:2] == (False, "해당하지 않음")
    assert rp_metrics.usage_judgement(u(14.99, 0), u(10.0, 10))[2][0] == "이번 달 14.99 USD < 전월 × 1.5(15.00 USD)"
    assert rp_metrics.usage_judgement(u(0.3, 0), u(0.2, 10))[0] is True  # 0.2 × 1.5의 부동소수 오차에도 경계 포함
    assert rp_metrics.usage_judgement(u(100.0, 0), u(10.0, 9)) == (None, "판정 불가", ["전월 검수 실행 9건 < 10건"])
    assert rp_metrics.usage_judgement(u(1.0, 0), u(0.0, 12)) == (None, "판정 불가", ["전월 API 환산 비용 기록 없음"])


def test_rerun_of_same_request_is_retry(rp_fetch):
    """같은 ticket에서 requestNo가 같고 반영에 성공한 review 실행이 앞서 있으면 그 뒤 실행은 재실행·실패 갈래.

    앞선 실행이 반영에 실패했으면 첫 성공 실행은 원래 갈래로 둔다.
    """
    tickets = load(
        rp_fetch,
        {
            "INNO-1": ticket(
                runs=[
                    run("2026-12-01T10:05", 1, 0.4),  # 같은 request 전환 1의 두 번째 실행
                    run("2026-12-01T10:00", 1, 0.3, verdict="failed"),  # 시각이 더 이름. 반영 실패
                    run("2026-12-02T10:00", 2, 0.5),
                    run("2026-12-02T11:00", 2, 0.6),  # 같은 request 전환 2의 두 번째 실행
                    run("2026-12-03T10:00", None, 0.2, mode="digest", verdict="digest"),
                    run("2026-12-03T11:00", None, 0.2, mode="digest", verdict="digest"),  # 정리 모드는 그대로
                ]
            ),
            "INNO-2": ticket(runs=[run("2026-12-01T10:00", 1, 1.0)]),  # 다른 ticket의 request 전환 1은 따로 셈
        },
    )
    branches = {t.key: [(r.request_no, r.branch) for r in t.runs] for t in tickets}
    assert branches == {
        "INNO-1": [(1, "retry"), (1, "first"), (2, "cycle"), (2, "retry"), (None, "digest"), (None, "digest")],
        "INNO-2": [(1, "first")],
    }


def test_candidates_conditions_a_b(rp_period, rp_fetch, rp_metrics, ja):
    tickets = load(
        rp_fetch,
        {
            "INNO-1": ticket(  # 김담당: 12월 반려 2회(팀장, 담당자 1회씩) -> 조건 A. 11월 반려는 세지 않음
                who=KIM,
                changelog=[
                    change("2026-11-30T10:00", "ready-to-done", "In Progress"),
                    change("2026-12-01T10:00", "ready-to-done", "In Progress"),
                    change("2026-12-02T10:00", "Ready-To-Done", "In Progress", KIM),
                ],
            ),
            "INNO-2": ticket(  # 이담당: T2 ❌가 두 ticket에 1회씩, T4 ❌가 두 문서화 리뷰 comment에 1회씩 -> 조건 B
                who=LEE,
                comments=[
                    review(ja, "2026-12-03T10:00", "fix", checks(T2="fail", R4="fail")),
                    doc(ja, "2026-12-03T10:01", checks(T4="fail")),
                ],
            ),
            "INNO-3": ticket(
                who=LEE,
                comments=[
                    review(ja, "2026-12-04T10:00", "fix", checks(T2="fail")),
                    doc(ja, "2026-12-04T10:01", checks(T4="fail")),
                    review(ja, "2026-11-04T10:00", "fix", checks(R4="fail")),  # 11월: 세지 않음
                ],
            ),
            "INNO-4": ticket(  # 박담당: 반려 1회, ❌ 1회 -> 후보 아님
                who=PARK,
                changelog=[change("2026-12-05T10:00", "ready-to-done", "In Progress")],
                comments=[review(ja, "2026-12-05T09:00", "fix", checks(T2="fail"))],
            ),
            "INNO-5": ticket(changelog=[change(f"2026-12-0{d}T10:00", "ready-to-done", "In Progress") for d in (1, 2)]),
        },
    )
    cands = rp_metrics.candidates(tickets, month(rp_period, "2026-12"))
    assert set(cands) == {"acc-kim", "acc-lee"}  # 담당자가 없는 INNO-5는 세지 않음
    assert (cands["acc-kim"].name, cands["acc-kim"].rejected, cands["acc-kim"].failed) == (
        "김담당",
        [("INNO-1", 2)],
        [],
    )
    assert cands["acc-lee"].rejected == []
    assert cands["acc-lee"].failed == [("T2", 2), ("T4", 2)]


def test_followup_four_results(rp_period, rp_fetch, rp_metrics, ja):
    """11월 후보 네 명(모두 T2 ❌ 2회, 11월 첫 검수 통과율 0/1)의 12월 개선 여부."""

    def person(who: dict, december: list[dict]) -> dict:
        nov = [
            review(ja, "2026-11-10T10:00", "fix", checks(T2="fail")),
            review(ja, "2026-11-12T10:00", "fix", checks(T2="fail")),
        ]
        return {
            f"{who['accountId']}-1": ticket(who=who, comments=nov),
            f"{who['accountId']}-2": ticket(who=who, comments=december),
        }

    a, b, c, d = ({**KIM, "accountId": f"acc-{x}", "displayName": f"담당{x}"} for x in "abcd")
    tickets = load(
        rp_fetch,
        {
            **person(a, [review(ja, "2026-12-03T10:00", "fix", checks(T2="fail"))]),  # 재발
            **person(b, [review(ja, "2026-12-03T10:00", "pass", checks(T2="pass"))]),  # 개선: 0/1 -> 1/1
            **person(c, [review(ja, "2026-12-03T10:00", "fix", checks(T2="pass", R3="fail"))]),  # 유지: 0/1 -> 0/1
            **person(d, []),  # 판단 불가: 12월 검수 없음
        },
    )
    dec, nov = month(rp_period, "2026-12"), month(rp_period, "2026-11")
    prev = rp_metrics.candidates(tickets, nov)
    assert set(prev) == {"acc-a", "acc-b", "acc-c", "acc-d"}
    result = {name: (res, detail) for name, res, detail in rp_metrics.followup(prev, tickets, dec, nov)}
    assert result["담당a"] == ("재발", ["이번 달 T2 ❌ 1회"])
    assert result["담당b"] == ("개선", ["지난달 첫 검수 통과율 0/1건, 0.0%", "이번 달 첫 검수 통과율 1/1건, 100.0%"])
    assert result["담당c"] == ("유지", ["지난달 첫 검수 통과율 0/1건, 0.0%", "이번 달 첫 검수 통과율 0/1건, 0.0%"])
    assert result["담당d"] == ("판단 불가", ["이번 달 검수 comment 0건"])


def test_judge_cells_and_exclusions(rp_period, rp_fetch, rp_metrics, ja):
    """3.3절 표의 여섯 칸, 팀장 외 전환 제외, 자동 보류 제외, 다음 검수 comment 전까지만 봄."""
    rtd, ip, done = "ready-to-done", "In Progress", "Done"

    def case(v: str, dst: str | None, who: dict = LEAD, note: str = "상태를 바꾸지 않고 팀장 확인을 요청함") -> dict:
        log = [change("2026-12-01T09:00", ip, rtd, KIM)]
        if dst:
            log.append(change("2026-12-01T11:00", rtd, dst, who))
        return ticket(who=KIM, changelog=log, comments=[review(ja, "2026-12-01T10:00", v, checks(T1="pass"), note)])

    specs = {
        "P-done": case("pass", done),
        "P-ip": case("pass", ip),
        "F-ip": case("fix", ip),
        "F-done": case("fix", done),
        "E-done": case("escalate", done),
        "E-ip": case("escalate", ip),
        "F-owner": case("fix", ip, KIM),
        "F-hold": case("fix", ip, LEAD, HOLD_NOTE),
        "P-backlog": case("pass", "Backlog"),
        "F-none": case("fix", None),
        "Two": ticket(  # 보류 뒤 전환 없이 다시 검수(통과), 그 뒤 팀장 done: 첫 검수 comment는 전환 없음
            who=KIM,
            changelog=[change("2026-12-01T09:00", ip, rtd, KIM), change("2026-12-02T11:00", rtd, done)],
            comments=[
                review(ja, "2026-12-01T10:00", "fix", checks(T1="pass")),
                review(ja, "2026-12-02T10:00", "pass", checks(T1="pass")),
            ],
        ),
    }
    tickets = {t.key: t for t in load(rp_fetch, specs)}
    got = {k: rp_metrics.judge(t.reviews, t.changes, LEAD_ID) for k, t in tickets.items()}
    assert got == {
        "P-done": ["일치"],
        "P-ip": ["불일치"],
        "F-ip": ["일치"],
        "F-done": ["불일치"],
        "E-done": ["검토 요청"],
        "E-ip": ["검토 요청"],
        "F-owner": ["팀장 외 전환"],
        "F-hold": ["자동 보류"],
        "P-backlog": ["기타 전환"],
        "F-none": ["전환 없음"],
        "Two": ["전환 없음", "일치"],
    }
    a = rp_metrics.agreement_summary(
        rp_metrics.agreement_cases(tickets.values(), LEAD_ID), rp_period.months_span("2026-10", "2026-12")
    )
    assert (a["match"], a["judged"]) == (3, 5)
    assert (a["escalate"], a["total"]) == (2, 12)
    assert a["excluded"] == {"자동 보류": 1, "팀장 외 전환": 1, "전환 없음": 2, "기타 전환": 1}
    no_lead = rp_metrics.judge(tickets["P-done"].reviews, tickets["P-done"].changes, "")
    assert no_lead == ["팀장 외 전환"]


def test_recent_20(rp_time, rp_period, rp_metrics, rp_message):
    dt = rp_message.dt
    base = dt.datetime(2026, 10, 1, tzinfo=rp_time.KST)
    cases = [(base + dt.timedelta(days=i), "불일치") for i in range(2)]
    cases += [(base + dt.timedelta(days=2 + i), "일치") for i in range(20)]
    cases += [(base + dt.timedelta(days=30), "검토 요청"), (dt.datetime(2027, 1, 2, tzinfo=rp_time.KST), "불일치")]
    a = rp_metrics.agreement_summary(sorted(cases), rp_period.months_span("2026-10", "2026-12"))
    assert (a["match"], a["judged"]) == (20, 22)  # 분기값
    assert (a["recent_match"], a["recent_n"]) == (20, 20)  # 분기 끝 전 최근 20건. 2027-01 건과 검토 요청은 뺌
    assert (a["escalate"], a["total"]) == (1, 23)


def test_kr_rows(rp_period, rp_fetch, rp_metrics):
    def task(parent, done_at: str | None, desc: str = "", itype: str = "Task") -> dict:
        log = [change(done_at, "ready-to-done", "Done")] if done_at else []
        return ticket(itype=itype, who=KIM, parent=parent, desc=desc, changelog=log)

    tickets = load(
        rp_fetch,
        {
            "INNO-1": task(KR23, "2026-11-02T10:00", "h2. 결과 산출물\n예상 산출물 3개 중 2개 달성\n# *a* / ✅ 달성"),
            "INNO-2": task(KR23, "2026-12-02T10:00", "예상 산출물 2개 중 2개 달성, 초과 달성 1건"),
            "INNO-3": task(KR23, "2026-10-02T10:00", "결과 산출물 구역 없음"),  # 달성 요약 줄 없음
            "INNO-4": task(KR23, "2026-09-30T23:59", "예상 산출물 9개 중 9개 달성"),  # 분기 전
            "INNO-5": task(KR23, None, "예상 산출물 9개 중 9개 달성"),  # done 아님
            "INNO-6": task(KR23, "2026-11-02T10:00", "예상 산출물 9개 중 9개 달성", "Bug"),  # Task 아님
            "INNO-7": task(KR33, "2026-12-03T10:00", "예상 산출물 1개 중 0개 달성"),
            "INNO-8": task(EPIC, "2026-12-03T10:00", "예상 산출물 1개 중 1개 달성"),  # 상위가 KR 아님
        },
    )
    rows = rp_metrics.kr_rows(tickets, rp_period.months_span("2026-10", "2026-12"))
    got = [(r.key, r.name, r.done, r.achieved, r.expected, r.no_line) for r in rows]
    assert got == [
        ("INNO-23", "문서화 품질 개선", 3, 4, 5, 1),
        ("INNO-33", "검수 자동화", 1, 0, 1, 0),
        ("", "상위 KR 없음", 1, 1, 1, 0),
    ]


SECTIONS = ["*팀장 업무*", "*팀 전체*", "*팀원별*", "*Claude 활용 요약*"]
DEF_LINE = "지표 정의: docs/jira-doc-agent/alerts.md"


def assert_shape(msg: str, title: str, span: str) -> None:
    """메시지 하나의 공통 규칙: 제목, 대상 기간, 절 순서, 끝 줄, 40줄 이내, 줄마다 50자 이내."""
    lines = msg.splitlines()
    assert lines[:2] == [title, f"대상 기간: {span}"]
    assert [x for x in lines if x.startswith("*")] == SECTIONS
    assert lines[-1] == DEF_LINE
    assert len(lines) <= 40
    assert_line_length(msg)


@pytest.mark.parametrize("m", range(1, 13))
def test_bundles_by_month_and_empty_data(rp_period, rp_message, rp_run, m):
    fake, summ = FakeJira({}), Summ()
    msgs = rp_run.run_report(fake, f"2026-{m:02d}", LEAD_ID, summ)
    assert len(msgs) == (3 if m == 12 else 2 if m % 3 == 0 else 1)
    last = (
        (rp_period.months_span(f"2026-{m:02d}", f"2026-{m:02d}")[1] - rp_message.dt.timedelta(days=1))
        .date()
        .isoformat()
    )
    assert_shape(msgs[0], f"[2026-{m:02d} jira-doc 월간 보고]", f"2026-{m:02d}-01–{last}")
    if m % 3 == 0:
        assert_shape(msgs[1], f"[2026 Q{m // 3} jira-doc 분기 보고]", f"2026-{m - 2:02d}-01–{last}")
    if m == 12:
        assert_shape(msgs[2], "[2026 jira-doc 연간 보고]", "2026-01-01–2026-12-31")
    body = "\n".join(msgs)
    assert "기록 없음임" not in body
    assert "PR 검색 실패" not in body
    assert block(msgs[0], "*팀장 업무*", 4) == [
        "• 판정 일치율: -",
        "    ◦ 최근 20건: -",
        "• 팀장 확인 대기: 0건",
        "• 팀장 확인 시간: -",
    ]
    assert block(msgs[0], "*팀 전체*", 7) == [
        "• 기존 ticket 0개",
        "    ◦ 업데이트 0개(그중 done 전환 0개)",
        "    ◦ 업데이트 없음 0개",
        "• 새 ticket 0개",
        "• 첫 검수 통과율: -",
        "• 최대 검수 반복 횟수: -",
        "• stop 사유 기록률: -",
    ]
    assert block(msgs[0], "*팀원별*", 2)[1] == "• 기록 없음"
    for i, msg in enumerate(msgs):
        want = [
            "• 자동 검수 소요 시간: -",
            "• API 환산 금액: 0.00 USD(직전 기록 없음)",
            "• 토큰: input 0, output 0",
            "    ◦ cache 쓰기(지시·기록 저장) 0",
            "    ◦ cache 읽기(저장분 재사용) 0",
            "• Claude 사용 ticket Top3: -",
        ]
        if i == 2:  # 연간 묶음
            want.insert(2, "    ◦ 분기별: Q1 -, Q2 -, Q3 -, Q4 -")
        assert block(msg, "*Claude 활용 요약*", len(want)) == want
    # 읽기 시작 달: 전월, 분기 묶음이 붙는 달은 전 분기 첫 달, 연간 묶음이 붙는 달은 전년 1월(JQL은 하루 앞선 날)
    since = {1: "2025-11-30", 3: "2025-09-30", 6: "2025-12-31", 9: "2026-03-31", 11: "2026-09-30", 12: "2024-12-31"}
    if m in since:
        assert fake.jql[0] == (
            "project = INNO AND issuetype in (Task, Bug, Issue) "
            f'AND (updated >= "{since[m]}" OR status not in ("done", "Backlog", "Deleted"))'
        )
    sp = rp_period.months_span(f"2026-{m:02d}", f"2026-{m:02d}")
    sub_day = (sp[0] - rp_message.dt.timedelta(days=1)).date().isoformat()
    assert fake.searches[1] == (
        "project = INNO AND issuetype in subTaskIssueTypes() "
        f'AND created >= "{sub_day}" AND created < "{(sp[1] + rp_message.dt.timedelta(days=1)).date().isoformat()}"',
        ["parent", "created"],
    )
    assert summ.extra and summ.rows == []


def test_build_jql(rp_fetch, rp_message):
    assert rp_fetch.build_jql("INNO", rp_message.dt.date(2026, 10, 1)) == (
        'project = INNO AND issuetype in (Task, Bug, Issue) AND (updated >= "2026-09-30" '
        'OR status not in ("done", "Backlog", "Deleted"))'
    )


def test_state_at(rp_period, rp_fetch, rp_metrics):
    """1일 0시 전 마지막 변경의 도착 상태, 없으면 뒤 첫 변경의 출발 상태, 변경이 없으면 지금 상태."""
    tickets = {
        t.key: t
        for t in load(
            rp_fetch,
            {
                "BEFORE": ticket(
                    status="done",
                    changelog=[
                        change("2026-11-05T10:00", "To Do", "In Progress"),
                        change("2026-11-30T23:59", "In Progress", "ready-to-done"),
                        change("2026-12-02T10:00", "ready-to-done", "done"),
                    ],
                ),
                "AFTER": ticket(status="In Progress", changelog=[change("2026-12-01T00:00", "Backlog", "In Progress")]),
                "NONE": ticket(status="To Do"),
            },
        )
    }
    start = rp_period.months_span("2026-12", "2026-12")[0]
    assert rp_metrics.state_at(tickets["BEFORE"], start) == "ready-to-done"
    assert rp_metrics.state_at(tickets["AFTER"], start) == "Backlog"  # 0시 정각의 변경은 그 뒤 첫 변경으로 봄
    assert rp_metrics.state_at(tickets["NONE"], start) == "To Do"


def test_ticket_overview_update_records_and_causes(rp_period, rp_fetch, rp_metrics, ja):
    """업데이트 기록 다섯 가지를 하나씩 세고, 업데이트 없는 기존 ticket은 기간 끝 상태 이름별로 셈."""
    old = "2026-11-01T09:00"
    specs = {
        "COMMENT": ticket(created=old, status="In Progress", comments=[human("2026-12-03T10:00")]),
        "DESC": ticket(created=old, status="In Progress", changelog=[desc_edit("2026-12-03T10:00")]),
        "STATUS": ticket(created=old, status="done", changelog=[change("2026-12-03T10:00", "ready-to-done", "done")]),
        "SUB": ticket(created=old, status="In Progress"),
        "INNO-5": ticket(created=old, status="In Progress"),  # PR 추가
        "AGENT": ticket(  # agent comment(검수 comment)만 있음 -> 업데이트 아님, In Progress
            created=old, status="In Progress", comments=[review(ja, "2026-12-03T10:00", "pass", checks(T1="pass"))]
        ),
        "WAIT": ticket(  # 11월 request 전환 뒤 기록 없음 -> ready-to-done
            created=old, status="ready-to-done", changelog=[change("2026-11-20T10:00", "In Progress", "ready-to-done")]
        ),
        "TODO": ticket(created=old, status="To Do", comments=[human("2026-11-30T23:59")]),  # 11월 comment -> To Do
        "DONE": ticket(created=old, status="done", changelog=[change("2026-11-30T10:00", "In Progress", "done")]),
        "BACKLOG": ticket(  # 1일 0시 Backlog, 12월에 다시 진행 -> 기존 ticket 아님
            created=old, status="In Progress", changelog=[change("2026-12-05T10:00", "Backlog", "In Progress")]
        ),
        "NEW1": ticket(created="2026-12-01T00:00", status="To Do"),
        "NEW2": ticket(created="2026-12-20T10:00", status="In Progress"),
        "NEW3": ticket(created="2026-12-21T10:00", status="Done"),
        "NEW4": ticket(created="2026-12-22T10:00", status="In Progress"),
        "LATER": ticket(created="2027-01-02T10:00", status="To Do"),  # 기간 뒤에 만듦 -> 세지 않음
    }
    tickets = load(rp_fetch, specs)
    by_key = {t.key: t for t in tickets}
    sp = month(rp_period, "2026-12")
    rp_fetch.attach_updates(
        tickets,
        rp_fetch.fetch_subtasks(
            FakeJira({}, [subtask("SUB-1", "SUB", "2026-12-04T10:00")]), "INNO", sp[0].date(), sp[1].date()
        ),
        rp_fetch.pr_times([{"title": "[INNO-5] PR 추가", "createdAt": "2026-12-05T01:00:00Z"}], "INNO"),
    )
    for key in ("COMMENT", "DESC", "STATUS", "SUB", "INNO-5"):
        assert rp_metrics.updated_in(by_key[key], sp), key
    for key in ("AGENT", "WAIT", "TODO"):
        assert not rp_metrics.updated_in(by_key[key], sp), key
    ov = rp_metrics.ticket_overview(tickets, sp, sp[0])
    assert (ov.existing, ov.updated, ov.updated_done) == (8, 5, 1)
    assert dict(ov.idle) == {"ready-to-done": 1, "In Progress": 1, "To Do": 1}
    assert dict(ov.new) == {"To Do": 1, "In Progress": 2, "done": 1}
    # PR 검색이 실패하면(PR 시각 없음) PR 추가만 있는 ticket은 업데이트 없음
    rp_fetch.attach_updates(tickets, {}, {})
    assert not rp_metrics.updated_in(by_key["INNO-5"], sp)


def test_max_request_count_and_members(rp_period, rp_fetch, rp_metrics, ja):
    def reqs(who: dict, days: list[int], **kw) -> dict:
        log = [change(f"2026-12-{d:02d}T09:00", "In Progress", "ready-to-done", who) for d in days]
        return ticket(who=who, changelog=log, **kw)

    tickets = load(
        rp_fetch,
        {
            "INNO-12": reqs(KIM, [1, 2]),
            "INNO-9": reqs(KIM, [3, 4]),  # 같은 횟수면 짧은 키가 앞섬
            "INNO-30": reqs(LEE, [1, 2, 3], comments=[review(ja, "2026-12-01T10:00", "pass", checks(R4="pass"))]),
            "INNO-31": ticket(who=LEE, changelog=[change("2026-11-30T09:00", "In Progress", "ready-to-done")]),
            "INNO-40": ticket(who=PARK, created="2026-10-01T09:00", status="done"),  # 기록 없음 -> 팀원별에서 뺌
        },
    )
    sp = month(rp_period, "2026-12")
    assert rp_metrics.max_request_count(tickets, sp) == (3, "INNO-30")
    assert rp_metrics.max_request_count([t for t in tickets if t.assignee_id == "acc-kim"], sp) == (2, "INNO-9")
    assert rp_metrics.max_request_count([], sp) == (0, "")
    rows = {r.name: r for r in rp_metrics.member_rows(tickets, sp)}
    assert set(rows) == {"김담당", "이담당"}
    assert (rows["이담당"].first, rows["이담당"].max_request, rows["이담당"].stop) == ((1, 1), (3, "INNO-30"), (1, 1))
    assert rows["김담당"].max_request == (2, "INNO-9")
    assert "박담당" in {r.name for r in rp_metrics.member_rows(tickets, sp, include=["acc-park"])}


def test_lead_wait_and_confirm(rp_time, rp_period, rp_fetch, rp_metrics, rp_message, ja):
    """팀장 확인 대기는 기간 끝 ready-to-done ticket, 팀장 확인 시간은 팀장 계정의 전환만 셈.

    팀장 확인 시간에서 자동 보류 전환(진입 뒤 전환 전 마지막 검수 comment가 자동 보류)은 뺌.
    """
    rtd, ip = "ready-to-done", "In Progress"
    tickets = load(
        rp_fetch,
        {
            "W1": ticket(  # 12-24(목) 진입 -> 12-31(목)까지 영업일 5일(25, 28, 29, 30, 31)
                status=rtd,
                changelog=[change("2026-12-01T09:00", ip, rtd, KIM), change("2026-12-24T18:00", ip, rtd, KIM)],
            ),
            "W2": ticket(status=rtd, changelog=[change("2026-12-30T09:00", ip, rtd, KIM)]),  # 1영업일
            "LATER": ticket(  # 기간 뒤 팀장 전환으로 지금은 done이지만 기간 끝에는 ready-to-done
                status="done",
                changelog=[change("2026-12-29T09:00", ip, rtd, KIM), change("2027-01-04T09:00", rtd, "done")],
            ),
            "C1": ticket(  # 팀장 전환 2회(1시간, 3시간), 담당자 전환 1회는 뺌
                status="done",
                changelog=[
                    change("2026-12-01T09:00", ip, rtd, KIM),
                    change("2026-12-01T10:00", rtd, ip, LEAD),
                    change("2026-12-02T09:00", ip, rtd, KIM),
                    change("2026-12-02T11:00", rtd, ip, KIM),
                    change("2026-12-03T09:00", ip, rtd, KIM),
                    change("2026-12-03T12:00", rtd, "done", LEAD),
                ],
            ),
            "PREV": ticket(  # 11월 진입, 12월 팀장 전환 -> 머문 시간 24시간
                status="done",
                changelog=[change("2026-11-30T10:00", ip, rtd, KIM), change("2026-12-01T10:00", rtd, "done", LEAD)],
            ),
            "HOLD": ticket(  # 자동 보류 검수 뒤 팀장 계정 전환은 뺌. 다음 진입의 보통 보류 뒤 전환(30분)은 셈
                status=ip,
                changelog=[
                    change("2026-12-07T09:00", ip, rtd, KIM),
                    change("2026-12-07T09:11", rtd, ip, LEAD),
                    change("2026-12-08T09:00", ip, rtd, KIM),
                    change("2026-12-08T09:30", rtd, ip, LEAD),
                ],
                comments=[
                    review(ja, "2026-12-07T09:05", "pass", checks(T1="pass")),
                    review(ja, "2026-12-07T09:10", "fix", checks(T1="fail"), HOLD_NOTE),
                    review(ja, "2026-12-08T09:10", "fix", checks(T1="fail")),
                ],
            ),
            "LATE": ticket(  # 실제 순서: 자동 보류 전환(09:05) 뒤에 auto_hold 검수 comment(09:06) -> 뺌
                status=ip,
                changelog=[change("2026-12-09T09:00", ip, rtd, KIM), change("2026-12-09T09:05", rtd, ip, LEAD)],
                comments=[review(ja, "2026-12-09T09:06", "fix", checks(T1="fail"), HOLD_NOTE)],
            ),
        },
    )
    sp = month(rp_period, "2026-12")
    assert rp_metrics.lead_wait(tickets, sp[1]) == (3, 5)
    assert rp_metrics.lead_wait([], sp[1]) == (0, 0)
    assert sorted(rp_metrics.lead_confirm_minutes(tickets, sp, LEAD_ID)) == [30, 60, 180, 1440]
    assert rp_metrics.lead_confirm_minutes(tickets, sp, "") == []
    assert (
        rp_metrics.business_days(rp_message.dt.datetime(2026, 12, 4, 18, tzinfo=rp_time.KST), sp[1]) == 19
    )  # precheck와 같은 규칙


def test_lead_wait_counts_until_now(rp_time, rp_fetch, rp_message):
    """달 중간 실행이면 팀장 확인 대기의 최장 영업일을 실행 시각까지만 셈. 실행 시각 뒤 진입과 생성은 뺌."""
    rtd, ip = "ready-to-done", "In Progress"
    tickets = load(
        rp_fetch,
        {
            "W1": ticket(created="2026-11-02T09:00", status=rtd, changelog=[change("2026-12-01T09:00", ip, rtd, KIM)]),
            "W2": ticket(created="2026-11-02T09:00", status=rtd, changelog=[change("2026-12-20T09:00", ip, rtd, KIM)]),
            "NEW": ticket(created="2026-12-15T09:00", status=rtd),
        },
    )
    mid = rp_message.Report(
        tickets, "2026-12", LEAD_ID, now=rp_message.dt.datetime(2026, 12, 10, 12, tzinfo=rp_time.KST)
    )
    assert "• 팀장 확인 대기: 1건(최장 7영업일)" in mid.monthly_message()  # 12-02–12-10의 영업일
    assert "- 팀장 확인 대기: 1건(최장 7영업일)" in mid.summary_lines()
    full = rp_message.Report(tickets, "2026-12", LEAD_ID)  # 실행 시각 2027-01-01(clock fixture): 기간 끝까지 셈
    assert "• 팀장 확인 대기: 3건(최장 22영업일)" in full.monthly_message()  # 12-02–12-31의 영업일


def test_idle_status_names(rp_period, rp_fetch, rp_message):
    """업데이트 없음은 기간 끝 상태 이름으로 STATUS_ORDER 순(목록 밖 상태는 뒤에 이름 순), 두 줄 이내로 적음."""
    old = "2026-10-01T09:00"
    names = ["QA", "ready-to-done", "To Do", "In Progress", "In Progress"]
    tickets = load(rp_fetch, {f"INNO-{i}": ticket(created=old, status=n) for i, n in enumerate(names)})
    lines = rp_message.Report(tickets, "2026-12", LEAD_ID).overview_lines(month(rp_period, "2026-12"))
    assert lines[2:4] == ["    ◦ 업데이트 없음 5개: To Do 1, In Progress 2", "    ◦ ready-to-done 1, QA 1"]
    many = [f"상태{i:02d}" for i in range(12)]
    tickets = load(rp_fetch, {f"INNO-{i}": ticket(created=old, status=n) for i, n in enumerate(many)})
    lines = rp_message.Report(tickets, "2026-12", LEAD_ID).overview_lines(month(rp_period, "2026-12"))
    assert lines[2:5] == [
        "    ◦ 업데이트 없음 12개: 상태00 1, 상태01 1, 상태02 1, 상태03 1",
        "    ◦ 상태04 1, 상태05 1, 상태06 1, 상태07 1, 상태08 1, 외 3종",
        "• 새 ticket 0개",
    ]


def test_pr_search_failure(rp_fetch, rp_message, rp_run, sample, monkeypatch, no_gh):
    """GH_TOKEN이 없거나 gh가 실패하면 None. 그때 PR 추가를 업데이트에서 빼고 월간 메시지에 한 줄 적음."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert no_gh("everex-ai", rp_message.dt.date(2026, 11, 30), rp_message.dt.date(2026, 12, 31)) is None
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return rp_fetch.subprocess.CompletedProcess(args, 1, "", "HTTP 401")

    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setattr(rp_fetch.subprocess, "run", fake_run)
    assert no_gh("everex-ai", rp_message.dt.date(2026, 11, 30), rp_message.dt.date(2026, 12, 31)) is None
    assert calls == [
        ["gh", "search", "prs", "--owner", "everex-ai", "--created", "2026-11-30..2026-12-31"]
        + ["--json", "title,createdAt", "--limit", "1000"]
    ]
    out = '[{"title": "INNO-1 a", "createdAt": "2026-12-01T00:00:00Z"}]'
    monkeypatch.setattr(
        rp_fetch.subprocess, "run", lambda args, **kw: rp_fetch.subprocess.CompletedProcess(args, 0, out, "")
    )
    assert no_gh("everex-ai", rp_message.dt.date(2026, 11, 30), rp_message.dt.date(2026, 12, 31)) == [
        {"title": "INNO-1 a", "createdAt": "2026-12-01T00:00:00Z"}
    ]
    seen = []
    summ = Summ()
    msgs = rp_run.run_report(
        FakeJira(sample, SUBTASKS), "2026-12", LEAD_ID, summ, pr_search=lambda *a: seen.append(a) or None
    )
    assert seen == [("everex-ai", rp_message.dt.date(2026, 11, 30), rp_message.dt.date(2026, 12, 31))]
    assert block(msgs[0], "*팀 전체*", 4) == [
        "• 기존 ticket 10개",
        "    ◦ 업데이트 6개(그중 done 전환 2개)",  # PR 추가만 있는 INNO-113이 빠짐
        "    ◦ 업데이트 없음 4개: In Progress 3, ready-to-done 1",
        "• PR 검색 실패: PR 추가는 업데이트에서 뺌",
    ]
    assert all("PR 검색 실패" not in m for m in msgs[1:])
    assert "- PR 검색 실패: PR 추가는 업데이트에서 뺌" in summ.extra


def test_many_members_fold_into_others(rp_run, ja):
    """담당자가 많으면 메시지가 40줄을 넘지 않게 앞 담당자만 적고 나머지는 "외 n명"으로 줄임."""
    people = [
        {"accountId": f"acc-{i:02d}", "displayName": f"담당{i:02d}", "accountType": "atlassian"} for i in range(30)
    ]
    specs = {
        f"INNO-{i + 1}": ticket(who=who, comments=[review(ja, "2026-12-03T10:00", "pass", checks(T1="pass"))])
        for i, who in enumerate(people)
    }
    for msg in rp_run.run_report(FakeJira(specs), "2026-12", LEAD_ID, Summ()):
        lines = msg.splitlines()
        assert len(lines) <= 40
        assert_line_length(msg)
        shown = [x for x in lines if x.startswith("• 담당")]
        rest = [x for x in lines if x.startswith("• 외 ")]
        assert len(rest) == 1
        assert len(shown) + int(re.fullmatch(r"• 외 (\d+)명", rest[0]).group(1)) == 30
        assert shown[0].startswith("• 담당00: 1/1")


def test_description_edit_outside_deliverables_only(rp_parse):
    """결과 산출물 구역만 바뀐 description 수정(jira-doc의 set_section)은 업데이트로 세지 않음."""
    base = "h2. 진행 배경\n배경\n\nh2. 결과 산출물\n이전 결과\n\nh2. 참고\n링크"

    def edit(when: str, old: str, new: str) -> dict:
        return {
            "created": ts(when),
            "author": LEAD,
            "items": [{"field": "description", "fromString": old, "toString": new}],
        }

    log = [
        edit("2026-12-01T10:00", base, base.replace("이전 결과", "새 결과")),  # 구역 안만 바뀜
        edit("2026-12-02T10:00", "h2. 진행 배경\n배경", "h2. 진행 배경\n배경\n\nh2. 결과 산출물\n결과"),  # 구역 추가
        edit("2026-12-03T10:00", base, base.replace("링크", "새 링크")),  # 구역 밖이 바뀜
        edit("2026-12-04T10:00", None, "h2. 진행 배경\n처음 작성"),  # 처음 작성
    ]
    assert [t.day for t in rp_parse.description_edits(log)] == [3, 4]


def test_new_ticket_statuses_and_worst_case_lines(rp_run):
    """새 ticket 상태가 많아도 줄마다 50자 이내이고, 하위 줄은 두 줄 이내."""
    statuses = ["To Do", "In Progress", "ready-to-done", "done", "Backlog"]
    five = {f"INNO-{i}": ticket(created="2026-12-10T10:00", status=st) for i, st in enumerate(statuses, 1)}
    monthly = rp_run.run_report(FakeJira(five), "2026-11", LEAD_ID, Summ())[0]  # 12월 생성: 11월 보고에는 안 나옴
    assert "• 새 ticket 0개" in monthly
    monthly = rp_run.run_report(FakeJira(five), "2026-12", LEAD_ID, Summ())[0]
    assert block(monthly, "• 새 ticket 5개", 2) == [
        "    ◦ To Do 1, In Progress 1, ready-to-done 1, done 1",
        "    ◦ Backlog 1",
    ]
    assert_line_length(monthly)


def test_worst_case_stays_within_40_lines(rp_fetch, rp_text, rp_run, ja, monkeypatch):
    """선택 줄이 모두 나와도 월간 묶음은 40줄이고, 팀원별 절에 담당자 한 명과 "외 n명"이 남음.

    선택 줄: 업데이트 없음 두 줄, PR 상한, 새 ticket 하위 두 줄, 검색 상한, 비용 기록 없음, Top3 세 ticket.
    담당자는 20명이다.
    """
    monkeypatch.setattr(rp_fetch, "SEARCH_LIMIT", 1)
    people = [
        {"accountId": f"acc-{i:02d}", "displayName": f"담당{i:02d}", "accountType": "atlassian"} for i in range(20)
    ]
    first = [review(ja, "2026-12-03T10:00", "pass", checks(T1="pass"))]
    specs = {
        f"INNO-{i + 1}": ticket(who=who, comments=first, runs=[run("2026-11-05T10:00", 1, 0.01)])
        for i, who in enumerate(people)
    }
    runs = [
        run("2026-12-02T10:00", 1, 1.0),
        run("2026-12-03T10:00", 2, 1.0),
        run("2026-12-04T10:00", 3, 1.0, verdict="failed"),
        run("2026-12-05T10:00", None, 1.0, mode="digest", verdict="digest"),
        run("2026-12-06T10:00", 4, None),
    ]
    specs |= {f"INNO-{90 + i}": ticket(title="가" * 30, runs=runs) for i in range(3)}
    names = ["To Do", "In Progress", "ready-to-done", "done", "Backlog", "Deleted", "QA", "Review", "Hold"]
    names += ["Blocked", "Design", "Testing"]
    specs |= {f"INNO-{100 + i}": ticket(created="2026-12-10T10:00", status=n) for i, n in enumerate(names)}
    # 업데이트 없는 기존 ticket: 진행 상태 9종
    idle = [n for n in names if n not in ("done", "Backlog", "Deleted")]
    specs |= {f"INNO-{200 + i}": ticket(created="2026-10-01T09:00", status=n) for i, n in enumerate(idle)}
    prs = [{"title": "x", "createdAt": "2026-12-01T00:00:00Z"}] * 1000
    msgs = rp_run.run_report(FakeJira(specs), "2026-12", LEAD_ID, Summ(), pr_search=lambda *a: prs)
    monthly = msgs[0]
    assert "• PR 검색이 상한 1000건에 닿음" in monthly
    assert "• 검색 결과 상한 1개에 닿음: 빠진 ticket 있을 수 있음" in monthly
    assert block(monthly, "    ◦ 업데이트 0개(그중 done 전환 0개)", 3) == [
        "    ◦ 업데이트 없음 9개: To Do 1, In Progress 1",
        "    ◦ ready-to-done 1, Blocked 1, Design 1, Hold 1, 외 3종",
        "• PR 검색이 상한 1000건에 닿음",
    ]
    new = block(monthly, "• 새 ticket 12개", 3)
    assert re.search(r", 외 \d+종$", new[1]) and not new[2].startswith("    ◦")  # 하위 줄 두 줄 이내
    assert "    ◦ 비용 기록 없는 실행 3건은 0 USD로 셈" in monthly
    # 제목에 남길 자리가 4자 미만이면 사유만 적음
    assert block(monthly, "    ◦ (1) INNO-90 Task - 4.00 USD", 1) == [
        "        ◦ 첫 검수 1회, 반려 cycle 재검수 2회, 재실행·실패 1회, 정리 모드 1회"
    ]
    assert block(monthly, "*팀원별*", 4) == [
        "• 순서: 첫 검수 통과율 / 최대 검수 반복 횟수 / stop 사유 기록률",
        "• 담당00: 1/1 / - / -",
        "    ◦ 업데이트 없음 0개",
        "• 외 19명",
    ]
    assert len(monthly.splitlines()) == 40
    # 남은 줄이 3줄 미만이면 절 제목과 요약 한 줄만 적음
    assert rp_text._fit_members("순서", [["• a"]] * 4, 2) == ["*팀원별*", "• 담당자 4명: 줄 수 상한으로 생략"]
    assert rp_text._fit_members("순서", [], 2) == ["*팀원별*", "• 기록 없음"]
    for msg in msgs:
        assert len(msg.splitlines()) <= 40
        assert [x for x in msg.splitlines() if x.startswith("*")] == SECTIONS
        assert_line_length(msg)


def test_long_member_name_is_cut(rp_run, ja):
    long = {"accountId": "acc-long", "displayName": "가나다라마바사아자차카타파하", "accountType": "atlassian"}
    specs = {"INNO-1": ticket(who=long, comments=[review(ja, "2026-12-03T10:00", "pass", checks(T1="pass"))])}
    for msg in rp_run.run_report(FakeJira(specs), "2026-12", LEAD_ID, Summ()):
        assert "• 가나다라마바사아자차카타…: 1/1" in msg
        assert "파하" not in msg


def test_monthly_message(rp_run, sample):
    msgs = report(rp_run, sample)
    assert len(msgs) == 3
    monthly = msgs[0]
    assert_shape(monthly, "[2026-12 jira-doc 월간 보고]", "2026-12-01–2026-12-31")
    assert monthly.splitlines()[2:] == [
        "*팀장 업무*",
        "• 판정 일치율: 3/3건, 100.0%",
        "    ◦ 최근 20건: 5/6건, 83.3%",
        "• 팀장 확인 대기: 1건(최장 29영업일)",  # INNO-109: 11-20(금) 진입
        "• 팀장 확인 시간: 중앙값 2.5시간(4건)",  # 60, 120, 180, 360분. INNO-101의 담당자 전환은 뺌
        "*팀 전체*",
        "• 기존 ticket 10개",
        "    ◦ 업데이트 7개(그중 done 전환 2개)",
        "    ◦ 업데이트 없음 3개: In Progress 2, ready-to-done 1",
        "• 새 ticket 2개",
        "    ◦ To Do 1, In Progress 1",
        "• 첫 검수 통과율: 1/3건, 33.3%",
        "• 최대 검수 반복 횟수: 3회(INNO-101)",
        "• stop 사유 기록률: 2/4건, 50.0%",
        "*팀원별*",
        "• 순서: 첫 검수 통과율 / 최대 검수 반복 횟수 / stop 사유 기록률",
        "• 김담당: 0/1 / 3회 / 2/3",
        "    ◦ 업데이트 없음 1개, 후보(A, B: T2)",
        "• 이담당: 1/1 / 1회 / -",
        "    ◦ 업데이트 없음 0개, 지난달 후보: 개선",
        "• 박담당: 0/1 / 1회 / 0/1",
        "    ◦ 업데이트 없음 2개",
        "*Claude 활용 요약*",
        "• 자동 검수 소요 시간: 평균 1740초(600–2700초, 5건)",  # 20, 30, 40, 10, 45분
        "• API 환산 금액: 5.80 USD(전월 대비 +81%, 사용량 많음)",  # 전월 3.20 USD
        "• 토큰: input 700, output 350",
        "    ◦ cache 쓰기(지시·기록 저장) 70",
        "    ◦ cache 읽기(저장분 재사용) 7.0천",
        "• Claude 사용 ticket Top3(API 환산 금액 순)",
        "    ◦ (1) INNO-101 Task 김담당 3.00 USD",
        "        ◦ PoseNet-S 경량화로 엣지 기기… | 첫 검수 1회, 반려 cycle 재검수 2회",
        "    ◦ (2) INNO-102 Task 이담당 1.70 USD",
        "        ◦ 검색 결과 &amp; 정렬 개선 | 첫 검수 1회, 재실행·실패 1회, 정리 모드 1회",
        "    ◦ (3) INNO-103 Bug 박담당 1.10 USD",
        "        ◦ 로그인 실패 버그 | 재실행·실패 1회",
        DEF_LINE,
    ]


def test_quarterly_and_yearly_messages(rp_run, sample):
    _, quarter, year = report(rp_run, sample)
    assert_shape(quarter, "[2026 Q4 jira-doc 분기 보고]", "2026-10-01–2026-12-31")
    assert quarter.splitlines()[2:] == [
        "*팀장 업무*",
        "• 판정 일치율: 5/6건, 83.3%",
        "    ◦ 최근 20건: 5/6건, 83.3%",
        "• 검토 요청 비율: 1/9건, 11.1%",
        "• 팀장 확인 시간: 중앙값 2시간(7건)",  # 60, 60, 60, 120, 180, 360, 1440분. INNO-108 자동 보류는 뺌
        "*팀 전체*",
        "• KR별 하위 Task",
        "    ◦ INNO-23: done 2개, 달성률 4/5개",
        "    ◦ INNO-33: done 2개, 달성률 4/6개",
        "• ❌ 상위 5개: T2 3, R3 2, R4 2, T4 2, B3 1",  # ❌ 횟수가 같으면 검사 ID 순
        "• 첫 검수 통과율: 1/6건, 16.7%",
        "• 최대 검수 반복 횟수: 3회(INNO-101)",
        "*팀원별*",
        "• 순서: 첫 검수 통과율 / 최대 검수 반복 횟수 / stop 사유 기록률",
        "• 김담당: 0/1 / 3회 / 2/3",
        "• 박담당: 0/3 / 1회 / 0/1",
        "• 이담당: 1/2 / 2회 / -",
        "*Claude 활용 요약*",
        "• 자동 검수 소요 시간: 평균 1473초(360–2700초, 9건)",
        "• API 환산 금액: 10.70 USD(직전 기록 없음)",  # 전 분기(Q3) 실행 없음. 사용량 많음은 월간에만
        "• 토큰: input 1.9천, output 950",
        "    ◦ cache 쓰기(지시·기록 저장) 190",
        "    ◦ cache 읽기(저장분 재사용) 1.9만",
        "• Claude 사용 ticket Top3(API 환산 금액 순)",
        "    ◦ (1) INNO-101 Task 김담당 3.00 USD",
        "        ◦ PoseNet-S 경량화로 엣지 기기… | 첫 검수 1회, 반려 cycle 재검수 2회",
        "    ◦ (2) INNO-106 Task 이담당 2.00 USD",
        "        ◦ 첫 검수 1회, 반려 cycle 재검수 1회",  # 제목 없음
        "    ◦ (3) INNO-102 Task 이담당 1.70 USD",
        "        ◦ 검색 결과 &amp; 정렬 개선 | 첫 검수 1회, 재실행·실패 1회, 정리 모드 1회",
        DEF_LINE,
    ]
    assert_shape(year, "[2026 jira-doc 연간 보고]", "2026-01-01–2026-12-31")
    assert year.splitlines()[2:] == [
        "*팀장 업무*",
        "• 판정 일치율: 5/6건, 83.3%",
        "    ◦ 분기별: Q1 -, Q2 -, Q3 -, Q4 5/6",
        "*팀 전체*",
        "• 첫 검수 통과율: 1/6건, 16.7%",
        "    ◦ 분기별: Q1 -, Q2 -, Q3 -, Q4 1/6",
        "• ❌ 상위 3개의 분기별 만족률",
        "    ◦ T2: Q1 -, Q2 -, Q3 -, Q4 4/7",
        "    ◦ R3: Q1 -, Q2 -, Q3 -, Q4 0/2",
        "    ◦ R4: Q1 -, Q2 -, Q3 -, Q4 2/4",
        "*팀원별*",
        "• 순서: 첫 검수 통과율 / 최대 검수 반복 횟수",
        "• 김담당: 0/1 / 3회",
        "• 박담당: 0/3 / 1회",
        "• 이담당: 1/2 / 2회",
        "*Claude 활용 요약*",
        "• 자동 검수 소요 시간: 평균 1473초(360–2700초, 9건)",
        "• API 환산 금액: 10.70 USD(직전 기록 없음)",
        "    ◦ 분기별: Q1 -, Q2 -, Q3 -, Q4 10.70",
        "• 토큰: input 1.9천, output 950",
        "    ◦ cache 쓰기(지시·기록 저장) 190",
        "    ◦ cache 읽기(저장분 재사용) 1.9만",
        "• Claude 사용 ticket Top3(API 환산 금액 순)",
        "    ◦ (1) INNO-101 Task 김담당 3.00 USD",
        "        ◦ PoseNet-S 경량화로 엣지 기기… | 첫 검수 1회, 반려 cycle 재검수 2회",
        "    ◦ (2) INNO-106 Task 이담당 2.00 USD",
        "        ◦ 첫 검수 1회, 반려 cycle 재검수 1회",  # 제목 없음
        "    ◦ (3) INNO-102 Task 이담당 1.70 USD",
        "        ◦ 검색 결과 &amp; 정렬 개선 | 첫 검수 1회, 재실행·실패 1회, 정리 모드 1회",
        DEF_LINE,
    ]


def test_candidate_mark_and_wrap(rp_model, rp_text, rp_message):
    mark = rp_message.Report.candidate_mark
    assert mark(rp_model.Candidate("a", [("INNO-1", 2)], [])) == "후보(A)"
    assert mark(rp_model.Candidate("a", [], [("T2", 3), ("R4", 2), ("T4", 2)])) == "후보(B: T2, R4 외 1)"
    assert rp_text._wrap("갈래: ", ["a" * 20, "b" * 20, "c" * 20]) == ["갈래: " + "a" * 20 + ", " + "b" * 20, "c" * 20]
    assert rp_text._wrap("갈래: ", []) == []
    assert rp_text._capped_wrap(["a" * 20, "b" * 20, "c" * 20], 1, "앞: ") == ["앞: " + "a" * 20 + ", 외 2종"]
    assert rp_text._capped_wrap([], 2, "앞: ") == []


def test_seconds_and_token_count(rp_text):
    """자동 검수 소요 시간(평균, 최소, 최대 초, 건수)과 토큰 단위 표기."""
    assert rp_text._seconds([2, 6.5, 15]) == "평균 470초(120–900초, 3건)"
    assert rp_text._seconds([0.25]) == "평균 15초(15–15초, 1건)"
    assert rp_text._seconds([]) == "-"
    nums = [0, 999, 1000, 1300, 5000, 61000, 235000, 10**8, 123_000_000]
    assert [rp_text._count(n) for n in nums] == [
        "0",
        "999",
        "1.0천",
        "1.3천",
        "5.0천",
        "6.1만",
        "23.5만",
        "1.0억",
        "1.2억",
    ]


def test_claude_cost_change_and_high_mark(rp_fetch, rp_message):
    """API 환산 금액의 직전 기간 대비 %(+, -, 0), 직전 기록 없음. 사용량 많음 표시는 월간 묶음에만 붙음."""

    def rep(prev: float, cur: float):
        runs = [run("2026-11-10T10:00", 1, prev)] if prev else []
        return rp_message.Report(
            load(rp_fetch, {"INNO-1": ticket(runs=[*runs, run("2026-12-10T10:00", 2, cur)])}), "2026-12", ""
        )

    def cost_line(r, monthly: bool = True) -> str:
        return r.claude_lines(r.cur, r.prev, "전월", monthly=monthly)[2]

    assert cost_line(rep(2.0, 3.62)) == "• API 환산 금액: 3.62 USD(전월 대비 +81%)"
    assert cost_line(rep(2.0, 1.76)) == "• API 환산 금액: 1.76 USD(전월 대비 -12%)"
    assert cost_line(rep(2.0, 2.0)) == "• API 환산 금액: 2.00 USD(전월 대비 +0%)"
    assert cost_line(rep(0.0, 1.0)) == "• API 환산 금액: 1.00 USD(직전 기록 없음)"
    r = rep(2.0, 3.62)
    r.high = (True, "해당함", [])
    assert cost_line(r) == "• API 환산 금액: 3.62 USD(전월 대비 +81%, 사용량 많음)"
    assert cost_line(r, monthly=False) == "• API 환산 금액: 3.62 USD(전월 대비 +81%)"
    r.high = (None, "판정 불가", [])
    assert cost_line(r) == "• API 환산 금액: 3.62 USD(전월 대비 +81%)"


def test_quarterly_and_yearly_compare_previous_period(rp_fetch, rp_message):
    """분기 묶음은 전 분기, 연간 묶음은 전년과 비교하고 연간 묶음은 분기별 줄을 둠."""
    runs = [run("2025-06-01T10:00", 1, 1.0), run("2026-09-01T10:00", 2, 2.0), run("2026-12-01T10:00", 3, 3.0)]
    r = rp_message.Report(load(rp_fetch, {"INNO-1": ticket(runs=runs)}), "2026-12", LEAD_ID)
    quarter, year = r.quarterly_message(), r.yearly_message()
    assert "• API 환산 금액: 3.00 USD(전 분기 대비 +50%)" in quarter.splitlines()
    assert block(year, "*Claude 활용 요약*", 3)[1:] == [
        "• API 환산 금액: 5.00 USD(전년 대비 +400%)",
        "    ◦ 분기별: Q1 -, Q2 -, Q3 2.00, Q4 3.00",
    ]


def test_top_ticket_lines(rp_period, rp_fetch, rp_text):
    """Top3: 비용 합 순, 같은 비용이면 키 순, 0회 갈래 뺌, 제목 20자 자르기, 둘째 줄 50자에 맞춰 제목 줄이기/빼기."""
    sp = month(rp_period, "2026-12")
    tickets = load(
        rp_fetch,
        {
            "INNO-10": ticket(  # 1.00 USD(비용 없는 실행은 0)
                who=KIM, title="가" * 25, runs=[run("2026-12-01T10:00", 1, 1.0), run("2026-12-02T10:00", 2, None)]
            ),
            "INNO-9": ticket(  # 담당자 없음. 11월 실행은 뺌
                itype="Bug", title="짧은 제목", runs=[run("2026-11-30T10:00", 2, 9.0), run("2026-12-01T10:00", 1, 0.5)]
            ),
            "INNO-11": ticket(who=LEE, title="나" * 20, runs=[run("2026-12-03T10:00", 1, 0.5)]),  # 같은 비용: 키 순
            "INNO-2": ticket(who=PARK, title="x", runs=[run("2026-12-03T10:00", 1, 0.1)]),  # 4위: 빠짐
            "INNO-3": ticket(who=PARK, runs=[run("2026-11-03T10:00", 1, 5.0)]),  # 기간 안 실행 없음
        },
    )
    assert rp_text.top_ticket_lines(tickets, sp) == [
        "• Claude 사용 ticket Top3(API 환산 금액 순)",
        "    ◦ (1) INNO-10 Task 김담당 1.00 USD",
        "        ◦ " + "가" * 20 + "… | 첫 검수 1회, 반려 cycle 재검수 1회",
        "    ◦ (2) INNO-9 Bug - 0.50 USD",
        "        ◦ 짧은 제목 | 첫 검수 1회",
        "    ◦ (3) INNO-11 Task 이담당 0.50 USD",
        "        ◦ " + "나" * 20 + " | 첫 검수 1회",
    ]
    three = [
        run("2026-12-01T10:00", 1, 0.3, verdict="failed"),
        run("2026-12-01T10:05", 1, 0.3),
        run("2026-12-02T10:00", None, 0.3, mode="digest", verdict="digest"),
    ]
    four = [*three, run("2026-12-03T10:00", 2, 0.3), run("2026-12-04T10:00", 3, 0.3)]
    tickets = load(
        rp_fetch,
        {
            "INNO-1": ticket(title="다" * 25, runs=three),  # 사유 28자: 제목을 18자 + "…"로 줄임
            "INNO-2": ticket(title="라" * 25, runs=four),  # 사유 45자: 제목 자리 2자라 사유만
        },
    )
    lines = rp_text.top_ticket_lines(tickets, sp)
    assert lines[1:] == [
        "    ◦ (1) INNO-2 Task - 1.50 USD",
        "        ◦ 첫 검수 1회, 반려 cycle 재검수 2회, 재실행·실패 1회, 정리 모드 1회",
        "    ◦ (2) INNO-1 Task - 0.90 USD",
        "        ◦ " + "다" * 18 + "… | 첫 검수 1회, 재실행·실패 1회, 정리 모드 1회",
    ]
    assert_line_length("\n".join(lines))
    assert rp_text.top_ticket_lines(tickets, month(rp_period, "2026-11")) == ["• Claude 사용 ticket Top3: -"]
    # 세 자리 횟수: 사유만으로 50자를 넘으면 뒤 갈래부터 "외 n종"으로 줄임
    many = [run(f"2026-12-01T09:{i % 60:02d}", None, 0.01) for i in range(100)]  # 첫 검수 100회(requestNo 없음)
    many += [run(f"2026-12-01T10:{i % 60:02d}", 2 + i, 0.01) for i in range(100)]  # 반려 cycle 재검수 100회
    many += [run(f"2026-12-02T10:{i % 60:02d}", 1, 0.01, verdict="failed") for i in range(100)]  # 재실행·실패
    many += [run(f"2026-12-03T10:{i % 60:02d}", None, 0.01, mode="digest", verdict="digest") for i in range(100)]
    lines = rp_text.top_ticket_lines(load(rp_fetch, {"INNO-1": ticket(title="마" * 25, runs=many)}), sp)
    assert lines[2] == "        ◦ 첫 검수 100회, 반려 cycle 재검수 100회, 재실행·실패 100회, 외 1종"
    assert_line_length("\n".join(lines))


def test_summary_extra_has_team_totals_only(rp_run, sample):
    summ = Summ()
    body = "\n".join(report(rp_run, sample, summ=summ))
    extra = "\n".join(summ.extra)
    assert "김담당" in body and "이담당" in body
    for person in (KIM, LEE, PARK):
        assert person["displayName"] not in extra
        assert person["accountId"] not in extra
    assert "후보(" not in extra
    assert "구두 피드백 대상 후보는 팀장 채널 메시지에만 적음" in extra
    assert "- Claude 사용량: 실행 7건, API 환산 비용 5.80 USD" in extra
    assert "- 기존 ticket: 10개(업데이트 7개, 그중 done 전환 2개)" in extra
    assert "- 업데이트 없는 기존 ticket: 3개(In Progress 2, ready-to-done 1)" in extra
    assert "- 자동 검수 소요 시간: 평균 1740초(600–2700초, 5건)" in extra
    assert "- 사용량 많음 판정: 해당함(이번 달 5.80 USD ≥ 전월 × 1.5(4.80 USD); 전월 검수 실행 10건 ≥ 10건)" in extra
    # Claude 사용 ticket Top3(ticket 키, 제목, 담당자)는 Slack 본문에만 적음
    assert "Top3" in body and "Top3" not in extra
    assert "PoseNet" not in extra and "INNO-102" not in extra
    assert "- 최대 검수 반복 횟수: 3회(INNO-101)" in extra
    assert "- Claude 토큰: input 700, output 350, cache 쓰기 70, cache 읽기 7,000" in extra
    assert "- 분기 판정 일치율: 5/6건, 83.3%" in extra


def test_search_fields_replace_issue_calls(rp_run, sample):
    """ticket 필드는 검색 결과로 받고 ticket마다 Jira.issue를 부르지 않음(FakeJira에 issue 없음)."""
    fake = FakeJira(sample)
    rp_run.run_report(fake, "2026-12", LEAD_ID, Summ())
    want = {"assignee", "parent", "issuetype", "description", "status", "summary", "created"}
    assert set(fake.searches[0][1]) >= want


def test_sample_body(rp_run, sample):
    """샘플 메시지(월 2026-12). `pytest -s`로 실행하면 출력됨."""
    msgs = report(rp_run, sample)
    body = "\n\n".join(msgs)
    print(body)
    assert "~" not in body
    assert "<@" not in body  # Slack 멘션 없음
    for msg in msgs:
        assert len(msg.splitlines()) <= 40
        assert_line_length(msg)
