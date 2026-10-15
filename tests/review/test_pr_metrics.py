from __future__ import annotations

from datetime import date

import apply
import pr_metrics
import pytest
from common import COMMENT_MARKER

CREATED = "2026-09-01T00:00:00Z"
MERGED = "2026-09-03T00:00:00Z"


def _pr(**kw) -> dict:
    base = {
        "number": 7,
        "user": {"login": "alice", "type": "User"},
        "created_at": CREATED,
        "merged_at": MERGED,
        "base": {"sha": "b" * 40},
        "head": {"sha": "h" * 40},
    }
    return {**base, **kw}


def _review(login: str, submitted_at: str | None, kind: str = "User") -> dict:
    return {"user": {"login": login, "type": kind}, "submitted_at": submitted_at, "state": "COMMENTED"}


def _label(event: str, name: str, at: str) -> dict:
    return {"event": event, "label": {"name": name}, "created_at": at}


def _comment(body: str, at: str) -> dict:
    return {"body": body, "created_at": at}


def test_author_review_is_not_human_review():
    reviews = [
        _review("alice", "2026-09-01T01:00:00Z"),  # 작성자 본인
        _review("github-actions[bot]", "2026-09-01T00:30:00Z", "Bot"),  # 수정 제안 review
        _review("bob", None),  # 제출 전(pending)
        _review("bob", "2026-09-01T03:00:00Z"),
        _review("carol", "2026-09-01T05:00:00Z"),
    ]
    row = pr_metrics.pr_row("r", _pr(), reviews, [], [])
    assert row["human_reviewed"] is True and row["human_review_after_merge"] is False
    assert row["first_human_review_hours"] == 3.0
    assert row["error"] == ""

    only_author = pr_metrics.pr_row("r", _pr(), [_review("alice", "2026-09-01T01:00:00Z")], [], [])
    assert only_author["human_reviewed"] is False
    assert only_author["first_human_review_hours"] is None


def test_review_after_merge_is_not_counted_for_p1():
    after = pr_metrics.pr_row("r", _pr(), [_review("bob", "2026-09-04T00:00:00Z")], [], [])
    assert after["human_reviewed"] is False and after["human_review_after_merge"] is True
    assert after["first_human_review_hours"] == 72.0  # P4는 merge 전후와 관계없이 계산

    at_merge = pr_metrics.pr_row("r", _pr(), [_review("bob", MERGED)], [], [])
    assert at_merge["human_reviewed"] is True and at_merge["human_review_after_merge"] is False


def test_agent_feedback_minutes_from_review_comment():
    comments = [
        _comment("LGTM", "2026-09-01T00:05:00Z"),
        _comment(f"{COMMENT_MARKER}\n## 코드 검수 결과: 반려", "2026-09-01T00:12:00Z"),
        _comment("본문 중간의 " + COMMENT_MARKER, "2026-09-01T00:01:00Z"),  # 첫 줄이 아니면 아님
    ]
    row = pr_metrics.pr_row("r", _pr(), [], comments, [])
    assert row["agent_reviewed"] is True and row["agent_comment_after_merge"] is False
    assert row["agent_feedback_minutes"] == 12.0
    assert row["base_sha"] == "b" * 40 and row["head_sha"] == "h" * 40

    none = pr_metrics.pr_row("r", _pr(), [], comments[:1], [])
    assert none["agent_reviewed"] is False and none["agent_feedback_minutes"] is None

    late = pr_metrics.pr_row("r", _pr(), [], [_comment(COMMENT_MARKER + "\n", "2026-09-03T01:00:00Z")], [])
    assert late["agent_reviewed"] is False and late["agent_comment_after_merge"] is True
    assert late["agent_feedback_minutes"] == 49 * 60.0


def test_reject_then_pass_before_merge_and_ignore_after_merge():
    timeline = [
        {"event": "committed", "created_at": None},
        _label("labeled", "review/reject", "2026-09-01T00:10:00Z"),
        _label("labeled", "enhancement", "2026-09-01T00:11:00Z"),
        _label("labeled", "review/pass", "2026-09-02T00:00:00Z"),
        _label("unlabeled", "review/reject", "2026-09-02T00:00:01Z"),
        _label("labeled", "review/reject", "2026-09-04T00:00:00Z"),  # merge 뒤
        _label("unlabeled", "review/pass", "2026-09-04T00:00:01Z"),  # merge 뒤
    ]
    row = pr_metrics.pr_row("r", _pr(), [], [], timeline)
    assert row["ever_rejected"] is True
    assert row["label_at_merge"] == "review/pass"

    after_only = [_label("labeled", "review/reject", "2026-09-04T00:00:00Z")]
    row2 = pr_metrics.pr_row("r", _pr(), [], [], after_only)
    assert row2["ever_rejected"] is False and row2["label_at_merge"] == ""


