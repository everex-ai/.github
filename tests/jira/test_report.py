"""scripts/report.py(정기 보고 메시지)의 계산과 Slack 본문.

검수 comment와 문서화 리뷰 comment는 scripts/apply.py의 review_comment, doc_review_comment, agent_comment로 만들어
실제 형식과 같게 한다. Jira는 읽기 메서드(search, issue, changelog, comments, prop_get)만 가진 가짜 객체로 대신하고
실제 API를 호출하지 않는다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
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


def _load(name: str, path: Path):
    """스크립트를 모듈 이름 name으로 불러오고, 스크립트가 바꾼 sys.path를 되돌린다(jira_metrics.load_module과 같음)."""
    if name in sys.modules:
        return sys.modules[name]
    saved = list(sys.path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        sys.path[:] = saved
    return mod


@pytest.fixture(scope="module")
def ja():
    """scripts/apply.py (모듈 이름 jira_apply)."""
    return _load("jira_apply", ROOT / "scripts" / "apply.py")


@pytest.fixture(scope="module")
def rp(ja):
    """scripts/report.py (모듈 이름 jira_report). apply.py가 불러온 jira_api를 함께 쓰게 apply.py를 먼저 불러온다."""
    return _load("jira_report", ROOT / "scripts" / "report.py")


@pytest.fixture(autouse=True)
def project(monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_KEY", "INNO")


def ts(when: str) -> str:
    """'YYYY-MM-DDTHH:MM'(한국 시간)을 Jira 시각 문자열로 바꾼다."""
    return f"{when}:00.000+0900"


def change(when: str, src: str, dst: str, who: dict = LEAD) -> dict:
    return {"created": ts(when), "author": who, "items": [{"field": "status", "fromString": src, "toString": dst}]}


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


def ticket(itype="Task", who=None, parent=None, desc="", changelog=(), comments=(), runs=()) -> dict:
    fields = {"issuetype": {"name": itype}, "assignee": who, "parent": parent, "description": desc}
    return {"fields": fields, "changelog": list(changelog), "comments": list(comments), "runs": list(runs)}


class FakeJira:
    """report.py가 부르는 Jira 읽기 메서드만 흉내 낸다. 쓰기 메서드와 issue를 부르면 AttributeError가 난다."""

    def __init__(self, tickets: dict[str, dict]):
        self.tickets = tickets
        self.jql: list[str] = []

    def search(self, jql: str, fields: list[str], limit: int = 500) -> list[dict]:
        """검색 결과에 요청한 필드만 담는다. ticket마다 Jira.issue를 부르지 않는지 확인하려고 issue는 두지 않는다."""
        self.jql.append(jql)
        self.fields = fields
        return [{"key": k, "fields": {f: t["fields"].get(f) for f in fields}} for k, t in self.tickets.items()]

    def changelog(self, key: str) -> list[dict]:
        return self.tickets[key]["changelog"]

    def comments(self, key: str) -> list[dict]:
        return self.tickets[key]["comments"]

    def prop_get(self, key: str) -> dict:
        return {"lastVerdict": "pass", "runs": self.tickets[key]["runs"]}


class Summ:
    """scripts/apply.py Summary의 extra와 row만 흉내 낸다."""

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


def load(rp, tickets: dict[str, dict]) -> list:
    return rp.collect(FakeJira(tickets), "jql")


def month(rp, m: str):
    return rp.months_span(m, m)


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
    """
    return {
        "INNO-101": ticket(
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
            itype="Issue",
            who=PARK,
            runs=[run(f"2026-11-{d:02d}T10:00", d - 1, 0.15) for d in range(2, 10)],
        ),
        "INNO-108": ticket(
            who=PARK,
            changelog=[
                change("2026-10-20T09:00", "In Progress", "ready-to-done", PARK),
                change("2026-10-20T09:05", "ready-to-done", "In Progress"),  # 자동 보류 전환(comment 전)
            ],
            comments=[review(ja, "2026-10-20T09:06", "fix", checks(T1="fail"), HOLD_NOTE)],
            runs=[run("2026-10-20T09:06", 1, 0.7, verdict="fix")],
        ),
    }


