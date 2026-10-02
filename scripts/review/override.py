#!/usr/bin/env python3
"""review/override 라벨 처리. 지정 리뷰어가 agent 판정을 뒤집을 때 쓴다.

    override.py check        --repo-slug o/r --pr N --reviewers "a,b" --event-action A --run-attempt K
    override.py handle-label --repo-slug o/r --pr N --reviewers "a,b" --label L --sender S --head-sha SHA

- check (pr-review 워크플로 첫 스텝): 새 push(synchronize, 첫 시도)면 override 라벨을 뗀다. 새 코드는 새로 검수한다.
  그 외에는 라벨이 있고 마지막으로 붙인 사람이 지정 리뷰어면 GITHUB_OUTPUT에 active=true, login=<리뷰어>.
- handle-label (pr-review-override 워크플로): 라벨이 붙은 순간 처리한다. 권한 없는 사람이 붙였으면 떼고 안내 comment,
  지정 리뷰어면 apply.py --override 와 같은 반영을 하고, 게이트 모드의 check를 갱신하려고 최신 pr-review 실행을 다시 돌린다.
- 리뷰어 목록이 비어 있으면 아무도 override할 수 없다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import github_output, log, run  # noqa: E402

LABEL = "review/override"


def parse_reviewers(text: str) -> set[str]:
    """쉼표나 공백으로 구분한 GitHub 계정 목록. 대소문자와 앞의 @ 는 무시한다."""
    return {x.strip().lstrip("@").lower() for x in text.replace(",", " ").split() if x.strip().lstrip("@")}


def last_labeler(events: list[dict], label: str = LABEL) -> str | None:
    """issue events 에서 그 라벨을 마지막으로 붙인 사람. 마지막 이벤트가 뗀 것이면 None."""
    last = None
    for e in events:
        if (e.get("label") or {}).get("name") != label:
            continue
        if e.get("event") == "labeled":
            last = (e.get("actor") or {}).get("login")
        elif e.get("event") == "unlabeled":
            last = None
    return last


def valid_override(labels: list[str], events: list[dict], reviewers: set[str]) -> str | None:
    """라벨이 붙어 있고 마지막으로 붙인 사람이 지정 리뷰어면 그 계정, 아니면 None."""
    if LABEL not in labels:
        return None
    who = last_labeler(events)
    return who if who and who.lower() in reviewers else None


def gh(args: list[str]) -> object:
    """gh 명령을 실행하고 JSON 출력을 돌려준다. 실패하면 RuntimeError."""
    r = run(["gh", *args])
    if not r.ok:
        raise RuntimeError(f"gh {' '.join(args[:3])} 실패: {r.err.strip()[:300]}")
    return json.loads(r.out) if r.out.strip() else None


def pr_labels(slug: str, number: int) -> list[str]:
    """PR에 붙은 라벨 이름."""
    return [x["name"] for x in gh(["api", f"repos/{slug}/issues/{number}/labels", "--paginate"]) or []]


def pr_events(slug: str, number: int) -> list[dict]:
    """PR(issue)의 라벨 이벤트 기록."""
    return gh(["api", f"repos/{slug}/issues/{number}/events", "--paginate"]) or []


def remove_label(slug: str, number: int) -> None:
    """override 라벨을 뗀다. 없으면 아무 일도 없다."""
    run(["gh", "pr", "edit", str(number), "--repo", slug, "--remove-label", LABEL])


def cmd_check(a: argparse.Namespace) -> None:
    """pr-review 워크플로 첫 스텝."""
    github_output("active", "false")
    if a.event_action == "synchronize" and a.run_attempt == "1":
        if LABEL in pr_labels(a.repo_slug, a.pr):
            remove_label(a.repo_slug, a.pr)
            log("override", "새 push라 review/override 라벨을 뗌. 새 코드를 처음부터 검수한다")
        return
    who = valid_override(pr_labels(a.repo_slug, a.pr), pr_events(a.repo_slug, a.pr), parse_reviewers(a.reviewers))
    if who:
        github_output("active", "true")
        github_output("login", who)
        log("override", f"@{who} 의 override가 있어 검수를 건너뛰고 override를 반영한다")


def rerun_latest_review(slug: str, head_sha: str) -> str:
    """이 head의 최신 pr-review 실행을 다시 돌린다. 게이트 모드에서 실패한 check를 override 결과로 바꾸기 위함이다."""
    runs = (
        gh(
            [
                "run",
                "list",
                "--repo",
                slug,
                "--workflow",
                "pr-review.yml",
                "--commit",
                head_sha,
                "--limit",
                "1",
                "--json",
                "databaseId,status",
            ]
        )
        or []
    )
    if not runs:
        return "이 commit의 pr-review 실행이 없음"
    rid = runs[0]["databaseId"]
    if runs[0]["status"] != "completed":
        return f"pr-review 실행 {rid} 이 아직 진행 중이라 다시 돌리지 않음 (끝나면 Actions에서 Re-run)"
    r = run(["gh", "run", "rerun", str(rid), "--repo", slug])
    return (
        f"pr-review 실행 {rid} 을 다시 돌림"
        if r.ok
        else f"다시 돌리기 실패 ({r.err.strip()[:200]}). Actions에서 Re-run 할 것"
    )


def cmd_handle_label(a: argparse.Namespace) -> None:
    """pr-review-override 워크플로. 라벨이 붙은 순간 처리한다."""
    if a.label != LABEL:
        log("override", f"{a.label} 라벨은 처리 대상이 아님")
        return
    reviewers = parse_reviewers(a.reviewers)
    if a.sender.lower() not in reviewers:
        remove_label(a.repo_slug, a.pr)
        who = ", ".join(f"@{r}" for r in sorted(reviewers)) or "없음"
        msg = (
            f"@{a.sender} 님은 지정 리뷰어가 아니라 {LABEL} 라벨을 뗌. "
            f"override(라벨로 검수 판정을 통과로 바꾸는 것)는 지정 리뷰어만 할 수 있음(지정 리뷰어: {who}). "
            "판정에 이의가 있으면 검수 comment에 근거를 남기고 지정 리뷰어에게 요청 필요."
        )
        run(["gh", "pr", "comment", str(a.pr), "--repo", a.repo_slug, "--body", msg])
        log("override", f"@{a.sender} 는 지정 리뷰어가 아니라 라벨을 뗌")
        return
    from apply import apply_override

    apply_override(a.repo_slug, a.pr, a.sender, a.head_sha, Path("."))
    log("override", f"@{a.sender} 의 override 반영")
    log("override", rerun_latest_review(a.repo_slug, a.head_sha))


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "handle-label"):
        p = sub.add_parser(name)
        p.add_argument("--repo-slug", required=True)
        p.add_argument("--pr", type=int, required=True)
        p.add_argument("--reviewers", default="")
    sub.choices["check"].add_argument("--event-action", default="")
    sub.choices["check"].add_argument("--run-attempt", default="1")
    for arg in ("--label", "--sender", "--head-sha"):
        sub.choices["handle-label"].add_argument(arg, required=True)
    a = ap.parse_args()
    {"check": cmd_check, "handle-label": cmd_handle_label}[a.cmd](a)


if __name__ == "__main__":
    main()