def test_override_detected_from_review_comment():
    previous = f"{COMMENT_MARKER}\n## 코드 검수 결과: 반려\n"
    body = apply.override_body(previous, "lead", "a" * 40)
    assert pr_metrics.pr_row("r", _pr(), [], [_comment(body, "2026-09-01T00:10:00Z")], [])["overridden"] is True
    assert pr_metrics.pr_row("r", _pr(), [], [_comment(previous, "2026-09-01T00:10:00Z")], [])["overridden"] is False
    assert pr_metrics.pr_row("r", _pr(), [], [], [])["overridden"] is False


@pytest.mark.parametrize(
    ("merged_at", "since", "until", "expected"),
    [
        ("2026-08-31T23:30:00Z", date(2026, 9, 1), date(2026, 9, 30), True),  # 한국 시간 9월 1일 08:30
        ("2026-08-31T23:30:00Z", None, date(2026, 8, 31), False),
        ("2026-08-31T14:59:59Z", None, date(2026, 8, 31), True),  # 한국 시간 8월 31일 23:59:59, until 포함
        ("2026-08-31T15:00:00Z", None, date(2026, 8, 31), False),  # 한국 시간 9월 1일 00:00
        ("2026-08-31T15:00:00Z", date(2026, 9, 1), None, True),  # since 포함
        ("2026-08-31T14:59:59Z", date(2026, 9, 1), None, False),
        ("2026-09-10T00:00:00Z", None, None, True),
        (None, None, None, False),  # merge되지 않은 PR
    ],
)
def test_merged_in_range_kst_boundaries(merged_at, since, until, expected):
    assert pr_metrics.merged_in_range(merged_at, since, until) is expected


def test_kst_month():
    assert pr_metrics.kst_month("2026-08-31T23:30:00Z") == "2026-09"
    assert pr_metrics.kst_month("2026-08-31T14:59:59Z") == "2026-08"
    assert pr_metrics.kst_month(None) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('[{"n": 1}, {"n": 2}]', [1, 2]),
        ('[{"n": 1}][{"n": 2}]\n', [1, 2]),
        ('[{"n": 1}]\n[{"n": 2}, {"n": 3}]\n', [1, 2, 3]),
        ("[]\n[]", []),
        ('{"n": 9}', [9]),
        ("", []),
    ],
)
def test_parse_json_stream(text, expected):
    assert [x["n"] for x in pr_metrics.parse_json_stream(text)] == expected


def _study(target: str, size: str, review: str, check: str, user: str, agent: str) -> dict:
    return {
        "kind": "pr",
        "target": target,
        "size": size,
        "review_minutes": review,
        "issues_found": "1",
        "user_verdict": user,
        "check_minutes": check,
        "agent_verdict": agent,
    }


