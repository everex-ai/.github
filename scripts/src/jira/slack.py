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
from src.constant.check import CHECK_NAMES, VERDICT_KO
from src.constant.slack import (
    ALERT_LIMIT,
    ALERT_SECTIONS,
    LEAD_ALERTS,
    LEAD_NAME_DEFAULT,
    MODE_KO,
    SLACK_AGENT_POINTS,
    SLACK_CHECK_FIX,
    SLACK_INDENT,
    SLACK_LATE_STOP,
    SLACK_MAX_POINTS,
    TOKEN_EXPIRY_DAYS,
    TOKEN_RENEWAL,
)
from src.constant.user import SLACK_USERS
from src.jira.comment import deliverables_summary
from src.jira.verdict import last_check, owner_fails
from src.jira.wiki import WIKI_COLOR_RE, WIKI_LINK_PARTS_RE


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


def lead_mention(users: dict[str, str], lead: str) -> str:
    """실패 알림 첫 줄에 적는 팀장 멘션. lead가 비면 "팀장 미지정", 대응표에 없으면 "팀장(Slack 멘션 없음)"."""
    if not lead:
        return "팀장 미지정"
    if users.get(lead):
        return f"<@{users[lead]}>"
    return "팀장(Slack 멘션 없음)"


def failure_slack_text(mode: str, failures: list[dict], users: dict[str, str], lead: str) -> str:
    """한 실행에서 반영하지 못한 Task를 알리는 Slack 실패 알림 본문.

    첫 줄에 실행 모드, 실패 건수, 팀장 멘션을 적고, Task마다 한 줄(링크, 키, 제목, 원인)을 적는다.
    run_url()이 있으면 마지막 줄에 GitHub Actions 실행 링크를 적는다. 제목과 원인은 wiki_to_slack으로 바꿔 적는다.

    Args:
        mode: 실행 모드(MODE_KO의 키: review, digest, alerts, report, decision). MODE_KO에 없으면 값을 그대로 적는다.
        failures: 실패한 Task 목록. 항목은 key, title, url, reason을 가진다. url이 비어 있으면 링크 없이 적는다.
        users: Jira accountId -> Slack 멤버 ID 대응표.
        lead: 팀장 accountId. 비어 있으면 "팀장 미지정", 대응표에 없으면 "팀장(Slack 멘션 없음)"을 적는다.

    Returns:
        Slack 메시지 본문.
    """
    mode_ko = MODE_KO.get(mode, mode)
    lines = [f"[jira-doc 실패] {mode_ko} ticket {len(failures)}건 처리 실패 {lead_mention(users, lead)}"]
    for f in failures:
        name = wiki_to_slack(f"{f['key']} {f.get('title') or ''}".strip())
        head = f"<{f['url']}|{name}>" if f.get("url") else name
        lines.append(f"• {head}: {wiki_to_slack(f['reason'])}")
    if url := run_url():
        lines.append(f"GitHub Actions 실행: <{url}|실행 로그>")
    return "\n".join(lines)


def run_failure_slack_text(mode: str, issue: str, error: dict | None, users: dict[str, str], lead: str) -> str:
    """검수·정리 모드의 ticket 실패 알림 밖에서 실행이 실패했을 때 연동 채널로 보내는 Slack 본문.

    첫 줄에 실행 모드와 팀장 멘션을 적고, ticket 키(있으면), 실패 단계와 원인을 글머리표로 적는다.
    실패 기록이 없으면 원인 줄에 "기록 없음. 실행 로그 확인 필요"를 적는다.
    run_url()이 있으면 마지막 줄에 GitHub Actions 실행 링크를 적는다. 원인은 wiki_to_slack으로 바꿔 적는다.

    Args:
        mode: 실패한 실행 모드. MODE_KO에 없으면 값을 그대로 적고, 빈 문자열이면 "?"를 적는다.
        issue: 대상 ticket 키. 비어 있으면 ticket 줄을 적지 않는다.
        error: out/_run_error.json의 내용(step, error). 없으면 None.
        users: Jira accountId -> Slack 멤버 ID 대응표.
        lead: 팀장 accountId. 비어 있으면 "팀장 미지정", 대응표에 없으면 "팀장(Slack 멘션 없음)"을 적는다.

    Returns:
        Slack 메시지 본문.
    """
    mode_ko = MODE_KO.get(mode, mode or "?")
    lines = [f"[jira-doc 실패] {mode_ko} 실행 실패 {lead_mention(users, lead)}"]
    if issue:
        lines.append(f"• ticket: {wiki_to_slack(issue)}")
    if error:
        lines.append(f"• 단계: {wiki_to_slack(str(error.get('step') or '?'))}")
        lines.append(f"• 원인: {wiki_to_slack(str(error.get('error') or '?'))}")
    else:
        lines.append("• 원인: 기록 없음. 실행 로그 확인 필요")
    if url := run_url():
        lines.append(f"GitHub Actions 실행: <{url}|실행 로그>")
    return "\n".join(lines)


