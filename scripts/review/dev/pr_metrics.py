#!/usr/bin/env python3
"""코드 리뷰 Agent(pr-review)의 도입 효과를 GitHub PR 기록에서 집계한다.

- 지표: P1 리뷰 범위, P2 환산 리뷰 시간(월), P3 발견·조치(라벨), P4 피드백 대기 시간, P5 merge 시점 품질(--backfill),
  P6 판정 일치율과 override 비율.
  지표 정의는 docs/pr-review-agent/metrics.md에 있다.
- `gh api`로 GitHub REST API를 읽기만 한다. gh 2.4.0의 `gh pr list --json`에는 head·base SHA 필드가 없기 때문이다.
  PR 번호, 작성자, 시각, head·base SHA는 PR 목록 응답에서 얻고, PR마다 reviews, comments, timeline만 더 읽는다.
- gh를 호출하는 함수(gh_api, fetch_merged_prs)와 계산하는 함수(pr_row 이하)를 나눈다. 테스트는 계산 함수만 확인한다.
- PR 하나의 gh 호출이 실패하면 그 PR의 error 열에 적고 다음 PR로 넘어간다. error가 있는 PR은 summary.md 집계에서 뺀다.
- 출력: <out>/prs.csv(PR별 한 줄. PR 작성자 이름은 이 파일에만 있음), <out>/summary.md(수치 요약. 개인별 수치 없음).
- 기간 필터(--since, --until)와 월 구분은 한국 시간(UTC+09:00) 기준이다. jira_metrics.py와 맞추기 위함이다.
  prs.csv의 시각 값은 GitHub API의 UTC 값 그대로다.

    python scripts/review/dev/pr_metrics.py --repos pose-griffin,mlops-flowex --since 2026-04-01 --until 2026-09-30 --out reports/agent-impact/pr
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apply import LABELS, OVERRIDE_MARK  # noqa: E402
from common import COMMENT_MARKER, log, run  # noqa: E402

PR_COLUMNS = [
    "repo",
    "number",
    "author",
    "created_at",
    "merged_at",
    "base_sha",
    "head_sha",
    "human_reviewed",
    "human_review_after_merge",
    "first_human_review_hours",
    "agent_reviewed",
    "agent_comment_after_merge",
    "agent_feedback_minutes",
    "ever_rejected",
    "label_at_merge",
    "overridden",
    "error",
]
# 표본 실측 기록표의 열. jira_metrics.py와 같은 파일을 읽으므로 이름을 바꾸지 않는다
STUDY_COLUMNS = [
    "kind",
    "target",
    "size",
    "review_minutes",
    "issues_found",
    "user_verdict",
    "check_minutes",
    "agent_verdict",
]
PASS_LABEL = LABELS["pass"][0]
REJECT_LABEL = LABELS["reject"][0]
REVIEW_LABELS = {name for name, _ in LABELS.values()}
# apply.py의 override_body가 PR 검수 comment 맨 위에 붙이는 문구
OVERRIDE_PHRASE = "검수 판정을 override함"
VERDICT_REJECT = "반려"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
KST = timezone(timedelta(hours=9), "KST")


# ---------- gh 호출 ----------


def parse_json_stream(text: str) -> list:
    """`gh api --paginate` 출력을 하나의 목록으로 합친다.

    gh 버전에 따라 페이지마다 JSON 배열을 이어 붙여(`[...][...]`) 출력하므로 JSON 값을 차례로 읽는다.
    값이 배열이면 원소를, 객체면 그 객체를 목록에 추가한다.

    Args:
        text: gh 출력 전체.

    Returns:
        모든 페이지의 원소를 합친 목록.
    """
    decoder = json.JSONDecoder()
    items: list = []
    i, n = 0, len(text)
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return items
        value, i = decoder.raw_decode(text, i)
        if isinstance(value, list):
            items.extend(value)
        else:
            items.append(value)


def gh_api(path: str) -> Any:
    """`gh api --paginate`로 GitHub REST API를 GET으로 읽고 모든 페이지를 한 목록으로 합친다. 실패하면 RuntimeError."""
    r = run(["gh", "api", "--paginate", path])
    if not r.ok:
        raise RuntimeError(f"gh api {path} 실패: {r.err.strip()[:300]}")
    return parse_json_stream(r.out)


def fetch_merged_prs(owner: str, repo: str, since: date | None, until: date | None) -> list[dict]:
    """repo에서 merge 날짜(한국 시간)가 범위 안인 PR의 기록을 읽어 PR별 행으로 돌려준다.

    PR 목록 호출이 실패하면 RuntimeError를 낸다. PR 하나의 호출이 실패하면 그 PR의 error 열에 적고 다음 PR로 넘어간다.
    """
    base = f"repos/{owner}/{repo}"
    rows: list[dict] = []
    for p in gh_api(f"{base}/pulls?state=closed&per_page=100"):
        if not merged_in_range(p.get("merged_at"), since, until):
            continue
        n = p["number"]
        try:
            reviews = gh_api(f"{base}/pulls/{n}/reviews?per_page=100")
            comments = gh_api(f"{base}/issues/{n}/comments?per_page=100")
            timeline = gh_api(f"{base}/issues/{n}/timeline?per_page=100")
        except (RuntimeError, ValueError) as e:
            log("pr_metrics", f"{repo}#{n} 조회 실패: {e}")
            rows.append(error_row(repo, p, str(e)))
            continue
        rows.append(pr_row(repo, p, reviews, comments, timeline))
    return rows


# ---------- PR별 계산 ----------


def parse_time(value: str | None) -> datetime | None:
    """GitHub API의 ISO 8601 시각(끝의 Z 포함)을 datetime으로 바꾼다. 없으면 None."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def merged_in_range(merged_at: str | None, since: date | None, until: date | None) -> bool:
    """merge 시각의 한국 시간 날짜가 since 이상 until 이하이면 True. merge되지 않은 PR은 False."""
    t = parse_time(merged_at)
    if t is None:
        return False
    d = t.astimezone(KST).date()
    return (since is None or d >= since) and (until is None or d <= until)