def test_monthly_saved_minutes():
    study = [
        _study("r#1", "100", "20", "2", "반려", "반려"),
        _study("r#2", "300", "60", "4", "통과", "반려"),
        _study("r#3", "100", "20", "9", "반려", "통과"),
    ]
    rate = pr_metrics.review_rate(study)
    assert rate["rows"] == 3 and rate["review_minutes"] == 100 and rate["size"] == 500
    assert rate["per_line"] == pytest.approx(0.2)
    check_median = pr_metrics.statistics.median(pr_metrics.check_minutes_values(study))
    assert check_median == 4

    rows = [
        {"repo": "r", "number": 1, "merged_at": "2026-08-10T00:00:00Z", "agent_reviewed": True},
        {"repo": "r", "number": 2, "merged_at": "2026-08-20T00:00:00Z", "agent_reviewed": True},
        {"repo": "r", "number": 5, "merged_at": "2026-08-21T00:00:00Z", "agent_reviewed": False},  # Agent 리뷰 없음
        {"repo": "r", "number": 8, "merged_at": "2026-08-31T23:30:00Z", "agent_reviewed": True},  # 한국 시간 9월
        {"repo": "r", "number": 3, "merged_at": "2026-09-01T00:00:00Z", "agent_reviewed": True},
        {"repo": "r", "number": 4, "merged_at": "2026-09-02T00:00:00Z", "agent_reviewed": True},  # 소급 실행 오류
        {"repo": "r", "number": 6, "merged_at": "2026-09-03T00:00:00Z", "agent_reviewed": False},  # 소급 판정 행 없음
        {"repo": "r", "number": 7, "merged_at": "2026-10-01T00:00:00Z", "agent_reviewed": False},
    ]
    backfill = {
        ("r", 1): {"py_lines": "100", "error": ""},
        ("r", 2): {"py_lines": "50", "error": ""},
        ("r", 3): {"py_lines": "200", "error": ""},
        ("r", 4): {"py_lines": "", "error": "RuntimeError: collect.py 실패"},
        ("r", 5): {"py_lines": "999", "error": ""},
        ("r", 8): {"py_lines": "10", "error": ""},
    }
    py_lines = pr_metrics.py_lines_map(backfill)
    assert py_lines == {("r", 1): 100, ("r", 2): 50, ("r", 3): 200, ("r", 5): 999, ("r", 8): 10}
    months = pr_metrics.monthly_saved_minutes(rows, py_lines, rate["per_line"], check_median)
    assert [m["month"] for m in months] == ["2026-08", "2026-09", "2026-10"]
    aug, sep, oct_ = months
    assert (aug["prs"], aug["py_lines"], aug["missing"]) == (2, 150, 0)  # Agent 리뷰가 없는 #5는 세지 않음
    assert aug["saved_minutes"] == pytest.approx(150 * 0.2 - 2 * 4)
    assert (sep["prs"], sep["py_lines"], sep["missing"]) == (2, 210, 1)  # #4만 missing, #6은 Agent 리뷰 없음
    assert sep["saved_minutes"] == pytest.approx(210 * 0.2 - 2 * 4)
    assert (oct_["prs"], oct_["py_lines"], oct_["missing"], oct_["saved_minutes"]) == (0, 0, 0, 0)


def _bf(lint: int = 0, fmt: int = 0, no_cand: int = 0, dead: int = 0, error: str = "") -> dict:
    return {
        "lint_violations": str(lint),
        "format_violations": str(fmt),
        "targets_without_candidates": str(no_cand),
        "dead_code_candidates": str(dead),
        "error": error,
    }


def test_merge_quality():
    rows = [
        {"repo": "r", "number": 1, "agent_reviewed": True},
        {"repo": "r", "number": 2, "agent_reviewed": True},
        {"repo": "r", "number": 3, "agent_reviewed": False},
        {"repo": "r", "number": 4, "agent_reviewed": False},
        {"repo": "r", "number": 5, "agent_reviewed": False},
        {"repo": "r", "number": 6, "agent_reviewed": True},  # 소급 실행 오류
        {"repo": "r", "number": 7, "agent_reviewed": False},  # 소급 판정 행 없음
    ]
    backfill = {
        ("r", 1): _bf(),  # 위반 없음
        ("r", 2): _bf(fmt=1, dead=2),  # (a), (c)
        ("r", 3): _bf(lint=3, no_cand=1),  # (a), (b)
        ("r", 4): _bf(no_cand=2),  # (b)
        ("r", 5): {**_bf(), "targets_without_candidates": ""},  # 빈 칸은 0
        ("r", 6): _bf(lint=5, error="RuntimeError: x"),  # error가 있으면 위반 수와 관계없이 뺌
    }
    q = pr_metrics.merge_quality(rows, backfill)
    assert q["all"] == {"n": 5, "violated": 3, "lint": 2, "no_candidates": 2, "dead_code": 1}
    assert q["with_agent"] == {"n": 2, "violated": 1, "lint": 1, "no_candidates": 0, "dead_code": 1}
    assert q["without_agent"] == {"n": 3, "violated": 2, "lint": 1, "no_candidates": 2, "dead_code": 0}
    assert (q["errors"], q["no_backfill"]) == (1, 1)

    empty = pr_metrics.merge_quality(rows[:1], {})
    assert empty["all"]["n"] == 0 and empty["no_backfill"] == 1