def slack_point(text: str) -> str:
    """CI agent가 verdict.slack에 적은 요점을 Slack 한 줄로 바꾼다.

    Slack이 제어 문자로 읽는 &, <, >를 먼저 &amp;, &lt;, &gt;로 바꾼 뒤, wiki 링크 [제목|URL]은 제목 글자만 남기고
    색 표식 {color:…}와 {color}를 뗀다. 줄바꿈은 공백으로 바꾼다.

    Args:
        text: verdict.slack의 항목 하나.

    Returns:
        Slack 한 줄 문장. 비어 있으면 빈 문자열.
    """
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    plain = WIKI_COLOR_RE.sub("", WIKI_LINK_PARTS_RE.sub(r"\1", escaped))
    return " ".join(plain.split())


def review_slack_reasons(
    v: str,
    itype: str,
    expected: list[dict],
    items: list[dict],
    extra: list[dict],
    checks: list[dict],
    change_points: list[str],
    agent_points: list[str],
) -> list[str]:
    """검수 결과 메시지에 적을 요점.

    아래 순서로 모으고, 같은 문구는 한 번만 적으며, 모두 SLACK_MAX_POINTS줄 이내다.
    1. 스크립트가 검수 결과를 바꾼 이유의 짧은 꼴(change_points).
    2. Task이고 예상 산출물이 있으면 달성 요약 줄(deliverables_summary).
    3. CI agent가 적은 요점(agent_points). 남은 줄 수와 SLACK_AGENT_POINTS 중 작은 수까지 적는다.
       비어 있고 검수 결과가 보류이면 실패한 담당자 검사마다 "<검사 이름> 보완 필요"를 대신 적는다.
    4. R4 결과가 늦게 기록(late)이면 SLACK_LATE_STOP. CI agent는 이 문구를 적지 않으므로 1, 2보다 먼저 자리를 남긴다.

    Args:
        v: 최종 검수 결과 코드값(pass, fix, escalate).
        itype: work type(Task, Bug, Issue).
        expected: precheck.json의 예상 산출물 목록.
        items: verdict.json의 items.
        extra: verdict.json의 extra(초과 달성 목록).
        checks: 검수 comment의 검사 표에 들어가는 검사 결과 목록.
        change_points: 검수 결과를 바꾼 이유의 Slack용 짧은 꼴.
        agent_points: verdict.json의 slack. 문자열이 아닌 항목은 건너뛴다.

    Returns:
        요점 목록(Slack 꼴, 글머리표 없이). 최대 SLACK_MAX_POINTS줄.
    """
    head = list(change_points)
    if itype == "Task" and expected:
        head.append(deliverables_summary(expected, items, extra))
    tail = [SLACK_LATE_STOP] if last_check(checks, "R4").get("late") else []
    head = list(dict.fromkeys(head))[: SLACK_MAX_POINTS - len(tail)]  # 늦은 stop 사유 자리는 남긴다
    points = [p for p in (slack_point(a) for a in agent_points if isinstance(a, str)) if p]
    if not points and v == "fix":  # 미달성 수는 달성 요약 줄에 있으므로 실패한 담당자 검사만 적는다
        points = [SLACK_CHECK_FIX.format(name=CHECK_NAMES.get(c["id"], c["id"])) for c in owner_fails(checks)]
    room = min(SLACK_AGENT_POINTS, SLACK_MAX_POINTS - len(head) - len(tail))
    points = [p for p in dict.fromkeys(points) if p not in head and p not in tail][:room]
    return head + points + tail


