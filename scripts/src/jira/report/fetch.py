"""정기 보고에 쓸 ticket, sub-task, PR을 Jira와 gh CLI에서 읽는다.

`search_prs`와 `subprocess`는 이 모듈에 두고, `SEARCH_LIMIT`은 `src/constant/jira.py`에서 가져와 이 모듈의 이름으로 둔다.
부르는 쪽은 `fetch.search_prs`처럼 모듈 이름을 붙여 부르므로, 테스트가 이 모듈의 이름을 바꿔치기하면 적용된다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys

from src.constant.jira import SEARCH_LIMIT, TARGET_TYPES, TYPE_ALIAS
from src.jira.report.model import JiraReader, ReviewComment, Ticket
from src.jira.report.parse import (
    VERDICT_KO,
    description_edits,
    mark_reruns,
    parse_doc_review,
    parse_review,
    parse_run,
    parse_ts,
    status_changes,
)
from src.jira.report.status import CLOSED_STATUSES
from src.jira.wiki import is_agent_comment

ISSUE_FIELDS = ["summary", "status", "assignee", "issuetype", "parent", "description", "created"]
SUBTASK_FIELDS = ["parent", "created"]  # sub-task 검색에서 받는 필드
PR_LIMIT = 1000  # gh search prs의 결과 상한
# 자동 보류 전환이 있던 검수 comment의 문구(자동 보류 전환을 없애기 전 scripts/apply.py의 review_transition)
AUTO_HOLD_NOTE = "상태를 in-progress로 되돌림"


def load_ticket(j: JiraReader, issue: dict) -> Ticket:
    """검색 결과의 ticket 하나에 변경 이력, comment, jira-doc 기록을 더해 읽는다.

    agent comment(scripts/src/jira/wiki.py의 is_agent_comment) 중 판정 줄이 있으면 검수 comment,
    "문서화 리뷰 결과(관찰 모드)" 줄이 있으면 문서화 리뷰 comment로 센다. agent comment가 아닌 comment는
    사람 comment로 작성 시각만 남긴다.

    Args:
        j: Jira 읽기 객체.
        issue: Jira.search 결과 항목 하나(key, fields). fields는 ISSUE_FIELDS를 담는다.

    Returns:
        ticket 기록.
    """
    key = issue["key"]
    f = issue.get("fields") or {}
    assignee = f.get("assignee") or {}
    parent = f.get("parent") or {}
    pf = parent.get("fields") or {}
    name = (f.get("issuetype") or {}).get("name") or ""
    log = j.changelog(key)
    t = Ticket(
        key=key,
        itype=TYPE_ALIAS.get(name.strip().lower(), TYPE_ALIAS.get(name, name)),
        assignee_id=assignee.get("accountId") or "",
        assignee_name=assignee.get("displayName") or "",
        parent_key=parent.get("key") or "",
        parent_name=pf.get("summary") or "",
        parent_type=(pf.get("issuetype") or {}).get("name") or "",
        description=f.get("description") or "",
        created=parse_ts(f.get("created")),
        title=f.get("summary") or "",
        status=(f.get("status") or {}).get("name") or "",
        changes=status_changes(log),
        description_edit_at=description_edits(log),
    )
    for c in j.comments(key):
        at = parse_ts(c.get("created"))
        if at is None:
            continue
        if not is_agent_comment(c):
            t.human_comment_at.append(at)
            continue
        body = c.get("body") or ""
        if parsed := parse_review(body):
            hold = parsed[0] == VERDICT_KO["fix"] and AUTO_HOLD_NOTE in body
            t.reviews.append(ReviewComment(at, parsed[0], parsed[1], hold))
        elif (doc := parse_doc_review(body)) is not None:
            t.docs.append(ReviewComment(at, "", doc))
    t.human_comment_at.sort()
    t.reviews.sort(key=lambda r: r.at)
    t.docs.sort(key=lambda r: r.at)
    runs = (j.prop_get(key) or {}).get("runs")
    t.runs = mark_reruns([r for r in (parse_run(e) for e in (runs if isinstance(runs, list) else [])) if r])
    return t


def build_jql(project: str, since: dt.date) -> str:
    """보고에 필요한 기간을 덮는 ticket 검색 조건.

    JQL의 날짜는 API 계정의 시간대로 해석되므로 한국 시간 since 첫날의 변경이 빠지지 않게 하루 앞선 날부터 찾는다.
    업데이트가 없는 기존 ticket은 updated 조건으로 찾을 수 없으므로 진행 상태(done, Backlog, Deleted가 아닌 상태)인
    ticket도 함께 찾는다.

    Args:
        project: Jira 프로젝트 키.
        since: 읽을 기간의 첫날(한국 시간).

    Returns:
        검색 조건.
    """
    day = since - dt.timedelta(days=1)
    closed = ", ".join(f'"{s}"' for s in CLOSED_STATUSES)
    return (
        f"project = {project} AND issuetype in ({', '.join(TARGET_TYPES)}) "
        f'AND (updated >= "{day.isoformat()}" OR status not in ({closed}))'
    )


def collect(j: JiraReader, jql: str) -> list[Ticket]:
    """JQL로 찾은 ticket마다 기록을 읽는다. Jira 읽기 메서드만 호출한다.

    ticket 필드(ISSUE_FIELDS)는 검색 결과로 함께 받아 ticket마다 Jira.issue를 부르지 않는다.

    Args:
        j: Jira 읽기 객체.
        jql: 검색 조건.

    Returns:
        ticket 기록 목록.
    """
    return [load_ticket(j, issue) for issue in j.search(jql, ISSUE_FIELDS, limit=SEARCH_LIMIT)]


def fetch_subtasks(j: JiraReader, project: str, since: dt.date, until: dt.date) -> dict[str, list[dt.datetime]]:
    """since부터 until 전까지 만들어진 sub-task를 Jira 검색 한 번으로 읽어 상위 ticket 키별 생성 시각으로 묶는다.

    JQL의 날짜는 API 계정의 시간대로 해석되므로 build_jql처럼 하루 앞선 날부터 찾는다. 기간 판정은 시각으로 다시 한다.

    Args:
        j: Jira 읽기 객체.
        project: Jira 프로젝트 키.
        since: 대상 기간의 첫날(한국 시간).
        until: 대상 기간 다음 날(한국 시간). 이 날 전에 만들어진 sub-task만 찾는다.

    Returns:
        상위 ticket 키 -> sub-task 생성 시각 목록.
    """
    day = since - dt.timedelta(days=1)
    # JQL 날짜는 API 계정의 시간대라 앞뒤로 하루씩 넓히고, 기간은 _in으로 다시 거른다
    end = until + dt.timedelta(days=1)
    jql = (
        f"project = {project} AND issuetype in subTaskIssueTypes() "
        f'AND created >= "{day.isoformat()}" AND created < "{end.isoformat()}"'
    )
    out: dict[str, list[dt.datetime]] = {}
    for issue in j.search(jql, SUBTASK_FIELDS, limit=SEARCH_LIMIT):
        f = issue.get("fields") or {}
        parent = (f.get("parent") or {}).get("key") or ""
        at = parse_ts(f.get("created"))
        if parent and at is not None:
            out.setdefault(parent, []).append(at)
    return out


def search_prs(org: str, since: dt.date, until: dt.date) -> list[dict] | None:
    """GitHub organization에서 since–until(양 끝 포함)에 만들어진 PR을 gh search prs 한 번으로 찾는다.

    scripts/src/jira/collect.py의 gh와 같이 환경 변수 GH_TOKEN이 없거나 명령이 실패하면 None을 돌려준다.

    Args:
        org: GitHub organization(환경 변수 GH_ORG).
        since: 첫날.
        until: 끝날.

    Returns:
        PR 목록(항목마다 title, createdAt). 검색하지 못하면 None.
    """
    if not org or not os.environ.get("GH_TOKEN"):
        return None
    args = ["gh", "search", "prs", "--owner", org, "--created", f"{since.isoformat()}..{until.isoformat()}"]
    args += ["--json", "title,createdAt", "--limit", str(PR_LIMIT)]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[report] gh search prs error: {e}", file=sys.stderr)
        return None
    if res.returncode != 0:
        print(f"[report] gh search prs failed: {res.stderr.strip()[:200]}", file=sys.stderr)
        return None
    try:
        data = json.loads(res.stdout) if res.stdout.strip() else []
    except json.JSONDecodeError:
        print("[report] gh search prs: JSON이 아닌 출력", file=sys.stderr)
        return None
    return data if isinstance(data, list) else None


def pr_times(prs: list[dict], project: str) -> dict[str, list[dt.datetime]]:
    """PR 제목의 ticket 키(<프로젝트 키>-숫자)별로 PR 생성 시각을 묶는다.

    Args:
        prs: search_prs의 결과.
        project: Jira 프로젝트 키.

    Returns:
        ticket 키 -> PR 생성 시각 목록. 한 PR 제목에 키가 여럿이면 키마다 넣는다.
    """
    pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(project)}-\d+")
    out: dict[str, list[dt.datetime]] = {}
    for pr in prs:
        at = parse_ts(str(pr.get("createdAt") or "")) if isinstance(pr, dict) else None
        if at is None:
            continue
        for key in sorted(set(pat.findall(str(pr.get("title") or "")))):
            out.setdefault(key, []).append(at)
    return out


def attach_updates(
    tickets: list[Ticket], subtasks: dict[str, list[dt.datetime]], prs: dict[str, list[dt.datetime]]
) -> None:
    """sub-task 생성 시각과 PR 생성 시각을 ticket 키로 붙인다.

    Args:
        tickets: ticket 기록. subtask_at, pr_at을 바꾼다.
        subtasks: fetch_subtasks의 결과.
        prs: pr_times의 결과. PR 검색이 실패했으면 빈 dict.
    """
    for t in tickets:
        t.subtask_at = sorted(subtasks.get(t.key, []))
        t.pr_at = sorted(prs.get(t.key, []))
