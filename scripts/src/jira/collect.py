"""Jira와 GitHub에서 입력을 모아 ctx/ 아래에 파일로 저장하는 함수(1단계 수집, scripts/collect.py가 부름)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from src.constant.jira import TARGET_TYPES, TYPE_ALIAS
from src.jira.client import Jira
from src.jira.wiki import is_agent_comment, section_body, sections

CTX = Path("ctx")
ISSUE_FIELDS = [
    "summary",
    "status",
    "assignee",
    "reporter",
    "created",
    "updated",
    "labels",
    "issuetype",
    "parent",
    "subtasks",
    "description",
    "attachment",
]
# 사람 구역(해시와 검사에 쓰는 구역). agent 구역은 제외한다.
HUMAN_SECTIONS = {
    "Task": ["진행 배경", "예상 산출물"],
    "Bug": ["기본 정보", "문제", "개선", "첨부"],
    "Issue": ["이슈 유형", "이슈 내용"],
}


def canon_type(name: str) -> str:
    """work type 이름을 Task, Bug, Issue 중 하나로 바꾼다. 별칭이 아니면 그대로 돌려준다."""
    return TYPE_ALIAS.get((name or "").strip().lower(), TYPE_ALIAS.get(name, name))


def now_iso() -> str:
    """지금 시각을 초 단위 ISO 형식(로컬 시간대)으로 돌려준다."""
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def write(path: Path, data: dict | list | str | None) -> None:
    """파일을 쓴다. dict나 list는 JSON으로, 그 밖에는 문자열로 쓰고, 상위 폴더가 없으면 만든다.

    Args:
        path: 쓸 파일 경로.
        data: 쓸 내용. None이면 빈 파일을 쓴다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(data or "", encoding="utf-8")


def gh(*args: str) -> list | dict | None:
    """gh CLI 호출. 토큰이 없거나 실패하면 None."""
    if not os.environ.get("GH_TOKEN"):
        return None
    try:
        res = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            print(f"[collect] gh {' '.join(args[:3])} failed: {res.stderr.strip()[:200]}", file=sys.stderr)
            return None
        return json.loads(res.stdout) if res.stdout.strip() else []
    except Exception as e:  # noqa: BLE001
        print(f"[collect] gh error: {e}", file=sys.stderr)
        return None


def key_pattern(key_re: str) -> re.Pattern:
    r"""task 키 일치 정규식을 만든다.

    INNO-1이 INNO-17에 걸리지 않게 앞뒤 경계를 본다.
    \b는 한글 앞에서 끊기지 않으므로('INNO-17에서') 영숫자 lookaround를 쓴다.

    Args:
        key_re: ticket 키의 정규식.

    Returns:
        앞뒤 경계를 붙인 정규식.
    """
    return re.compile(rf"(?<![A-Za-z0-9]){key_re}(?![0-9])")


def fetch_prs(key: str, org: str) -> list[dict]:
    """ticket 키가 제목, 브랜치, 본문에 있는 PR을 GitHub 조직에서 찾는다.

    Args:
        key: ticket 키.
        org: GitHub 조직 이름.

    Returns:
        PR 목록. 항목마다 title, url, repo, state, branch, mergedAt, matched_in, body_head를 담는다.
    """
    found = (
        gh("search", "prs", key, "--owner", org, "--json", "title,url,state,repository,number", "--limit", "20") or []
    )
    pat = key_pattern(re.escape(key))
    out = []
    for pr in found:
        detail = gh("pr", "view", pr["url"], "--json", "title,state,headRefName,mergedAt,body,url") or {}
        title = detail.get("title") or pr.get("title", "")
        branch = detail.get("headRefName", "")
        body = detail.get("body") or ""
        where = [w for w, txt in (("title", title), ("branch", branch), ("body", body)) if pat.search(txt)]
        if not where:
            continue
        out.append(
            {
                "title": title,
                "url": pr["url"],
                "repo": (pr.get("repository") or {}).get("nameWithOwner", ""),
                "state": detail.get("state") or pr.get("state"),
                "branch": branch,
                "mergedAt": detail.get("mergedAt"),
                "matched_in": where,
                "body_head": "\n".join(body.splitlines()[:40]),
            }
        )
    return out


