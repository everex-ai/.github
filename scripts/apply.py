#!/usr/bin/env python3
"""4단계: Claude의 출력(out/<KEY>/)을 검증해 Jira에 반영한다. Jira에 쓰는 유일한 단계.

반영 순서: 구역 교체(결과 산출물) -> TL;DR -> comment -> property -> 상태 전환(게이트 모드만)
- verdict.json이 없거나 형식이 틀리면 반영하지 않고 실패로 기록한다 (검수 모드면 팀장 멘션 comment).
- T3: 결과 산출물의 AC 번호가 precheck의 AC 목록과 1:1이 아니면 판정을 보류로 내린다.
- comment의 멘션은 담당자와 팀장 두 사람으로 제한한다.
- 정리 모드의 누락 알림은 ctx/_scan/*.alerts.json에서 만들고 property의 notified로 중복을 막는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from jira_api import AGENT_MARK, Jira, JiraError, set_section  # noqa: E402

VERDICT_KO = {"pass": "통과", "fix": "보완 요청", "escalate": "보류", None: "정리"}
TRANSITION_FOR = {"pass": "end", "fix": "rejected"}
MENTION_RE = re.compile(r"\[~(?:accountid:)?([^\]]+)\]")
SECTION_NOTE = {"Task": "agent가 comment, sub-task, PR 기록을 근거로 작성함. 직접 수정하지 말고 \"정정:\" comment로 남길 것."}


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
    for k in ("issueKey", "mode", "verdict", "checks", "acStatus", "requests"):
        if k not in v:
            return f"필수 키 없음: {k}"
    if v["issueKey"] != key:
        return f"issueKey 불일치: {v['issueKey']}"
    if v["mode"] != mode:
        return f"mode 불일치: {v['mode']}"
    if v["verdict"] not in ("pass", "fix", "escalate", None):
        return f"verdict 값 오류: {v['verdict']}"
    if mode == "review" and v["verdict"] is None:
        return "검수 모드인데 verdict가 null"
    if not isinstance(v["checks"], list) or not all(isinstance(c, dict) and c.get("result") in ("pass", "fail", "n/a") for c in v["checks"]):
        return "checks 형식 오류"
    if not isinstance(v["acStatus"], list) or not all(isinstance(a, dict) and "id" in a and isinstance(a.get("done"), bool) for a in v["acStatus"]):
        return "acStatus 형식 오류"
    if not isinstance(v["requests"], list):
        return "requests 형식 오류"
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
    pre = load_json(d / "precheck.json", {"ac": [], "checks": {}})
    meta = load_json(d / "meta.json", {})
    assignee = (issue.get("assignee") or {}).get("accountId") or ""
    allowed = {a for a in (assignee, lead) if a}
    o = out_dir / key
    verdict = load_json(o / "verdict.json")
    err = validate_verdict(verdict, mode, key) if verdict is not None else "verdict.json 없음 (Claude 단계 실패 또는 미완료)"
    if err is None and mode == "review" and itype == "Task" and not (o / "deliverables.wiki").exists():
        err = "deliverables.wiki 없음"

    if err:
        if mode == "review":
            body = agent_comment(f"자동 검수를 실행하지 못했습니다 ({err}). 팀장이 확인합니다.\n실행 로그: {run_url()}\n[~accountid:{lead}]" if lead else f"자동 검수를 실행하지 못했습니다 ({err}). 실행 로그: {run_url()}")
            try:
                j.add_comment(key, sanitize_mentions(body, allowed))
            except JiraError as e:
                print(f"[apply] {key}: 실패 comment 등록 실패: {e}", file=sys.stderr)
        summ.row(key, itype, mode, "실패", err)
        return

    v = verdict["verdict"]
    notes = []
    # T3: 결과 산출물의 AC 번호와 완료 기준의 AC 번호가 1:1인지 (Task 검수만)
    if mode == "review" and itype == "Task":
        want = {a["id"] for a in pre.get("ac", [])}
        got = {a["id"] for a in verdict.get("acStatus", [])}
        if want != got:
            notes.append(f"T3 불일치(AC {sorted(want)} vs 출력 {sorted(got)}) -> 보류")
            v = "escalate"

    # 1) description의 agent 구역 (Task 검수만). 방금 다시 읽어서 사람 구역이 바뀌었어도 보존한다
    fresh = j.issue(key, ["description"] + ([j.tldr_field] if j.tldr_field else []))
    desc = (fresh.get("fields") or {}).get("description") or ""
    stamp = now_stamp()
    fields: dict = {}
    if mode == "review" and itype == "Task":
        body = (o / "deliverables.wiki").read_text(encoding="utf-8")
        new_desc = set_section(desc, "결과 산출물", body, stamp, SECTION_NOTE["Task"])
        if new_desc != desc:
            fields["description"] = new_desc
    # 2) TL;DR
    if j.tldr_field and (o / "tldr.wiki").exists():
        tldr = (o / "tldr.wiki").read_text(encoding="utf-8").strip()
        if tldr:
            fields[j.tldr_field] = tldr
    else:
        notes.append("TL;DR 출력 없음")
    if fields:
        j.set_fields(key, fields)

    # 3) comment (검수 모드만. 정리 모드의 알림은 apply_alerts가 처리)
    if mode == "review":
        text = (o / "comment.wiki").read_text(encoding="utf-8") if (o / "comment.wiki").exists() else ""
        head = f"검수 결과: {VERDICT_KO[v]}"
        if not text.strip().startswith("검수 결과"):
            text = head + "\n" + text
        elif v != verdict["verdict"]:                       # T3로 판정이 바뀐 경우 첫 줄을 맞춘다
            text = head + "\n" + "\n".join(text.strip().splitlines()[1:])
        if notes:
            text += "\n\n" + "\n".join(f"* {n}" for n in notes)
        if v == "escalate" and lead and f"[~accountid:{lead}]" not in text:
            text += f"\n\n팀장 확인 요청: [~accountid:{lead}]"
        elif assignee and f"[~accountid:{assignee}]" not in text:
            text += f"\n\n[~accountid:{assignee}]"
        j.add_comment(key, sanitize_mentions(agent_comment(text), allowed))

    # 4) property
    prop = j.prop_get(key)
    prop.update({"lastRunAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "lastMode": mode,
                 "inputHash": meta.get("inputHash", prop.get("inputHash")), "lastVerdict": v})
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
    if verdict.get("requests"):
        notes.append(f"requests {len(verdict['requests'])}건")
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
            body = agent_comment(f"{head}아침 정리에서 확인된 누락 항목입니다.\n" + "\n".join(lines))
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", default="ctx")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mode", choices=["review", "digest"], required=True)
    ap.add_argument("--gate", default="false")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    args = ap.parse_args()

    ctx, out = Path(args.ctx), Path(args.out)
    gate = str(args.gate).lower() == "true"
    lead = os.environ.get("JIRA_LEAD_ACCOUNT_ID", "")
    j = Jira()
    summ = Summary(args.summary)

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
