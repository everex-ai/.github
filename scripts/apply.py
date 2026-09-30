#!/usr/bin/env python3
"""4단계: Claude의 출력(out/<KEY>/)을 검증해 Jira에 반영한다. Jira에 쓰는 유일한 단계.

alerts 모드(주간 점검)는 Jira에 쓰지 않고 ctx/_scan/*.alerts.json을 Slack 한 건으로 보낸다.

반영 순서: 구역 교체(결과 산출물) -> TL;DR -> comment -> property -> 상태 전환(게이트 모드만)
- verdict.json이 없거나 형식이 틀리면 반영하지 않고 실패로 기록한다 (검수 모드면 팀장 멘션 comment).
- 결과 산출물 구역과 검수 comment의 검사 표는 Claude의 verdict.json(items, extra, checks)을 받아 이 스크립트가
  정해진 형식으로 조립한다. Claude가 쓰는 자유 서술은 comment.wiki(결과 요약)와 tldr.wiki뿐이다.
- T3: verdict.items의 번호가 precheck의 예상 산출물 번호(1..n)와 1:1이 아니면 판정을 보류로 내린다.
- 문서화 리뷰(T4~T8, Task 검수): 관찰 모드는 판정을 바꾸지 않고 팀장용 comment와 Actions Summary로 공유하고,
  게이트 모드는 미달이면 통과를 보완 요청으로 내린다. T8(초과 달성·미달성 사유)은 이 스크립트가 계산한다.
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
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jira_api import AGENT_MARK, Jira, JiraError, set_section  # noqa: E402

VERDICT_KO = {"pass": "통과", "fix": "보완 요청", "escalate": "보류", None: "정리"}
TRANSITION_FOR = {"pass": "end", "fix": "rejected"}
MENTION_RE = re.compile(r"\[~(?:accountid:)?([^\]]+)\]")
WIKI_LINK_RE = re.compile(r"\[([^|\]]+)\|[^\]]*\]")                 # [제목|URL]
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
    "A1": "5영업일 이상 활동 없음",
    "A2": "완료 기준의 사후 변경",
}
CHECK_ORDER = {cid: i for i, cid in enumerate(CHECK_NAMES)}
RESULT_KO = {"pass": "✅ 만족", "fail": "❌ 보완 필요", "n/a": "➖ 해당 없음"}   # 검사 표 결과 칸. 모든 검사 항목에 같은 표시
# 문서화 리뷰(Task 검수). 관찰 모드에서는 판정을 바꾸지 않고 팀장용 comment로, 게이트 모드에서는 보완 요청으로 이어진다
DOC_CHECKS = ("T4", "T5", "T6", "T7", "T8")
CELL_BREAK = " \\\\ "                                                              # Jira wiki 표 칸 안의 줄바꿈
NO_REASON = "사유 미기재"
VERDICT_KEY_KO = {"issueKey": "task 키", "mode": "실행 모드", "verdict": "판정", "checks": "검사 결과 목록",
                  "items": "예상 산출물별 결과 목록", "extra": "초과 달성 목록", "requests": "담당자 요청 목록"}
VERDICT_FILE_KO = "CI agent(GitHub Actions에서 실행되는 문서화 Agent)의 판정 파일(verdict.json)"   # 실패 알림에 나가는 이름
OBSERVE_NOTE = "관찰 모드: 문서화 리뷰 결과가 판정을 바꾸지 않고 이 comment로 팀장에게만 공유됨"
GATE_KO = "게이트 모드(문서화 리뷰에 보완 필요 항목이 있으면 통과를 보완 요청으로 내리는 모드)"
# 주간 점검(alerts) Slack 보고
SLACK_USERS = Path(__file__).parent.parent / "config" / "slack-users.json"   # Jira accountId -> Slack member ID
ALERT_SECTIONS = [("R4", "stop 사유 미기재"), ("A1", "5영업일 이상 활동 없음")]
ALERT_LIMIT = 15


def result_label(result: str) -> str:
    """검사 결과 값(pass, fail, n/a)을 검사 표 결과 칸의 표시 문구로 바꾼다."""
    return RESULT_KO.get(result, result)


def check_label(cid: str) -> str:
    """검사 ID를 검사 표와 같은 꼴 "검사 이름 (검사 ID)"로 적는다. 검사 표보다 먼저 나오는 문장에서 ID만 쓰지 않기 위함."""
    return f"{CHECK_NAMES.get(cid, cid)} ({cid})"


def check_table(checks: list[dict], feedback: list[dict] | None = None) -> str:
    """verdict.checks를 고정 순서로 정렬해 wiki 표로 만든다. 같은 ID가 여러 번 있으면 마지막 것을 쓴다.
    문서화 리뷰 항목은 feedback의 points가 있으면 그것을 내용 칸에 쓴다(없으면 detail)."""
    by_id: dict[str, dict] = {}
    for c in checks:
        by_id[c["id"]] = c
    points_by_id = {f["id"]: f.get("points") or [] for f in feedback or []}
    rows = ["|| 검사 || 결과 || 내용 ||"]
    for cid in sorted(by_id, key=lambda x: (CHECK_ORDER.get(x, 99), x)):
        c = by_id[cid]
        texts = points_by_id.get(cid) or [c.get("detail") or ""]
        cell = CELL_BREAK.join(t.replace("|", "/").replace("\n", " ").strip() for t in texts if t.strip())
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
    """fail인 문서화 리뷰 항목의 보완 요청 문장."""
    by_id = {f["id"]: f for f in feedback}
    out = []
    for c in doc_checks:
        if c["result"] != "fail":
            continue
        req = c.get("request") if c["id"] == "T8" else (by_id.get(c["id"], {}).get("request") or "").strip()
        if req:
            out.append(req)
    return out


def doc_review_comment(key: str, main_v: str, gate_v: str, doc_checks: list[dict], feedback: list[dict], lead: str) -> str:
    """관찰 모드의 팀장용 문서화 리뷰 comment."""
    parts = [f"문서화 리뷰 (팀장 확인용): {key}", OBSERVE_NOTE, "",
             f"담당자에게 보낸 검수 결과: *{VERDICT_KO[main_v]}* / {GATE_KO}였다면: *{VERDICT_KO[gate_v]}*",
             "", check_table(doc_checks, feedback)]
    reqs = doc_requests(doc_checks, feedback)
    if reqs:
        parts += ["", "*보완 요청 후보* (게이트 모드라면 담당자에게 보냈을 내용)"] + [f"# {r}" for r in reqs]
    if lead:
        parts += ["", f"[~accountid:{lead}]"]
    return "\n".join(parts)


def doc_review_md(key: str, main_v: str, gate_v: str, doc_checks: list[dict], feedback: list[dict]) -> list[str]:
    """Actions Summary용 문서화 리뷰 (markdown)."""
    by_id = {f["id"]: f for f in feedback}
    lines = [f"### 문서화 리뷰 {key} (관찰 모드): 검수 {VERDICT_KO[main_v]} / 게이트 모드였다면 {VERDICT_KO[gate_v]}", "",
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


def apply_issue(j: Jira, d: Path, out_dir: Path, mode: str, gate: bool, lead: str, summ: Summary) -> None:
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

    v = verdict["verdict"]
    notes = []
    checks = [c for c in verdict["checks"] if c["id"] != "T3"]           # T3는 Claude가 아니라 여기서 계산한다
    expected, items, extra = pre.get("expected", []), verdict["items"], verdict["extra"]
    # 스크립트가 계산한 검사(precheck.json)는 Claude가 옮겨 적은 값보다 우선한다. 판정도 그 규칙에 맞춘다
    script_checks = {cid: c for cid, c in pre.get("checks", {}).items() if c.get("result") in ("pass", "fail", "n/a")}
    checks = [c for c in checks if c["id"] not in script_checks] + [{"id": cid, **c} for cid, c in script_checks.items()]
    if mode == "review":
        if script_checks.get("A2", {}).get("result") == "fail" and v != "escalate":
            notes.append(f"{check_label('A2')} 결과에 따라 보류로 내림: 마지막 request 전환(담당자가 완료를 요청해 task를 Ready-to-Done 상태로 보내는 Jira 전환) 이후 완료 기준이 바뀜")
            v = "escalate"
        elif v == "pass" and (fails := [cid for cid in ("T1", "T2", "B1", "B2", "I1", "I2", "R4") if script_checks.get(cid, {}).get("result") == "fail"]):
            notes.append(f"스크립트 검사에 보완 필요 항목이 있어 통과를 보완 요청으로 내림: {', '.join(check_label(cid) for cid in fails)}")
            v = "fix"
    # T3: Claude가 낸 items의 번호가 예상 산출물 번호(1..n)와 1:1인지 (Task)
    # 검수에서는 어긋나면 보류로 내리고, F3 정리는 판정이 없으므로 결과 산출물을 갱신하지 않는다
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
            checks.append({"id": "T3", "result": "fail", "detail": f"예상 산출물 번호({', '.join(want)})와 CI agent 출력 번호({', '.join(got)})가 다름. CI agent 출력 오류로 간주해 보류"})
            notes.append(f"{check_label('T3')} 결과에 따라 보류로 내림: 예상 산출물과 결과 산출물의 번호가 맞지 않음")
            v = "escalate"

    # 문서화 리뷰 (Task 검수만): Claude가 T4~T7과 feedback을 내고 T8은 여기서 계산한다.
    # 관찰 모드는 판정을 그대로 두고 팀장용 comment로 공유, 게이트 모드는 미달이면 통과를 보완 요청으로 내린다
    requests = list(verdict["requests"])
    feedback = verdict.get("feedback") or []
    doc_checks = [c for c in checks if c["id"] in DOC_CHECKS and c["id"] != "T8"]
    checks = [c for c in checks if c["id"] not in DOC_CHECKS]
    doc_fails: list[str] = []
    gate_v = v
    if mode == "review" and itype == "Task":
        doc_checks.append(check_t8(expected, items, extra))
        missing = [cid for cid in DOC_CHECKS if cid not in {c["id"] for c in doc_checks}]
        if missing:
            notes.append(f"문서화 리뷰 항목 누락: {', '.join(check_label(cid) for cid in missing)}")
        doc_fails = [c["id"] for c in doc_checks if c["result"] == "fail"]
        if doc_fails and v == "pass":
            gate_v = "fix"
        if gate:
            checks += doc_checks
            requests += [r for r in doc_requests(doc_checks, feedback) if r not in requests]
            if gate_v != v:
                notes.append(f"문서화 리뷰에 보완 필요 항목이 있어 통과를 보완 요청으로 내림: {', '.join(check_label(cid) for cid in doc_fails)}")
                v = gate_v
    else:
        doc_checks = []

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
    # 2) TL;DR
    tldr_missing = not (j.tldr_field and (o / "tldr.wiki").exists())
    if not tldr_missing:
        tldr = (o / "tldr.wiki").read_text(encoding="utf-8").strip()
        if tldr:
            fields[j.tldr_field] = tldr
    if fields:
        j.set_fields(key, fields)

    # 3) comment (검수 모드만. 정리 모드의 알림은 apply_alerts가 처리)
    if mode == "review":
        summary = strip_claude_header((o / "comment.wiki").read_text(encoding="utf-8")) if (o / "comment.wiki").exists() else ""
        text = review_comment(v, checks, expected if itype == "Task" else [], items, extra, summary, requests, notes,
                              feedback if gate else None)   # 게이트 모드에서만 검수 표에 T4~T8이 들어간다
        if v == "escalate" and lead and f"[~accountid:{lead}]" not in text:
            text += f"\n\n팀장 확인 필요: [~accountid:{lead}]"
        elif v != "escalate" and assignee and f"[~accountid:{assignee}]" not in text:
            text += f"\n\n[~accountid:{assignee}]"
        j.add_comment(key, sanitize_mentions(agent_comment(text), allowed))
        if doc_checks and not gate:
            j.add_comment(key, sanitize_mentions(agent_comment(doc_review_comment(key, v, gate_v, doc_checks, feedback, lead)), allowed))
            summ.extra += doc_review_md(key, v, gate_v, doc_checks, feedback)
            notes.append(f"문서화 리뷰 보완 필요 {','.join(doc_fails)} (팀장 확인 comment)" if doc_fails else "문서화 리뷰 만족 (팀장 확인 comment)")
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

    # 5) 상태 전환 (게이트 모드, 검수만)
    result = VERDICT_KO[v]
    if mode == "review" and gate and v in TRANSITION_FOR:
        try:
            j.transition(key, TRANSITION_FOR[v])
            notes.append(f"전환 {TRANSITION_FOR[v]} 실행")
        except JiraError as e:
            notes.append(f"전환 실패: {e}")
    elif mode == "review" and v in TRANSITION_FOR:
        notes.append(f"관찰 모드: {TRANSITION_FOR[v]} 전환은 담당자가 실행")
    if requests:
        notes.append(f"requests {len(requests)}건")
    summ.row(key, itype, mode, result, "; ".join(notes))


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
            lines.append(f"• {it['key']} {it.get('summary') or ''} — 담당 {who}, {it['detail']}")
            if it.get("url"):
                lines.append(f"  {it['url']}")
        if len(items) > ALERT_LIMIT:
            lines.append(f"• 외 {len(items) - ALERT_LIMIT}건")
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
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
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
