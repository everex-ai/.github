"""Slack 알림 본문 조립과 전송(검수, 실패, 주간 점검, 팀장 확인 결과, 토큰(인증 키) 만료)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.request
from pathlib import Path

from src.common.github import run_url
from src.common.io import load_json
from src.constant.check import SENTENCE_DETAIL_CHECKS, VERDICT_KO
from src.constant.slack import (
    ALERT_LIMIT,
    ALERT_SECTIONS,
    LEAD_ALERTS,
    SLACK_CLOSING,
    SLACK_INDENT,
    TOKEN_EXPIRY_DAYS,
    TOKEN_RENEWAL,
)
from src.constant.user import SLACK_USERS
from src.jira.comment import deliverables_summary
from src.jira.verdict import check_label, last_check, owner_fails, owner_reason
from src.jira.wiki import LIST_MARK_RE, WIKI_COLOR_RE, WIKI_LINK_PARTS_RE


def slack_post(text: str, webhook_env: str = "SLACK_WEBHOOK_URL") -> str:
    """Slack Incoming Webhook 전송. 나중에 봇 토큰으로 바꾸려면 이 함수만 교체한다.

    Args:
        text: Slack 본문.
        webhook_env: webhook URL을 담은 환경 변수 이름. 연동 채널은 SLACK_WEBHOOK_URL, 팀장 채널은 SLACK_LEAD_WEBHOOK_URL이다.

    Returns:
        전송 결과 문구. 환경 변수 값이 없으면 "<변수 이름> 없음. 전송 건너뜀"이다.
    """
    url = os.environ.get(webhook_env, "")
    if not url:
        return f"{webhook_env} 없음. 전송 건너뜀"
    body = json.dumps({"text": text}).encode()
    try:
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    except ValueError:  # URL 형식 오류. public 로그에 secret 값이 남지 않게 값은 적지 않는다
        return f"전송 실패: {webhook_env} 값이 URL 형식이 아님"
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "전송 완료" if r.status == 200 else f"전송 응답 {r.status}"
    except OSError as e:  # HTTPError, URLError, TimeoutError는 모두 OSError의 하위 클래스
        return f"전송 실패: {e}"


def load_slack_users() -> tuple[dict[str, str], str | None]:
    """Slack 멘션 대응표(Jira accountId -> Slack 멤버 ID)를 읽는다.

    환경 변수 SLACK_USERS_JSON(GitHub Actions secret)이 비어 있지 않으면 그 값을 JSON으로 읽고, 비어 있거나 없으면
    config/slack-users.json(SLACK_USERS)을 읽는다. 키가 "_"로 시작하는 항목(설명)과 값이 문자열이 아닌 항목은 뺀다.
    secret 값은 로그와 Actions Summary에 나가지 않게 경고 문구에도 넣지 않는다.

    Returns:
        (대응표, 경고 문구). SLACK_USERS_JSON이 JSON 객체가 아니면 빈 대응표와 형식 오류 경고를 돌려준다.
        그 밖에 대응표가 비면(파일이 없거나 설명만 있을 때 포함) 빈 대응표 경고를, 비지 않으면 경고 자리에 None을 돌려준다.
    """
    raw = os.environ.get("SLACK_USERS_JSON", "").strip()
    if raw:
        try:
            users = json.loads(raw)
        except json.JSONDecodeError:
            users = None
        if not isinstance(users, dict):
            return {}, "SLACK_USERS_JSON 형식이 틀려 Slack 멘션 없이 이름으로 보냄"
    else:
        users = load_json(SLACK_USERS, {})
        if not isinstance(users, dict):  # 파일이 없거나 읽을 수 없으면 빈 대응표로 간주한다
            users = {}
    users = {k: v for k, v in users.items() if not k.startswith("_") and isinstance(v, str)}
    return users, (None if users else "Slack 멘션 대응표가 비어 멘션 없이 이름으로 보냄")


def slack_mention(assignee: dict | None, users: dict) -> str:
    """Slack 멤버 ID가 있으면 멘션으로, 없으면 이름으로. 담당자가 없으면 '담당자 미지정'."""
    a = assignee or {}
    uid = users.get(a.get("accountId") or "")
    if uid:
        return f"<@{uid}>"
    return a.get("displayName") or "담당자 미지정"


def wiki_to_slack(text: str) -> str:
    """CI agent나 사람이 작성한 Jira wiki 문장을 Slack 꼴로 바꾼다.

    Slack이 제어 문자로 읽는 &, <, >를 먼저 &amp;, &lt;, &gt;로 바꾼 뒤, 링크 [제목|URL]을 <URL|제목>으로 바꾸고
    색 표식 {color:…}와 {color}를 뗀다.

    Args:
        text: Jira wiki markup 문장.

    Returns:
        Slack 메시지 문장.
    """
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return WIKI_COLOR_RE.sub("", WIKI_LINK_PARTS_RE.sub(r"<\2|\1>", escaped))


def failure_slack_text(mode: str, failures: list[dict], users: dict[str, str], lead: str) -> str:
    """한 실행에서 반영하지 못한 Task를 알리는 Slack 실패 알림 본문.

    첫 줄에 실행 모드, 실패 건수, 팀장 멘션을 적고, Task마다 한 줄(링크, 키, 제목, 원인)을 적는다.
    run_url()이 있으면 마지막 줄에 GitHub Actions 실행 링크를 적는다. 제목과 원인은 wiki_to_slack으로 바꿔 적는다.

    Args:
        mode: 실행 모드(review: 검수, digest: F3 정리).
        failures: 실패한 Task 목록. 항목은 key, title, url, reason을 가진다. url이 비어 있으면 링크 없이 적는다.
        users: Jira accountId -> Slack 멤버 ID 대응표.
        lead: 팀장 accountId. 비어 있으면 "팀장 미지정", 대응표에 없으면 "팀장(Slack 멘션 없음)"을 적는다.

    Returns:
        Slack 메시지 본문.
    """
    if not lead:
        mention = "팀장 미지정"
    elif users.get(lead):
        mention = f"<@{users[lead]}>"
    else:
        mention = "팀장(Slack 멘션 없음)"
    mode_ko = "검수 모드" if mode == "review" else "정리 모드"
    lines = [f"[jira-doc 실패] {mode_ko} ticket {len(failures)}건 처리 실패 {mention}"]
    for f in failures:
        name = wiki_to_slack(f"{f['key']} {f.get('title') or ''}".strip())
        head = f"<{f['url']}|{name}>" if f.get("url") else name
        lines.append(f"• {head}: {wiki_to_slack(f['reason'])}")
    if url := run_url():
        lines.append(f"GitHub Actions 실행: <{url}|실행 로그>")
    return "\n".join(lines)


def review_slack_reasons(
    v: str,
    itype: str,
    expected: list[dict],
    items: list[dict],
    extra: list[dict],
    checks: list[dict],
    changes: list[str],
    summary: str,
) -> list[str]:
    """Slack 검수 알림의 이유 묶음에 적을 항목.

    보류: 사유 없는 미달성 항목, 실패한 담당자 검사(OWNER_FIXABLE_CHECKS), 검수 결과를 바꾼 이유.
    담당자 검사는 "<검사 이름>: <detail>"로 적고, detail이 그 자체로 이유 문장인 검사(SENTENCE_DETAIL_CHECKS)는
    detail만 적는다.
    검토 요청: 담당자가 남긴 미달성 사유, 검수 결과를 바꾼 이유. 통과: 달성 요약 줄(Task)과 결과 요약 줄.
    위 항목이 비면 결과 요약 줄을 적는다. 검토 요청과 통과에서 R4의 사유가 늦게 기록되었으면(late) 끝에 R4 줄을
    붙여 팀장이 확인하게 한다.

    Args:
        v: 최종 검수 결과 코드값(pass, fix, escalate).
        itype: work type(Task, Bug, Issue).
        expected: precheck.json의 예상 산출물 목록.
        items: verdict.json의 items.
        extra: verdict.json의 extra(초과 달성 목록).
        checks: 검수 comment의 검사 표에 들어가는 검사 결과 목록.
        changes: 검수 결과를 바꾼 이유 목록(상태 처리 문구는 빼고).
        summary: 결과 요약(comment.wiki에서 판정 줄과 검사 표를 뗀 것, wiki markup).

    Returns:
        이유 항목 목록(Slack 꼴, 글머리표 없이).
    """
    texts = {str(e["id"]): e["text"] for e in expected}
    missed = [it for it in items if not it.get("done")]
    names = [
        f'예상 산출물 {it["id"]}번 "{wiki_to_slack(texts.get(str(it["id"]), it.get("result", "")))}"' for it in missed
    ]
    summary_lines = [wiki_to_slack(LIST_MARK_RE.sub("", s.strip())) for s in summary.splitlines() if s.strip()]
    out: list[str] = []
    if v == "fix":
        out += [f"사유 없는 미달성: {n}" for n, it in zip(names, missed) if not owner_reason(it)]
        out += [
            wiki_to_slack(c.get("detail") or "")
            if c["id"] in SENTENCE_DETAIL_CHECKS
            else f"{check_label(c['id'])}: {wiki_to_slack(c.get('detail') or '')}"
            for c in owner_fails(checks)
        ]
        out += [wiki_to_slack(c) for c in changes]
    elif v == "escalate":
        out += [f"{n}: {wiki_to_slack(owner_reason(it))}" for n, it in zip(names, missed) if owner_reason(it)]
        out += [wiki_to_slack(c) for c in changes]
    else:
        if itype == "Task" and expected:
            out.append(deliverables_summary(expected, items, extra))
        out += summary_lines
    out = out or summary_lines
    r4 = last_check(checks, "R4")
    if v in ("pass", "escalate") and r4.get("late"):
        out.append(f"{check_label('R4')}: {wiki_to_slack(r4.get('detail') or '')}")
    return out


def review_slack_text(
    v: str,
    key: str,
    title: str,
    url: str,
    mention: str,
    state_line: str,
    reasons: list[str],
    requests: list[str],
    comment_link: str,
) -> str:
    """검수 모드의 Slack 알림 본문.

    첫 줄에 받는 사람 멘션, task 키, 검수 결과, 상태 처리를 적고, 둘째 줄에 task 링크를 적는다.
    보류에만 요청 묶음을 붙이고, 검토 요청과 통과에는 팀장이 직접 전환하도록 맺음 줄을 붙인다.
    이유와 요청 항목은 묶음 제목 아래로 SLACK_INDENT만큼 들여 쓴다.
    task 제목과 요청 문장은 wiki_to_slack으로 바꿔 적는다(&, <, > escape 포함).

    Args:
        v: 최종 검수 결과 코드값(pass, fix, escalate).
        key: task 키.
        title: task 제목(issue.json의 summary).
        url: task 링크. 비어 있으면 링크 줄을 적지 않는다.
        mention: 받는 사람(Slack 멘션 또는 이름).
        state_line: 상태 처리를 적은 짧은 문구.
        reasons: 이유 묶음 항목(review_slack_reasons의 결과).
        requests: 담당자 요청 목록(wiki markup). 보류에만 적는다.
        comment_link: 검수 comment 링크. 비어 있으면 근거 줄을 적지 않는다.

    Returns:
        Slack 메시지 본문.
    """
    label = VERDICT_KO[v]
    lines = [f"{mention} [{key}] 검수 결과: {label}. {state_line}"]
    if url:
        lines.append(f"<{url}|{wiki_to_slack(f'{key} {title}'.strip())}>")
    if reasons:
        lines += [f"{label} 이유"] + [f"{SLACK_INDENT}• {r}" for r in reasons]
    if v == "fix" and requests:
        lines += ["요청"] + [f"{SLACK_INDENT}{i}. {wiki_to_slack(r)}" for i, r in enumerate(requests, 1)]
    if v in SLACK_CLOSING:
        lines.append(SLACK_CLOSING[v])
    if comment_link:
        lines.append(f"근거: <{comment_link}|검수 comment>")
    return "\n".join(lines)


def weekly_lines(scan: Path, users: dict, lead: str = "") -> tuple[list[str], list[str]]:
    """주간 점검 Slack 메시지의 절 줄과 머리줄 멘션 목록을 만든다.

    절 제목은 "*{이름}* ({n}건) — {조치 문구}"이고, 절은 ALERT_SECTIONS의 순서로 나간다.
    R4, A1 항목은 담당자를 멘션하고, A3 항목은 담당자의 Jira 표시 이름과 팀장 멘션을 적는다.
    멘션 목록에는 Slack 멤버 ID로 바꾼 멘션만 중복 없이 넣는다.

    Args:
        scan: 주간 점검 데이터 폴더(ctx/_scan). *.alerts.json을 읽는다.
        users: Jira accountId -> Slack 멤버 ID 대응표.
        lead: 팀장 accountId. A3 항목에 비어 있으면 "팀장 미지정"을, 대응표에 있으면 "팀장 <@멤버 ID>"를,
            대응표에 없으면 "팀장(Slack 멘션 없음)"을 적는다.

    Returns:
        (Slack 본문 줄, 멘션 목록). 알림이 없으면 본문은 빈 목록.
    """
    rows: dict[str, list[dict]] = {cid: [] for cid, _, _ in ALERT_SECTIONS}
    mentions: list[str] = []
    lead_uid = users.get(lead) if lead else None
    lead_who = f"<@{lead_uid}>" if lead_uid else ""
    lead_text = "팀장 미지정" if not lead else (f"팀장 {lead_who}" if lead_uid else "팀장(Slack 멘션 없음)")
    for p in sorted(scan.glob("*.alerts.json")) if scan.exists() else []:
        a = load_json(p, {})
        for al in a.get("alerts", []):
            if al.get("check") in rows:
                rows[al["check"]].append({**a, "detail": al.get("detail") or al.get("message", "")})
    lines: list[str] = []
    for cid, title, action in ALERT_SECTIONS:
        items = rows[cid]
        if not items:
            continue
        lines += ["", f"*{title}* ({len(items)}건) — {action}"]
        for it in items[:ALERT_LIMIT]:
            if cid in LEAD_ALERTS:  # 팀장이 할 조치: 담당자는 이름만 적고 팀장을 멘션한다
                who = f"{(it.get('assignee') or {}).get('displayName') or '담당자 미지정'}, {lead_text}"
                mention = lead_who
            else:
                who = mention = slack_mention(it.get("assignee"), users)
            if mention.startswith("<@") and mention not in mentions:
                mentions.append(mention)
            lines.append(f"{SLACK_INDENT}• {it['key']} {it.get('summary') or ''} — 담당 {who}, {it['detail']}")
            if it.get("url"):
                lines.append(f"{SLACK_INDENT}  {it['url']}")
        if len(items) > ALERT_LIMIT:
            lines.append(f"{SLACK_INDENT}• 외 {len(items) - ALERT_LIMIT}건")
    return lines, mentions


def decision_slack_text(key: str, title: str, url: str, mention: str, decision: str, status: str) -> str:
    """팀장 확인 결과 알림 본문. 담당자에게 팀장이 바꾼 상태 변경 결과만 알린다.

    Args:
        key: ticket 키.
        title: ticket 제목.
        url: ticket 링크. 비어 있으면 링크 줄을 적지 않는다.
        mention: 받는 사람(담당자의 Slack 멘션 또는 이름).
        decision: 팀장 확인 결과(통과, 보류).
        status: 전환 뒤 Jira 상태 이름(Done, In Progress).

    Returns:
        Slack 메시지 본문.
    """
    lines = [f"{mention} [{key}] 팀장 확인 결과: {decision}", f"상태 변경: ready-to-done → {status}"]
    if url:
        lines.append(f"<{url}|{wiki_to_slack(f'{key} {title}'.strip())}>")
    return "\n".join(lines)


def token_expiry_text(raw: str, today: dt.date) -> tuple[str, str]:
    """환경 변수 TOKEN_EXPIRY로 토큰(인증 키) 만료 알림 본문을 만든다.

    Args:
        raw: TOKEN_EXPIRY 값(JSON 객체: 이름 -> "YYYY-MM-DD").
        today: 오늘 날짜(한국 시간).

    Returns:
        (Slack 본문, 경고). 값이 없거나 형식이 틀리면 본문은 빈 문자열이고 경고에 이유를 적는다.
        날짜 형식이 틀린 항목만 경고에 적고 건너뛰며 나머지는 계속 처리한다(경고는 "; "로 이음).
        남은 날이 TOKEN_EXPIRY_DAYS 이하인 항목이 없으면 본문은 빈 문자열이다.
    """
    if not raw.strip():
        return "", "TOKEN_EXPIRY 없음. 토큰(인증 키) 만료 알림을 보내지 않음"
    try:
        data = json.loads(raw)
    except ValueError:
        return "", "TOKEN_EXPIRY가 JSON이 아님. 토큰(인증 키) 만료 알림을 보내지 않음"
    if not isinstance(data, dict):
        return "", "TOKEN_EXPIRY가 JSON 객체가 아님. 토큰(인증 키) 만료 알림을 보내지 않음"
    due: list[tuple[dt.date, str]] = []
    bad: list[str] = []
    for name, value in data.items():
        try:
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError(value)
            due.append((dt.date.fromisoformat(value), name))
        except ValueError:
            bad.append(f"TOKEN_EXPIRY의 {name} 날짜 형식(YYYY-MM-DD)이 틀려 건너뜀")
    lines = ["[jira-doc 토큰(인증 키) 만료 알림]"]
    sub = f"{SLACK_INDENT}◦ "
    for day, name in sorted(due):
        left = (day - today).days
        if left > TOKEN_EXPIRY_DAYS:
            continue
        remain = "만료됨" if left < 0 else "오늘 만료" if left == 0 else f"남은 날 {left}일"
        lines += [f"• {name}: {remain}", f"{sub}만료일 {day.isoformat()}({today.isoformat()} 기준)"]
        if name in TOKEN_RENEWAL:
            issue_at, replace_at = TOKEN_RENEWAL[name]
            lines += [f"{sub}갱신: 팀장이 {issue_at}", f"{sub}교체: 팀장이 {replace_at}"]
        else:
            lines.append(f"{sub}갱신: 갱신할 곳 미등록")
    warning = "; ".join(bad)
    if len(lines) == 1:
        return "", warning
    lines.append("팀장이 갱신 뒤 variable `TOKEN_EXPIRY`의 만료일도 고쳐야 함")
    return "\n".join(lines), warning