def norm_comments(raw: list[dict], site: str, key: str) -> list[dict]:
    """Jira comment 응답을 저장 형식으로 바꾼다.

    Args:
        raw: Jira comment 응답 목록.
        site: Jira 사이트 주소. 비면 comment URL을 비운다.
        key: ticket 키.

    Returns:
        comment 목록. 항목마다 id, created, updated, author, kind(agent 또는 human), body, url을 담는다.
    """
    out = []
    for c in raw:
        a = c.get("author") or {}
        out.append(
            {
                "id": c.get("id"),
                "created": c.get("created"),
                "updated": c.get("updated"),
                "author": {
                    "accountId": a.get("accountId"),
                    "displayName": a.get("displayName"),
                    "accountType": a.get("accountType"),
                },
                "kind": "agent" if is_agent_comment(c) else "human",
                "body": c.get("body") or "",
                "url": f"{site}/browse/{key}?focusedCommentId={c.get('id')}" if site else "",
            }
        )
    return out


def norm_changelog(raw: list[dict]) -> list[dict]:
    """변경 이력 응답에서 상태, description, 담당자, 첨부 변경만 골라 시간순으로 정리한다.

    Args:
        raw: Jira 변경 이력 응답 목록.

    Returns:
        변경 목록. 항목마다 created, author, field, from, to를 담는다.
    """
    out = []
    for h in raw:
        a = h.get("author") or {}
        for it in h.get("items", []):
            if it.get("field") in ("status", "description", "assignee", "Attachment"):
                out.append(
                    {
                        "created": h.get("created"),
                        "author": {
                            "accountId": a.get("accountId"),
                            "displayName": a.get("displayName"),
                            "accountType": a.get("accountType"),
                        },
                        "field": it.get("field"),
                        "from": it.get("fromString"),
                        "to": it.get("toString"),
                    }
                )
    out.sort(key=lambda x: x["created"] or "")
    return out


def human_input_hash(
    itype: str, desc: str, comments: list[dict], subtasks: list[dict], prs: list[dict], changelog: list[dict]
) -> str:
    """사람이 쓴 입력의 해시를 만든다. 사람 구역, 사람 comment, sub-task, PR, 상태 변경이 바뀌면 값이 바뀐다.

    Args:
        itype: work type(Task, Bug, Issue).
        desc: description(wiki markup).
        comments: norm_comments의 결과.
        subtasks: sub-task 목록. 항목마다 key, summary, status, description, comments, prs를 담는다.
        prs: fetch_prs의 결과.
        changelog: norm_changelog의 결과.

    Returns:
        "sha256:"로 시작하는 해시 문자열.
    """
    h = hashlib.sha256()
    for sec in HUMAN_SECTIONS.get(itype, []):
        h.update(section_body(desc, sec).encode())
    for c in comments:
        if c["kind"] == "human":
            h.update(f"{c['id']}|{c['updated']}|{c['body']}".encode())
    for s in subtasks:
        h.update(
            json.dumps(
                {k: s.get(k) for k in ("key", "summary", "status", "description")}, ensure_ascii=False, sort_keys=True
            ).encode()
        )
        for c in s.get("comments", []):
            if c["kind"] == "human":
                h.update(f"{c['id']}|{c['updated']}".encode())
        for p in s.get("prs", []):
            h.update(f"{p['url']}|{p['state']}|{p['mergedAt']}".encode())
    for p in prs:
        h.update(f"{p['url']}|{p['state']}|{p['mergedAt']}".encode())
    for ch in changelog:
        if ch["field"] == "status":
            h.update(f"{ch['created']}|{ch['to']}".encode())
    return "sha256:" + h.hexdigest()


