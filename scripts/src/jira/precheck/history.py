"""stop 전환과 사유 comment, 완료 기준 변경 이력(R4, A2)과 문서화 리뷰용 수치를 계산하는 함수."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from src.constant.jira import RTD, STOP_REASON_PREFIX, STOP_TO, WORK_STATUS
from src.jira.wiki import section_body, strip_placeholders

REQUEST_KO = "request 전환(담당자가 완료를 요청해 ticket을 ready-to-done 상태로 보내는 Jira 전환)"  # 검사 표에 나가는 전환 이름. 프롬프트의 "request 전환"과 같은 이름
# 문서화 리뷰의 배경 구역. 구역은 제목 앞부분으로 찾으므로 "문제"로 Bug의 "문제(As-Is)"를 찾는다
DOC_BACKGROUND_SECTIONS = {"Task": "진행 배경", "Bug": "문제", "Issue": "이슈 내용"}


def is_status(name: str | None, target: str) -> bool:
    """상태 이름이 target과 같은지. 앞뒤 공백과 대소문자는 무시하고, None은 빈 문자열로 본다."""
    return (name or "").strip().lower() == target.lower()


def parse_ts(s: str | None) -> dt.datetime | None:
    """Jira 시각 문자열을 datetime으로 읽는다. 비었거나 읽을 수 없으면 None."""
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
    """사람이 읽는 문구의 시각 꼴. 결과 산출물 구역의 갱신 시각(src/jira/issue.py now_stamp)과 같은 YYYY-MM-DD HH:MM."""
    return t.strftime("%Y-%m-%d %H:%M")


def business_days_since(t: dt.datetime | None, now: dt.datetime) -> int:
    """t의 다음 날부터 now의 날짜까지 지난 영업일(토요일과 일요일 제외) 수. t가 None이면 0."""
    if not t:
        return 0
    d, n = t.date(), now.date()
    days = 0
    while d < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


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
        reasons = [
            c for c in human_comments if (parse_ts(c["created"]) or now) > t0 and (parse_ts(c["created"]) or now) <= t1
        ]
        late = None
        if not reasons:
            marked = [
                c
                for c in human_comments
                if (parse_ts(c.get("created")) or t0) > t0  # created가 없으면 뺀다
                and (c.get("body") or "").lstrip().lower().startswith(prefix)
            ]
            late = min(marked, key=lambda c: parse_ts(c["created"])) if marked else None
        reason = reasons[0] if reasons else late
        st = {
            "at": ch["created"],
            "hasReason": reason is not None,
            "hoursOpen": round((t1 - t0).total_seconds() / 3600, 1),
            "reasonUrl": reason.get("url", "") if reason else "",
            "late": late is not None,
        }
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
        return {
            "result": "fail",
            "detail": f"stop 전환(In Progress → Backlog)에 대한 사유를 담당자가 comment에 남기지 않음(stop 시점: {days})",
        }
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
            return {
                "result": "fail",
                "detail": f"{fmt_ts(changed)}에 '{label or desc_section}' 구역이 바뀜 (마지막 {REQUEST_KO} {fmt_ts(last)} 이후)",
            }
    return {"result": "pass", "detail": f"마지막 {REQUEST_KO} 이후 완료 기준 변경 없음"}


def doc_facts(d: Path, itype: str, desc: str, changelog: list[dict], human: list[dict], now: dt.datetime) -> dict:
    """문서화 리뷰(Task T4–T7과 T9, Bug B5–B7, Issue I4–I5)를 agent가 판단할 때 인용하는 사실. 판단은 하지 않고 수치만 모은다.

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
        subtasks.append(
            {
                "key": st.get("key"),
                "status": st.get("status"),
                "descLines": len(strip_placeholders(st.get("description") or "")),
                "humanComments": sum(1 for c in st.get("comments", []) if c.get("kind") == "human"),
                "prs": len(st.get("prs", [])),
            }
        )
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