def test_verdict_agreement_and_zero_denominator():
    study = [
        _study("r#1", "10", "5", "1", "반려", "반려"),
        _study("r#2", "10", "5", "1", "통과", "반려"),
        _study("r#3", "10", "5", "1", "반려", "통과"),
        _study("r#4", "10", "5", "1", "반려", "통과"),
        _study("r#5", "10", "5", "1", "", "반려"),  # user_verdict 없음
        _study("r#6", "10", "5", "1", "반려", " "),  # agent_verdict 없음
    ]
    v = pr_metrics.verdict_agreement(study)
    assert (v["n"], v["excluded"], v["both"], v["agent_reject"], v["user_reject"]) == (4, 2, 1, 2, 3)
    assert v["precision"] == pytest.approx(1 / 2)
    assert v["recall"] == pytest.approx(1 / 3)

    no_reject = [_study("r#1", "10", "5", "1", "통과", "통과")]
    z = pr_metrics.verdict_agreement(no_reject)
    assert z["precision"] is None and z["recall"] is None
    assert pr_metrics.ratio_text(z["both"], z["agent_reject"]) == "계산 불가 (0/0)"
    assert pr_metrics.review_rate([_study("r#1", "0", "5", "1", "통과", "통과")])["per_line"] is None

    lines = pr_metrics._p6_lines(study)
    assert "- 표본: 표본 실측 기록표의 pr 행 중 user_verdict와 agent_verdict가 모두 있는 4개" in lines
    assert "  - 둘 중 하나라도 빈 pr 행 2개는 뺌" in lines


def test_render_summary_p1_p3_p4_override():
    override = apply.override_body(f"{COMMENT_MARKER}\n## 코드 검수 결과: 반려\n", "lead", "a" * 40)
    plain = f"{COMMENT_MARKER}\n## 코드 검수 결과: 반려\n"
    rows = [
        # 1: merge 전 사람 리뷰, merge 전 PR 검수 comment(override), 반려 뒤 통과
        pr_metrics.pr_row(
            "r",
            _pr(number=1),
            [_review("bob", "2026-09-01T02:00:00Z")],
            [_comment(override, "2026-09-01T00:10:00Z")],
            [
                _label("labeled", "review/reject", "2026-09-01T00:10:00Z"),
                _label("labeled", "review/pass", "2026-09-02T00:00:00Z"),
                _label("unlabeled", "review/reject", "2026-09-02T00:00:01Z"),
            ],
        ),
        # 2: 사람 리뷰와 PR 검수 comment가 모두 merge 뒤
        pr_metrics.pr_row(
            "r",
            _pr(number=2),
            [_review("bob", "2026-09-04T00:00:00Z")],
            [_comment(plain, "2026-09-04T00:00:00Z")],
            [],
        ),
        # 3: merge 전 PR 검수 comment, 반려 라벨인 채로 merge
        pr_metrics.pr_row(
            "r",
            _pr(number=3),
            [],
            [_comment(plain, "2026-09-01T00:05:00Z")],
            [_label("labeled", "review/reject", "2026-09-01T00:05:00Z")],
        ),
        pr_metrics.error_row("r", _pr(number=4), "gh api repos/o/r/issues/4/timeline 실패:\nHTTP 502"),
    ]
    assert rows[3]["error"] == "gh api repos/o/r/issues/4/timeline 실패: HTTP 502"
    assert rows[3]["head_sha"] == "h" * 40 and rows[3]["human_reviewed"] is None

    s = pr_metrics.render_summary(rows, "o의 repo 1개(r), merge 날짜 전체 기간", None, None)
    assert "merge된 PR 4개 중 집계한 PR 3개" in s
    assert "gh 조회가 실패해 집계에서 뺀 PR: 1개(prs.csv의 error 열)" in s
    assert "- 사람 리뷰를 받은 PR 비율: 33.3% (1/3)" in s
    assert "- Agent 리뷰를 받은 PR 비율: 66.7% (2/3)" in s
    assert "- 사람 리뷰와 Agent 리뷰 중 하나라도 받은 PR 비율: 66.7% (2/3)" in s
    assert "- merge 뒤에만 달려 세지 않은 PR: 사람 GitHub review 1개, PR 검수 comment 1개" in s
    assert "인 PR 비율: 50.0% (1/2)" in s.split("## P3")[1].split("## P4")[0]
    assert "중앙값 37.0시간, 범위 2.0–72.0시간, n=2" in s
    assert "중앙값 10.0분, 범위 5.0–4320.0분, n=3" in s
    assert "- override 비율: 50.0% (1/2)" in s
    assert "한국 시간(UTC+09:00) 기준" in s
    assert "~" not in s and "alice" not in s and "bob" not in s
