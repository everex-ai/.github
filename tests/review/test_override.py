from __future__ import annotations

import override


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
