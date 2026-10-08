#!/usr/bin/env python3
"""2단계: 사람의 판단이 필요 없는 검사를 계산한다.

- ctx/<KEY>/            -> ctx/<KEY>/precheck.json   (Claude가 인용하는 검사 결과와 예상 산출물 목록)
- ctx/_scan/<KEY>.json  -> ctx/_scan/<KEY>.alerts.json (주간 점검 알림. apply.py가 Slack으로 보냄)
검사 항목의 정의는 prompts/rules.md와 prompts/types/<work type>/review.md의 검사 항목 표와 같다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jira_api import STOP_REASON_PREFIX, section_body, strip_placeholders  # noqa: E402

LIST_ITEM_RE = re.compile(r"^\s*(?:([*#\-]+)|(\d+)[.)])\s+(.*)$")   # 글머리표(*, #, -), 번호(1. 1))
LEGACY_AC_RE = re.compile(r"^AC-\d+\s*[:：]\s*")                       # 예전 형식 'AC-1:'은 접두어만 떼고 본문을 쓴다
# Bug의 기본 정보 구역 항목. 키는 줄을 찾는 접두어, 값은 내용 칸에 적는 템플릿의 항목 이름
BUG_INFO_FIELDS = {"발생 기기": "발생 기기/서비스", "발생 일자": "발생 일자", "발생 장비": "발생 장비", "발생 계정": "발생 계정"}
CHECKBOX_CHECKED = re.compile(r"\[\s*[xX✓✔]\s*\]|\(\s*[xX]\s*\)|☑|✅")
CHECKBOX_ANY = re.compile(r"\[\s*[xX✓✔ ]?\s*\]|\(\s*[xX ]?\s*\)|☐|☑")
# Jira 체크 목록(ADF taskList)에서 선택한 항목은 REST API v2의 wiki markup에 취소선 "-제안-"으로 나온다(INNO-36 검수, 2026-10-08)
STRUCK_ITEM = re.compile(r"^\s*(?:[*#-]+\s+)?-(?=[^\s-])[^\n]*[^\s-]-\s*$", re.M)
STOP_TO, WORK_STATUS, RTD = "Backlog", "In Progress", "ready-to-done"     # Jira 상태 이름. 비교는 is_status로 대소문자 무시
REQUEST_KO = "request 전환(담당자가 완료를 요청해 task를 ready-to-done 상태로 보내는 Jira 전환)"   # 검사 표에 나가는 전환 이름. 프롬프트의 "request 전환"과 같은 이름
LINES_NOTE = "(안내문과 빈 줄을 뺀 줄 수)"
# 문서화 리뷰의 배경 구역. 구역은 제목 앞부분으로 찾으므로 "문제"로 Bug의 "문제(As-Is)"를 찾는다
DOC_BACKGROUND_SECTIONS = {"Task": "진행 배경", "Bug": "문제", "Issue": "이슈 내용"}


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


def fmt_ts(t: dt.datetime) -> str:
    """사람이 읽는 문구의 시각 꼴. 결과 산출물 구역의 갱신 시각(apply.py now_stamp)과 같은 YYYY-MM-DD HH:MM."""
    return t.strftime("%Y-%m-%d %H:%M")


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
    return {"expected": expected, "T1": {"result": t1, "detail": t1_detail}, "T2": {"result": t2, "detail": f"진행 배경 내용 {len(bg)}줄{LINES_NOTE}"}}


def check_bug_template(desc: str, attachment_count: int) -> dict:
    """Bug의 description field 구성(기본 정보, 문제(As-Is), 개선(To-Be), 첨부 자료(필수))을 검사한다.

    Args:
        desc: description(wiki markup).
        attachment_count: Jira task의 첨부 파일 개수.

    Returns:
        B1(기본 정보 항목, 문제(As-Is) 내용, Jira 첨부 파일)과 B2(개선(To-Be) 내용)의 결과와 내용 칸 문구.
    """
    info = section_body(desc, "기본 정보")
    missing = []
    for prefix, name in BUG_INFO_FIELDS.items():
        m = re.search(rf"{re.escape(prefix)}[^:：\n]*[:：][ \t]*(.*)", info)
        if not m or not strip_placeholders(m.group(1)):
            missing.append(name)
    problem = strip_placeholders(section_body(desc, "문제"))
    b1_ok = not missing and bool(problem) and attachment_count >= 1
    if missing:
        info_detail = f"기본 정보 중 비어 있는 항목 {len(missing)}개({', '.join(missing)})"
    else:
        info_detail = f"기본 정보 항목 {len(BUG_INFO_FIELDS)}개({', '.join(BUG_INFO_FIELDS.values())}) 모두 채워짐"
    b1_detail = ", ".join([
        info_detail,
        f"문제(As-Is) 내용 {len(problem)}줄{LINES_NOTE}",
        f"첨부 파일 {attachment_count}개",
    ])
    tobe = strip_placeholders(section_body(desc, "개선"))
    return {"B1": {"result": "pass" if b1_ok else "fail", "detail": b1_detail},
            "B2": {"result": "pass" if tobe else "fail", "detail": f"개선(To-Be) 내용 {len(tobe)}줄{LINES_NOTE}"}}


def check_issue_template(desc: str) -> dict:
    """Issue의 description 구역(이슈 유형, 이슈 내용)을 검사한다.

    이슈 유형은 체크 글자(CHECKBOX_CHECKED)나 Jira 체크 목록의 선택 항목(취소선 줄, STRUCK_ITEM)이 있으면 선택됨으로 본다.

    Args:
        desc: description(wiki markup).

    Returns:
        이슈 유형 선택(I1)과 이슈 내용 작성(I2)의 결과와 내용 칸 문구. I1은 체크 상태를 글자로 알 수 없으면 unknown이다.
    """
    kind = section_body(desc, "이슈 유형")
    if CHECKBOX_CHECKED.search(kind) or STRUCK_ITEM.search(kind):
        i1 = ("pass", "유형 선택됨")
    elif CHECKBOX_ANY.search(kind):
        i1 = ("fail", "체크된 유형 없음")
    else:
        i1 = ("unknown", "체크박스 상태를 텍스트로 확인할 수 없음. agent가 판단")
    body = strip_placeholders(section_body(desc, "이슈 내용"))
    i2_ok = len(body) >= 2 or (len(body) == 1 and len(body[0]) >= 40)
    return {"I1": {"result": i1[0], "detail": i1[1]}, "I2": {"result": "pass" if i2_ok else "fail", "detail": f"이슈 내용 {len(body)}줄{LINES_NOTE}"}}


def stop_events(status_changes: list[dict], human_comments: list[dict], now: dt.datetime) -> list[dict]:
    """stop(Backlog 진입)마다 사유 comment가 있었는지 확인한다.

    stop 뒤 다음 상태 변경 전까지 사람 comment가 있으면 사유로 센다. 없으면 stop 뒤에 작성된 사람 comment 중
    본문이 STOP_REASON_PREFIX("stop 사유:")로 시작하는 첫 comment를 늦은 사유로 센다. 비교는 본문 앞 공백을 빼고
    대소문자를 무시하며, 작성 시각(created)이 없는 comment는 뺀다.
    늦은 사유 comment 하나가 그 앞의 사유 없는 stop 여러 개를 함께 인정할 수 있다.

    Args:
        status_changes: 상태 변경 이력(시간순).
        human_comments: 사람 comment 목록. 항목마다 created, url, body(없을 수 있음).
        now: 지금 시각. 마지막 stop 뒤에 상태 변경이 없으면 사유를 찾는 구간의 끝으로 쓴다.

    Returns:
        stop마다 at(stop 시각), hasReason, hoursOpen, reasonUrl, late(늦은 사유 여부)를 담은 목록.
        늦은 사유가 있으면 lateReasonAt(그 comment의 작성 시각)도 담는다.
    """
    prefix = STOP_REASON_PREFIX.lower()
    out = []
    for i, ch in enumerate(status_changes):
        if not is_status(ch.get("to"), STOP_TO):
            continue
        t0 = parse_ts(ch["created"])
        t1 = parse_ts(status_changes[i + 1]["created"]) if i + 1 < len(status_changes) else now
        reasons = [c for c in human_comments if (parse_ts(c["created"]) or now) > t0 and (parse_ts(c["created"]) or now) <= t1]
        late = None
        if not reasons:
            marked = [c for c in human_comments if (parse_ts(c.get("created")) or t0) > t0   # created가 없으면 뺀다
                      and (c.get("body") or "").lstrip().lower().startswith(prefix)]
            late = min(marked, key=lambda c: parse_ts(c["created"])) if marked else None
        reason = reasons[0] if reasons else late
        st = {"at": ch["created"], "hasReason": reason is not None, "hoursOpen": round((t1 - t0).total_seconds() / 3600, 1),
              "reasonUrl": reason.get("url", "") if reason else "", "late": late is not None}
        if late is not None:
            st["lateReasonAt"] = late["created"]
        out.append(st)
    return out


def check_r4(stops: list[dict]) -> dict:
    """stop마다 사유 comment가 있는지로 R4(stop 사유 comment) 결과를 정한다.

    사유 없는 stop이 있으면 fail이다. 모두 사유가 있고 그중 늦은 사유가 있으면 pass이고, detail 끝에
    "늦게 기록: stop <날짜> 뒤 다음 상태 변경 이후에 남긴 [stop 사유 comment <날짜>|<URL>]"를 붙이고 late를 True로 둔다.
    늦은 사유가 여러 개면 "; "로 잇고, comment URL이 비면 링크 없이 "stop 사유 comment <날짜>"로 적는다.

    Args:
        stops: stop_events의 결과.

    Returns:
        검사 결과(result, detail, 늦은 사유가 있으면 late).
    """
    if not stops:
        return {"result": "n/a", "detail": "stop 이력 없음"}
    missing = [s for s in stops if not s["hasReason"]]
    if missing:
        days = ", ".join(s["at"][:10] for s in missing)
        return {"result": "fail",
                "detail": f"stop 전환(In Progress → Backlog)에 대한 사유를 담당자가 comment에 남기지 않음(stop 시점: {days})"}
    detail = f"stop {len(stops)}회 모두 사유 comment 있음"
    late = [s for s in stops if s.get("late")]
    if not late:
        return {"result": "pass", "detail": detail}
    marks = []
    for s in late:
        name = f"stop 사유 comment {s['lateReasonAt'][:10]}"
        link = f"[{name}|{s['reasonUrl']}]" if s.get("reasonUrl") else name
        marks.append(f"stop {s['at'][:10]} 뒤 다음 상태 변경 이후에 남긴 {link}")
    return {"result": "pass", "detail": f"{detail}. 늦게 기록: {'; '.join(marks)}", "late": True}


def check_a2(changelog: list[dict], desc_section: str, label: str | None = None) -> dict:
    """가장 최근 Ready-to-Done 전환 이후에 완료 기준 구역(Task: 예상 산출물, Bug: 개선)이 바뀌었는지 검사한다.

    Issue는 완료 기준 구역이 없어 이 검사를 하지 않는다(run_issue_dir이 부르지 않음).

    Args:
        changelog: changelog.json의 상태 전환과 description 변경 이력.
        desc_section: 구역을 찾는 제목 앞부분.
        label: 내용 칸에 적는 구역 이름. 없으면 desc_section을 적는다.

    Returns:
        A2(완료 기준의 사후 변경)의 결과와 내용 칸 문구.
    """
    rtd_times = [parse_ts(c["created"]) for c in changelog if c["field"] == "status" and is_status(c.get("to"), RTD)]
    if not rtd_times:
        return {"result": "n/a", "detail": f"{REQUEST_KO} 이력 없음"}
    last = max(rtd_times)
    for c in changelog:
        changed = parse_ts(c["created"])
        if c["field"] != "description" or (changed or last) <= last:
            continue
        before = section_body(c.get("from") or "", desc_section)
        after = section_body(c.get("to") or "", desc_section)
        if before.strip() != after.strip():
            return {"result": "fail", "detail": f"{fmt_ts(changed)}에 '{label or desc_section}' 구역이 바뀜 (마지막 {REQUEST_KO} {fmt_ts(last)} 이후)"}
    return {"result": "pass", "detail": f"마지막 {REQUEST_KO} 이후 완료 기준 변경 없음"}


def doc_facts(d: Path, itype: str, desc: str, changelog: list[dict], human: list[dict], now: dt.datetime) -> dict:
    """문서화 리뷰(Task T4–T7, Bug B5–B7, Issue I4–I5)를 agent가 판단할 때 인용하는 사실. 판단은 하지 않고 수치만 모은다.

    배경 줄 수는 work type별 배경 구역(DOC_BACKGROUND_SECTIONS: Task 진행 배경, Bug 문제(As-Is), Issue 이슈 내용)에서 센다.

    Args:
        d: ctx/<KEY>/ 폴더. subtasks/*.json이 있으면 sub-task별 수치를 센다.
        itype: work type(Task, Bug, Issue).
        desc: description(wiki markup).
        changelog: changelog.json의 상태 전환과 description 변경 이력.
        human: 사람 comment 목록.
        now: 지금 시각. request 전환 이력이 없으면 작업 기간의 끝으로 쓴다.

    Returns:
        background(배경 줄 수와 글자 수), activity(작업 기간과 기간 중 comment 수), subtasks(sub-task별 수치).
    """
    bg = strip_placeholders(section_body(desc, DOC_BACKGROUND_SECTIONS[itype]))
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

    out = {"key": issue["key"], "type": itype, "status": issue["status"], "expected": [], "checks": {}, "stops": stops,
           "humanCommentCount": len(human), "lastHumanComment": max((c["created"] for c in human), default=None)}
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
    s = json.loads(p.read_text(encoding="utf-8"))
    itype, status = s["type"], s["status"]
    changes = s.get("statusChanges", [])
    started = [parse_ts(c["created"]) for c in changes if is_status(c.get("to"), WORK_STATUS)]
    first_start = min(started) if started else None
    alerts = []

    # 템플릿 누락(T1, T2, B1, B2, I1, I2)은 Jira 전환 검증이 막고 있어 알림에서 다룬다.
    # 주간 점검은 사람이 보지 않으면 드러나지 않는 두 가지(stop 사유, 무활동)만 본다.

    # R4: stop 뒤 24시간이 지나도 사유 comment가 없으면 알림
    stops = stop_events(changes, s.get("humanComments", []), now)
    for st in stops:
        if st["hasReason"]:
            continue
        if is_status(status, STOP_TO) and st["hoursOpen"] >= 24:
            alerts.append({"code": f"HOLD_REASON_MISSING:{st['at'][:10]}", "check": "R4",
                           "detail": f"{st['at'][:10]} stop 뒤 {int(st['hoursOpen'] // 24)}일 경과(지난 시간을 24시간 단위로 셈, 나머지 버림)",
                           "message": f"{st['at'][:10]}에 stop 전환으로 Backlog에 보낸 뒤 사유 comment 없음. 무엇을 기다리는지와 언제 다시 진행할지 comment 필요"})

    # A1: In Progress에서 5영업일 이상 사람의 활동이 없으면 알림
    if is_status(status, WORK_STATUS):
        last_activity = max([parse_ts(c["created"]) for c in s.get("humanComments", [])] + started + [parse_ts(s["created"])], default=None)
        idle = business_days_since(last_activity, now)
        if idle >= 5:
            alerts.append({"code": f"STALE:{(last_activity.date() if last_activity else now.date()).isoformat()}", "check": "A1",
                           "detail": f"마지막 활동 {last_activity.date().isoformat() if last_activity else '기록 없음'} 뒤 영업일 기준 {idle}일 경과(토요일과 일요일 제외)",
                           "message": f"마지막 활동(사람 comment, In Progress 전환, task 생성 중 가장 늦은 것) 뒤 영업일(토요일과 일요일을 뺀 날) 기준 {idle}일 동안 활동 없음. 진행 상황 comment, 또는 멈춘 task면 stop 전환으로 Backlog에 보내는 것 필요"})

    out = {"key": s["key"], "type": itype, "status": status, "summary": s.get("summary"), "url": s.get("url"),
           "assignee": s.get("assignee"), "alerts": alerts, "clear": []}
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
