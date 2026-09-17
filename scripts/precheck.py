#!/usr/bin/env python3
"""2단계: 사람의 판단이 필요 없는 검사를 계산한다.

- ctx/<KEY>/            -> ctx/<KEY>/precheck.json   (Claude가 인용하는 검사 결과와 예상 산출물 목록)
- ctx/_scan/<KEY>.json  -> ctx/_scan/<KEY>.alerts.json (정리 모드 누락 알림. apply.py가 comment로 보냄)
검사 항목의 정의는 prompts/rules.md(설계 문서 5.5절)와 같다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jira_api import section_body, strip_placeholders  # noqa: E402

LIST_ITEM_RE = re.compile(r"^\s*(?:([*#\-]+)|(\d+)[.)])\s+(.*)$")   # 글머리표(*, #, -), 번호(1. 1))
LEGACY_AC_RE = re.compile(r"^AC-\d+\s*[:：]\s*")                       # 예전 형식 'AC-1:'은 접두어만 떼고 본문을 쓴다
BUG_ASIS_FIELDS = ["발생 기기", "발생 일자", "발생 장비", "발생 계정", "발생 내용"]
CHECKBOX_CHECKED = re.compile(r"\[\s*[xX✓✔]\s*\]|\(\s*[xX]\s*\)|☑|✅")
CHECKBOX_ANY = re.compile(r"\[\s*[xX✓✔ ]?\s*\]|\(\s*[xX ]?\s*\)|☐|☑")
STOP_TO, WORK_STATUS, RTD = "Backlog", "In Progress", "ready-to-done"     # Jira 상태 이름. 비교는 is_status로 대소문자 무시


def is_status(name: str | None, target: str) -> bool:
    return (name or "").strip().lower() == target.lower()


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def business_days_since(t: dt.datetime | None, now: dt.datetime) -> int:
    if not t:
        return 0
    d, n = t.date(), now.date()
    days = 0
    while d < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def expected_items(desc: str) -> list[dict]:
    """예상 산출물 구역의 목록을 항목별로 나눈다. 형식은 강제하지 않는다.

    - 최상위 글머리표(*, #, -)나 번호(1. 1))로 시작하는 줄이 한 항목이다.
    - 들여쓴 하위 항목(**, ##, -- 또는 글머리표 없는 이어지는 줄)은 바로 위 항목의 본문에 붙인다.
    - 괄호로만 된 템플릿 안내문 줄은 항목으로 세지 않는다.
    - id는 1부터 순서대로 붙인다. Claude 출력(items)과 결과 산출물 구역은 이 번호로 대응시킨다(T3).
    """
    body = section_body(desc, "예상 산출물")
    items: list[dict] = []
    for raw in body.splitlines():
        if not raw.strip():
            continue
        m = LIST_ITEM_RE.match(raw)
        if m:
            marker, text = m.group(1) or m.group(2), m.group(3).strip()
            nested = bool(m.group(1)) and len(m.group(1)) > 1
        else:
            marker, text, nested = None, raw.strip(), True                # 글머리표 없는 줄은 이어지는 줄로 본다
        text = LEGACY_AC_RE.sub("", text)
        if not strip_placeholders(text):                                  # 안내문 또는 빈 항목
            continue
        if nested and items:
            items[-1]["text"] += ", " + text
        elif marker is None and not items:
            items.append({"id": "1", "text": text})                       # 목록 없이 문장만 쓴 경우도 한 항목으로 인정
        else:
            items.append({"id": str(len(items) + 1), "text": text})
    return items


def check_task_template(desc: str) -> dict:
    expected = expected_items(desc)
    t1 = "pass" if expected else "fail"
    t1_detail = f"예상 산출물 {len(expected)}개" if expected else "예상 산출물 항목 없음"
    bg = strip_placeholders(section_body(desc, "진행 배경"))
    t2 = "pass" if len(bg) >= 2 else "fail"
    return {"expected": expected, "T1": {"result": t1, "detail": t1_detail}, "T2": {"result": t2, "detail": f"진행 배경 내용 {len(bg)}줄"}}


def check_bug_template(desc: str, attachment_count: int) -> dict:
    asis = section_body(desc, "현황")
    missing = []
    for label in BUG_ASIS_FIELDS:
        m = re.search(rf"{re.escape(label)}[^:：\n]*[:：][ \t]*(.*)", asis)
        if not m or not strip_placeholders(m.group(1)):
            missing.append(label)
    b1_ok = not missing and attachment_count >= 1
    b1_detail = ("모두 채워짐" if not missing else "비어 있음: " + ", ".join(missing)) + f", 첨부 {attachment_count}개"
    tobe = strip_placeholders(section_body(desc, "개선"))
    return {"B1": {"result": "pass" if b1_ok else "fail", "detail": b1_detail},
            "B2": {"result": "pass" if tobe else "fail", "detail": f"개선(To-be) 내용 {len(tobe)}줄"}}


def check_issue_template(desc: str) -> dict:
    kind = section_body(desc, "이슈 유형")
    if CHECKBOX_CHECKED.search(kind):
        i1 = ("pass", "유형 선택됨")
    elif CHECKBOX_ANY.search(kind):
        i1 = ("fail", "체크된 유형 없음")
    else:
        i1 = ("unknown", "체크박스 상태를 텍스트로 확인할 수 없음. agent가 판단")
    body = strip_placeholders(section_body(desc, "이슈 내용"))
    i2_ok = len(body) >= 2 or (len(body) == 1 and len(body[0]) >= 40)
    return {"I1": {"result": i1[0], "detail": i1[1]}, "I2": {"result": "pass" if i2_ok else "fail", "detail": f"이슈 내용 {len(body)}줄"}}


def stop_events(status_changes: list[dict], human_comments: list[dict], now: dt.datetime) -> list[dict]:
    """stop(Backlog 진입)마다 그 뒤 다음 상태 변경 전까지 사람 comment가 있었는지."""
    out = []
    for i, ch in enumerate(status_changes):
        if not is_status(ch.get("to"), STOP_TO):
            continue
        t0 = parse_ts(ch["created"])
        t1 = parse_ts(status_changes[i + 1]["created"]) if i + 1 < len(status_changes) else now
        reasons = [c for c in human_comments if (parse_ts(c["created"]) or now) > t0 and (parse_ts(c["created"]) or now) <= t1]
        out.append({"at": ch["created"], "hasReason": bool(reasons), "hoursOpen": round((t1 - t0).total_seconds() / 3600, 1),
                    "reasonUrl": reasons[0].get("url", "") if reasons else ""})
    return out


def check_r4(stops: list[dict]) -> dict:
    if not stops:
        return {"result": "n/a", "detail": "stop 이력 없음"}
    missing = [s for s in stops if not s["hasReason"]]
    if not missing:
        return {"result": "pass", "detail": f"stop {len(stops)}회 모두 사유 comment 있음"}
    return {"result": "fail", "detail": "사유 comment 없는 stop: " + ", ".join(s["at"][:10] for s in missing)}


def check_a2(itype: str, changelog: list[dict], desc_section: str) -> dict:
    """가장 최근 Ready-to-Done 전환 이후에 완료 기준 구역(Task: 예상 산출물, Bug: 개선)이 바뀌었는가."""
    if itype == "Issue":
        return {"result": "n/a", "detail": "Issue는 해당 없음"}
    rtd_times = [parse_ts(c["created"]) for c in changelog if c["field"] == "status" and is_status(c.get("to"), RTD)]
    if not rtd_times:
        return {"result": "n/a", "detail": "Ready-to-Done 이력 없음"}
    last = max(rtd_times)
    for c in changelog:
        if c["field"] != "description" or (parse_ts(c["created"]) or last) <= last:
            continue
        before = section_body(c.get("from") or "", desc_section)
        after = section_body(c.get("to") or "", desc_section)
        if before.strip() != after.strip():
            return {"result": "fail", "detail": f"{c['created'][:16]}에 '{desc_section}' 구역이 바뀜 (Ready-to-Done 전환 {last.isoformat()[:16]} 이후)"}
    return {"result": "pass", "detail": "Ready-to-Done 이후 완료 기준 변경 없음"}


def doc_facts(d: Path, desc: str, changelog: list[dict], human: list[dict], now: dt.datetime) -> dict:
    """문서화 리뷰(T4~T7)를 agent가 판단할 때 인용하는 사실. 판단은 하지 않고 수치만 모은다."""
    bg = strip_placeholders(section_body(desc, "진행 배경"))
    status_changes = [c for c in changelog if c["field"] == "status"]
    starts = [t for t in (parse_ts(c["created"]) for c in status_changes if is_status(c.get("to"), WORK_STATUS)) if t]
    requests = [t for t in (parse_ts(c["created"]) for c in status_changes if is_status(c.get("to"), RTD)) if t]
    start = min(starts) if starts else None
    end = max(requests) if requests else now
    times = [t for t in (parse_ts(c["created"]) for c in human) if t]
    during = [t for t in times if start and start <= t <= end]
    subtasks = []
    for p in sorted((d / "subtasks").glob("*.json")) if (d / "subtasks").exists() else []:
        st = json.loads(p.read_text(encoding="utf-8"))
        subtasks.append({
            "key": st.get("key"), "status": st.get("status"),
            "descLines": len(strip_placeholders(st.get("description") or "")),
            "humanComments": sum(1 for c in st.get("comments", []) if c.get("kind") == "human"),
            "prs": len(st.get("prs", [])),
        })
    return {
        "background": {"lines": len(bg), "chars": sum(len(x) for x in bg)},
        "activity": {
            "workStartedAt": start.isoformat(timespec="minutes") if start else None,
            "lastRequestedAt": max(requests).isoformat(timespec="minutes") if requests else None,
            "workBusinessDays": business_days_since(start, end) if start else None,
            "humanComments": len(times),
            "humanCommentsDuringWork": len(during),
            "commentDaysDuringWork": len({t.date() for t in during}),
        },
        "subtasks": subtasks,
    }


def run_issue_dir(d: Path, now: dt.datetime) -> None:
    issue = json.loads((d / "issue.json").read_text(encoding="utf-8"))
    desc = (d / "description.wiki").read_text(encoding="utf-8")
    comments = json.loads((d / "comments.json").read_text(encoding="utf-8"))
    changelog = json.loads((d / "changelog.json").read_text(encoding="utf-8"))
    itype = issue["type"]
    human = [c for c in comments if c["kind"] == "human"]
    status_changes = [c for c in changelog if c["field"] == "status"]
    stops = stop_events(status_changes, human, now)

    out = {"key": issue["key"], "type": itype, "status": issue["status"], "expected": [], "checks": {}, "stops": stops,
           "humanCommentCount": len(human), "lastHumanComment": max((c["created"] for c in human), default=None)}
    if itype == "Task":
        t = check_task_template(desc)
        out["expected"] = t.pop("expected")
        out["checks"].update(t)
        out["checks"]["A2"] = check_a2(itype, changelog, "예상 산출물")
        out["docFacts"] = doc_facts(d, desc, changelog, human, now)
    elif itype == "Bug":
        out["checks"].update(check_bug_template(desc, len(issue.get("attachments", []))))
        out["checks"]["A2"] = check_a2(itype, changelog, "개선")
    elif itype == "Issue":
        out["checks"].update(check_issue_template(desc))
        out["checks"]["A2"] = check_a2(itype, changelog, "")
    out["checks"]["R4"] = check_r4(stops)
    (d / "precheck.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    fails = [k for k, v in out["checks"].items() if v["result"] == "fail"]
    print(f"[precheck] {issue['key']} ({itype}): 예상 산출물 {len(out['expected'])}개, 실패 {fails or '없음'}")


def run_scan(p: Path, now: dt.datetime) -> None:
    s = json.loads(p.read_text(encoding="utf-8"))
    itype, status = s["type"], s["status"]
    changes = s.get("statusChanges", [])
    started = [parse_ts(c["created"]) for c in changes if is_status(c.get("to"), WORK_STATUS)]
    first_start = min(started) if started else None
    alerts, clear = [], []

    # 템플릿 누락: In Progress로 처음 전환된 뒤 1영업일이 지나도 비어 있으면 알림
    if is_status(status, WORK_STATUS) and first_start and business_days_since(first_start, now) >= 1:
        day = first_start.date().isoformat()
        fake_desc = "".join(f"h2. {t}\n{b}\n" for t, b in s["sections"].items() if b)
        if itype == "Task":
            t = check_task_template(fake_desc)
            if t["T1"]["result"] == "fail":
                alerts.append({"code": f"EXPECTED_MISSING:{day}", "check": "T1", "message": "예상 산출물(Task 완료 기준)이 아직 비어 있습니다. 이 task가 끝났을 때 무엇이 나와야 하는지를 글머리표 또는 번호 목록으로 한 항목씩 적어 주세요."})
            else:
                clear.append("EXPECTED_MISSING")
            if t["T2"]["result"] == "fail":
                alerts.append({"code": f"BG_MISSING:{day}", "check": "T2", "message": "진행 배경이 아직 비어 있습니다. 왜 이 일을 하는지 두 줄 이상 적어 주세요."})
            else:
                clear.append("BG_MISSING")
        elif itype == "Bug":
            b = check_bug_template(fake_desc, s.get("attachmentCount", 0))
            if b["B1"]["result"] == "fail":
                alerts.append({"code": f"BUG_ASIS_MISSING:{day}", "check": "B1", "message": f"현황(AS-IS)이 불완전합니다 ({b['B1']['detail']}). 다섯 항목과 캡쳐 첨부를 채워 주세요."})
            else:
                clear.append("BUG_ASIS_MISSING")
            if b["B2"]["result"] == "fail":
                alerts.append({"code": f"BUG_TOBE_MISSING:{day}", "check": "B2", "message": "개선(To-be)에 정상 동작 설명이 없습니다."})
            else:
                clear.append("BUG_TOBE_MISSING")
        elif itype == "Issue":
            i = check_issue_template(fake_desc)
            if i["I1"]["result"] == "fail":
                alerts.append({"code": f"ISSUE_TYPE_MISSING:{day}", "check": "I1", "message": "이슈 유형 체크박스가 선택되지 않았습니다."})
            elif i["I1"]["result"] == "pass":
                clear.append("ISSUE_TYPE_MISSING")
            if i["I2"]["result"] == "fail":
                alerts.append({"code": f"ISSUE_BODY_MISSING:{day}", "check": "I2", "message": "이슈 내용이 비어 있습니다."})
            else:
                clear.append("ISSUE_BODY_MISSING")

    # R4: stop 뒤 24시간이 지나도 사유 comment가 없으면 알림
    stops = stop_events(changes, s.get("humanComments", []), now)
    for st in stops:
        if st["hasReason"]:
            continue
        if is_status(status, STOP_TO) and st["hoursOpen"] >= 24:
            alerts.append({"code": f"HOLD_REASON_MISSING:{st['at'][:10]}", "check": "R4", "message": f"{st['at'][:10]}에 stop으로 Backlog에 보냈지만 사유 comment가 없습니다. 무엇을 기다리는지, 언제 다시 볼지를 comment로 남겨 주세요."})
    if stops and all(st["hasReason"] for st in stops):
        clear.append("HOLD_REASON_MISSING")

    # A1: In Progress에서 5영업일 이상 사람의 활동이 없으면 알림
    if is_status(status, WORK_STATUS):
        last_activity = max([parse_ts(c["created"]) for c in s.get("humanComments", [])] + started + [parse_ts(s["created"])], default=None)
        idle = business_days_since(last_activity, now)
        if idle >= 5:
            alerts.append({"code": f"STALE:{(last_activity.date() if last_activity else now.date()).isoformat()}", "check": "A1", "message": f"{idle}영업일 동안 comment, sub-task 변경, PR이 없습니다. 진행 상황을 comment로 남기거나, 멈춘 상태라면 stop으로 Backlog에 보내 주세요."})
        else:
            clear.append("STALE")

    out = {"key": s["key"], "type": itype, "status": status, "assignee": s.get("assignee"), "alerts": alerts, "clear": clear}
    p.with_suffix(".alerts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if alerts:
        print(f"[precheck] {s['key']} 알림 {len(alerts)}건: {[a['check'] for a in alerts]}")


def main() -> None:
    ctx = Path(sys.argv[1] if len(sys.argv) > 1 else "ctx")
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    for d in sorted(ctx.glob("[A-Z]*-[0-9]*/")):
        if (d / "issue.json").exists():
            run_issue_dir(d, now)
    for p in sorted((ctx / "_scan").glob("*.json")) if (ctx / "_scan").exists() else []:
        if not p.name.endswith(".alerts.json"):
            run_scan(p, now)


if __name__ == "__main__":
    main()