def kst_month(value: str | None) -> str:
    """GitHub API 시각의 한국 시간 월(YYYY-MM). 시각이 없으면 빈 문자열."""
    t = parse_time(value)
    return t.astimezone(KST).strftime("%Y-%m") if t else ""


def hours_between(start: datetime | None, end: datetime | None) -> float | None:
    """start부터 end까지의 시간(시간 단위). 둘 중 하나라도 없으면 None."""
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600


def before_merge(t: datetime | None, merged_at: datetime | None) -> bool:
    """시각 t가 merge 시각 이전(같은 시각 포함)이면 True. merge 시각이 없으면 True, t가 없으면 False."""
    if merged_at is None:
        return True
    return t is not None and t <= merged_at


def is_bot(user: dict | None) -> bool:
    """GitHub 계정이 bot(type이 Bot이거나 이름이 [bot]으로 끝남)이면 True."""
    u = user or {}
    return u.get("type") == "Bot" or (u.get("login") or "").endswith("[bot]")


def human_reviews(author: str | None, reviews: list[dict]) -> list[dict]:
    """PR 작성자가 아니고 bot이 아닌 계정이 제출한 GitHub review를 제출 시각 순서로 돌려준다.

    bot을 빼는 이유: apply.py가 수정 제안을 워크플로 토큰(github-actions bot)의 GitHub review로 달기 때문이다.
    제출되지 않은 review(submitted_at 없음)는 세지 않는다.
    """
    found = [
        r
        for r in reviews
        if r.get("submitted_at")
        and (r.get("user") or {}).get("login") not in (None, author)
        and not is_bot(r.get("user"))
    ]
    return sorted(found, key=lambda r: parse_time(r["submitted_at"]) or EPOCH)


def review_comment(comments: list[dict]) -> dict | None:
    """첫 줄이 COMMENT_MARKER인 PR comment(PR 검수 comment) 중 가장 먼저 게시된 것. 없으면 None."""
    found = [c for c in comments if (c.get("body") or "").startswith(COMMENT_MARKER)]
    return min(found, key=lambda c: parse_time(c.get("created_at")) or EPOCH, default=None)


