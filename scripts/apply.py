#!/usr/bin/env python3
"""4단계: Claude의 출력(out/<KEY>/)을 검증해 Jira에 반영한다. Jira에 쓰는 유일한 단계.

alerts 모드(주간 점검)는 Jira에 쓰지 않고 ctx/_scan/*.alerts.json을 Slack 한 건으로 보낸다.

반영 순서: 구역 교체(결과 산출물) -> TL;DR -> 상태 전환(검수 모드) -> comment -> property -> Slack 알림(검수 모드)
- verdict.json이 없거나 형식이 틀리면 반영하지 않고 실패로 기록한다 (검수 모드면 팀장 멘션 comment).
- 결과 산출물 구역과 검수 comment의 검사 표는 Claude의 verdict.json(items, extra, checks)을 받아 이 스크립트가
  정해진 형식으로 조립한다. Claude가 쓰는 자유 서술은 comment.wiki(결과 요약)와 tldr.wiki뿐이다.
- 검수 결과는 통과(pass), 검토 요청(escalate), 보류(fix)다. 괄호 안은 verdict.json의 코드값이다.
- T3: verdict.items의 번호가 precheck의 예상 산출물 번호(1..n)와 1:1이 아니면 검수 결과를 검토 요청으로 바꾼다.
- 문서화 리뷰(T4–T8, Task 검수): 관찰 모드는 검수 결과를 바꾸지 않고 팀장용 comment와 Actions Summary로 공유하고,
  게이트 모드는 미달이면 통과를 보류로 바꾼다. T8(초과 달성·미달성 사유)은 이 스크립트가 계산한다.
- Task 검수는 미달성 항목마다 담당자가 남긴 사유가 있으면 검토 요청으로, 통과인데 미달성 항목이 있으면 보류로 바꾼다.
- 상태 전환(검수 모드): 보류이고 지금 상태가 ready-to-done이면 In Progress로 되돌린다. 통과와 검토 요청은 상태를
  바꾸지 않고 팀장이 확인 뒤 직접 전환한다. 게이트 모드는 문서화 리뷰를 검수 결과에 넣을지만 정하고 전환과 관계없다.
- Slack 알림(검수 모드): 보류는 담당자에게, 검토 요청과 통과는 팀장에게 검수 결과, 이유, 검수 comment 링크를 보낸다.
- comment의 멘션은 담당자와 팀장 두 사람으로 제한한다.
- 정리 모드(F3)도 Task의 결과 산출물 구역을 다시 쓴다. comment와 판정은 없고, 사유가 기록에 없으면 "사유 미기재"를 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jira_api import AGENT_MARK, STOP_REASON_PREFIX, Jira, JiraError, set_section  # noqa: E402

VERDICT_KO = {"pass": "통과", "fix": "보류", "escalate": "검토 요청", None: "정리"}
# 검수 결과를 다시 정하는 규칙(recheck_verdict)과 상태 전환. 검사 이름은 CHECK_NAMES, prompts/rules.md와 같다
OWNER_FIXABLE_CHECKS = ("T1", "T2", "R2", "R4", "B1", "B2", "B3", "B4", "I1", "I2", "I3")   # 담당자가 고칠 수 있는 검사. 실패하면 보류
SENTENCE_DETAIL_CHECKS = ("R4",)                                              # detail이 이유 문장이라 Slack 보류 이유에 검사 이름 없이 적는 검사
SLACK_INDENT = "    "                                                         # Slack 메시지의 묶음 항목 들여쓰기(공백 4칸). 검수 알림과 주간 점검에 사용
NOT_A_REASON = "근거 부족으로 확인 불가"                             # CI agent가 why에 넣는 문구. 담당자가 남긴 사유로 세지 않는다
READY_STATUS, IN_PROGRESS = "ready-to-done", "In Progress"           # Jira 상태 이름. READY_STATUS는 대소문자 무시로 비교
SLACK_CLOSING = {"escalate": "팀장이 task를 확인한 뒤 in-progress 또는 done으로 직접 전환 필요",
                 "pass": "팀장이 task를 확인한 뒤 done으로 직접 전환 필요"}
MENTION_RE = re.compile(r"\[~(?:accountid:)?([^\]]+)\]")
WIKI_LINK_RE = re.compile(r"\[([^|\]]+)\|[^\]]*\]")                 # [제목|URL]
WIKI_LINK_PARTS_RE = re.compile(r"\[([^|\]]+)\|([^\]]*)\]")          # [제목|URL]에서 제목과 URL을 함께 잡는다 (Slack 링크로 바꿀 때)
WIKI_COLOR_RE = re.compile(r"\{color(?::[^}]*)?\}")                  # {color:#6b778c}, {color}
LIST_MARK_RE = re.compile(r"^[*#-]+\s+")                             # wiki 목록 표식("* ", "# ", "#* ")
SECTION_NOTE = {"Task": "agent가 comment, sub-task, PR 기록을 근거로 작성함. 고칠 내용은 직접 수정하지 않고 \"정정:\"으로 시작하는 comment로 요청 필요."}
SOURCE_KO = {"review": "검수", "digest": "F3 정리: Jira task 화면의 문서 정리 버튼"}     # 결과 산출물 구역의 안내 줄 끝에 붙여 출처를 보인다

# 검사 항목의 표시 순서와 이름. Jira comment의 검사 표는 항상 이 순서로, 이 이름으로 나간다 (ID는 괄호로 덧붙인다).
# 정의는 prompts/rules.md(설계 문서 5.5절)와 같다.
CHECK_NAMES = {
    "T1": "예상 산출물 작성",
    "T2": "진행 배경 작성",
    "T3": "예상 산출물과 결과 산출물의 1:1 대응",
    "T4": "진행 배경 충실도",
    "T5": "예상 산출물 분할 단위",
    "T6": "진행 기록 comment",
    "T7": "sub-task 기록",
    "T8": "초과 달성·미달성 사유",
    "B1": "현황(AS-IS) 작성과 첨부",
    "B2": "개선(To-be) 작성",
    "B3": "원인과 해결의 근거 링크",
    "B4": "To-be 동작 확인 comment",
    "I1": "이슈 유형 선택",
    "I2": "이슈 내용 작성",
    "I3": "대응 결과의 근거",
    "R2": "1년 뒤에도 이해 가능한 기록",
    "R3": "개조식 작성",
    "R4": "stop 사유 comment",
    "A1": "영업일 기준 5일 이상 활동 없음",
    "A2": "완료 기준의 사후 변경",
}
CHECK_ORDER = {cid: i for i, cid in enumerate(CHECK_NAMES)}
RESULT_KO = {"pass": "✅ 만족", "fail": "❌ 보완 필요", "n/a": "➖ 해당 없음"}   # 검사 표 결과 칸. 모든 검사 항목에 같은 표시
# 문서화 리뷰(Task 검수). 관찰 모드에서는 검수 결과를 바꾸지 않고 팀장용 comment로, 게이트 모드에서는 보류로 이어진다
DOC_CHECKS = ("T4", "T5", "T6", "T7", "T8")
CELL_BREAK = " \\\\ "                                                              # Jira wiki 표 칸 안의 줄바꿈
NO_REASON = "사유 미기재"
VERDICT_KEY_KO = {"issueKey": "task 키", "mode": "실행 모드", "verdict": "판정", "checks": "검사 결과 목록",
                  "items": "예상 산출물별 결과 목록", "extra": "초과 달성 목록", "requests": "담당자 요청 목록"}
VERDICT_FILE_KO = "CI agent(GitHub Actions에서 실행되는 문서화 Agent)의 판정 파일(verdict.json)"   # 실패 알림에 나가는 이름
# 주간 점검(alerts) Slack 보고
SLACK_USERS = Path(__file__).parent.parent / "config" / "slack-users.json"   # Jira accountId -> Slack member ID
ALERT_SECTIONS = [("R4", "stop 사유 미기재"), ("A1", "영업일 기준 5일 이상 활동 없음")]
ALERT_LIMIT = 15


def result_label(result: str) -> str:
    """검사 결과 값(pass, fail, n/a)을 검사 표 결과 칸의 표시 문구로 바꾼다."""
    return RESULT_KO.get(result, result)


def check_label(cid: str) -> str:
    """검사 ID를 검사 표와 같은 꼴 "검사 이름 (검사 ID)"로 적는다. 검사 표보다 먼저 나오는 문장에서 ID만 쓰지 않기 위함."""
    return f"{CHECK_NAMES.get(cid, cid)} ({cid})"


def cell_text(text: str) -> str:
    """검사 표 칸에 넣을 문장. 칸 구분자와 겹치지 않게 Jira wiki 링크 밖의 "|"만 "/"로 바꾼다.

    링크 [제목|URL] 안의 "|"는 링크를 이루므로 그대로 둔다.

    Args:
        text: 검사 결과의 detail 또는 문서화 리뷰 points의 한 줄(wiki markup).

    Returns:
        링크 밖의 "|"를 "/"로 바꾼 문장.
    """
    parts: list[str] = []
    pos = 0
    for m in WIKI_LINK_RE.finditer(text):
        parts += [text[pos:m.start()].replace("|", "/"), m.group(0)]
        pos = m.end()
    parts.append(text[pos:].replace("|", "/"))
    return "".join(parts)


def check_table(checks: list[dict], feedback: list[dict] | None = None) -> str:
    """verdict.checks를 고정 순서로 정렬해 wiki 표로 만든다.

    같은 ID가 여러 번 있으면 마지막 것을 쓴다. 문서화 리뷰 항목은 feedback의 points가 있으면 그것을 내용 칸에
    쓴다(없으면 detail). 내용 칸의 문장은 cell_text로 바꿔 링크 밖의 "|"만 "/"로 바꾼다.

    Args:
        checks: 검사 결과 목록.
        feedback: verdict.json의 feedback(문서화 리뷰 항목별 피드백). 없으면 detail만 쓴다.

    Returns:
        검사 표(wiki markup).
    """
    by_id: dict[str, dict] = {}
    for c in checks:
        by_id[c["id"]] = c
    points_by_id = {f["id"]: f.get("points") or [] for f in feedback or []}
    rows = ["|| 검사 || 결과 || 내용 ||"]
    for cid in sorted(by_id, key=lambda x: (CHECK_ORDER.get(x, 99), x)):
        c = by_id[cid]
        texts = points_by_id.get(cid) or [c.get("detail") or ""]
        cell = CELL_BREAK.join(cell_text(t).replace("\n", " ").strip() for t in texts if t.strip())
        rows.append(f"| {check_label(cid)} | {result_label(c['result'])} | {cell} |")
    return "\n".join(rows)


def deliverables_wiki(expected: list[dict], items: list[dict], extra: list[dict], final: bool = True) -> str:
    """결과 산출물 구역 본문. 예상 산출물 번호 순서로 한 항목씩, 그 뒤에 초과 달성 목록.
    final=False(F3 정리)면 사유가 기록에 없을 때 "사유 미기재" 줄을 쓰지 않는다. 진행 중인 항목은 아직 사유가 없기 때문."""
    by_id = {str(it["id"]): it for it in items}
    lines: list[str] = []
    if not expected:
        lines.append("* (예상 산출물이 비어 있어 대응할 항목이 없음)")
    else:
        lines += [deliverables_summary(expected, items, extra), ""]
    for e in expected:
        it = by_id.get(str(e["id"]), {})
        mark = "✅ 달성" if it.get("done") else "❌ 미달성"
        lines.append(f"# *{e['text']}* / {mark}")
        if it.get("result"):
            lines.append(f"#* 결과: {it['result']}")
        why = (it.get("why") or "").strip()
        if not it.get("done") and (why or final):
            lines.append(f"#* 미달성 사유: {why or NO_REASON}")
        if it.get("evidence"):
            lines.append(f"#* 근거: {it['evidence']}")
        if not it.get("done") and it.get("reason"):
            lines.append(f"#* 요청: {it['reason']}")
    if extra:
        lines.append("")
        lines.append("*초과 달성* {color:#6b778c}(예상 산출물에 없었지만 추가로 나온 결과){color}")
        for x in extra:
            lines.append(f"* {x['result']}")
            why = (x.get("why") or "").strip()
            if why or final:
                lines.append(f"** 추가 사유: {why or NO_REASON}")
            if x.get("evidence"):
                lines.append(f"** 근거: {x['evidence']}")
    return "\n".join(lines)


def deliverables_summary(expected: list[dict], items: list[dict], extra: list[dict]) -> str:
    """결과 산출물 구역과 검수 comment의 달성 요약 한 줄. 달성 수는 예상 산출물 번호에 대응한 items 중 done인 것."""
    want = {str(e["id"]) for e in expected}
    done = sum(1 for it in items if it.get("done") and str(it["id"]) in want)
    line = f"예상 산출물 {len(expected)}개 중 {done}개 달성"
    if extra:
        line += f", 초과 달성 {len(extra)}건"
    return line


def strip_claude_header(text: str) -> str:
    """Claude가 comment.wiki에 판정 줄이나 검사 표를 넣었더라도 스크립트가 만든 것과 겹치지 않게 뗀다."""
    kept = []
    for line in text.strip().splitlines():
        s = line.strip()
        if s.startswith("검수 결과") or s.startswith("||") or (s.startswith("|") and s.endswith("|")):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def short_name(text: str, limit: int = 40) -> str:
    """T8 detail과 요청에서 초과 달성 항목을 가리키는 이름. wiki 링크는 제목만 남기고 limit자에서 자른다."""
    s = WIKI_LINK_RE.sub(r"\1", text).replace("|", "/").strip()
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def check_t8(expected: list[dict], items: list[dict], extra: list[dict]) -> dict:
    """초과 달성마다 추가 사유, 미달성 항목마다 미달성 사유가 채워졌는지 (Task 검수만)."""
    if not expected:
        return {"id": "T8", "result": "n/a", "detail": "예상 산출물이 없어 대응할 수 없음"}
    no_why_miss = [str(it["id"]) for it in items if not it.get("done") and not (it.get("why") or "").strip()]
    no_why_extra = [short_name(x["result"]) for x in extra if not (x.get("why") or "").strip()]
    if not no_why_miss and not no_why_extra:
        return {"id": "T8", "result": "pass", "detail": "미달성·초과 달성 항목 모두 사유 있음"}
    parts, asks = [], []
    if no_why_miss:
        parts.append(f"사유 없는 미달성 항목 {', '.join(no_why_miss)}번")
        asks.append(f"미달성 항목 {', '.join(no_why_miss)}번의 미달성 사유")
    if no_why_extra:
        names = ", ".join(f"'{r}'" for r in no_why_extra)
        parts.append(f"사유 없는 초과 달성 {len(no_why_extra)}건({names})")
        asks.append(f"초과 달성 {names}의 추가 사유")
    return {"id": "T8", "result": "fail", "detail": ", ".join(parts), "request": f"{'와 '.join(asks)} comment 필요"}


def doc_requests(doc_checks: list[dict], feedback: list[dict]) -> list[str]:
    """fail인 문서화 리뷰 항목의 요청 문장."""
    by_id = {f["id"]: f for f in feedback}
    out = []
    for c in doc_checks:
        if c["result"] != "fail":
            continue
        req = c.get("request") if c["id"] == "T8" else (by_id.get(c["id"], {}).get("request") or "").strip()
        if req:
            out.append(req)
    return out


def owner_reason(item: dict) -> str:
    """미달성 항목에 담당자가 남긴 사유. why가 비었거나 CI agent가 넣는 NOT_A_REASON이면 빈 문자열.

    Args:
        item: verdict.json items의 항목 하나.

    Returns:
        사유 문장(wiki markup) 또는 빈 문자열.
    """
    why = (item.get("why") or "").strip()
    return "" if why == NOT_A_REASON else why


def owner_fails(checks: list[dict]) -> list[dict]:
    """실패한 검사 중 담당자가 고칠 수 있는 검사(OWNER_FIXABLE_CHECKS).

    Args:
        checks: 검사 결과 목록.

    Returns:
        실패한 담당자 검사 목록. checks의 순서를 따른다.
    """
    return [c for c in checks if c["result"] == "fail" and c["id"] in OWNER_FIXABLE_CHECKS]


def last_check(checks: list[dict], cid: str) -> dict:
    """검사 결과 목록에서 ID가 cid인 검사를 찾는다. 검사 표(check_table)처럼 같은 ID가 여러 번 있으면 마지막 것을 쓴다.

    Args:
        checks: 검사 결과 목록.
        cid: 검사 ID.

    Returns:
        찾은 검사 결과. 없으면 빈 dict.
    """
    return next((c for c in reversed(checks) if c["id"] == cid), {})


def recheck_verdict(v: str, itype: str, items: list[dict], checks: list[dict], gate: bool,
                    doc_fails: list[str]) -> tuple[str, str | None]:
    """Task의 미달성 항목과 사유로 검수 결과를 다시 정한다.

    미달성 항목마다 담당자가 남긴 사유가 있고, 담당자가 고칠 수 있는 검사가 실패하지 않았고, 게이트 모드에서
    문서화 리뷰에 보완 필요가 없으면 검토 요청으로 바꾼다. 그렇지 않은데 통과이면 보류로 바꾼다.
    Task가 아니거나, 이미 검토 요청이거나, 미달성 항목이 없으면 바꾸지 않는다.

    Args:
        v: 지금의 검수 결과 코드값(pass, fix, escalate).
        itype: work type(Task, Bug, Issue).
        items: verdict.json의 items.
        checks: 검수 comment의 검사 표에 들어가는 검사 결과 목록.
        gate: 게이트 모드 여부.
        doc_fails: 보완 필요인 문서화 리뷰 항목 ID 목록.

    Returns:
        (새 검수 결과 코드값, 바꾼 이유). 바꾸지 않았으면 이유는 None.
    """
    if itype != "Task" or v == "escalate":
        return v, None
    missed = [it for it in items if not it.get("done")]
    if not missed:
        return v, None
    unreasoned = [it for it in missed if not owner_reason(it)]
    if not unreasoned and not owner_fails(checks) and not (gate and doc_fails):
        return "escalate", f"미달성 항목마다 담당자가 남긴 사유가 있어 {VERDICT_KO[v]}에서 검토 요청으로 바꿈"
    if v == "pass":
        return "fix", "미달성 항목이 있어 통과에서 보류로 바꿈"
    return v, None


def doc_review_result(doc_checks: list[dict]) -> str:
    """문서화 리뷰 결과 값. 문서화 리뷰 항목에 보완 필요가 하나라도 있으면 "보완 필요", 없으면 "통과".

    Args:
        doc_checks: 문서화 리뷰 항목(T4–T8)의 검사 결과 목록.

    Returns:
        "보완 필요" 또는 "통과".
    """
    return "보완 필요" if any(c["result"] == "fail" for c in doc_checks) else "통과"


def doc_review_comment(doc_checks: list[dict], feedback: list[dict], lead: str) -> str:
    """관찰 모드의 팀장용 문서화 리뷰 comment. 첫 줄에 문서화 리뷰 결과 값(doc_review_result)을 적는다.

    Args:
        doc_checks: 문서화 리뷰 항목(T4–T8)의 검사 결과 목록.
        feedback: verdict.json의 feedback(문서화 리뷰 항목별 피드백).
        lead: 팀장의 Jira accountId. 비어 있으면 멘션하지 않는다.

    Returns:
        comment 본문(wiki markup).
    """
    parts = [f"문서화 리뷰 결과(관찰 모드): *{doc_review_result(doc_checks)}*", "", check_table(doc_checks, feedback)]
    reqs = doc_requests(doc_checks, feedback)
    if reqs:
        parts += ["", "*담당자에게 보낼 요청 후보*"] + [f"# {r}" for r in reqs]
    if lead:
        parts += ["", f"[~accountid:{lead}]"]
    return "\n".join(parts)


def doc_review_md(key: str, doc_checks: list[dict], feedback: list[dict]) -> list[str]:
    """Actions Summary용 문서화 리뷰 (markdown).

    Args:
        key: task 키.
        doc_checks: 문서화 리뷰 항목(T4–T8)의 검사 결과 목록.
        feedback: verdict.json의 feedback(문서화 리뷰 항목별 피드백).

    Returns:
        markdown 줄 목록.
    """
    by_id = {f["id"]: f for f in feedback}
    lines = [f"### 문서화 리뷰 {key}(관찰 모드): {doc_review_result(doc_checks)}", "",
             "| 검사 | 결과 | 피드백 |", "|---|---|---|"]
    for c in sorted(doc_checks, key=lambda x: CHECK_ORDER.get(x["id"], 99)):
        pts = by_id.get(c["id"], {}).get("points") or [c.get("detail") or ""]
        txt = "<br>".join(pt.replace("|", "/") for pt in pts if pt)
        lines.append(f"| {check_label(c['id'])} | {result_label(c['result'])} | {txt} |")
    return lines + [""]


def review_comment(v: str, checks: list[dict], expected: list[dict], items: list[dict], extra: list[dict],
                   summary: str, requests: list[str], notes: list[str], feedback: list[dict] | None = None) -> str:
    """검수 comment. 판정과 판정을 바꾼 이유, 달성 요약, 결과 요약, 요청을 먼저 적고 검사 표를 마지막에 적는다."""
    parts = [f"검수 결과: *{VERDICT_KO[v]}*"] + [f"* {n}" for n in notes]
    if expected:
        parts += ["", deliverables_summary(expected, items, extra) + " (자세한 내용은 description의 결과 산출물 구역)"]
    if summary:
        parts += ["", "*결과 요약*", summary]
    if requests:
        parts += ["", "*요청*"] + [f"# {r}" for r in requests]
    parts += ["", "*검사 항목*", check_table(checks, feedback)]
    return "\n".join(parts)


def failure_comment(err: str, lead: str) -> str:
    """검수 모드에서 verdict.json을 반영하지 못했을 때 남기는 실패 알림 comment."""
    parts = [f"자동 검수 실행 실패: {err}", "팀장 확인 필요"]
    if url := run_url():
        parts.append(f"근거: [GitHub Actions 실행 로그|{url}]")
    if lead:
        parts.append(f"[~accountid:{lead}]")
    return "\n".join(parts)


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def run_url() -> str:
    s, r, i = os.environ.get("GITHUB_SERVER_URL", ""), os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    return f"{s}/{r}/actions/runs/{i}" if s and r and i else ""


def load_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def validate_verdict(v, mode: str, key: str) -> str | None:
    """schemas/verdict.json의 핵심 제약만 검사한다. 문제가 있으면 이유를 돌려준다."""
    if not isinstance(v, dict):
        return "verdict.json이 객체가 아님"
    for k in ("issueKey", "mode", "verdict", "checks", "items", "extra", "requests"):
        if k not in v:
            return f"필수 키 없음: {k}({VERDICT_KEY_KO[k]})"
    if v["issueKey"] != key:
        return f"issueKey(task 키) 불일치: {v['issueKey']}"
    if v["mode"] != mode:
        return f"mode(실행 모드) 불일치: {v['mode']}"
    if v["verdict"] not in ("pass", "fix", "escalate", None):
        return f"verdict(판정) 값 오류: {v['verdict']}"
    if mode == "review" and v["verdict"] is None:
        return "검수 모드인데 verdict(판정)가 비어 있음(null)"
    if not isinstance(v["checks"], list) or not all(isinstance(c, dict) and isinstance(c.get("id"), str) and c.get("result") in ("pass", "fail", "n/a") for c in v["checks"]):
        return "checks(검사 결과 목록) 형식 오류"
    if not isinstance(v["items"], list) or not all(isinstance(a, dict) and "id" in a and isinstance(a.get("done"), bool) and isinstance(a.get("result"), str) for a in v["items"]):
        return "items(예상 산출물별 결과 목록) 형식 오류 (항목마다 id(예상 산출물 번호), done(달성 여부), result(결과) 필요)"
    if not isinstance(v["extra"], list) or not all(isinstance(a, dict) and isinstance(a.get("result"), str) for a in v["extra"]):
        return "extra(초과 달성 목록) 형식 오류 (항목마다 result(결과) 필요)"
    if not isinstance(v["requests"], list) or not all(isinstance(r, str) for r in v["requests"]):
        return "requests(담당자 요청 목록) 형식 오류"
    fb = v.get("feedback", [])
    if not isinstance(fb, list) or not all(isinstance(f, dict) and f.get("id") in DOC_CHECKS[:-1] and isinstance(f.get("points"), list) for f in fb):
        return (f"feedback(문서화 리뷰 항목별 피드백) 형식 오류 (항목마다 id(검사 ID)는 {', '.join(check_label(c) for c in DOC_CHECKS[:-1])} "
                "중 하나, points(피드백 문장 목록) 필요)")
    return None


def sanitize_mentions(text: str, allowed: set[str]) -> str:
    def repl(m):
        return m.group(0) if m.group(1) in allowed else ""
    return MENTION_RE.sub(repl, text)


def agent_comment(body: str) -> str:
    body = body.strip()
    return body if body.startswith(AGENT_MARK) else f"{AGENT_MARK}\n{body}"


class Summary:
    def __init__(self, path: str):
        self.path = path
        self.rows: list[str] = []
        self.extra: list[str] = []

    def row(self, key: str, itype: str, mode: str, result: str, note: str = "") -> None:
        self.rows.append(f"| {key} | {itype} | {mode} | {result} | {note} |")
        print(f"[apply] {key}: {result} {note}")

    def flush(self) -> None:
        lines = ["## jira-doc 실행 결과", "", "| task | type | mode | 결과 | 비고 |", "|---|---|---|---|---|", *self.rows, "", *self.extra]
        text = "\n".join(lines) + "\n"
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text)


def review_transition(j: Jira, key: str, v: str, status: str) -> tuple[str, str]:
    """검수 결과에 따라 Jira 상태를 처리한다. 보류이고 지금 상태가 ready-to-done일 때만 In Progress로 되돌린다.

    Args:
        j: Jira 클라이언트.
        key: task 키.
        v: 최종 검수 결과 코드값(pass, fix, escalate).
        status: issue.json의 지금 상태 이름.

    Returns:
        (검수 comment와 Actions 요약에 적는 문구, Slack 알림 첫 줄에 적는 짧은 문구).
    """
    if v != "fix":
        return "상태를 바꾸지 않고 팀장 확인을 요청함", "상태 변경 없음"
    if status.strip().lower() != READY_STATUS:
        return f"지금 상태가 {status}라 in-progress로 되돌리지 않음", f"지금 상태 {status} 유지"
    try:
        j.transition_to(key, IN_PROGRESS)
    except JiraError as e:
        return f"in-progress 전환 실패: {str(e)[:160]}", "in-progress 전환 실패"
    return "상태를 in-progress로 되돌림", "상태를 in-progress로 되돌림"


def apply_issue(j: Jira, d: Path, out_dir: Path, mode: str, gate: bool, lead: str, summ: Summary) -> None:
    """task 하나의 CI agent 출력을 Jira에 반영한다. 검수 모드면 상태 전환과 Slack 알림까지 처리한다.

    Args:
        j: Jira 클라이언트.
        d: 수집 단계가 만든 ctx/<KEY>/ 폴더(issue.json, precheck.json, meta.json).
        out_dir: CI agent 출력 폴더. out_dir/<KEY>/에서 verdict.json, tldr.wiki, comment.wiki를 읽는다.
        mode: 실행 모드(review: 검수, digest: F3 정리).
        gate: 게이트 모드 여부. 문서화 리뷰를 검수 결과에 넣을지만 정한다.
        lead: 팀장의 Jira accountId.
        summ: Actions 요약.
    """
    issue = load_json(d / "issue.json", {})
    key, itype = issue.get("key", d.name), issue.get("type", "?")
    pre = load_json(d / "precheck.json", {"expected": [], "checks": {}})
    meta = load_json(d / "meta.json", {})
    assignee = (issue.get("assignee") or {}).get("accountId") or ""
    allowed = {a for a in (assignee, lead) if a}
    o = out_dir / key
    verdict = load_json(o / "verdict.json")
    if verdict is None and (o / "verdict.json").exists():   # load_json은 JSON으로 읽을 수 없어도 None을 돌려준다
        err = f"{VERDICT_FILE_KO}을 JSON으로 읽을 수 없음"
    elif verdict is None:
        err = f"{VERDICT_FILE_KO} 없음. 추정: CI agent 실행이 실패했거나 끝나지 않음"
    elif problem := validate_verdict(verdict, mode, key):
        err = f"{VERDICT_FILE_KO}이 형식에 맞지 않음. {problem}. 키 정의는 schemas/verdict.json에 있음"
    else:
        err = None

    if err:
        if mode == "review":
            body = agent_comment(failure_comment(err, lead))
            try:
                j.add_comment(key, sanitize_mentions(body, allowed))
            except JiraError as e:
                print(f"[apply] {key}: 실패 comment 등록 실패: {e}", file=sys.stderr)
        summ.row(key, itype, mode, "실패", err)
        return

    v = orig_v = verdict["verdict"]
    notes = []
    changes: list[str] = []      # 검수 결과를 바꾼 이유. notes에도 같은 순서로 들어가고, Slack 알림의 이유 묶음에 다시 쓴다
    checks = [c for c in verdict["checks"] if c["id"] != "T3"]           # T3는 Claude가 아니라 여기서 계산한다
    expected, items, extra = pre.get("expected", []), verdict["items"], verdict["extra"]
    # 스크립트가 계산한 검사(precheck.json)는 Claude가 옮겨 적은 값보다 우선한다. 판정도 그 규칙에 맞춘다
    script_checks = {cid: c for cid, c in pre.get("checks", {}).items() if c.get("result") in ("pass", "fail", "n/a")}
    checks = [c for c in checks if c["id"] not in script_checks] + [{"id": cid, **c} for cid, c in script_checks.items()]
    if mode == "review":
        if script_checks.get("A2", {}).get("result") == "fail" and v != "escalate":
            changes.append(f"{check_label('A2')} 결과에 따라 {VERDICT_KO[v]}에서 검토 요청으로 바꿈: 마지막 request 전환(담당자가 완료를 요청해 task를 ready-to-done 상태로 보내는 Jira 전환) 이후 완료 기준이 바뀜")
            notes.append(changes[-1])
            v = "escalate"
        elif v == "pass" and (fails := [cid for cid in ("T1", "T2", "B1", "B2", "I1", "I2", "R4") if script_checks.get(cid, {}).get("result") == "fail"]):
            changes.append(f"스크립트 검사에 보완 필요 항목이 있어 통과에서 보류로 바꿈: {', '.join(check_label(cid) for cid in fails)}")
            notes.append(changes[-1])
            v = "fix"
    # T3: Claude가 낸 items의 번호가 예상 산출물 번호(1..n)와 1:1인지 (Task)
    # 검수에서는 어긋나면 검토 요청으로 바꾸고, F3 정리는 판정이 없으므로 결과 산출물을 갱신하지 않는다
    items_ok = True
    if itype == "Task" and mode in ("review", "digest"):
        want = [str(e["id"]) for e in expected]
        got = sorted((str(a["id"]) for a in items), key=lambda x: (len(x), x))
        items_ok = not want or want == got
    if mode == "digest" and not items_ok:
        notes.append(f"{check_label('T3')} 결과에 따라 결과 산출물은 갱신하지 않음: 예상 산출물과 결과의 번호가 맞지 않음")
    if mode == "review" and itype == "Task":
        if not want:
            checks.append({"id": "T3", "result": "n/a", "detail": "예상 산출물이 없어 대응할 수 없음"})
        elif want == got:
            checks.append({"id": "T3", "result": "pass", "detail": f"예상 산출물 {len(want)}개에 결과가 모두 대응함"})
        else:
            checks.append({"id": "T3", "result": "fail", "detail": f"예상 산출물 번호({', '.join(want)})와 CI agent 출력 번호({', '.join(got)})가 다름. CI agent 출력 오류로 간주해 검토 요청"})
            if v != "escalate":                       # 이미 검토 요청이면 바뀌지 않으므로 바꾼 이유를 적지 않는다(검사 표에는 남음)
                changes.append(f"{check_label('T3')} 결과에 따라 {VERDICT_KO[v]}에서 검토 요청으로 바꿈: 예상 산출물과 결과 산출물의 번호가 맞지 않음")
                notes.append(changes[-1])
            v = "escalate"

    # 문서화 리뷰 (Task 검수만): Claude가 T4–T7과 feedback을 내고 T8은 여기서 계산한다.
    # 관찰 모드는 검수 결과를 그대로 두고 팀장용 comment로 공유, 게이트 모드는 미달이면 통과를 보류로 바꾼다
    requests = list(verdict["requests"])
    feedback = verdict.get("feedback") or []
    doc_checks = [c for c in checks if c["id"] in DOC_CHECKS and c["id"] != "T8"]
    checks = [c for c in checks if c["id"] not in DOC_CHECKS]
    doc_fails: list[str] = []
    if mode == "review" and itype == "Task":
        doc_checks.append(check_t8(expected, items, extra))
        missing = [cid for cid in DOC_CHECKS if cid not in {c["id"] for c in doc_checks}]
        if missing:
            notes.append(f"문서화 리뷰 항목 누락: {', '.join(check_label(cid) for cid in missing)}")
        doc_fails = [c["id"] for c in doc_checks if c["result"] == "fail"]
        if gate:
            checks += doc_checks
            requests += [r for r in doc_requests(doc_checks, feedback) if r not in requests]
            if doc_fails and v == "pass":
                changes.append(f"문서화 리뷰에 보완 필요 항목이 있어 통과에서 보류로 바꿈: {', '.join(check_label(cid) for cid in doc_fails)}")
                notes.append(changes[-1])
                v = "fix"
    else:
        doc_checks = []
    # 미달성 항목마다 담당자가 남긴 사유가 있으면 검토 요청, 통과인데 미달성 항목이 있으면 보류 (Task 검수만)
    if mode == "review":
        v, reason = recheck_verdict(v, itype, items, checks, gate, doc_fails)
        if reason:
            changes.append(reason)
            notes.append(reason)
        # 보류이고 R4가 보완 필요면 늦은 사유를 남기는 방법을 요청 끝에 넣는다. CI agent는 이 문장을 requests에 적지 않는다
        if v == "fix" and last_check(checks, "R4").get("result") == "fail":
            days = list(dict.fromkeys(s["at"][:10] for s in pre.get("stops") or [] if not s.get("hasReason")))
            when = f"({', '.join(days)})" if days else ""
            r4_request = (f"stop{when}의 사유를 '{STOP_REASON_PREFIX}'로 시작하는 comment로 남긴 뒤 다시 "
                          "request 전환(담당자가 완료를 요청해 task를 ready-to-done 상태로 보내는 Jira 전환) 필요")
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
    tldr_label_missed = False                          # 바꿀 표시를 찾지 못하면 Actions 요약에만 남긴다
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

    # 3) 상태 전환과 comment (검수 모드만. 정리 모드의 알림은 apply_alerts가 처리)
    summary, state_line, comment_link = "", "", ""
    if mode == "review":
        state_note, state_line = review_transition(j, key, v, issue.get("status") or "?")
        notes.append(state_note)                       # 검수 comment의 검수 결과 줄 아래에 나간다
        summary = strip_claude_header((o / "comment.wiki").read_text(encoding="utf-8")) if (o / "comment.wiki").exists() else ""
        text = review_comment(v, checks, expected if itype == "Task" else [], items, extra, summary, requests, notes,
                              feedback if gate else None)   # 게이트 모드에서만 검수 표에 T4–T8이 들어간다
        if v == "escalate" and lead and f"[~accountid:{lead}]" not in text:
            text += f"\n\n팀장 확인 필요: [~accountid:{lead}]"
        elif v != "escalate" and assignee and f"[~accountid:{assignee}]" not in text:
            text += f"\n\n[~accountid:{assignee}]"
        try:                                           # 상태를 이미 바꿨을 수 있으므로 등록 실패를 남기고 Slack 알림은 그대로 보낸다
            posted = j.add_comment(key, sanitize_mentions(agent_comment(text), allowed))
        except JiraError as e:
            posted = None
            notes.append(f"검수 comment 등록 실패: {str(e)[:160]}")
        cid, issue_url = (posted or {}).get("id"), issue.get("url") or ""
        comment_link = f"{issue_url}?focusedCommentId={cid}" if cid and issue_url else ""
        if doc_checks and not gate:
            summ.extra += doc_review_md(key, doc_checks, feedback)
            try:
                j.add_comment(key, sanitize_mentions(agent_comment(doc_review_comment(doc_checks, feedback, lead)), allowed))
                notes.append(f"문서화 리뷰 보완 필요 {','.join(doc_fails)} (팀장 확인 comment)" if doc_fails else "문서화 리뷰 만족 (팀장 확인 comment)")
            except JiraError as e:
                notes.append(f"문서화 리뷰 comment 등록 실패: {str(e)[:160]}")
    if tldr_label_missed:
        notes.append("TL;DR의 검수 결과 표시를 찾지 못해 바꾸지 않음")
    if tldr_missing:                                   # 판정 변경 이유가 아니므로 검수 comment에 넣지 않고 Actions 요약에만 남긴다
        notes.append("TL;DR 출력 없음")

    # 4) property
    prop = j.prop_get(key)
    prop.update({"lastRunAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "lastMode": mode,
                 "inputHash": meta.get("inputHash", prop.get("inputHash"))})
    if mode == "review":                               # F3 정리는 판정을 내지 않으므로 검수 판정을 덮어쓰지 않는다
        prop["lastVerdict"] = v
        if itype == "Task":
            prop["docFails"] = doc_fails
    prop.setdefault("notified", [])
    j.prop_set(key, prop)

    # 5) Slack 알림 (검수 모드만). 보류는 담당자, 검토 요청과 통과는 팀장에게 보낸다
    if mode == "review":
        users = load_json(SLACK_USERS, {}) or {}
        if v == "fix":
            mention, who = slack_mention(issue.get("assignee"), users), "담당자"
        else:
            mention, who = (f"<@{users[lead]}>" if users.get(lead) else "팀장"), "팀장"
        reasons = review_slack_reasons(v, itype, expected, items, extra, checks, changes, summary)
        text = review_slack_text(v, key, issue.get("summary") or "", issue.get("url") or "", mention, state_line,
                                 reasons, requests, comment_link)
        sent = slack_post(text)
        notes.append(f"Slack 알림 전송({who})" if sent == "전송 완료" else f"Slack {VERDICT_KO[v]} 알림: {sent}")
    if requests:
        notes.append(f"requests {len(requests)}건")
    summ.row(key, itype, mode, VERDICT_KO[v], "; ".join(notes))


def apply_alerts(j: Jira, ctx: Path, lead: str, summ: Summary) -> None:
    scan = ctx / "_scan"
    if not scan.exists():
        return
    sent = 0
    for p in sorted(scan.glob("*.alerts.json")):
        a = load_json(p, {})
        key = a.get("key")
        if not key:
            continue
        prop = j.prop_get(key)
        notified = set(prop.get("notified", []))
        clear = tuple(a.get("clear", []))
        if clear:
            notified = {n for n in notified if not n.startswith(clear)}
        new = [al for al in a.get("alerts", []) if al["code"] not in notified]
        if new:
            assignee = (a.get("assignee") or {}).get("accountId") or ""
            allowed = {x for x in (assignee, lead) if x}
            lines = [f"* {al['message']}" for al in new]
            head = f"[~accountid:{assignee}] " if assignee else ""
            body = agent_comment(f"{head}정리 모드(task 키 없이 실행한 문서 정리)에서 확인한 누락 항목 {len(new)}건\n" + "\n".join(lines))
            try:
                j.add_comment(key, sanitize_mentions(body, allowed))
                notified.update(al["code"] for al in new)
                sent += 1
                summ.row(key, a.get("type", "?"), "digest", "알림", ", ".join(al["check"] for al in new))
            except JiraError as e:
                summ.row(key, a.get("type", "?"), "digest", "알림 실패", str(e)[:120])
        if set(prop.get("notified", [])) != notified:
            prop["notified"] = sorted(notified)
            j.prop_set(key, prop)
    print(f"[apply] 누락 알림 comment {sent}건")


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


def review_slack_reasons(v: str, itype: str, expected: list[dict], items: list[dict], extra: list[dict],
                         checks: list[dict], changes: list[str], summary: str) -> list[str]:
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
    names = [f"예상 산출물 {it['id']}번 \"{wiki_to_slack(texts.get(str(it['id']), it.get('result', '')))}\"" for it in missed]
    summary_lines = [wiki_to_slack(LIST_MARK_RE.sub("", s.strip())) for s in summary.splitlines() if s.strip()]
    out: list[str] = []
    if v == "fix":
        out += [f"사유 없는 미달성: {n}" for n, it in zip(names, missed) if not owner_reason(it)]
        out += [wiki_to_slack(c.get("detail") or "") if c["id"] in SENTENCE_DETAIL_CHECKS
                else f"{check_label(c['id'])}: {wiki_to_slack(c.get('detail') or '')}" for c in owner_fails(checks)]
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


def review_slack_text(v: str, key: str, title: str, url: str, mention: str, state_line: str, reasons: list[str],
                      requests: list[str], comment_link: str) -> str:
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


def weekly_lines(scan: Path, users: dict) -> tuple[list[str], list[str]]:
    """(Slack 본문 줄, 멘션 목록). 알림이 없으면 본문은 빈 목록."""
    rows: dict[str, list[dict]] = {cid: [] for cid, _ in ALERT_SECTIONS}
    mentions: list[str] = []
    for p in sorted(scan.glob("*.alerts.json")) if scan.exists() else []:
        a = load_json(p, {})
        for al in a.get("alerts", []):
            if al.get("check") in rows:
                rows[al["check"]].append({**a, "detail": al.get("detail") or al.get("message", "")})
    lines: list[str] = []
    for cid, title in ALERT_SECTIONS:
        items = rows[cid]
        if not items:
            continue
        lines += ["", f"*{title}* ({len(items)}건)"]
        for it in items[:ALERT_LIMIT]:
            who = slack_mention(it.get("assignee"), users)
            if who.startswith("<@") and who not in mentions:
                mentions.append(who)
            lines.append(f"{SLACK_INDENT}• {it['key']} {it.get('summary') or ''} — 담당 {who}, {it['detail']}")
            if it.get("url"):
                lines.append(f"{SLACK_INDENT}  {it['url']}")
        if len(items) > ALERT_LIMIT:
            lines.append(f"{SLACK_INDENT}• 외 {len(items) - ALERT_LIMIT}건")
    return lines, mentions


def slack_post(text: str) -> str:
    """Slack Incoming Webhook 전송. 나중에 봇 토큰으로 바꾸려면 이 함수만 교체한다."""
    url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not url:
        return "SLACK_WEBHOOK_URL 없음. 전송 건너뜀"
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "전송 완료" if r.status == 200 else f"전송 응답 {r.status}"
    except OSError as e:                               # HTTPError, URLError, TimeoutError는 모두 OSError의 하위 클래스
        return f"전송 실패: {e}"


def slack_weekly(ctx: Path, summ: Summary) -> None:
    """주간 점검 결과를 Slack 한 건으로 보낸다. 첫 줄이 스레드 제목이고 담당자를 멘션한다."""
    users = load_json(SLACK_USERS, {}) or {}
    lines, mentions = weekly_lines(ctx / "_scan", users)
    head = f"[{dt.datetime.now().strftime('%Y-%m-%d')} 전달사항] " + (" ".join(mentions) if mentions else "")
    if lines:
        text = "\n".join([head.rstrip(), "아래 task마다 사유 또는 진행 상황 comment 필요", *lines])
    else:
        text = "\n".join([f"[{dt.datetime.now().strftime('%Y-%m-%d')} 전달사항]", "점검 결과 이상 없음"])
    result = slack_post(text)
    note = f"알림 대상 {len(mentions)}명" if lines else "알림 없음"
    summ.row("-", "-", "alerts", result, note)
    summ.extra += [f"### 주간 점검 (Slack {result})", "", "```", text, "```", ""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", default="ctx")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mode", choices=["review", "digest", "alerts"], required=True)
    ap.add_argument("--gate", default="false")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    args = ap.parse_args()

    ctx, out = Path(args.ctx), Path(args.out)
    gate = str(args.gate).lower() == "true"
    lead = os.environ.get("JIRA_LEAD_ACCOUNT_ID", "")
    summ = Summary(args.summary)

    if args.mode == "alerts":                      # 주간 점검: Jira에 쓰지 않고 Slack으로만 보고
        slack_weekly(ctx, summ)
        summ.flush()
        return

    j = Jira()

    for d in sorted(ctx.glob("[A-Z]*-[0-9]*/")):
        if (d / "issue.json").exists():
            try:
                apply_issue(j, d, out, args.mode, gate, lead, summ)
            except JiraError as e:
                summ.row(d.name, "?", args.mode, "실패", f"Jira 오류: {str(e)[:160]}")

    if args.mode == "digest":
        apply_alerts(j, ctx, lead, summ)
        unlinked = load_json(ctx / "_org" / "unlinked_prs.json", [])
        if unlinked:
            summ.extra.append(f"### 미연결 PR (최근 3일 병합, 제목에 task 키 없음): {len(unlinked)}건")
            summ.extra.extend(f"- {p['repo']} [{p['title']}]({p['url']}) by {p.get('author')}" for p in unlinked)
    failed = out / "_failed.txt"
    if failed.exists():
        summ.extra.append("### Claude 단계가 처리하지 못한 task")
        summ.extra.append("```\n" + failed.read_text(encoding="utf-8") + "\n```")
    summ.flush()


if __name__ == "__main__":
    main()
