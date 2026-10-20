"""ticket 하나에 CI agent 출력과 팀장 확인 결과를 반영하고 주간 점검 알림을 보내는 함수(4단계 반영, scripts/apply.py가 부름).

dt.datetime.now를 부르는 함수를 이 모듈에 모아, 샘플 생성과 테스트가 이 모듈의 dt만 바꾸면 시각이 고정되게 한다.
Slack 전송은 slack.slack_post로 불러 src.jira.slack.slack_post를 바꾸면 적용되게 한다.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from src.common.github import Summary
from src.common.io import load_json
from src.constant.check import (
    ALL_DOC_CHECKS,
    CHECK_NAMES,
    DECISION_KO,
    DOC_CHECKS,
    NO_VERDICT_KO,
    REVIEW_OUTPUT_FILES_KO,
    SECTION_NOTE,
    SOURCE_KO,
    STATE_NOTE,
    VERDICT_FILE_KO,
    VERDICT_KO,
)
from src.constant.jira import READY_STATUS, STOP_REASON_PREFIX
from src.constant.slack import (
    LEAD_WEBHOOK_ENV,
    SLACK_A2_CHANGED,
    SLACK_CHECK_FIX,
    SLACK_MISSED_HOLD,
    SLACK_MISSED_REASONED,
    SLACK_T3_CHANGED,
)
from src.constant.time import KST
from src.jira import slack
from src.jira.client import Jira, JiraError
from src.jira.comment import (
    agent_comment,
    deliverables_wiki,
    doc_review_comment,
    doc_review_md,
    review_comment,
    sanitize_mentions,
)
from src.jira.runs import add_run, request_count, usage_total
from src.jira.slack import (
    decision_slack_text,
    load_slack_users,
    review_slack_reasons,
    review_slack_text,
    slack_mention,
    token_expiry_text,
    weekly_lines,
)
from src.jira.verdict import check_label, check_t8, doc_requests, last_check, recheck_verdict, validate_verdict
from src.jira.wiki import set_section, strip_claude_header


def now_stamp() -> str:
    """지금 시각을 결과 산출물 구역의 갱신 시각 꼴(YYYY-MM-DD HH:MM)로 돌려준다."""
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _has_text(path: Path) -> bool:
    """파일이 있고 공백이 아닌 내용이 있는지 확인한다. CI agent가 파일만 만들고 멈춘 경우를 없는 것과 같이 다루기 위함."""
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def run_entry(d: Path, out_dir: Path, mode: str, verdict: str) -> dict:
    """jira-doc 기록의 runs에 추가할 실행 기록 항목 하나를 만든다.

    Args:
        d: 수집 단계가 만든 ctx/<KEY>/ 폴더.
        out_dir: CI agent 출력 폴더.
        mode: 실행 모드(review, digest).
        verdict: pass, fix, escalate, failed, digest 중 하나.

    Returns:
        at, mode, verdict, requestNo, retry, costUsd, tokens, models를 담은 dict.
    """
    usage = usage_total(out_dir)
    if usage["costUsd"] is not None:
        usage["costUsd"] = round(usage["costUsd"], 4)
    return {
        "at": dt.datetime.now(KST).isoformat(timespec="seconds"),
        "mode": mode,
        "verdict": verdict,
        "requestNo": request_count(d) if mode == "review" else None,
        **usage,
    }


def record_failed_run(j: Jira, d: Path, out_dir: Path, mode: str, summ: Summary) -> None:
    """반영에 실패한 실행을 runs에 기록한다. Jira 오류는 Summary에만 남기고 흐름을 막지 않는다.

    Args:
        j: Jira 클라이언트.
        d: 수집 단계가 만든 ctx/<KEY>/ 폴더.
        out_dir: CI agent 출력 폴더.
        mode: 실행 모드(review, digest).
        summ: Actions 요약.
    """
    try:
        prop = j.prop_get(d.name)
        add_run(prop, run_entry(d, out_dir, mode, "failed"))
        j.prop_set(d.name, prop)
    except JiraError as e:
        summ.extra.append(f"- {d.name}: 실행 기록(runs) 저장 실패: {str(e)[:160]}")


def apply_issue(j: Jira, d: Path, out_dir: Path, mode: str, gate: bool, lead: str, summ: Summary) -> dict | None:
    """task 하나의 CI agent 출력을 Jira에 반영한다. 검수 모드면 Slack 알림까지 처리한다.

    verdict.json이 없거나 형식에 맞지 않거나 판정 없음 기본값(NO_VERDICT_KO)이거나, 검수 모드에서 verdict.json은 있으나
    tldr.wiki나 comment.wiki가 없거나 비어 있으면 Jira에 아무것도 남기지 않고(comment, TL;DR, 결과 산출물 구역,
    Slack 검수 알림 모두) Actions 요약에 실패 행만 남긴다. 실패 이유 끝에는 out/<KEY>/_agent_error.txt의 원인을 붙인다.
    정리 모드에서 tldr.wiki가 없으면 Actions 요약에만 남긴다.

    Args:
        j: Jira 클라이언트.
        d: 수집 단계가 만든 ctx/<KEY>/ 폴더(issue.json, precheck.json, meta.json).
        out_dir: CI agent 출력 폴더. out_dir/<KEY>/에서 verdict.json, tldr.wiki, comment.wiki를 읽는다.
        mode: 실행 모드(review: 검수, digest: F3 정리).
        gate: 게이트 모드 여부. 문서화 리뷰를 검수 결과에 넣을지만 정한다.
        lead: 팀장의 Jira accountId.
        summ: Actions 요약.

    Returns:
        실패하면 {"key", "title", "url", "reason"}(failure_slack_text가 받는 항목), 반영했으면 None.
    """
    issue = load_json(d / "issue.json", {})
    key, itype = issue.get("key", d.name), issue.get("type", "?")
    pre = load_json(d / "precheck.json", {"expected": [], "checks": {}})
    meta = load_json(d / "meta.json", {})
    assignee = (issue.get("assignee") or {}).get("accountId") or ""
    allowed = {a for a in (assignee, lead) if a}
    o = out_dir / key
    verdict = load_json(o / "verdict.json")
    if verdict is None and (o / "verdict.json").exists():  # load_json은 JSON으로 읽을 수 없어도 None을 돌려준다
        err = f"{VERDICT_FILE_KO}을 JSON으로 읽을 수 없음"
    elif verdict is None:
        err = f"{VERDICT_FILE_KO} 없음. 추정: CI agent 실행이 실패했거나 끝나지 않음"
    elif problem := validate_verdict(verdict, mode, key):
        err = (
            problem
            if problem == NO_VERDICT_KO
            else f"{VERDICT_FILE_KO}이 형식에 맞지 않음. {problem}. 키 정의는 schemas/verdict.json에 있음"
        )
    else:
        err = None
    # 구조화 출력에 TL;DR 본문이나 검수 comment 결과 요약이 없으면 save_output.py가 파일을 쓰지 않는다
    if err is None and mode == "review":
        absent = [ko for name, ko in REVIEW_OUTPUT_FILES_KO.items() if not _has_text(o / name)]
        if absent:
            err = f"CI agent 출력에 {', '.join(absent)} 없음 또는 비어 있음"
    try:
        cause = (o / "_agent_error.txt").read_text(encoding="utf-8").strip()
    except OSError:
        cause = ""
    # save_output.py는 이 Task의 CI agent 출력이 없을 때만 원인을 쓴다. 예상 산출물이 없는 정리 모드 Task는 기본값만으로 형식 검사를 통과한다
    if err is None and cause:
        err = NO_VERDICT_KO

    if err:
        if cause:
            err += f". 원인: {cause}"
        summ.row(key, itype, mode, "실패", err)
        record_failed_run(j, d, out_dir, mode, summ)
        return {"key": key, "title": issue.get("summary") or "", "url": issue.get("url") or "", "reason": err}

    v = orig_v = verdict["verdict"]
    notes = []
    changes: list[str] = []  # 검수 결과를 바꾼 이유. notes에도 같은 순서로 들어간다
    slack_changes: list[str] = []  # 검수 결과를 바꾼 이유의 Slack용 짧은 꼴. Slack 알림의 요점에 쓴다
    checks = [c for c in verdict["checks"] if c["id"] != "T3"]  # T3는 Claude가 아니라 여기서 계산한다
    expected, items, extra = pre.get("expected", []), verdict["items"], verdict["extra"]
    # 스크립트가 계산한 검사(precheck.json)는 Claude가 옮겨 적은 값보다 우선한다. 판정도 그 규칙에 맞춘다
    script_checks = {cid: c for cid, c in pre.get("checks", {}).items() if c.get("result") in ("pass", "fail", "n/a")}
    checks = [c for c in checks if c["id"] not in script_checks] + [
        {"id": cid, **c} for cid, c in script_checks.items()
    ]
    if mode == "review":
        if script_checks.get("A2", {}).get("result") == "fail" and v != "escalate":
            changes.append(
                f"{check_label('A2')} 결과에 따라 {VERDICT_KO[v]}에서 검토 요청으로 바꿈: 마지막 request 전환(담당자가 완료를 요청해 ticket을 ready-to-done 상태로 보내는 Jira 전환) 이후 완료 기준이 바뀜"
            )
            notes.append(changes[-1])
            slack_changes.append(SLACK_A2_CHANGED)
            v = "escalate"
        elif v == "pass" and (
            fails := [
                cid
                for cid in ("T1", "T2", "B1", "B2", "I1", "I2", "R4")
                if script_checks.get(cid, {}).get("result") == "fail"
            ]
        ):
            changes.append(
                f"스크립트 검사에 보완 필요 항목이 있어 통과에서 보류로 바꿈: {', '.join(check_label(cid) for cid in fails)}"
            )
            notes.append(changes[-1])
            slack_changes += [SLACK_CHECK_FIX.format(name=CHECK_NAMES[cid]) for cid in fails]
            v = "fix"
    # T3: Claude가 낸 items의 번호가 예상 산출물 번호(1..n)와 1:1인지 (Task)
    # 검수에서는 어긋나면 검토 요청으로 바꾸고, F3 정리는 판정이 없으므로 결과 산출물을 갱신하지 않는다
    items_ok = True
    if itype == "Task" and mode in ("review", "digest"):
        want = [str(e["id"]) for e in expected]
        got = sorted((str(a["id"]) for a in items), key=lambda x: (len(x), x))
        items_ok = not want or want == got
    if mode == "digest" and not items_ok:
        notes.append(
            f"{check_label('T3')} 결과에 따라 결과 산출물은 갱신하지 않음: 예상 산출물과 결과의 번호가 맞지 않음"
        )
    if mode == "review" and itype == "Task":
        if not want:
            checks.append({"id": "T3", "result": "n/a", "detail": "예상 산출물이 없어 대응할 수 없음"})
        elif want == got:
            checks.append({"id": "T3", "result": "pass", "detail": f"예상 산출물 {len(want)}개에 결과가 모두 대응함"})
        else:
            checks.append(
                {
                    "id": "T3",
                    "result": "fail",
                    "detail": f"예상 산출물 번호({', '.join(want)})와 CI agent 출력 번호({', '.join(got)})가 다름. CI agent 출력 오류로 간주해 검토 요청",
                }
            )
            if v != "escalate":  # 이미 검토 요청이면 바뀌지 않으므로 바꾼 이유를 적지 않는다(검사 표에는 남음)
                changes.append(
                    f"{check_label('T3')} 결과에 따라 {VERDICT_KO[v]}에서 검토 요청으로 바꿈: 예상 산출물과 결과 산출물의 번호가 맞지 않음"
                )
                notes.append(changes[-1])
                slack_changes.append(SLACK_T3_CHANGED)
            v = "escalate"

    # 문서화 리뷰 (검수 모드, 문서화 리뷰 항목이 있는 work type): Claude가 그 work type의 항목(Task T4–T7과 T9, Bug B5–B7,
    # Issue I4–I5)과 feedback을 내고, Task의 T8은 여기서 계산한다. 검수 comment의 검사 표에서는 세 work type의 항목을 모두 뺀다.
    # 관찰 모드는 검수 결과를 그대로 두고 팀장용 comment로 공유, 게이트 모드는 미달이면 통과를 보류로 바꾼다
    requests = list(verdict["requests"])
    feedback = verdict.get("feedback") or []
    type_doc_checks = DOC_CHECKS.get(itype, ())
    doc_checks = [c for c in checks if c["id"] in type_doc_checks and c["id"] != "T8"]
    checks = [c for c in checks if c["id"] not in ALL_DOC_CHECKS]
    doc_fails: list[str] = []
    if mode == "review" and type_doc_checks:
        if itype == "Task":
            doc_checks.append(check_t8(expected, items, extra))
        missing = [cid for cid in type_doc_checks if cid not in {c["id"] for c in doc_checks}]
        if missing:
            notes.append(f"문서화 리뷰 항목 누락: {', '.join(check_label(cid) for cid in missing)}")
        doc_fails = [c["id"] for c in doc_checks if c["result"] == "fail"]
        if gate:
            checks += doc_checks
            requests += [r for r in doc_requests(doc_checks, feedback) if r not in requests]
            if doc_fails and v == "pass":
                changes.append(
                    f"문서화 리뷰에 보완 필요 항목이 있어 통과에서 보류로 바꿈: {', '.join(check_label(cid) for cid in doc_fails)}"
                )
                notes.append(changes[-1])
                slack_changes += [SLACK_CHECK_FIX.format(name=CHECK_NAMES[cid]) for cid in doc_fails]
                v = "fix"
    else:
        doc_checks = []
    # 미달성 항목마다 담당자가 남긴 사유가 있으면 검토 요청, 통과인데 미달성 항목이 있으면 보류 (Task 검수만)
    if mode == "review":
        v, reason = recheck_verdict(v, itype, items, checks, gate, doc_fails)
        if reason:
            changes.append(reason)
            notes.append(reason)
            missed = sum(1 for it in items if not it.get("done"))
            slack_changes.append((SLACK_MISSED_REASONED if v == "escalate" else SLACK_MISSED_HOLD).format(n=missed))
        # 보류이고 R4가 보완 필요면 늦은 사유를 남기는 방법을 요청 끝에 넣는다. CI agent는 이 문장을 requests에 적지 않는다
        if v == "fix" and last_check(checks, "R4").get("result") == "fail":
            days = list(dict.fromkeys(s["at"][:10] for s in pre.get("stops") or [] if not s.get("hasReason")))
            when = f"({', '.join(days)})" if days else ""
            r4_request = (
                f"stop{when}의 사유를 '{STOP_REASON_PREFIX}'로 시작하는 comment로 남긴 뒤 다시 "
                "request 전환(담당자가 완료를 요청해 ticket을 ready-to-done 상태로 보내는 Jira 전환) 필요"
            )
            if r4_request not in requests:
                requests.append(r4_request)

    # 1) description의 agent 구역 (Task 검수와 F3 정리). 방금 다시 읽어서 사람 구역이 바뀌었어도 보존한다
    fresh = j.issue(key, ["description"] + ([j.tldr_field] if j.tldr_field else []))
    desc = (fresh.get("fields") or {}).get("description") or ""
    stamp = now_stamp()
    fields: dict = {}
    if itype == "Task" and (mode == "review" or (mode == "digest" and items_ok)):
        body = deliverables_wiki(expected, items, extra, final=(mode == "review"))
        new_desc = set_section(desc, "결과 산출물", body, f"{stamp} ({SOURCE_KO[mode]})", SECTION_NOTE["Task"])
        if new_desc != desc:
            fields["description"] = new_desc
    # 2) TL;DR. CI agent가 검수 결과를 바꾸기 전에 작성했으므로 "검수 결과: …" 표시를 최종 검수 결과로 맞춘다.
    #    이전 형식의 "판정: …"은 "검수 결과: …"가 없을 때만 찾는다
    tldr_missing = not (j.tldr_field and (o / "tldr.wiki").exists())
    tldr_label_missed = False  # 바꿀 표시를 찾지 못하면 Actions 요약에만 남긴다
    if not tldr_missing:
        tldr = (o / "tldr.wiki").read_text(encoding="utf-8").strip()
        if tldr and v != orig_v:
            for prefix in ("검수 결과: ", "판정: "):
                if f"{prefix}{VERDICT_KO[orig_v]}" in tldr:
                    tldr = tldr.replace(f"{prefix}{VERDICT_KO[orig_v]}", f"{prefix}{VERDICT_KO[v]}", 1)
                    break
            else:
                tldr_label_missed = True
        if tldr:
            fields[j.tldr_field] = tldr
    if fields:
        j.set_fields(key, fields)

    # 3) 상태 처리 문구와 comment (검수 모드만. Jira 상태는 바꾸지 않는다)
    summary, comment_link = "", ""
    if mode == "review":
        notes.append(STATE_NOTE)  # 검수 comment의 검수 결과 줄 아래에 나간다
        summary = strip_claude_header(
            (o / "comment.wiki").read_text(encoding="utf-8")
        )  # 검수 모드는 위에서 comment.wiki가 있음을 확인함
        text = review_comment(
            v,
            checks,
            expected if itype == "Task" else [],
            items,
            extra,
            summary,
            requests,
            notes,
            feedback if gate else None,
        )  # 게이트 모드에서만 검수 표에 문서화 리뷰 항목이 들어간다
        if v == "escalate" and lead and f"[~accountid:{lead}]" not in text:
            text += f"\n\n팀장 확인 필요: [~accountid:{lead}]"
        elif v != "escalate" and assignee and f"[~accountid:{assignee}]" not in text:
            text += f"\n\n[~accountid:{assignee}]"
        try:  # 등록 실패를 남기고 Slack 알림은 그대로 보낸다
            posted = j.add_comment(key, sanitize_mentions(agent_comment(text), allowed))
        except JiraError as e:
            posted = None
            notes.append(f"검수 comment 등록 실패: {str(e)[:160]}")
        cid, issue_url = (posted or {}).get("id"), issue.get("url") or ""
        comment_link = f"{issue_url}?focusedCommentId={cid}" if cid and issue_url else ""
        if doc_checks and not gate:
            summ.extra += doc_review_md(key, doc_checks, feedback)
            try:
                j.add_comment(
                    key, sanitize_mentions(agent_comment(doc_review_comment(doc_checks, feedback, lead)), allowed)
                )
                notes.append(
                    f"문서화 리뷰 보완 필요 {','.join(doc_fails)} (팀장 확인 comment)"
                    if doc_fails
                    else "문서화 리뷰 만족 (팀장 확인 comment)"
                )
            except JiraError as e:
                notes.append(f"문서화 리뷰 comment 등록 실패: {str(e)[:160]}")
    if tldr_label_missed:
        notes.append("TL;DR의 검수 결과 표시를 찾지 못해 바꾸지 않음")
    if tldr_missing:  # 판정 변경 이유가 아니므로 검수 comment에 넣지 않고 Actions 요약에만 남긴다
        notes.append("TL;DR 출력 없음")

    # 4) property
    prop = j.prop_get(key)
    prop.update(
        {
            "lastRunAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "lastMode": mode,
            "inputHash": meta.get("inputHash", prop.get("inputHash")),
        }
    )
    if mode == "review":  # F3 정리는 판정을 내지 않으므로 검수 판정을 덮어쓰지 않는다
        prop["lastVerdict"] = v
        if type_doc_checks:  # 문서화 리뷰 항목이 있는 work type만 기록한다
            prop["docFails"] = doc_fails
    add_run(prop, run_entry(d, out_dir, mode, v if mode == "review" else "digest"))
    j.prop_set(key, prop)

    # 5) Slack 알림 (검수 모드만). 검수 결과와 관계없이 팀장에게 보낸다. 담당자는 통과·보류 검수 comment의 멘션으로 검수 결과를 받고,
    # 팀장이 상태를 전환하면 decision 모드로 상태 변경을 받는다
    if mode == "review":
        users, warning = load_slack_users()
        if warning:
            notes.append(warning)
        mention, who = (f"<@{users[lead]}>" if users.get(lead) else "팀장"), "팀장"
        agent_points = verdict.get("slack")
        agent_points = agent_points if isinstance(agent_points, list) else []
        reasons = review_slack_reasons(v, itype, expected, items, extra, checks, slack_changes, agent_points)
        text = review_slack_text(
            v, key, issue.get("summary") or "", issue.get("url") or "", mention, reasons, comment_link
        )
        sent = slack.slack_post(text)
        notes.append(f"Slack 알림 전송({who})" if sent == "전송 완료" else f"Slack {VERDICT_KO[v]} 알림: {sent}")
    if requests:
        notes.append(f"requests {len(requests)}건")
    summ.row(key, itype, mode, VERDICT_KO[v], "; ".join(notes))
    return None


def last_ready_exit(changelog: list[dict]) -> dict | None:
    """Jira changelog에서 ready-to-done에서 나간 마지막 상태 전환을 찾는다.

    Args:
        changelog: Jira.changelog의 결과. 항목마다 created, author, items(field, fromString, toString).

    Returns:
        {"at", "to", "author"}(author는 accountId). 그런 전환이 없으면 None.
    """
    found = None
    for history in sorted(changelog, key=lambda h: h.get("created") or ""):
        for item in history.get("items") or []:
            if item.get("field") == "status" and (item.get("fromString") or "").strip().lower() == READY_STATUS:
                found = {
                    "at": history.get("created") or "",
                    "to": item.get("toString") or "",
                    "author": (history.get("author") or {}).get("accountId") or "",
                }
    return found


def apply_decision(j: Jira, key: str, lead: str, summ: Summary) -> None:
    """팀장이 ready-to-done ticket을 전환했을 때 담당자에게 팀장 확인 결과를 Slack으로 보낸다(decision 모드).

    ready-to-done에서 나간 마지막 전환이 done 또는 In Progress이고, 지금 상태가 그 도착 상태이며, 전환한 사람이
    팀장일 때만 보낸다. 그 밖에는 보내지 않고 Summary 탭에 이유를 남긴다.
    같은 전환을 두 번 알리지 않도록 알린 전환 시각을 jira-doc 기록의 lastDecisionAt에 남기고, 같으면 건너뛴다.
    메시지의 팀장 이름은 j.user_name(lead)로 읽고, 읽지 못하면 "팀장"을 적는다.

    Args:
        j: Jira 클라이언트.
        key: ticket 키.
        lead: 팀장 accountId. 비어 있으면 전환한 사람을 확인할 수 없어 changelog를 읽지 않고 알리지 않으며 Summary 탭에 "경고"로 남긴다.
        summ: Actions 요약.
    """
    issue = j.issue(key, ["summary", "status", "assignee", "issuetype"])
    f = issue.get("fields") or {}
    itype = (f.get("issuetype") or {}).get("name") or "?"
    if not lead:  # 전환한 사람을 확인할 수 없어 changelog를 읽지 않고 경고만 남긴다
        summ.row(key, itype, "decision", "경고", "팀장 accountId(JIRA_LEAD_ACCOUNT_ID)가 비어 알리지 않음")
        return
    status = ((f.get("status") or {}).get("name") or "").strip()
    last = last_ready_exit(j.changelog(key))
    decision = DECISION_KO.get(status.lower())
    skip = ""
    if decision is None:
        skip = f"지금 상태가 {status or '?'}라 알리지 않음"
    elif last is None or last["to"].strip().lower() != status.lower():
        skip = "ready-to-done에서 지금 상태로 온 전환이 없어 알리지 않음"
    elif last["author"] != lead:
        skip = "팀장이 한 전환이 아니라 알리지 않음"
    prop = j.prop_get(key) if not skip else {}
    if not skip and prop.get("lastDecisionAt") == last["at"]:
        skip = "이미 알린 전환이라 다시 알리지 않음"
    if skip:
        summ.row(key, itype, "decision", "건너뜀", skip)
        return
    site = j.site_url()
    users, warning = load_slack_users()
    text = decision_slack_text(
        key,
        f.get("summary") or "",
        f"{site}/browse/{key}" if site else "",
        slack_mention(f.get("assignee"), users),
        decision,
        status,
        j.user_name(lead),
    )
    sent = slack.slack_post(text)
    note = f"팀장 확인 결과 {decision}, 담당자에게 Slack 알림"
    if sent == "전송 완료":
        prop["lastDecisionAt"] = last["at"]
        try:
            j.prop_set(key, prop)
        except JiraError as e:
            note += f"; jira-doc 기록 저장 실패: {str(e)[:120]}"
    summ.row(key, itype, "decision", sent, f"{note}; {warning}" if warning else note)


def slack_weekly(ctx: Path, summ: Summary, lead: str = "") -> None:
    """주간 점검 결과를 Slack 한 건으로 보낸다. 첫 줄이 스레드 제목이고 담당자와 팀장(A3 항목이 있을 때)을 멘션한다.

    Actions Summary는 GitHub에 로그인한 누구나 볼 수 있어, Summary에는 빈 대응표로 다시 만든 본문을 남긴다.
    이 본문에는 Slack 멤버 ID(멘션) 대신 담당자의 Jira 표시 이름이 들어가고, 팀장은 "팀장(Slack 멘션 없음)"으로 적히며,
    머리줄의 멘션 목록이 빠진다.

    Args:
        ctx: 수집 단계가 만든 ctx 폴더. ctx/_scan/*.alerts.json을 읽는다.
        summ: Actions 요약.
        lead: 팀장 accountId(환경 변수 JIRA_LEAD_ACCOUNT_ID). A3 항목의 팀장 멘션에 사용한다.
    """
    day = dt.datetime.now().strftime("%Y-%m-%d")

    def weekly_text(users: dict[str, str]) -> tuple[str, list[str], bool]:
        """(본문, 멘션 목록, 알림 유무)."""
        lines, mentions = weekly_lines(ctx / "_scan", users, lead)
        if not lines:
            return "\n".join([f"[{day} 전달사항]", "점검 결과 이상 없음"]), mentions, False
        head = f"[{day} 전달사항] " + " ".join(mentions)
        return "\n".join([head.rstrip(), *lines]), mentions, True

    users, warning = load_slack_users()
    text, mentions, alerted = weekly_text(users)
    result = slack.slack_post(text)
    note = f"Slack 멘션 {len(mentions)}명(대응표에 있는 담당자와 팀장만 셈)" if alerted else "알림 없음"
    summ.row("-", "-", "alerts", result, f"{note}; {warning}" if warning else note)
    summary_text, _, _ = weekly_text({})
    summ.extra += [f"### 주간 점검 (Slack {result})", "", "```", summary_text, "```", ""]


def slack_token_expiry(summ: Summary) -> None:
    """토큰(인증 키) 만료 알림을 팀장 채널로 Slack 한 건 보낸다. 보낼 항목이 없으면 아무것도 하지 않는다.

    Args:
        summ: Actions 요약. 전송 결과나 경고를 행으로 남긴다.
    """
    text, warning = token_expiry_text(os.environ.get("TOKEN_EXPIRY", ""), dt.datetime.now(KST).date())
    if warning:
        summ.row("-", "-", "alerts", "경고", warning)
    if text:
        summ.row("-", "-", "alerts", slack.slack_post(text, LEAD_WEBHOOK_ENV), "토큰(인증 키) 만료 알림(팀장 채널)")


def last_month(today: dt.date) -> str:
    """오늘이 속한 달의 지난달을 YYYY-MM으로 돌려준다.

    Args:
        today: 오늘 날짜(한국 시간).

    Returns:
        지난달(YYYY-MM).
    """
    return (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
