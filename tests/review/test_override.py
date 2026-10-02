from __future__ import annotations

import argparse

import override
import pytest


def ev(event: str, label: str, who: str) -> dict:
    return {"event": event, "label": {"name": label}, "actor": {"login": who}}


def test_parse_reviewers():
    assert override.parse_reviewers("Kim, @lee  park,") == {"kim", "lee", "park"}
    assert override.parse_reviewers("") == set()


def test_last_labeler():
    events = [
        ev("labeled", "review/override", "author"),
        ev("unlabeled", "review/override", "bot"),
        ev("labeled", "bug", "kim"),
        ev("labeled", "review/override", "Kim"),
        {"event": "commented"},
    ]
    assert override.last_labeler(events) == "Kim"
    assert override.last_labeler(events[:2]) is None  # 마지막이 뗀 것


def test_valid_override():
    events = [ev("labeled", "review/override", "Kim")]
    assert override.valid_override(["review/override"], events, {"kim"}) == "Kim"
    assert override.valid_override(["review/override"], events, {"lee"}) is None  # 지정 리뷰어가 아님
    assert override.valid_override(["review/override"], events, set()) is None  # 목록이 비면 아무도 못 한다
    assert override.valid_override(["review/pass"], events, {"kim"}) is None  # 라벨이 없음


def test_handle_label_from_non_reviewer_posts_notice(monkeypatch: pytest.MonkeyPatch):
    calls: list[list[str]] = []
    removed: list[tuple[str, int]] = []
    monkeypatch.setattr(override, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(override, "remove_label", lambda slug, pr: removed.append((slug, pr)))
    a = argparse.Namespace(
        label="review/override", reviewers="kim, lee", sender="park", repo_slug="o/r", pr=7, head_sha="abc"
    )
    override.cmd_handle_label(a)
    assert removed == [("o/r", 7)]
    body = calls[-1][calls[-1].index("--body") + 1]
    assert body == (
        "@park 님이 붙인 review/override 라벨을 everex-review가 뗌. "
        "지정 리뷰어만 override(라벨로 검수 판정을 통과로 바꾸는 것)를 할 수 있음(지정 리뷰어: @kim, @lee). "
        "판정에 이의가 있으면 검수 comment에 근거를 남기고 지정 리뷰어에게 요청 필요."
    )
    a.reviewers = ""
    override.cmd_handle_label(a)
    assert "(지정 리뷰어: 없음)" in calls[-1][calls[-1].index("--body") + 1]
