#!/usr/bin/env python3
"""4단계: Claude의 출력(out/<KEY>/)을 검증해 Jira에 반영한다. Jira에 쓰는 유일한 단계.

alerts 모드(주간 점검)는 Jira에 쓰지 않고 ctx/_scan/*.alerts.json을 Slack 한 건으로 보낸다. 같은 모드에서 환경 변수 TOKEN_EXPIRY의
만료일이 30일 이하로 남은 토큰(인증 키)이 있으면 팀장 채널로 Slack 한 건을 더 보낸다.
report 모드는 scripts/report.py의 run_report가 만든 정기 보고 메시지를 팀장 채널로 보낸다.
decision 모드는 팀장이 ready-to-done ticket을 done(통과) 또는 In Progress(보류)로 전환했을 때 담당자에게 Slack 한 건을 보낸다.

반영 순서: 구역 교체(결과 산출물) -> TL;DR -> comment(검수 모드) -> property -> Slack 알림(검수 모드)
- out/<KEY>/ 파일은 CI agent가 아니라 scripts/save_output.py가 CI agent의 구조화 출력(JSON)으로 만든다. 구조화 출력에 없는 Task는
  판정 없음 기본값(verdict "no_verdict", items의 done null)으로 채워져 있다.
- verdict.json이 없거나 형식이 틀리거나 판정 없음 기본값이면 반영하지 않고 실패로 기록한다. 검수 모드에서 verdict.json은 있으나
  tldr.wiki나 comment.wiki가 없거나 비어 있어도 같은 실패로 처리한다. 실패한 Task에는 Jira comment를 남기지 않는다.
- 실패한 Task는 모아서 Slack 한 건(팀장 멘션)으로 알리고, 실패가 하나라도 있으면 종료 코드 1로 끝난다(Actions 실행 상태가 failure).
- 결과 산출물 구역과 검수 comment의 검사 표는 Claude의 verdict.json(items, extra, checks)을 받아 이 스크립트가
  정해진 형식으로 조립한다. Claude가 쓰는 자유 서술은 comment.wiki(결과 요약)와 tldr.wiki뿐이다.
- 검수 결과는 통과(pass), 검토 요청(escalate), 보류(fix)다. 괄호 안은 verdict.json의 코드값이다.
- T3: verdict.items의 번호가 precheck의 예상 산출물 번호(1..n)와 1:1이 아니면 검수 결과를 검토 요청으로 바꾼다.
- 문서화 리뷰 항목(Task T4–T9, Bug B5–B7, Issue I4–I5. 검수 모드): 관찰 모드는 검수 결과를 바꾸지 않고 팀장용 comment와 Actions Summary로 공유하고,
  게이트 모드는 미달이면 통과를 보류로 바꾼다. T8(초과 달성·미달성 사유)은 이 스크립트가 계산한다.
- Task 검수는 미달성 항목마다 담당자가 남긴 사유가 있으면 검토 요청으로, 통과인데 미달성 항목이 있으면 보류로 바꾼다.
- 상태 처리(검수 모드): 어떤 검수 결과에서도 Jira 상태를 바꾸지 않고 팀장이 확인 뒤 직접 전환한다. 게이트 모드는
  문서화 리뷰를 검수 결과에 넣을지만 정하고 전환과 관계없다.
- Slack 알림(검수 모드): 검수 결과와 관계없이 팀장에게 검수 결과, 이유, 검수 comment 링크를 보낸다. 담당자는 팀장이 상태를
  전환한 뒤 decision 모드의 팀장 확인 결과 알림으로 결과를 받는다.
- 실행 기록: 실행마다 jira-doc 기록(issue property ai-doc-agent)의 runs에 항목 하나를 추가한다(최근 100건). 반영 성공과 실패 모두 기록한다.
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
# 검수 결과를 다시 정하는 규칙(recheck_verdict). 검사 이름은 CHECK_NAMES, prompts/review.md와 prompts/types/<work type>/review.md의 검사 항목 표와 같다
OWNER_FIXABLE_CHECKS = ("T1", "T2", "R2", "R4", "B1", "B2", "B3", "B4", "I1", "I2", "I3")   # 담당자가 고칠 수 있는 검사. 실패하면 보류
SENTENCE_DETAIL_CHECKS = ("R4",)                                              # detail이 이유 문장이라 Slack 보류 이유에 검사 이름 없이 적는 검사
SLACK_INDENT = "    "                                                         # Slack 메시지의 묶음 항목 들여쓰기(공백 4칸). 검수 알림과 주간 점검에 사용
NOT_A_REASON = "근거 부족으로 확인 불가"                             # CI agent가 why에 넣는 문구. 담당자가 남긴 사유로 세지 않는다
READY_STATUS = "ready-to-done"                                       # Jira 상태 이름. 대소문자 무시로 비교
DECISION_KO = {"done": "통과", "in progress": "보류"}                  # 팀장 확인 결과: ready-to-done에서 전환한 도착 상태(소문자) -> 결과 이름
SLACK_CLOSING = {"escalate": "팀장이 ticket을 확인한 뒤 In Progress 또는 done으로 직접 전환 필요",
                 "pass": "팀장이 ticket을 확인한 뒤 done으로 직접 전환 필요",
                 "fix": "팀장이 ticket을 확인한 뒤 In Progress로 직접 전환 필요"}
MENTION_RE = re.compile(r"\[~(?:accountid:)?([^\]]+)\]")
WIKI_LINK_RE = re.compile(r"\[([^|\]]+)\|[^\]]*\]")                 # [제목|URL]
WIKI_LINK_PARTS_RE = re.compile(r"\[([^|\]]+)\|([^\]]*)\]")          # [제목|URL]에서 제목과 URL을 함께 잡는다 (Slack 링크로 바꿀 때)
WIKI_COLOR_RE = re.compile(r"\{color(?::[^}]*)?\}")                  # {color:#6b778c}, {color}
LIST_MARK_RE = re.compile(r"^[*#-]+\s+")                             # wiki 목록 표식("* ", "# ", "#* ")
SECTION_NOTE = {"Task": "agent가 comment, sub-task, PR 기록을 근거로 작성함. 고칠 내용은 직접 수정하지 않고 \"정정:\"으로 시작하는 comment로 요청 필요."}
SOURCE_KO = {"review": "검수", "digest": "F3 정리: Jira ticket 화면의 문서 정리 버튼"}     # 결과 산출물 구역의 안내 줄 끝에 붙여 출처를 보인다

# 검사 항목의 표시 순서와 이름. Jira comment의 검사 표는 항상 이 순서로, 이 이름으로 나간다 (ID는 괄호로 덧붙인다).
# 정의는 prompts/review.md와 prompts/types/<work type>/review.md의 검사 항목 표와 같다(A1, A3는 docs/jira-doc-agent/README.md의 "스크립트가 계산하는 값" 절).
CHECK_NAMES = {
    "T1": "예상 산출물 작성",
    "T2": "진행 배경 작성",
    "T3": "예상 산출물과 결과 산출물의 1:1 대응",
    "T4": "진행 배경 충실도",
    "T5": "예상 산출물 분할 단위",
    "T6": "진행 기록 comment",
    "T7": "sub-task 기록",
    "T8": "초과 달성·미달성 사유",
    "T9": "진행 배경부터 결과 산출물까지의 연결",
    "B1": "기본 정보와 문제(As-Is) 작성, 첨부 파일",
    "B2": "개선(To-Be) 작성",
    "B3": "원인과 해결의 근거 링크",
    "B4": "To-Be 동작 확인 comment",
    "B5": "문제(As-Is) 재현 정보",
    "B6": "원인 분석 기록",
    "B7": "해결 확인 기록",
    "I1": "이슈 유형 선택",
    "I2": "이슈 내용 작성",
    "I3": "대응 결과의 근거",
    "I4": "이슈 내용 충실도",
    "I5": "논의와 결정 기록",
    "R2": "1년 뒤에도 이해 가능한 기록",
    "R3": "개조식 작성",
    "R4": "stop 사유 comment",
    "A1": "영업일 기준 5일 이상 활동 없음",
    "A2": "완료 기준의 사후 변경",
    "A3": "ready-to-done 영업일 기준 3일 이상 대기",
}
CHECK_ORDER = {cid: i for i, cid in enumerate(CHECK_NAMES)}
RESULT_KO = {"pass": "✅ 만족", "fail": "❌ 보완 필요", "n/a": "➖ 해당 없음"}   # 검사 표 결과 칸. 모든 검사 항목에 같은 표시
# 문서화 리뷰(검수 모드)의 work type별 항목. 관찰 모드에서는 검수 결과를 바꾸지 않고 팀장용 comment로, 게이트 모드에서는 보류로 이어진다
DOC_CHECKS = {"Task": ("T4", "T5", "T6", "T7", "T8", "T9"), "Bug": ("B5", "B6", "B7"), "Issue": ("I4", "I5")}
ALL_DOC_CHECKS = tuple(cid for ids in DOC_CHECKS.values() for cid in ids)          # 세 work type의 문서화 리뷰 항목 전체
FEEDBACK_DOC_CHECKS = tuple(cid for cid in ALL_DOC_CHECKS if cid != "T8")         # CI agent가 feedback에 적는 항목. T8은 이 스크립트가 계산(check_t8)
CELL_BREAK = " \\\\ "                                                              # Jira wiki 표 칸 안의 줄바꿈
NO_REASON = "사유 미기재"
VERDICT_KEY_KO = {"issueKey": "ticket 키", "mode": "실행 모드", "verdict": "판정", "checks": "검사 결과 목록",
                  "items": "예상 산출물별 결과 목록", "extra": "초과 달성 목록", "requests": "담당자 요청 목록"}
VERDICT_FILE_KO = "CI agent(GitHub Actions에서 실행되는 문서화 Agent)의 판정을 저장한 파일(verdict.json)"   # 실패 알림에 나가는 이름
REVIEW_OUTPUT_FILES_KO = {"tldr.wiki": "TL;DR 본문", "comment.wiki": "검수 comment의 결과 요약"}   # 검수 모드에서 save_output.py가 verdict.json과 함께 쓰는 출력 파일과 실패 알림에 나가는 뜻
NO_VERDICT_KO = "CI agent 판정 없음"                                                  # save_output.py의 판정 없음 기본값(verdict "no_verdict", items의 done null)이 그대로 남았을 때의 실패 이유
# 주간 점검(alerts) Slack 보고
SLACK_USERS = Path(__file__).parent.parent / "config" / "slack-users.json"   # Jira accountId -> Slack member ID
# (검사 ID, 절 제목, 절 제목 뒤의 조치 문구). 절은 이 순서로 나간다
ALERT_SECTIONS = [("R4", "stop 사유 미기재", "담당자: stop 사유 comment 필요"),
                  ("A1", "영업일 기준 5일 이상 활동 없음", "담당자: 진행 상황 comment, 또는 멈춘 ticket이면 stop 전환 필요"),
                  ("A3", "ready-to-done 영업일 기준 3일 이상 대기", "팀장: done 전환, 또는 미루는 이유 comment 필요")]
LEAD_ALERTS = ("A3",)   # 팀장이 할 조치의 알림. 주간 점검 Slack(weekly_lines)에서 담당자 대신 팀장을 멘션한다
STATE_NOTE = ("상태를 바꾸지 않고 팀장 확인을 요청함", "상태 변경 없음")   # 어떤 검수 결과에서도 Jira 상태를 바꾸지 않는다. (검수 comment와 Actions 요약 문구, Slack 첫 줄 문구)
KST = dt.timezone(dt.timedelta(hours=9))                                      # 한국 시간. runs의 at과 토큰(인증 키) 만료일 계산에 사용
RUNS_MAX_BYTES = 30000                                                        # property 값 크기 상한(Jira 상한 32KB보다 작게). 넘으면 오래된 runs부터 지운다
RUNS_LIMIT = 100                                                              # jira-doc 기록(issue property)의 runs에 남기는 최근 실행 수
LEAD_WEBHOOK_ENV = "SLACK_LEAD_WEBHOOK_URL"                                   # 팀장 채널 webhook 환경 변수 이름
TOKEN_EXPIRY_DAYS = 30                                                        # 만료까지 남은 날이 이 값 이하인 토큰(인증 키)을 알린다
# 토큰(인증 키) 이름 -> (팀장이 발급하는 곳, 교체하는 곳)
TOKEN_RENEWAL = {
    "JIRA_API_TOKEN": ("Atlassian 계정의 API 토큰 화면에서 발급", "secret JIRA_API_TOKEN"),
    "ORG_READ_TOKEN": ("GitHub Settings의 Personal access tokens 발급", "secret ORG_READ_TOKEN"),
    "F1 PAT": ("GitHub Settings의 Personal access tokens 발급", "Jira Automation F1 설정"),
    "CLAUDE_CODE_OAUTH_TOKEN": ("`claude setup-token`으로 발급", "secret CLAUDE_CODE_OAUTH_TOKEN"),
}
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
    """초과 달성마다 추가 사유, 미달성 항목마다 담당자가 남긴 사유가 있는지 계산한다 (Task 검수만).

    미달성 항목의 why가 비었거나 CI agent가 넣는 NOT_A_REASON이면 담당자의 사유가 없는 것으로 센다(owner_reason).

    Args:
        expected: precheck.json의 예상 산출물 목록.
        items: verdict.json의 items.
        extra: verdict.json의 extra.

    Returns:
        T8 검사 결과(id, result, detail, 보완 필요이면 request).
    """
    if not expected:
        return {"id": "T8", "result": "n/a", "detail": "예상 산출물이 없어 대응할 수 없음"}
    no_why_miss = [str(it["id"]) for it in items if not it.get("done") and not owner_reason(it)]
    no_why_extra = [short_name(x["result"]) for x in extra if not (x.get("why") or "").strip()]
    if not no_why_miss and not no_why_extra:
        return {"id": "T8", "result": "pass", "detail": "미달성·초과 달성 항목 모두 사유 있음"}
    parts, asks = [], []
    if no_why_miss:
        parts.append(f"사유 없는 미달성 항목 {', '.join(no_why_miss)}번")
        asks.append(f"미달성 항목 {', '.join(no_why_miss)}번의 달성 근거 또는 미달성 사유")
    if no_why_extra:
        names = ", ".join(f"'{r}'" for r in no_why_extra)
        parts.append(f"사유 없는 초과 달성 {len(no_why_extra)}건({names})")
        asks.append(f"초과 달성 {names}의 추가 사유")
    return {"id": "T8", "result": "fail", "detail": ", ".join(parts), "request": f"{', '.join(asks)} comment 필요"}


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
        doc_checks: 문서화 리뷰 항목(Task T4–T9, Bug B5–B7, Issue I4–I5)의 검사 결과 목록.

    Returns:
        "보완 필요" 또는 "통과".
    """
    return "보완 필요" if any(c["result"] == "fail" for c in doc_checks) else "통과"