def test_comment_format_matches_apply(rp, ja):
    """report.py가 따로 둔 문구가 apply.py와 같고, apply.py가 만든 comment를 읽음."""
    assert rp.VERDICT_KO == {k: v for k, v in ja.VERDICT_KO.items() if k is not None}
    assert rp.RESULT_KO == ja.RESULT_KO
    head = ja.review_comment("pass", [], [], [], [], "", [], []).splitlines()[0]
    assert head == f"{rp.VERDICT_PREFIX}{ja.VERDICT_KO['pass']}{rp.VERDICT_SUFFIX}"
    body = review(ja, "2026-12-01T09:00", "fix", checks(T2="fail", R4="pass", A2="n/a"))["body"]
    assert rp.parse_review(body) == ("보류", {"T2": "fail", "R4": "pass", "A2": "n/a"})
    assert rp.parse_doc_review(body) is None
    doc_body = doc(ja, "2026-12-01T09:00", checks(T4="fail", T5="pass"))["body"]
    assert rp.parse_review(doc_body) is None
    assert rp.parse_doc_review(doc_body) == {"T4": "fail", "T5": "pass"}
    assert rp.table_rows(doc_body)[0] == ("T4", ja.check_label("T4"), "fail")
    line = ja.deliverables_summary([{"id": 1}, {"id": 2}], [{"id": 1, "done": True}], [{"result": "x"}])
    assert rp.DELIVERABLES_RE.search(line).groups() == ("2", "1")


def test_run_branch(rp):
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
    assert [rp.run_branch(e) for e, _ in cases] == [b for _, b in cases]