def review_slack_text(
    v: str,
    key: str,
    title: str,
    url: str,
    mention: str,
    reasons: list[str],
    comment_link: str,
) -> str:
    """검수 모드의 Slack 알림 본문.

    첫 줄에 받는 사람 멘션, 검수 결과, ticket 링크(키와 제목)를 적고, 요점마다 "• " 줄을 적는다.
    comment_link가 있으면 마지막 줄에 검수 comment 링크를 적는다.
    ticket 제목은 wiki_to_slack으로 바꿔 적는다(&, <, > escape 포함).

    Args:
        v: 최종 검수 결과 코드값(pass, fix, escalate).
        key: ticket 키.
        title: ticket 제목(issue.json의 summary).
        url: ticket 링크. 비어 있으면 첫 줄에 링크 없이 "키 제목"을 적는다.
        mention: 받는 사람(Slack 멘션 또는 이름).
        reasons: 요점 목록(review_slack_reasons의 결과).
        comment_link: 검수 comment 링크. 비어 있으면 마지막 줄을 적지 않는다.

    Returns:
        Slack 메시지 본문.
    """
    name = wiki_to_slack(f"{key} {title}".strip())
    head = f"<{url}|{name}>" if url else name
    lines = [f"{mention} [{VERDICT_KO[v]}] {head}"]
    lines += [f"• {r}" for r in reasons]
    if comment_link:
        lines.append(f"<{comment_link}|검수 comment>")
    return "\n".join(lines)


def weekly_lines(scan: Path, users: dict, lead: str = "") -> tuple[list[str], list[str]]:
    """주간 점검 Slack 메시지의 절 줄과 머리줄 멘션 목록을 만든다.

    절 제목은 "*{이름}* {n}건"이고, 조치 문구가 있으면 " — {조치 문구}"를 붙인다. 절은 ALERT_SECTIONS의 순서로 나간다.
    ticket 줄은 "• <URL|키 제목> 담당자 · 경과"이고, URL이 없으면 링크 없이 "키 제목"을 적는다.
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
        lines += ["", f"*{title}* {len(items)}건" + (f" — {action}" if action else "")]
        for it in items[:ALERT_LIMIT]:
            if cid in LEAD_ALERTS:  # 팀장이 할 조치: 담당자는 이름만 적고 팀장을 멘션한다
                who = f"{(it.get('assignee') or {}).get('displayName') or '담당자 미지정'}, {lead_text}"
                mention = lead_who
            else:
                who = mention = slack_mention(it.get("assignee"), users)
            if mention.startswith("<@") and mention not in mentions:
                mentions.append(mention)
            name = wiki_to_slack(f"{it['key']} {it.get('summary') or ''}".strip())
            head = f"<{it['url']}|{name}>" if it.get("url") else name
            lines.append(f"{SLACK_INDENT}• {head} {who} · {it['detail']}")
        if len(items) > ALERT_LIMIT:
            lines.append(f"{SLACK_INDENT}• 외 {len(items) - ALERT_LIMIT}건")
    return lines, mentions


def lead_name() -> str:
    """메시지에 적을 팀장 이름을 환경 변수 JIRA_LEAD_NAME에서 읽는다. 호출할 때마다 읽는다.

    Returns:
        앞뒤 공백을 뗀 JIRA_LEAD_NAME. 없거나 공백뿐이면 LEAD_NAME_DEFAULT.
    """
    return os.environ.get("JIRA_LEAD_NAME", "").strip() or LEAD_NAME_DEFAULT


def decision_slack_text(
    key: str, title: str, url: str, mention: str, decision: str, status: str, lead_name: str = ""
) -> str:
    """팀장 확인 결과 알림 본문. 담당자에게 팀장이 바꾼 상태 변경 결과만 알린다.

    첫 줄에 받는 사람 멘션, 팀장 확인 결과, ticket 링크(키와 제목)를 적고, 둘째 줄에 이동한 상태를 적는다.
    ticket 제목과 팀장 이름은 wiki_to_slack으로 바꿔 적는다(&, <, > escape 포함).
    상태가 Done이면 "Done으로", 그 밖에는 "<상태>로"를 적는다.

    Args:
        key: ticket 키.
        title: ticket 제목.
        url: ticket 링크. 비어 있으면 첫 줄에 링크 없이 "키 제목"을 적는다.
        mention: 받는 사람(담당자의 Slack 멘션 또는 이름).
        decision: 팀장 확인 결과(통과, 보류).
        status: 전환 뒤 Jira 상태 이름(Done, In Progress).
        lead_name: 팀장 표시 이름. 비어 있으면 LEAD_NAME_DEFAULT를 적는다.

    Returns:
        Slack 메시지 본문.
    """
    name = wiki_to_slack(f"{key} {title}".strip())
    head = f"<{url}|{name}>" if url else name
    lead = wiki_to_slack(lead_name or LEAD_NAME_DEFAULT)
    josa = "으로" if status.strip().lower() == "done" else "로"  # "Done으로", "In Progress로"
    return "\n".join([f"{mention} [{lead} 확인: {decision}] {head}", f"• {status}{josa} 이동"])


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