def doc_review_comment(doc_checks: list[dict], feedback: list[dict], lead: str) -> str:
    """관찰 모드의 팀장용 문서화 리뷰 comment. 첫 줄에 문서화 리뷰 결과 값(doc_review_result)을 적는다.

    Args:
        doc_checks: 문서화 리뷰 항목(Task T4–T9, Bug B5–B7, Issue I4–I5)의 검사 결과 목록.
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
        doc_checks: 문서화 리뷰 항목(Task T4–T9, Bug B5–B7, Issue I4–I5)의 검사 결과 목록.
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


def _has_text(path: Path) -> bool:
    """파일이 있고 공백이 아닌 내용이 있는지 확인한다. CI agent가 파일만 만들고 멈춘 경우를 없는 것과 같이 다루기 위함."""
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


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


def validate_verdict(v: object, mode: str, key: str) -> str | None:
    """schemas/verdict.json의 핵심 제약만 검사한다.

    feedback의 id는 CI agent가 적는 문서화 리뷰 항목(FEEDBACK_DOC_CHECKS: T4–T7, T9, B5–B7, I4–I5) 중 하나여야 한다.

    Args:
        v: verdict.json을 JSON으로 읽은 값.
        mode: 실행 모드(review: 검수, digest: F3 정리).
        key: task 키(ctx 폴더 이름).

    Returns:
        문제가 있으면 그 이유, 없으면 None. verdict가 "no_verdict"이거나 items에 done이 null인 항목이 있으면(save_output.py의
        판정 없음 기본값) NO_VERDICT_KO를 돌려준다.
    """
    if not isinstance(v, dict):
        return "verdict.json이 객체가 아님"
    for k in ("issueKey", "mode", "verdict", "checks", "items", "extra", "requests"):
        if k not in v:
            return f"필수 키 없음: {k}({VERDICT_KEY_KO[k]})"
    if v["issueKey"] != key:
        return f"issueKey(ticket 키) 불일치: {v['issueKey']}"
    if v["mode"] != mode:
        return f"mode(실행 모드) 불일치: {v['mode']}"
    items = v["items"]
    if v["verdict"] == "no_verdict" or (
        isinstance(items, list) and any(isinstance(a, dict) and "done" in a and a["done"] is None for a in items)
    ):
        return NO_VERDICT_KO
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
    if not isinstance(fb, list) or not all(isinstance(f, dict) and f.get("id") in FEEDBACK_DOC_CHECKS and isinstance(f.get("points"), list) for f in fb):
        return (f"feedback(문서화 리뷰 항목별 피드백) 형식 오류 (항목마다 id(검사 ID)는 {', '.join(check_label(c) for c in FEEDBACK_DOC_CHECKS)} "
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
        lines = ["## jira-doc 실행 결과", "", "| ticket | type | mode | 결과 | 비고 |", "|---|---|---|---|---|", *self.rows, "", *self.extra]
        text = "\n".join(lines) + "\n"
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text)


def usage_total(out_dir: Path) -> dict:
    """out/_usage.json의 항목을 합산한다.

    Args:
        out_dir: CI agent 출력 폴더. save_output.py가 out_dir/_usage.json(목록)에 실행마다 항목 하나를 추가한다.

    Returns:
        {"retry", "costUsd", "tokens", "models"}. 파일이 없으면 costUsd는 None, tokens는 0, retry는 False이다.
        costUsd는 값이 있는 항목의 합이고 값이 있는 항목이 없으면 None이다.
    """
    entries = load_json(out_dir / "_usage.json", [])
    entries = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    costs = [e["costUsd"] for e in entries if isinstance(e.get("costUsd"), (int, float))]
    tokens = {k: 0 for k in ("input", "output", "cacheWrite", "cacheRead")}
    models: list[str] = []
    for e in entries:
        t = e.get("tokens") if isinstance(e.get("tokens"), dict) else {}
        for k in tokens:
            tokens[k] += t[k] if isinstance(t.get(k), int) else 0
        models += [m for m in e.get("models") or [] if isinstance(m, str) and m not in models]
    return {"retry": any(e.get("retry") is True for e in entries), "costUsd": sum(costs) if costs else None,
            "tokens": tokens, "models": models}


def request_count(d: Path) -> int | None:
    """ticket이 지금까지 ready-to-done으로 전환된 횟수(몇 번째 request 전환인지).

    Args:
        d: 수집 단계가 만든 ctx/<KEY>/ 폴더. changelog.json의 상태 변경 이력을 읽는다.

    Returns:
        ready-to-done으로의 전환 횟수. changelog.json이 없거나 목록이 아니면 None.
    """
    changes = load_json(d / "changelog.json")
    if not isinstance(changes, list):
        return None
    return sum(1 for c in changes if isinstance(c, dict) and c.get("field") == "status"
               and str(c.get("to") or "").strip().lower() == READY_STATUS)


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
    return {"at": dt.datetime.now(KST).isoformat(timespec="seconds"), "mode": mode, "verdict": verdict,
            "requestNo": request_count(d) if mode == "review" else None, **usage}


def add_run(prop: dict, entry: dict) -> None:
    """property의 runs 목록 끝에 항목을 추가하고 최근 RUNS_LIMIT건만 남긴다.

    추가한 뒤 property를 JSON으로 만든 크기가 RUNS_MAX_BYTES(바이트)를 넘으면 오래된 runs부터 지운다.
    방금 추가한 항목은 지우지 않는다.

    Args:
        prop: jira-doc 기록(issue property). 이 dict를 고친다.
        entry: run_entry의 결과.
    """
    runs = prop.get("runs")
    prop["runs"] = ([r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else [])[-(RUNS_LIMIT - 1):] + [entry]
    while len(prop["runs"]) > 1 and len(json.dumps(prop, ensure_ascii=False).encode("utf-8")) > RUNS_MAX_BYTES:
        del prop["runs"][0]


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
    if verdict is None and (o / "verdict.json").exists():   # load_json은 JSON으로 읽을 수 없어도 None을 돌려준다
        err = f"{VERDICT_FILE_KO}을 JSON으로 읽을 수 없음"
    elif verdict is None:
        err = f"{VERDICT_FILE_KO} 없음. 추정: CI agent 실행이 실패했거나 끝나지 않음"
    elif problem := validate_verdict(verdict, mode, key):
        err = problem if problem == NO_VERDICT_KO else f"{VERDICT_FILE_KO}이 형식에 맞지 않음. {problem}. 키 정의는 schemas/verdict.json에 있음"
    else:
        err = None
    if err is None and mode == "review":     # 구조화 출력에 TL;DR 본문이나 검수 comment 결과 요약이 없으면 save_output.py가 파일을 쓰지 않는다
        absent = [ko for name, ko in REVIEW_OUTPUT_FILES_KO.items() if not _has_text(o / name)]
        if absent:
            err = f"CI agent 출력에 {', '.join(absent)} 없음 또는 비어 있음"
    try:
        cause = (o / "_agent_error.txt").read_text(encoding="utf-8").strip()
    except OSError:
        cause = ""
    if err is None and cause:   # save_output.py는 이 Task의 CI agent 출력이 없을 때만 원인을 쓴다. 예상 산출물이 없는 정리 모드 Task는 기본값만으로 형식 검사를 통과한다
        err = NO_VERDICT_KO

    if err:
        if cause:
            err += f". 원인: {cause}"
        summ.row(key, itype, mode, "실패", err)
        record_failed_run(j, d, out_dir, mode, summ)
        return {"key": key, "title": issue.get("summary") or "", "url": issue.get("url") or "", "reason": err}

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
            changes.append(f"{check_label('A2')} 결과에 따라 {VERDICT_KO[v]}에서 검토 요청으로 바꿈: 마지막 request 전환(담당자가 완료를 요청해 ticket을 ready-to-done 상태로 보내는 Jira 전환) 이후 완료 기준이 바뀜")
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
                          "request 전환(담당자가 완료를 요청해 ticket을 ready-to-done 상태로 보내는 Jira 전환) 필요")
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

    # 3) 상태 처리 문구와 comment (검수 모드만. Jira 상태는 바꾸지 않는다)
    summary, state_line, comment_link = "", "", ""
    if mode == "review":
        state_note, state_line = STATE_NOTE
        notes.append(state_note)                       # 검수 comment의 검수 결과 줄 아래에 나간다
        summary = strip_claude_header((o / "comment.wiki").read_text(encoding="utf-8"))   # 검수 모드는 위에서 comment.wiki가 있음을 확인함
        text = review_comment(v, checks, expected if itype == "Task" else [], items, extra, summary, requests, notes,
                              feedback if gate else None)   # 게이트 모드에서만 검수 표에 문서화 리뷰 항목이 들어간다
        if v == "escalate" and lead and f"[~accountid:{lead}]" not in text:
            text += f"\n\n팀장 확인 필요: [~accountid:{lead}]"
        elif v != "escalate" and assignee and f"[~accountid:{assignee}]" not in text:
            text += f"\n\n[~accountid:{assignee}]"
        try:                                           # 등록 실패를 남기고 Slack 알림은 그대로 보낸다
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
        if type_doc_checks:                            # 문서화 리뷰 항목이 있는 work type만 기록한다
            prop["docFails"] = doc_fails
    add_run(prop, run_entry(d, out_dir, mode, v if mode == "review" else "digest"))
    j.prop_set(key, prop)

    # 5) Slack 알림 (검수 모드만). 검수 결과와 관계없이 팀장에게 보낸다. 담당자는 팀장이 상태를 전환한 뒤 decision 모드로 알린다
    if mode == "review":
        users, warning = load_slack_users()
        if warning:
            notes.append(warning)
        mention, who = (f"<@{users[lead]}>" if users.get(lead) else "팀장"), "팀장"
        reasons = review_slack_reasons(v, itype, expected, items, extra, checks, changes, summary)
        text = review_slack_text(v, key, issue.get("summary") or "", issue.get("url") or "", mention, state_line,
                                 reasons, requests, comment_link)
        sent = slack_post(text)
        notes.append(f"Slack 알림 전송({who})" if sent == "전송 완료" else f"Slack {VERDICT_KO[v]} 알림: {sent}")
    if requests:
        notes.append(f"requests {len(requests)}건")
    summ.row(key, itype, mode, VERDICT_KO[v], "; ".join(notes))
    return None


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
                found = {"at": history.get("created") or "", "to": item.get("toString") or "",
                         "author": (history.get("author") or {}).get("accountId") or ""}
    return found


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


def apply_decision(j: Jira, key: str, lead: str, summ: Summary) -> None:
    """팀장이 ready-to-done ticket을 전환했을 때 담당자에게 팀장 확인 결과를 Slack으로 보낸다(decision 모드).

    ready-to-done에서 나간 마지막 전환이 done 또는 In Progress이고, 지금 상태가 그 도착 상태이며, 전환한 사람이
    팀장일 때만 보낸다. 그 밖에는 보내지 않고 Summary 탭에 이유를 남긴다.
    같은 전환을 두 번 알리지 않도록 알린 전환 시각을 jira-doc 기록의 lastDecisionAt에 남기고, 같으면 건너뛴다.

    Args:
        j: Jira 클라이언트.
        key: ticket 키.
        lead: 팀장 accountId. 비어 있으면 전환한 사람을 확인하지 않는다.
        summ: Actions 요약.
    """
    issue = j.issue(key, ["summary", "status", "assignee", "issuetype"])
    f = issue.get("fields") or {}
    itype = (f.get("issuetype") or {}).get("name") or "?"
    status = ((f.get("status") or {}).get("name") or "").strip()
    last = last_ready_exit(j.changelog(key))
    decision = DECISION_KO.get(status.lower())
    skip = ""
    if decision is None:
        skip = f"지금 상태가 {status or '?'}라 알리지 않음"
    elif last is None or last["to"].strip().lower() != status.lower():
        skip = "ready-to-done에서 지금 상태로 온 전환이 없어 알리지 않음"
    elif lead and last["author"] != lead:
        skip = "팀장이 한 전환이 아니라 알리지 않음"
    prop = j.prop_get(key) if not skip else {}
    if not skip and prop.get("lastDecisionAt") == last["at"]:
        skip = "이미 알린 전환이라 다시 알리지 않음"
    if skip:
        summ.row(key, itype, "decision", "건너뜀", skip)
        return
    site = j.site_url()
    users, warning = load_slack_users()
    text = decision_slack_text(key, f.get("summary") or "", f"{site}/browse/{key}" if site else "",
                               slack_mention(f.get("assignee"), users), decision, status)
    sent = slack_post(text)
    note = f"팀장 확인 결과 {decision}, 담당자에게 Slack 알림"
    if sent == "전송 완료":
        prop["lastDecisionAt"] = last["at"]
        try:
            j.prop_set(key, prop)
        except JiraError as e:
            note += f"; jira-doc 기록 저장 실패: {str(e)[:120]}"
    summ.row(key, itype, "decision", sent, f"{note}; {warning}" if warning else note)


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
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "전송 완료" if r.status == 200 else f"전송 응답 {r.status}"
    except OSError as e:                               # HTTPError, URLError, TimeoutError는 모두 OSError의 하위 클래스
        return f"전송 실패: {e}"


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
    result = slack_post(text)
    note = f"Slack 멘션 {len(mentions)}명(대응표에 있는 담당자와 팀장만 셈)" if alerted else "알림 없음"
    summ.row("-", "-", "alerts", result, f"{note}; {warning}" if warning else note)
    summary_text, _, _ = weekly_text({})
    summ.extra += [f"### 주간 점검 (Slack {result})", "", "```", summary_text, "```", ""]


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


def slack_token_expiry(summ: Summary) -> None:
    """토큰(인증 키) 만료 알림을 팀장 채널로 Slack 한 건 보낸다. 보낼 항목이 없으면 아무것도 하지 않는다.

    Args:
        summ: Actions 요약. 전송 결과나 경고를 행으로 남긴다.
    """
    text, warning = token_expiry_text(os.environ.get("TOKEN_EXPIRY", ""), dt.datetime.now(KST).date())
    if warning:
        summ.row("-", "-", "alerts", "경고", warning)
    if text:
        summ.row("-", "-", "alerts", slack_post(text, LEAD_WEBHOOK_ENV), "토큰(인증 키) 만료 알림(팀장 채널)")


def last_month(today: dt.date) -> str:
    """오늘이 속한 달의 지난달을 YYYY-MM으로 돌려준다.

    Args:
        today: 오늘 날짜(한국 시간).

    Returns:
        지난달(YYYY-MM).
    """
    return (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", default="ctx")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mode", choices=["review", "digest", "alerts", "report", "decision"], required=True)
    ap.add_argument("--issue", default="", help="decision 모드의 대상 ticket 키")
    ap.add_argument("--month", default="", help="report 모드의 보고 대상 달(YYYY-MM). 비우면 한국 시간 기준 지난달")
    ap.add_argument("--gate", default="false")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    args = ap.parse_args()

    ctx, out = Path(args.ctx), Path(args.out)
    gate = str(args.gate).lower() == "true"
    lead = os.environ.get("JIRA_LEAD_ACCOUNT_ID", "")
    summ = Summary(args.summary)

    if args.mode == "alerts":                      # 주간 점검: Jira에 쓰지 않고 Slack으로만 보고
        slack_weekly(ctx, summ, lead)
        slack_token_expiry(summ)
        summ.flush()
        return

    if args.mode == "decision":                    # 팀장 확인 결과 알림: ctx를 읽지 않고 Jira에서 그 ticket만 읽는다
        if not args.issue.strip():
            sys.exit("decision 모드에는 --issue가 필요하다")
        apply_decision(Jira(), args.issue.strip(), lead, summ)
        summ.flush()
        return

    if args.mode == "report":                      # 정기 보고: ctx를 읽지 않고 report.py가 Jira 기록으로 본문을 만든다
        month = args.month or last_month(dt.datetime.now(KST).date())
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            sys.exit(f"--month는 YYYY-MM 형식이어야 한다: {month}")
        from report import run_report

        texts = run_report(Jira(), month, lead, summ)   # 12월 보고는 연간 묶음이 두 번째 메시지다
        for i, text in enumerate(texts, 1):
            summ.row("-", "-", "report", slack_post(text, LEAD_WEBHOOK_ENV), f"{month} 정기 보고 {i}/{len(texts)}")
        summ.flush()
        return

    j = Jira()

    failures: list[dict] = []                      # 반영하지 못한 Task. 끝에서 Slack 한 건으로 알린다
    for d in sorted(ctx.glob("[A-Z]*-[0-9]*/")):
        if (d / "issue.json").exists():
            try:
                failure = apply_issue(j, d, out, args.mode, gate, lead, summ)
            except JiraError as e:
                reason = f"Jira 오류: {str(e)[:160]}"
                summ.row(d.name, "?", args.mode, "실패", reason)
                record_failed_run(j, d, out, args.mode, summ)
                issue = load_json(d / "issue.json", {})
                failure = {"key": d.name, "title": issue.get("summary") or "", "url": issue.get("url") or "", "reason": reason}
            if failure:
                failures.append(failure)

    failed = out / "_failed.txt"
    if failed.exists():
        summ.extra.append("### Claude 단계가 처리하지 못한 ticket")
        summ.extra.append("```\n" + failed.read_text(encoding="utf-8") + "\n```")
    if failures:        # Actions Summary는 GitHub에 로그인한 누구나 볼 수 있어, Summary에는 빈 대응표로 다시 만든 본문을 남긴다
        users, warning = load_slack_users()
        result = slack_post(failure_slack_text(args.mode, failures, users, lead))
        note = f"실패 알림 ticket {len(failures)}건"
        summ.row("-", "-", args.mode, result, f"{note}; {warning}" if warning else note)
        summ.extra += [f"### 실패 알림 (Slack {result})", "", "```", failure_slack_text(args.mode, failures, {}, lead), "```", ""]
    summ.flush()
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