def test_usage_sums_by_kst_month(rp):
    tickets = load(
        rp,
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
    u = rp.usage(tickets, month(rp, "2026-12"))
    assert dict(u.runs) == {"cycle": 1, "digest": 1, "retry": 1}
    assert u.cost == {"first": 0.0, "cycle": 1.0, "retry": 0.5, "digest": 0.0}
    assert (u.review_runs, u.missing_cost, u.total_runs) == (2, 1, 3)
    assert u.tokens == {"input": 300, "output": 150, "cacheWrite": 30, "cacheRead": 3000}
    assert rp.usage(tickets, month(rp, "2026-11")).cost["first"] == 2.0


def test_usage_judgement_boundaries(rp):
    def u(cost: float, reviews: int):
        x = rp.Usage()
        x.cost["first"], x.review_runs = cost, reviews
        return x

    assert rp.usage_judgement(u(15.0, 0), u(10.0, 10)) == (
        True,
        "해당함",
        ["이번 달 15.00 USD ≥ 전월 × 1.5(15.00 USD)", "전월 검수 실행 10건 ≥ 10건"],
    )
    assert rp.usage_judgement(u(14.99, 0), u(10.0, 10))[:2] == (False, "해당하지 않음")
    assert rp.usage_judgement(u(14.99, 0), u(10.0, 10))[2][0] == "이번 달 14.99 USD < 전월 × 1.5(15.00 USD)"
    assert rp.usage_judgement(u(0.3, 0), u(0.2, 10))[0] is True  # 0.2 × 1.5의 부동소수 오차에도 경계 포함
    assert rp.usage_judgement(u(100.0, 0), u(10.0, 9)) == (None, "판정 불가", ["전월 검수 실행 9건 < 10건"])
    assert rp.usage_judgement(u(1.0, 0), u(0.0, 12)) == (None, "판정 불가", ["전월 API 환산 비용 기록 없음"])


def test_rerun_of_same_request_is_retry(rp):
    """같은 ticket에서 requestNo가 같고 반영에 성공한 review 실행이 앞서 있으면 그 뒤 실행은 재실행·실패 갈래.

    앞선 실행이 반영에 실패했으면 첫 성공 실행은 원래 갈래로 둔다.
    """
    tickets = load(
        rp,
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


def test_candidates_conditions_a_b(rp, ja):
    tickets = load(
        rp,
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
    cands = rp.candidates(tickets, month(rp, "2026-12"))
    assert set(cands) == {"acc-kim", "acc-lee"}  # 담당자가 없는 INNO-5는 세지 않음
    assert (cands["acc-kim"].name, cands["acc-kim"].rejected, cands["acc-kim"].failed) == (
        "김담당",
        [("INNO-1", 2)],
        [],
    )
    assert cands["acc-lee"].rejected == []
    assert cands["acc-lee"].failed == [("T2", 2), ("T4", 2)]


def test_followup_four_results(rp, ja):
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
        rp,
        {
            **person(a, [review(ja, "2026-12-03T10:00", "fix", checks(T2="fail"))]),  # 재발
            **person(b, [review(ja, "2026-12-03T10:00", "pass", checks(T2="pass"))]),  # 개선: 0/1 -> 1/1
            **person(c, [review(ja, "2026-12-03T10:00", "fix", checks(T2="pass", R3="fail"))]),  # 유지: 0/1 -> 0/1
            **person(d, []),  # 판단 불가: 12월 검수 없음
        },
    )
    dec, nov = month(rp, "2026-12"), month(rp, "2026-11")
    prev = rp.candidates(tickets, nov)
    assert set(prev) == {"acc-a", "acc-b", "acc-c", "acc-d"}
    result = {name: (res, detail) for name, res, detail in rp.followup(prev, tickets, dec, nov)}
    assert result["담당a"] == ("재발", ["이번 달 T2 ❌ 1회"])
    assert result["담당b"] == ("개선", ["지난달 첫 검수 통과율 0/1건, 0.0%", "이번 달 첫 검수 통과율 1/1건, 100.0%"])
    assert result["담당c"] == ("유지", ["지난달 첫 검수 통과율 0/1건, 0.0%", "이번 달 첫 검수 통과율 0/1건, 0.0%"])
    assert result["담당d"] == ("판단 불가", ["이번 달 검수 comment 0건"])


def test_judge_cells_and_exclusions(rp, ja):
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
    tickets = {t.key: t for t in load(rp, specs)}
    got = {k: rp.judge(t.reviews, t.changes, LEAD_ID) for k, t in tickets.items()}
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
    a = rp.agreement_summary(rp.agreement_cases(tickets.values(), LEAD_ID), rp.months_span("2026-10", "2026-12"))
    assert (a["match"], a["judged"]) == (3, 5)
    assert (a["escalate"], a["total"]) == (2, 12)
    assert a["excluded"] == {"자동 보류": 1, "팀장 외 전환": 1, "전환 없음": 2, "기타 전환": 1}
    no_lead = rp.judge(tickets["P-done"].reviews, tickets["P-done"].changes, "")
    assert no_lead == ["팀장 외 전환"]


def test_recent_20(rp):
    dt = rp.dt
    base = dt.datetime(2026, 10, 1, tzinfo=rp.KST)
    cases = [(base + dt.timedelta(days=i), "불일치") for i in range(2)]
    cases += [(base + dt.timedelta(days=2 + i), "일치") for i in range(20)]
    cases += [(base + dt.timedelta(days=30), "검토 요청"), (dt.datetime(2027, 1, 2, tzinfo=rp.KST), "불일치")]
    a = rp.agreement_summary(sorted(cases), rp.months_span("2026-10", "2026-12"))
    assert (a["match"], a["judged"]) == (20, 22)  # 분기값
    assert (a["recent_match"], a["recent_n"]) == (20, 20)  # 분기 끝 전 최근 20건. 2027-01 건과 검토 요청은 뺌
    assert (a["escalate"], a["total"]) == (1, 23)


def test_kr_rows(rp):
    def task(parent, done_at: str | None, desc: str = "", itype: str = "Task") -> dict:
        log = [change(done_at, "ready-to-done", "Done")] if done_at else []
        return ticket(itype=itype, who=KIM, parent=parent, desc=desc, changelog=log)

    tickets = load(
        rp,
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
    rows = rp.kr_rows(tickets, rp.months_span("2026-10", "2026-12"))
    got = [(r.key, r.name, r.done, r.achieved, r.expected, r.no_line) for r in rows]
    assert got == [
        ("INNO-23", "문서화 품질 개선", 3, 4, 5, 1),
        ("INNO-33", "검수 자동화", 1, 0, 1, 0),
        ("", "상위 KR 없음", 1, 1, 1, 0),
    ]


@pytest.mark.parametrize("m", range(1, 13))
def test_bundles_by_month_and_empty_data(rp, m):
    fake, summ = FakeJira({}), Summ()
    msgs = rp.run_report(fake, f"2026-{m:02d}", LEAD_ID, summ)
    body = "\n".join(msgs)
    assert len(msgs) == (2 if m == 12 else 1)
    assert body.splitlines()[0] == f"[2026-{m:02d} jira-doc 정기 보고]"
    assert "*월간*" in body
    assert "기록 없음임" not in body
    assert ("*분기(" in body) == (m % 3 == 0)
    if m % 3 == 0:
        assert f"*분기(2026 Q{m // 3})*" in body
    assert ("*연간(2026)*" in body) == (m == 12)
    assert "• 이번 달 실행 기록 없음" in body
    assert "• 기록 없음\n    ◦ 이 기간 반려 0회, 검수 comment 0건" in body
    assert "• 사용량 많음 판정: 판정 불가\n    ◦ 전월 검수 실행 0건 < 10건" in body
    assert "• 피드백 대기 시간은 기록 없음" in body
    assert_line_length(body)
    since = {1: "2025-11-30", 3: "2025-12-31", 6: "2026-03-31", 9: "2026-06-30", 11: "2026-09-30", 12: "2025-12-31"}
    if m in since:
        assert fake.jql == [f'project = INNO AND issuetype in (Task, Bug, Issue) AND updated >= "{since[m]}"']
    assert summ.extra and summ.rows == []


def test_monthly_team_metrics_and_usage(rp, sample):
    body = "\n".join(rp.run_report(FakeJira(sample), "2026-12", LEAD_ID, Summ()))
    assert block(body, "• done ticket 수는 2개임", 1) == ["    ◦ 기준: 이 기간에 done으로 전환된 ticket 수"]
    assert "• 첫 검수 통과율은 1/3건, 33.3%임" in body
    assert block(body, "• 검사 ID별 ❌(보완 필요) 상위 3개는 아래와 같음", 5) == [
        "    ◦ 대상: 검수 comment 5건, 문서화 리뷰 comment 2건",
        "    ◦ 값: 검사 표의 ❌ 행 수",
        "    1. stop 사유 comment (R4): ❌ 2회",
        "    2. 진행 배경 작성 (T2): ❌ 2회",
        "    3. 진행 배경 충실도 (T4): ❌ 2회",
    ]
    assert "• stop 사유 기록률은 2/4건, 50.0%임" in body
    assert block(body, "• 피드백 대기 시간은 중앙값 30분임", 4) == [
        "    ◦ 표본: request 전환 5건",
        "    ◦ request 전환: 담당자가 완료를 요청하는 Jira 전환",
        "    ◦ 범위: 10–45분",
        "    ◦ 측정: request 전환부터 다음 첫 검수 comment까지",
    ]
    # INNO-102의 12-05 09:10 실행은 같은 request 전환 1의 앞선 실행이 반영 실패라 첫 검수로 남는다
    assert block(body, "• API 환산 비용 합계는 5.80 USD임", 2) == ["    ◦ 실행 7건", "    ◦ 전월: 실행 10건, 3.20 USD"]
    assert block(body, "• 사용량 많음 판정: 해당함", 2) == [
        "    ◦ 이번 달 5.80 USD ≥ 전월 × 1.5(4.80 USD)",
        "    ◦ 전월 검수 실행 10건 ≥ 10건",
    ]
    assert "    ◦ 첫 검수: 2건, 1.90 USD(전월 2건, 1.15 USD)" in body
    assert "    ◦ 재실행·실패: 2건, 1.40 USD(전월 0건, 0.00 USD)" in body
    assert block(body, "• 이번 달 토큰 수는 아래와 같음", 4) == [
        "    ◦ input: 700",
        "    ◦ output: 350",
        "    ◦ cache 쓰기: 70",
        "    ◦ cache 읽기: 7,000",
    ]
    assert block(body, "• 전월보다 API 환산 비용이 늘어난 갈래는 아래와 같음", 9) == [
        "    ◦ 재실행·실패: +1.40 USD, 실행 +2건",
        "    ◦ 첫 검수: +0.75 USD, 실행 +0건",
        "    ◦ 정리 모드: +0.50 USD, 실행 +1건",
        "• 추정: 재실행·실패 증가는 CI agent 쪽 문제 때문임",
        "• 추정: 첫 검수 증가는 done 전환 ticket 증가 때문임",
        "• 추정: 정리 모드 증가는 F3 요청 증가 때문임",
        "• 참고: done ticket 2개(전월 1개)",
        "• 참고: 반려 3회(전월 1회)",
        "    ◦ 반려: ready-to-done에서 In Progress로 돌아간 전환",
    ]
    assert body.count("◦ 반려: ready-to-done") == 1  # 처음 나올 때만 풀어 적음
    assert block(body, "• 김담당", 2) == ["    ◦ 조건 A: INNO-101 반려 2회", "    ◦ 조건 B: 진행 배경 작성 (T2) ❌ 2회"]
    assert "이담당" not in body.split("지난달 후보의 개선 여부")[0]
    assert block(body, "• 이담당: 개선", 3) == [
        "    ◦ 기준: 재발이 없고 첫 검수 통과율이 지난달보다 높음",
        "    ◦ 지난달 첫 검수 통과율 0/1건, 0.0%",
        "    ◦ 이번 달 첫 검수 통과율 1/1건, 100.0%",
    ]


def test_quarterly_and_yearly_bundles(rp, sample):
    body = "\n".join(rp.run_report(FakeJira(sample), "2026-12", LEAD_ID, Summ()))
    quarter = body.split("*분기(2026 Q4)*", 1)[1].split("*연간(2026)*", 1)[0]
    assert block(quarter, "• INNO-23 문서화 품질 개선", 2) == [
        "    ◦ done 2개",
        "    ◦ 결과 산출물 달성률: 4/5개, 80.0%",
    ]
    assert block(quarter, "• INNO-33 검수 자동화", 2) == ["    ◦ done 2개", "    ◦ 결과 산출물 달성률: 4/6개, 66.7%"]
    assert "    1. 진행 배경 작성 (T2): ❌ 3회" in quarter
    assert "    5. 원인과 해결의 근거 링크 (B3): ❌ 1회" in quarter  # ❌ 1회끼리는 검사 ID 순
    assert "• 분기값은 5/6건, 83.3%임" in quarter
    assert "• 최근 20건 값은 5/6건, 83.3%임" in quarter
    assert "• 검토 요청 비율은 1/9건, 11.1%임" in quarter
    assert block(quarter, "• 일치율에서 뺀 검수 comment는 아래와 같음", 9) == [
        "    ◦ 검토 요청: 1건",
        "    ◦ 자동 보류: 1건",
        "        ◦ 뜻: 자동 보류 전환을 없애기 전의 보류 판정",
        "    ◦ 팀장 외 전환: 1건",
        "        ◦ 뜻: 팀장이 아닌 계정이 한 첫 전환",
        "    ◦ 전환 없음: 0건",
        "        ◦ 뜻: 다음 검수 comment 전까지 전환이 없음",
        "    ◦ 기타 전환: 0건",
        "        ◦ 뜻: done, In Progress가 아닌 상태로 간 전환",
    ]
    year = body.split("*연간(2026)*", 1)[1]
    assert block(year, "• 첫 실행 기록은 2026-10-14임", 1) == ["    ◦ 그 전 달은 기록 없음으로 적음"]
    assert "• 2026-09: 기록 없음\n• 2026-10: 1.70 USD, 실행 2건" in year
    assert block(year, "• 2026-12: 5.80 USD, 실행 7건", 4) == [
        "    ◦ 첫 검수 1.90 USD",
        "    ◦ 반려 cycle 재검수 2.00 USD",
        "    ◦ 재실행·실패 1.40 USD",
        "    ◦ 정리 모드 0.50 USD",
    ]
    assert "• 2026-10: 0/2건, 0.0%" in year
    assert "• 2026-12: 1/3건, 33.3%" in year
    assert "• 2026-05: 기록 없음" in year
    assert "• 진행 배경 작성 (T2): 4/7건, 57.1%" in year


def test_yearly_check_rates_monthly_for_top5(rp, sample):
    """연간 만족률은 검사 ID마다 한 줄, 월별 값은 연간 ❌ 횟수 상위 5개 검사 ID에만 붙음."""
    body = "\n".join(rp.run_report(FakeJira(sample), "2026-12", LEAD_ID, Summ()))
    rates = body.split("\n검사 ID별 만족률\n", 1)[1]
    assert block(rates, "• 해당 없음인 행은 뺌", 2) == [
        "• 월별 값은 연간 ❌ 횟수 상위 5개 검사 ID에만 적음",
        "    ◦ 같은 횟수면 검사 ID 순",
    ]
    # 연간 ❌: T2 3회, R3·R4·T4 2회, B3·T1·T6 1회 -> 상위 5개는 T2, R3, R4, T4, B3(1회끼리는 검사 ID 순)
    heads = {
        "T2": "• 진행 배경 작성 (T2): 4/7건, 57.1%",
        "R3": "• 개조식 작성 (R3): 0/2건, 0.0%",
        "R4": "• stop 사유 comment (R4): 2/4건, 50.0%",
        "T4": "• 진행 배경 충실도 (T4): 0/2건, 0.0%",
        "B3": "• 원인과 해결의 근거 링크 (B3): 0/1건, 0.0%",
    }
    for head in heads.values():
        sub = block(rates, head, 13)
        assert sub[0].startswith("    ◦ 연간 ❌ ")
        assert [x.split(":")[0] for x in sub[1:]] == [f"    ◦ 2026-{m:02d}" for m in range(1, 13)]
    t2 = block(rates, heads["T2"], 13)
    assert t2[0] == "    ◦ 연간 ❌ 3회"
    assert t2[1] == "    ◦ 2026-01: 기록 없음"
    assert t2[10:] == ["    ◦ 2026-10: 0/1건, 0.0%", "    ◦ 2026-11: 2/2건, 100.0%", "    ◦ 2026-12: 2/4건, 50.0%"]
    for other in ("• 예상 산출물 작성 (T1): 1/2건, 50.0%", "• 진행 기록 comment (T6): 0/1건, 0.0%"):
        assert not block(rates, other, 1) or not block(rates, other, 1)[0].startswith("    ◦")
    assert rates.count("◦ 연간 ❌") == 5
    assert_line_length(body)


def test_summary_extra_has_team_totals_only(rp, sample):
    summ = Summ()
    body = "\n".join(rp.run_report(FakeJira(sample), "2026-12", LEAD_ID, summ))
    extra = "\n".join(summ.extra)
    assert "김담당" in body and "이담당" in body
    for person in (KIM, LEE, PARK):
        assert person["displayName"] not in extra
        assert person["accountId"] not in extra
    assert "구두 피드백 대상 후보는 팀장 채널 메시지에만 적음" in extra
    assert "- Claude 사용량: 실행 7건, API 환산 비용 5.80 USD" in extra
    assert "- 분기 판정 일치율: 5/6건, 83.3%" in extra


def test_search_fields_replace_issue_calls(rp, sample):
    """ticket 필드는 검색 결과로 받고 ticket마다 Jira.issue를 부르지 않음(FakeJira에 issue 없음)."""
    fake = FakeJira(sample)
    rp.run_report(fake, "2026-12", LEAD_ID, Summ())
    assert set(fake.fields) >= {"assignee", "parent", "issuetype", "description", "status", "summary"}


def test_sample_body(rp, sample):
    """샘플 본문(월 2026-12). `pytest -s`로 실행하면 출력됨."""
    body = "\n".join(rp.run_report(FakeJira(sample), "2026-12", LEAD_ID, Summ()))
    print(body)
    assert "~" not in body
    assert "<@" not in body  # Slack 멘션 없음
    assert_line_length(body)