def collect_issue(j: Jira, key: str, site: str, org: str, mode: str) -> dict | None:
    """task 하나의 입력을 ctx/<KEY>/ 아래에 저장한다. 사람 입력이 지난 실행과 같아도 건너뛰지 않는다.

    Returns:
        {"key": 키}. work type이 대상이 아니면 None.
    """
    fields = ISSUE_FIELDS + ([j.tldr_field] if j.tldr_field else [])
    data = j.issue(key, fields)
    f = data.get("fields", {})
    itype = canon_type((f.get("issuetype") or {}).get("name", ""))
    if itype not in TARGET_TYPES or (f.get("issuetype") or {}).get("subtask"):
        print(f"[collect] {key}: work type '{itype}'은 대상이 아님. 건너뜀")
        return None
    desc = f.get("description") or ""
    comments = norm_comments(j.comments(key), site, key)
    changelog = norm_changelog(j.changelog(key))
    subtasks = []
    for st in f.get("subtasks", []) or []:
        sk = st.get("key")
        sd = j.issue(sk, ["summary", "status", "description", "updated", "assignee"])
        sf = sd.get("fields", {})
        subtasks.append(
            {
                "key": sk,
                "summary": sf.get("summary"),
                "status": (sf.get("status") or {}).get("name"),
                "updated": sf.get("updated"),
                "description": sf.get("description") or "",
                "comments": norm_comments(j.comments(sk), site, sk),
                "prs": fetch_prs(sk, org) if org else [],  # sub-task 키로만 연결된 PR
            }
        )
    prs = fetch_prs(key, org) if org else []
    state = j.prop_get(key)
    ihash = human_input_hash(itype, desc, comments, subtasks, prs, changelog)

    d = CTX / key
    if d.exists():
        shutil.rmtree(d)
    write(
        d / "issue.json",
        {
            "key": key,
            "type": itype,
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "statusCategory": ((f.get("status") or {}).get("statusCategory") or {}).get("key"),
            "assignee": {
                "accountId": (f.get("assignee") or {}).get("accountId"),
                "displayName": (f.get("assignee") or {}).get("displayName"),
            },
            "reporter": {
                "accountId": (f.get("reporter") or {}).get("accountId"),
                "displayName": (f.get("reporter") or {}).get("displayName"),
            },
            "created": f.get("created"),
            "updated": f.get("updated"),
            "labels": f.get("labels", []),
            "attachments": [
                {"filename": a.get("filename"), "created": a.get("created"), "mimeType": a.get("mimeType")}
                for a in (f.get("attachment") or [])
            ],
            "subtaskKeys": [s["key"] for s in subtasks],
            "url": f"{site}/browse/{key}" if site else "",
            "sectionTitles": [t for t, *_ in sections(desc)],
        },
    )
    write(d / "description.wiki", desc)
    write(d / "tldr.txt", (f.get(j.tldr_field) or "") if j.tldr_field else "")
    write(d / "comments.json", comments)
    write(d / "changelog.json", changelog)
    for s in subtasks:
        write(d / "subtasks" / f"{s['key']}.json", s)
    write(d / "prs.json", prs)
    write(d / "state.json", state)
    write(d / "meta.json", {"mode": mode, "inputHash": ihash, "collectedAt": now_iso(), "site": site})
    sub_prs = sum(len(s["prs"]) for s in subtasks)
    print(
        f"[collect] {key}: 수집 완료 (comment {len(comments)}, sub-task {len(subtasks)}, PR {len(prs)}, sub-task PR {sub_prs})"
    )
    return {"key": key}


def collect_scan(j: Jira, project: str, site: str) -> None:
    """누락 검사(stop 사유 comment, 활동 없음)용 요약. Done/Deleted가 아닌 모든 최상위 task.

    src/jira/precheck/run.py의 run_scan이 읽는다. 템플릿 누락은 Jira 전환 검증이 막으므로 description 구역과 첨부 수는 모으지 않는다.
    """
    types = ", ".join(TARGET_TYPES)
    jql = f"project = {project} AND issuetype in ({types}) AND statusCategory != Done AND status != Deleted ORDER BY created ASC"
    fields = ["summary", "status", "issuetype", "created", "updated", "assignee", "comment"]
    for it in j.search(jql, fields):
        key = it["key"]
        f = it.get("fields", {})
        itype = canon_type((f.get("issuetype") or {}).get("name", ""))
        raw_comments = (f.get("comment") or {}).get("comments", [])
        comments = norm_comments(raw_comments, site, key)
        changelog = norm_changelog(j.changelog(key))
        write(
            CTX / "_scan" / f"{key}.json",
            {
                "key": key,
                "type": itype,
                "status": (f.get("status") or {}).get("name"),
                "summary": f.get("summary"),
                "url": f"{site}/browse/{key}" if site else "",
                "created": f.get("created"),
                "updated": f.get("updated"),
                "assignee": {
                    "accountId": (f.get("assignee") or {}).get("accountId"),
                    "displayName": (f.get("assignee") or {}).get("displayName"),
                },
                # body: 본문 앞 100자. 늦게 남긴 stop 사유("stop 사유:"로 시작하는 comment)를 precheck가 찾는 데 쓴다
                "humanComments": [
                    {"created": c["created"], "url": c["url"], "body": c["body"][:100]}
                    for c in comments
                    if c["kind"] == "human"
                ],
                "statusChanges": [c for c in changelog if c["field"] == "status"],
            },
        )