def label_state_at_merge(timeline: list[dict], merged_at: datetime | None) -> tuple[bool, str]:
    """timeline의 labeled, unlabeled 이벤트로 반려 이력과 merge 시각의 review 라벨을 구한다.

    merge 시각보다 뒤의 이벤트는 무시한다. merge 시각이 없으면 모든 이벤트를 쓴다.

    Args:
        timeline: `/issues/{번호}/timeline` 응답.
        merged_at: merge 시각.

    Returns:
        (review/reject 라벨이 붙은 적이 있는가, merge 시각에 붙어 있던 review 라벨).
        라벨이 여러 개면 붙은 순서대로 ';'로 잇고, 없으면 빈 문자열이다.
    """
    events = [
        e
        for e in timeline
        if e.get("event") in ("labeled", "unlabeled") and (e.get("label") or {}).get("name") in REVIEW_LABELS
    ]
    events.sort(key=lambda e: parse_time(e.get("created_at")) or EPOCH)
    current: list[str] = []
    ever_rejected = False
    for e in events:
        t = parse_time(e.get("created_at"))
        if merged_at is not None and t is not None and t > merged_at:
            break
        name = e["label"]["name"]
        if e["event"] == "labeled":
            ever_rejected = ever_rejected or name == REJECT_LABEL
            if name not in current:
                current.append(name)
        elif name in current:
            current.remove(name)
    return ever_rejected, ";".join(current)


def is_overridden(comment: dict | None) -> bool:
    """PR 검수 comment에 override 표식(OVERRIDE_MARK)이나 override 문구가 있으면 True."""
    body = (comment or {}).get("body") or ""
    return OVERRIDE_MARK in body or OVERRIDE_PHRASE in body


def base_fields(repo: str, pr: dict) -> dict:
    """PR 목록 응답 원소에서 prs.csv의 기본 열(repo, 번호, 작성자, 시각, SHA)을 뽑는다."""
    return {
        "repo": repo,
        "number": pr.get("number"),
        "author": (pr.get("user") or {}).get("login"),
        "created_at": pr.get("created_at"),
        "merged_at": pr.get("merged_at"),
        "base_sha": (pr.get("base") or {}).get("sha"),
        "head_sha": (pr.get("head") or {}).get("sha"),
    }


def error_row(repo: str, pr: dict, message: str) -> dict:
    """gh 호출이 실패한 PR의 prs.csv 한 줄. 기본 열과 error만 채운다."""
    return {**dict.fromkeys(PR_COLUMNS), **base_fields(repo, pr), "error": " ".join(message.split())}


def pr_row(repo: str, pr: dict, reviews: list[dict], comments: list[dict], timeline: list[dict]) -> dict:
    """PR 하나의 REST API 응답으로 prs.csv 한 줄을 만든다.

    human_reviewed, agent_reviewed는 merge 시각 이전의 GitHub review, PR 검수 comment만 센다 (지표 P1).
    first_human_review_hours, agent_feedback_minutes는 merge 전후와 관계없이 계산한다 (지표 P4).

    Args:
        repo: repo 이름.
        pr: `/pulls?state=closed` 목록 응답의 원소.
        reviews: `/pulls/{번호}/reviews` 응답.
        comments: `/issues/{번호}/comments` 응답.
        timeline: `/issues/{번호}/timeline` 응답.

    Returns:
        PR_COLUMNS를 키로 가진 dict. 시간 값은 해당 기록이 없으면 None이고 error는 빈 문자열이다.
    """
    row = base_fields(repo, pr)
    created = parse_time(pr.get("created_at"))
    merged = parse_time(pr.get("merged_at"))
    humans = human_reviews(row["author"], reviews)
    human_times = [parse_time(r["submitted_at"]) for r in humans]
    human_hours = hours_between(created, human_times[0]) if humans else None
    agent = review_comment(comments)
    agent_at = parse_time(agent.get("created_at")) if agent else None
    agent_hours = hours_between(created, agent_at)
    ever_rejected, label_at_merge = label_state_at_merge(timeline, merged)
    return {
        **row,
        "human_reviewed": any(before_merge(t, merged) for t in human_times),
        "human_review_after_merge": any(not before_merge(t, merged) for t in human_times),
        "first_human_review_hours": None if human_hours is None else round(human_hours, 2),
        "agent_reviewed": agent is not None and before_merge(agent_at, merged),
        "agent_comment_after_merge": agent is not None and not before_merge(agent_at, merged),
        "agent_feedback_minutes": None if agent_hours is None else round(agent_hours * 60, 1),
        "ever_rejected": ever_rejected,
        "label_at_merge": label_at_merge,
        "overridden": is_overridden(agent),
        "error": "",
    }


# ---------- 표본 실측, 소급 판정 ----------


def _num(value: str | None) -> float | None:
    """CSV 칸을 숫자로 바꾼다. 비었거나 숫자가 아니면 None."""
    try:
        return float(value) if value is not None and value.strip() else None
    except ValueError:
        return None


