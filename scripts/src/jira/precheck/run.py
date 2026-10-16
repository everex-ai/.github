"""ctx/ 폴더의 수집 결과로 사전 검사를 계산해 precheck.json과 alerts.json에 쓰는 함수."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from src.constant.jira import RTD, STOP_TO, WORK_STATUS
from src.jira.precheck.history import (
    business_days_since,
    check_a2,
    check_r4,
    doc_facts,
    is_status,
    parse_ts,
    stop_events,
)
from src.jira.precheck.template import check_bug_template, check_issue_template, check_task_template

# 주간 점검 A3: ready-to-done 진입 뒤 이 영업일 수 이상 done 전환이 없으면 알림.
# 값을 바꾸면 scripts/src/constant/check.py의 CHECK_NAMES["A3"]와 scripts/src/constant/slack.py의 ALERT_SECTIONS 절 제목의 일수도 바꿔야 함
READY_WAIT_DAYS = 3


def run_issue_dir(d: Path, now: dt.datetime) -> None:
    """ctx/<KEY>/의 수집 결과로 스크립트 검사를 계산해 ctx/<KEY>/precheck.json에 쓴다.

    work type별 템플릿 검사(Task T1, T2, Bug B1, B2, Issue I1, I2), 완료 기준의 사후 변경(A2, Task와 Bug만),
    stop 사유 comment(R4)와 문서화 리뷰용 수치(docFacts)를 담는다.

    Args:
        d: 수집 단계가 만든 ctx/<KEY>/ 폴더(issue.json, description.wiki, comments.json, changelog.json).
        now: 지금 시각.
    """
    issue = json.loads((d / "issue.json").read_text(encoding="utf-8"))
    desc = (d / "description.wiki").read_text(encoding="utf-8")
    comments = json.loads((d / "comments.json").read_text(encoding="utf-8"))
    changelog = json.loads((d / "changelog.json").read_text(encoding="utf-8"))
    itype = issue["type"]
    human = [c for c in comments if c["kind"] == "human"]
    status_changes = [c for c in changelog if c["field"] == "status"]
    stops = stop_events(status_changes, human, now)

    out = {
        "key": issue["key"],
        "type": itype,
        "status": issue["status"],
        "expected": [],
        "checks": {},
        "stops": stops,
        "humanCommentCount": len(human),
        "lastHumanComment": max((c["created"] for c in human), default=None),
    }
    if itype == "Task":
        t = check_task_template(desc)
        out["expected"] = t.pop("expected")
        out["checks"].update(t)
        out["checks"]["A2"] = check_a2(changelog, "예상 산출물")
        out["docFacts"] = doc_facts(d, itype, desc, changelog, human, now)
    elif itype == "Bug":
        out["checks"].update(check_bug_template(desc, len(issue.get("attachments", []))))
        out["checks"]["A2"] = check_a2(changelog, "개선", "개선(To-Be)")
        out["docFacts"] = doc_facts(d, itype, desc, changelog, human, now)
    elif itype == "Issue":
        out["checks"].update(check_issue_template(desc))
        out["docFacts"] = doc_facts(d, itype, desc, changelog, human, now)
    out["checks"]["R4"] = check_r4(stops)
    (d / "precheck.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    fails = [k for k, v in out["checks"].items() if v["result"] == "fail"]
    print(f"[precheck] {issue['key']} ({itype}): 예상 산출물 {len(out['expected'])}개, 실패 {fails or '없음'}")


def run_scan(p: Path, now: dt.datetime) -> None:
    """주간 점검 데이터 ctx/_scan/<KEY>.json으로 알림을 계산해 ctx/_scan/<KEY>.alerts.json에 쓴다.

    알림은 R4(stop 사유 미기재), A1(영업일 기준 5일 이상 활동 없음), A3(ready-to-done 영업일 기준
    READY_WAIT_DAYS일 이상 대기)다. A3는 팀장이 할 조치라 apply.py가 Slack 주간 메시지에만 넣는다.

    Args:
        p: 수집 단계가 만든 ctx/_scan/<KEY>.json.
        now: 지금 시각.
    """
    s = json.loads(p.read_text(encoding="utf-8"))
    itype, status = s["type"], s["status"]
    changes = s.get("statusChanges", [])
    started = [parse_ts(c["created"]) for c in changes if is_status(c.get("to"), WORK_STATUS)]
    first_start = min(started) if started else None  # noqa: F841
    alerts = []

    # 템플릿 누락(T1, T2, B1, B2, I1, I2)은 Jira 전환 검증이 막고 있어 알림에서 다룬다.
    # 주간 점검은 사람이 보지 않으면 드러나지 않는 세 가지(stop 사유, 무활동, ready-to-done 대기)만 본다.

    # R4: stop 뒤 24시간이 지나도 사유 comment가 없으면 알림
    stops = stop_events(changes, s.get("humanComments", []), now)
    for st in stops:
        if st["hasReason"]:
            continue
        if is_status(status, STOP_TO) and st["hoursOpen"] >= 24:
            alerts.append(
                {
                    "code": f"HOLD_REASON_MISSING:{st['at'][:10]}",
                    "check": "R4",
                    "detail": f"{st['at'][:10]} stop 뒤 {int(st['hoursOpen'] // 24)}일 경과(지난 시간을 24시간 단위로 셈, 나머지 버림)",
                    "message": f"{st['at'][:10]}에 stop 전환으로 Backlog에 보낸 뒤 사유 comment 없음. 무엇을 기다리는지와 언제 다시 진행할지 comment 필요",
                }
            )

    # A1: In Progress에서 5영업일 이상 사람의 활동이 없으면 알림
    if is_status(status, WORK_STATUS):
        last_activity = max(
            [parse_ts(c["created"]) for c in s.get("humanComments", [])] + started + [parse_ts(s["created"])],
            default=None,
        )
        idle = business_days_since(last_activity, now)
        if idle >= 5:
            alerts.append(
                {
                    "code": f"STALE:{(last_activity.date() if last_activity else now.date()).isoformat()}",
                    "check": "A1",
                    "detail": f"마지막 활동 {last_activity.date().isoformat() if last_activity else '기록 없음'} 뒤 영업일 기준 {idle}일 경과(토요일과 일요일 제외)",
                    "message": f"마지막 활동(사람 comment, In Progress 전환, ticket 생성 중 가장 늦은 것) 뒤 영업일(토요일과 일요일을 뺀 날) 기준 {idle}일 동안 활동 없음. 진행 상황 comment, 또는 멈춘 ticket이면 stop 전환으로 Backlog에 보내는 것 필요",
                }
            )

    # A3: ready-to-done에서 READY_WAIT_DAYS영업일 이상 done 전환이 없으면 알림. 진입 시각은 마지막 ready-to-done 전환
    if is_status(status, RTD):
        entered = [t for t in (parse_ts(c.get("created")) for c in changes if is_status(c.get("to"), RTD)) if t]
        if entered:
            ready_at = max(entered)
            waited = business_days_since(ready_at, now)
            if waited >= READY_WAIT_DAYS:
                day = ready_at.date().isoformat()
                alerts.append(
                    {
                        "code": f"READY_WAIT:{day}",
                        "check": "A3",
                        "detail": f"ready-to-done 진입 {day} 뒤 영업일 기준 {waited}일 경과(토요일과 일요일 제외)",
                        "message": f"ready-to-done 진입 뒤 영업일 기준 {waited}일 동안 done 전환 없음. 팀장의 done 전환 또는 보류 comment 필요",
                    }
                )

    out = {
        "key": s["key"],
        "type": itype,
        "status": status,
        "summary": s.get("summary"),
        "url": s.get("url"),
        "assignee": s.get("assignee"),
        "alerts": alerts,
        "clear": [],
    }
    p.with_suffix(".alerts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if alerts:
        print(f"[precheck] {s['key']} 알림 {len(alerts)}건: {[a['check'] for a in alerts]}")