def load_time_study(path: Path) -> list[dict]:
    """표본 실측 기록표 CSV에서 kind가 pr인 행만 읽는다. 열이 빠져 있으면 종료한다."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in STUDY_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[pr_metrics] {path}에 열이 없음: {', '.join(missing)}")
        return [r for r in reader if (r.get("kind") or "").strip() == "pr"]


def load_backfill(paths: list[Path]) -> dict[tuple[str, int], dict]:
    """backfill_precheck.py 출력 CSV를 (repo, PR 번호) -> 행으로 읽는다. 같은 PR이 여러 번 나오면 뒤의 행을 쓴다."""
    out: dict[tuple[str, int], dict] = {}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("repo") and (r.get("number") or "").strip():
                    out[(r["repo"], int(r["number"]))] = r
    return out


def py_lines_map(backfill: dict[tuple[str, int], dict]) -> dict[tuple[str, int], int]:
    """소급 판정 행에서 (repo, PR 번호) -> py_lines를 뽑는다. py_lines가 빈 행(소급 실행 실패)은 뺀다."""
    out: dict[tuple[str, int], int] = {}
    for key, r in backfill.items():
        lines = _num(r.get("py_lines"))
        if lines is not None:
            out[key] = int(lines)
    return out


def _count(value: str | None) -> int:
    """CSV 칸의 개수 값을 정수로 바꾼다. 비었거나 숫자가 아니면 0."""
    v = _num(value)
    return 0 if v is None else int(v)


def merge_quality(rows: list[dict], backfill: dict[tuple[str, int], dict]) -> dict:
    """merge된 PR 중 스크립트 검사 기준 위반이 남은 PR을 센다 (지표 P5).

    대상은 소급 판정 행이 있고 error가 빈 PR이다. 아래 중 하나라도 있으면 위반이 남은 PR로 센다.
    (a) lint: lint_violations + format_violations ≥ 1, (b) no_candidates: targets_without_candidates ≥ 1,
    (c) dead_code: dead_code_candidates ≥ 1.
    with_agent는 merge 전에 PR 검수 comment가 게시된 PR(agent_reviewed가 참)이다.

    Args:
        rows: prs.csv 행(pr_row 결과).
        backfill: 소급 판정의 (repo, PR 번호) -> 행.

    Returns:
        묶음 all, with_agent, without_agent마다 n, violated, lint, no_candidates, dead_code의 PR 수.
        그리고 errors(error가 있어 빠진 PR 수), no_backfill(소급 판정 행이 없어 빠진 PR 수).
    """
    groups = {
        g: {"n": 0, "violated": 0, "lint": 0, "no_candidates": 0, "dead_code": 0}
        for g in ("all", "with_agent", "without_agent")
    }
    errors = no_backfill = 0
    for r in rows:
        bf = backfill.get((r["repo"], int(r["number"])))
        if bf is None:
            no_backfill += 1
            continue
        if (bf.get("error") or "").strip():
            errors += 1
            continue
        hits = {
            "lint": _count(bf.get("lint_violations")) + _count(bf.get("format_violations")) >= 1,
            "no_candidates": _count(bf.get("targets_without_candidates")) >= 1,
            "dead_code": _count(bf.get("dead_code_candidates")) >= 1,
        }
        for g in ("all", "with_agent" if r["agent_reviewed"] else "without_agent"):
            c = groups[g]
            c["n"] += 1
            c["violated"] += int(any(hits.values()))
            for k, hit in hits.items():
                c[k] += int(hit)
    return {**groups, "errors": errors, "no_backfill": no_backfill}


def review_rate(study: list[dict]) -> dict:
    """줄당 팀장 리뷰 시간(분/줄) = pr 행 review_minutes 합 / size 합. size 합이 0이면 per_line은 None.

    review_minutes와 size가 모두 숫자인 행만 쓴다.

    Args:
        study: 표본 실측 기록표의 pr 행.

    Returns:
        rows(사용한 행 수), review_minutes(합), size(합), per_line(분/줄 또는 None).
    """
    used = [
        (m, s)
        for m, s in ((_num(r.get("review_minutes")), _num(r.get("size"))) for r in study)
        if m is not None and s is not None
    ]
    minutes = sum(m for m, _ in used)
    size = sum(s for _, s in used)
    return {"rows": len(used), "review_minutes": minutes, "size": size, "per_line": minutes / size if size else None}


def check_minutes_values(study: list[dict]) -> list[float]:
    """표본 실측 기록표 pr 행의 check_minutes(건당 결과 확인 시간) 중 숫자인 값."""
    return [v for v in (_num(r.get("check_minutes")) for r in study) if v is not None]


def monthly_saved_minutes(
    rows: list[dict], py_lines: dict[tuple[str, int], int], per_line: float, check_median: float
) -> list[dict]:
    """월별 환산 리뷰 시간(분)을 계산한다 (지표 P2).

    월별로 (그 달 merge PR 중 Agent 리뷰 PR의 Python 변경 줄 합 × per_line) − (그 달 Agent 리뷰 PR 수 × check_median)이다.
    Agent 리뷰 PR은 merge 전에 PR 검수 comment가 게시된 PR(agent_reviewed가 참)이다. 환산 리뷰 시간은 Agent가 리뷰한 PR을
    팀장이 리뷰했다면 걸렸을 시간이기 때문이다. Agent 리뷰 PR 중 py_lines가 없는 PR 수는 missing에 적는다.

    Args:
        rows: prs.csv 행(pr_row 결과).
        py_lines: (repo, PR 번호) -> 테스트 외 Python 파일의 추가·삭제 줄 합.
        per_line: 줄당 팀장 리뷰 시간(분/줄).
        check_median: 건당 결과 확인 시간 중앙값(분).

    Returns:
        월 순서의 dict 목록. 키는 month(YYYY-MM, merge 시각의 한국 시간), prs, py_lines, missing, saved_minutes.
        merge PR이 있으면 Agent 리뷰 PR이 없는 달도 0으로 나온다.
    """
    months: dict[str, dict] = {}
    for r in rows:
        month = kst_month(r.get("merged_at"))
        m = months.setdefault(month, {"month": month, "prs": 0, "py_lines": 0, "missing": 0})
        if not r.get("agent_reviewed"):
            continue
        lines = py_lines.get((r["repo"], int(r["number"])))
        if lines is None:
            m["missing"] += 1
            continue
        m["prs"] += 1
        m["py_lines"] += lines
    for m in months.values():
        m["saved_minutes"] = m["py_lines"] * per_line - m["prs"] * check_median
    return [months[k] for k in sorted(months)]


def verdict_agreement(study: list[dict]) -> dict:
    """표본 실측 pr 행의 반려 정밀도와 반려 재현율을 계산한다 (지표 P6). 분모가 0이면 그 값은 None.

    user_verdict와 agent_verdict가 모두 있는 행만 세고, 둘 중 하나라도 빈 행 수는 excluded에 적는다.

    Args:
        study: 표본 실측 기록표의 pr 행.

    Returns:
        n(사용한 행 수), excluded, both(둘 다 반려), agent_reject, user_reject,
        precision(both / agent_reject), recall(both / user_reject).
    """
    used = [r for r in study if (r.get("user_verdict") or "").strip() and (r.get("agent_verdict") or "").strip()]
    user = [r["user_verdict"].strip() == VERDICT_REJECT for r in used]
    agent = [r["agent_verdict"].strip() == VERDICT_REJECT for r in used]
    both = sum(1 for u, a in zip(user, agent) if u and a)
    agent_reject, user_reject = sum(agent), sum(user)
    return {
        "n": len(used),
        "excluded": len(study) - len(used),
        "both": both,
        "agent_reject": agent_reject,
        "user_reject": user_reject,
        "precision": both / agent_reject if agent_reject else None,
        "recall": both / user_reject if user_reject else None,
    }


# ---------- summary.md ----------


def ratio_text(k: int, n: int) -> str:
    """비율을 'X.X% (k/n)'로 적는다. 분모가 0이면 '계산 불가 (k/0)'."""
    if n == 0:
        return f"계산 불가 ({k}/{n})"
    return f"{100 * k / n:.1f}% ({k}/{n})"


def dist_text(values: list[float], unit: str) -> str:
    """값 목록을 '중앙값 X, 범위 a–b, n=k'로 적는다. 평균은 적지 않는다."""
    if not values:
        return "값 없음(n=0)"
    med, lo, hi = statistics.median(values), min(values), max(values)
    return f"중앙값 {med:.1f}{unit}, 범위 {lo:.1f}–{hi:.1f}{unit}, n={len(values)}"


def period_text(since: date | None, until: date | None) -> str:
    """merge 날짜 범위를 글로 적는다."""
    if since and until:
        return f"{since}–{until}"
    if since:
        return f"{since} 이후"
    if until:
        return f"{until} 이전"
    return "전체 기간"


def _p2_lines(rows: list[dict], study: list[dict] | None, backfill: dict[tuple[str, int], dict] | None) -> list[str]:
    if study is None or backfill is None:
        return ["- `--backfill`과 `--time-study`가 둘 다 있어야 계산함. 이번 실행에서는 계산하지 않음"]
    py_lines = py_lines_map(backfill)
    rate = review_rate(study)
    checks = check_minutes_values(study)
    if rate["per_line"] is None or not checks:
        return [
            "- 계산 불가임. 표본 실측 기록표의 pr 행에서 size 합이 0이거나 check_minutes 값이 없음",
            f"  - 사용한 pr 행 {rate['rows']}개, check_minutes 값 {len(checks)}개",
        ]
    check_median = statistics.median(checks)
    lines = [
        "- 계산 방법: 월별로 (그 달 Agent 리뷰 PR의 Python 변경 줄 합 × 줄당 팀장 리뷰 시간)"
        " − (그 달 Agent 리뷰 PR 수 × 건당 결과 확인 시간 중앙값)",
        "  - Agent 리뷰 PR: 그 달 merge된 PR 중 merge 전에 PR 검수 comment가 게시된 PR."
        " 환산 리뷰 시간은 Agent가 리뷰한 PR을 팀장이 리뷰했다면 걸렸을 시간이기 때문임",
        "  - Python 변경 줄: `--backfill` CSV의 py_lines(테스트 외 Python 파일의 추가·삭제 줄 합)",
        "  - Agent 리뷰 PR 수와 Python 변경 줄 합은 py_lines가 있는 PR만 셈."
        ' py_lines가 없는 Agent 리뷰 PR 수는 표의 "소급 판정 없음" 칸에 적음',
        f"- 줄당 팀장 리뷰 시간: {rate['per_line']:.3f}분/줄",
        f"  - 표본 실측 기록표 pr 행 {rate['rows']}개의 review_minutes 합 {rate['review_minutes']:.1f}분"
        f" / size 합 {rate['size']:.0f}줄",
        f"- 건당 결과 확인 시간: 표본 실측 기록표 pr 행의 check_minutes. {dist_text(checks, '분')}",
        "",
        "| 월(merge 시각, 한국 시간) | Agent 리뷰 PR 수 | Python 변경 줄 합 | 소급 판정 없음 | 환산 리뷰 시간(분) |",
        "|---|---|---|---|---|",
    ]
    for m in monthly_saved_minutes(rows, py_lines, rate["per_line"], check_median):
        lines.append(f"| {m['month']} | {m['prs']} | {m['py_lines']} | {m['missing']} | {m['saved_minutes']:.1f} |")
    return lines


def _p5_group_line(label: str, g: dict) -> str:
    return (
        f"- {label}: 위반이 남은 PR 비율 {ratio_text(g['violated'], g['n'])}."
        f" 기준별 PR 수는 (a) {g['lint']}개, (b) {g['no_candidates']}개, (c) {g['dead_code']}개임"
    )


def _p5_lines(rows: list[dict], backfill: dict[tuple[str, int], dict] | None) -> list[str]:
    if backfill is None:
        return ["- `--backfill`이 있어야 계산함. 이번 실행에서는 계산하지 않음"]
    q = merge_quality(rows, backfill)
    return [
        "- 계산 방법: 위반이 남은 PR 수 / 대상 PR 수",
        "  - 대상 PR: 기간 안에 merge된 PR 중 `--backfill` CSV에 행이 있고 error 열이 빈 PR",
        "  - 위반이 남은 PR: 아래 세 기준 중 하나라도 해당하는 PR",
        "    - (a) 검사 3-1번 위반: lint_violations + format_violations가 1 이상",
        "    - (b) 테스트 후보가 없는 판단 대상 심볼: targets_without_candidates가 1 이상",
        "    - (c) 검사 6번 미사용 후보: dead_code_candidates가 1 이상",
        _p5_group_line("전체", q["all"]),
        _p5_group_line("merge 전에 PR 검수 comment가 게시된 PR", q["with_agent"]),
        _p5_group_line("그 밖의 PR(PR 검수 comment가 없거나 merge 뒤에 게시됨)", q["without_agent"]),
        f"- 빠진 PR: 소급 실행 오류(error 열)가 있는 PR {q['errors']}개,"
        f" `--backfill` CSV에 행이 없는 PR {q['no_backfill']}개",
    ]


def _p6_lines(study: list[dict] | None) -> list[str]:
    if study is None:
        return ["- 반려 정밀도와 반려 재현율은 `--time-study`가 있어야 계산함. 이번 실행에서는 계산하지 않음"]
    v = verdict_agreement(study)
    return [
        f"- 반려 정밀도: {ratio_text(v['both'], v['agent_reject'])}",
        '  - user_verdict와 agent_verdict가 모두 "반려"인 PR 수 / agent_verdict가 "반려"인 PR 수',
        f"- 반려 재현율: {ratio_text(v['both'], v['user_reject'])}",
        '  - user_verdict와 agent_verdict가 모두 "반려"인 PR 수 / user_verdict가 "반려"인 PR 수',
        f"- 표본: 표본 실측 기록표의 pr 행 중 user_verdict와 agent_verdict가 모두 있는 {v['n']}개",
        f"  - 둘 중 하나라도 빈 pr 행 {v['excluded']}개는 뺌",
    ]


def render_summary(
    rows: list[dict], scope: str, study: list[dict] | None, backfill: dict[tuple[str, int], dict] | None
) -> str:
    """prs.csv 행으로 summary.md 본문을 만든다. error가 있는 PR은 집계에서 빼고, 개인별 수치는 넣지 않는다.

    Args:
        rows: prs.csv 행(pr_row, error_row 결과).
        scope: 대상 repo와 merge 날짜 범위를 적은 글.
        study: 표본 실측 기록표의 pr 행. 없으면 None.
        backfill: 소급 판정의 (repo, PR 번호) -> 행. 없으면 None.

    Returns:
        markdown 본문.
    """
    ok = [r for r in rows if not r.get("error")]
    n = len(ok)
    human = [r for r in ok if r["human_reviewed"]]
    agent = [r for r in ok if r["agent_reviewed"]]
    either = [r for r in ok if r["human_reviewed"] or r["agent_reviewed"]]
    human_late = [r for r in ok if r["human_review_after_merge"] and not r["human_reviewed"]]
    agent_late = [r for r in ok if r["agent_comment_after_merge"]]
    rejected = [r for r in ok if r["ever_rejected"]]
    fixed = [r for r in rejected if r["label_at_merge"] == PASS_LABEL]
    overridden = [r for r in agent if r["overridden"]]
    human_hours = [r["first_human_review_hours"] for r in ok if r["first_human_review_hours"] is not None]
    agent_minutes = [r["agent_feedback_minutes"] for r in ok if r["agent_feedback_minutes"] is not None]
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        "# 코드 리뷰 Agent(pr-review) PR 기록 집계",
        "",
        f"- 대상: {scope}, merge된 PR {len(rows)}개 중 집계한 PR {n}개",
        f"  - gh 조회가 실패해 집계에서 뺀 PR: {len(rows) - n}개(prs.csv의 error 열)",
        "- 데이터: GitHub REST API(`gh api`)로 읽은 PR 목록, GitHub review, PR comment, timeline 기록",
        f"- 집계 시각: {now}",
        "- 기간과 월은 한국 시간(UTC+09:00) 기준임. prs.csv의 시각 값은 GitHub API의 UTC 값 그대로임",
        "- PR별 값은 같은 폴더의 `prs.csv`에 있음",
        "",
        "## P1 리뷰 범위",
        "",
        "- 계산 방법: merge 전에 리뷰를 받은 PR 수 / 집계한 merge PR 수",
        "  - 사람 리뷰: merge 시각 이전에 제출된, PR 작성자가 아니고 bot이 아닌 계정의 GitHub review가 있음",
        f"  - Agent 리뷰: merge 시각 이전에 게시된 PR 검수 comment(첫 줄이 `{COMMENT_MARKER}`인 PR comment)가 있음",
        f"- 사람 리뷰를 받은 PR 비율: {ratio_text(len(human), n)}",
        f"- Agent 리뷰를 받은 PR 비율: {ratio_text(len(agent), n)}",
        f"- 사람 리뷰와 Agent 리뷰 중 하나라도 받은 PR 비율: {ratio_text(len(either), n)}",
        f"- merge 뒤에만 달려 세지 않은 PR: 사람 GitHub review {len(human_late)}개, PR 검수 comment {len(agent_late)}개",
        "",
        "## P2 환산 리뷰 시간(월)",
        "",
        *_p2_lines(ok, study, backfill),
        "",
        "## P3 발견·조치(라벨)",
        "",
        f"- 반려된 적이 있는 PR 중 merge 시각의 라벨이 `{PASS_LABEL}`인 PR 비율: {ratio_text(len(fixed), len(rejected))}",
        f"  - 반려된 적이 있는 PR: merge 시각 전에 timeline의 `labeled` 이벤트로 `{REJECT_LABEL}` 라벨이 붙은 적이 있는 PR",
        "  - merge 시각의 라벨: merge 시각까지의 `labeled`, `unlabeled` 이벤트로 계산한 `review/*` 라벨. merge 뒤의 이벤트는 무시함",
        "",
        "## P4 피드백 대기 시간",
        "",
        f"- 사람: PR 생성부터 작성자 외 첫 GitHub review 제출(`submitted_at`)까지 시간. {dist_text(human_hours, '시간')}",
        "  - n은 merge 전후와 관계없이 작성자 외 GitHub review가 있는 PR 수임",
        f"- Agent: PR 생성부터 PR 검수 comment 게시(`created_at`)까지 시간. {dist_text(agent_minutes, '분')}",
        "  - n은 merge 전후와 관계없이 PR 검수 comment가 있는 PR 전체의 수임(merge 뒤에 게시된 PR 포함)",
        "  - PR 검수 comment는 같은 comment를 갱신하므로 `created_at`이 첫 게시 시각임",
        "",
        "## P5 merge 시점 품질",
        "",
        *_p5_lines(ok, backfill),
        "",
        "## P6 판정 일치율과 override 비율",
        "",
        f"- override 비율: {ratio_text(len(overridden), len(agent))}",
        f'  - merge 전에 PR 검수 comment가 게시된 PR 중 comment에 override 문구("{OVERRIDE_PHRASE}")가 있는 PR 수'
        " / merge 전에 PR 검수 comment가 게시된 PR 수",
        "  - PR 검수 comment는 같은 comment를 갱신하므로 마지막 검수 결과가 override인 PR만 셈",
        *_p6_lines(study),
    ]
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    """행 목록을 CSV로 쓴다. None은 빈 칸으로 쓴다. 상위 디렉터리는 만든다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else r.get(k) for k in columns})


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", default="everex-ai", help="GitHub 조직 또는 계정 (기본 everex-ai)")
    ap.add_argument("--repos", required=True, help="쉼표로 구분한 repo 이름")
    ap.add_argument("--since", default=None, help="merge 날짜 시작 YYYY-MM-DD (포함, 한국 시간)")
    ap.add_argument("--until", default=None, help="merge 날짜 끝 YYYY-MM-DD (포함, 한국 시간)")
    ap.add_argument("--out", required=True, help="출력 폴더 (prs.csv, summary.md)")
    ap.add_argument("--backfill", default=None, help="backfill_precheck.py 출력 CSV. repo마다 하나면 쉼표로 구분")
    ap.add_argument("--time-study", default=None, help="표본 실측 기록표 CSV")
    a = ap.parse_args()

    repos = [r.strip() for r in a.repos.split(",") if r.strip()]
    since = date.fromisoformat(a.since) if a.since else None
    until = date.fromisoformat(a.until) if a.until else None
    # 입력 파일의 형식 오류를 GitHub 조회 전에 알린다
    study = load_time_study(Path(a.time_study)) if a.time_study else None
    backfill = load_backfill([Path(p.strip()) for p in a.backfill.split(",") if p.strip()]) if a.backfill else None

    rows: list[dict] = []
    for repo in repos:
        repo_rows = fetch_merged_prs(a.owner, repo, since, until)
        failed = sum(1 for r in repo_rows if r["error"])
        log("pr_metrics", f"{a.owner}/{repo}: merge된 PR {len(repo_rows)}개 (조회 실패 {failed}개)")
        rows.extend(repo_rows)

    out = Path(a.out)
    write_csv(out / "prs.csv", rows, PR_COLUMNS)
    scope = f"{a.owner}의 repo {len(repos)}개({', '.join(repos)}), merge 날짜 {period_text(since, until)}"
    (out / "summary.md").write_text(render_summary(rows, scope, study, backfill), encoding="utf-8")
    log("pr_metrics", f"PR {len(rows)}개 -> {out / 'prs.csv'}, {out / 'summary.md'}")


if __name__ == "__main__":
    main()
