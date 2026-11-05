"""검수 comment, 문서화 리뷰 comment, 결과 산출물 구역의 wiki 본문 조립."""

from __future__ import annotations

import re

from src.constant.check import (
    ATTACHMENT_CONTENT_KO,
    ATTACHMENT_DESCRIPTION_KO,
    ATTACHMENT_LINE,
    ATTACHMENT_MEDIA_ID_RE,
    ATTACHMENT_NOTE,
    CHECK_ORDER,
    NO_REASON,
    RESULT_KO,
    VERDICT_KO,
)
from src.constant.jira import AGENT_MARK
from src.jira.verdict import check_label, doc_requests, doc_review_result
from src.jira.wiki import CELL_BREAK, MENTION_RE, cell_text


def result_label(result: str) -> str:
    """검사 결과 값(pass, fail, n/a)을 검사 표 결과 칸의 표시 문구로 바꾼다."""
    return RESULT_KO.get(result, result)


def attachment_lines(attachments: list[dict] | None) -> list[str]:
    """verdict.json의 attachments(R5 첨부별 결과)를 검사 표 R5 칸의 줄 목록으로 만든다.

    줄 꼴은 "{file}: 설명 존재 {설명 기준}, 내용 일치 {내용 기준} – {note}"다(ATTACHMENT_LINE, ATTACHMENT_NOTE).
    note가 비면 " – " 뒤를 뺀다. 파일 이름 끝의 Jira media id(ATTACHMENT_MEDIA_ID_RE)는 지운다. 목록이 아니면 빈 목록을 돌려주고,
    file이 문자열이 아니거나 설명 기준, 내용 기준 값이 정해진 값이 아닌 항목은 건너뛴다.

    Args:
        attachments: verdict.json의 attachments. 없으면 None.

    Returns:
        첨부마다 한 줄. 표시할 항목이 없으면 빈 목록.
    """
    if not isinstance(attachments, list):
        return []
    lines = []
    for a in attachments:
        if not isinstance(a, dict) or not isinstance(a.get("file"), str) or not a["file"].strip():
            continue
        if a.get("description") not in ATTACHMENT_DESCRIPTION_KO or a.get("content") not in ATTACHMENT_CONTENT_KO:
            continue
        line = ATTACHMENT_LINE.format(
            file=re.sub(ATTACHMENT_MEDIA_ID_RE, "", a["file"].strip()),
            description=ATTACHMENT_DESCRIPTION_KO[a["description"]],
            content=ATTACHMENT_CONTENT_KO[a["content"]],
        )
        note = str(a.get("note") or "").strip()
        lines.append(line + (ATTACHMENT_NOTE.format(note=note) if note else ""))
    return lines


def check_table(checks: list[dict], feedback: list[dict] | None = None, attachments: list[dict] | None = None) -> str:
    """verdict.checks를 고정 순서로 정렬해 wiki 표로 만든다.

    같은 ID가 여러 번 있으면 마지막 것을 쓴다. 문서화 리뷰 항목은 feedback의 points가 있으면 그것을 내용 칸에
    쓴다(없으면 detail). R5는 attachments가 있으면 첨부마다 한 줄(attachment_lines)을 내용 칸에 쓴다(없으면 detail).
    내용 칸의 문장은 cell_text로 바꿔 링크 밖의 "|"만 "/"로 바꾼다.

    Args:
        checks: 검사 결과 목록.
        feedback: verdict.json의 feedback(문서화 리뷰 항목별 피드백). 없으면 detail만 쓴다.
        attachments: verdict.json의 attachments(R5 첨부별 결과). 없으면 R5도 detail을 쓴다.

    Returns:
        검사 표(wiki markup).
    """
    by_id: dict[str, dict] = {}
    for c in checks:
        by_id[c["id"]] = c
    points_by_id = {f["id"]: f.get("points") or [] for f in feedback or []}
    if lines := attachment_lines(attachments):
        points_by_id["R5"] = lines
    rows = ["|| 검사 || 결과 || 내용 ||"]
    for cid in sorted(by_id, key=lambda x: (CHECK_ORDER.get(x, 99), x)):
        c = by_id[cid]
        texts = points_by_id.get(cid) or [c.get("detail") or ""]
        cell = CELL_BREAK.join(cell_text(t).replace("\n", " ").strip() for t in texts if t.strip())
        rows.append(f"| {check_label(cid)} | {result_label(c['result'])} | {cell} |")
    return "\n".join(rows)


def deliverables_wiki(expected: list[dict], items: list[dict], extra: list[dict], final: bool = True) -> str:
    """결과 산출물 구역 본문. 예상 산출물 번호 순서로 한 항목씩, 그 뒤에 초과 달성 목록.

    final=False(F3 정리)면 사유가 기록에 없을 때 "사유 미기재" 줄을 쓰지 않는다. 진행 중인 항목은 아직 사유가 없기 때문.
    """
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
    lines = [
        f"### 문서화 리뷰 {key}(관찰 모드): {doc_review_result(doc_checks)}",
        "",
        "| 검사 | 결과 | 피드백 |",
        "|---|---|---|",
    ]
    for c in sorted(doc_checks, key=lambda x: CHECK_ORDER.get(x["id"], 99)):
        pts = by_id.get(c["id"], {}).get("points") or [c.get("detail") or ""]
        txt = "<br>".join(pt.replace("|", "/") for pt in pts if pt)
        lines.append(f"| {check_label(c['id'])} | {result_label(c['result'])} | {txt} |")
    return lines + [""]


def review_comment(
    v: str,
    checks: list[dict],
    expected: list[dict],
    items: list[dict],
    extra: list[dict],
    summary: str,
    requests: list[str],
    notes: list[str],
    feedback: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> str:
    """검수 comment. 판정과 판정을 바꾼 이유, 달성 요약, 결과 요약, 요청을 먼저 적고 검사 표를 마지막에 적는다.

    Args:
        v: 최종 검수 결과 코드값(pass, fix, escalate).
        checks: 검사 표에 들어가는 검사 결과 목록.
        expected: precheck.json의 예상 산출물 목록. Task가 아니면 빈 목록.
        items: verdict.json의 items.
        extra: verdict.json의 extra.
        summary: 결과 요약(comment.wiki).
        requests: 요청 목록.
        notes: 검수 결과 줄 아래에 적는 문장 목록.
        feedback: 문서화 리뷰 항목별 피드백. 게이트 모드에서만 준다.
        attachments: verdict.json의 attachments(R5 첨부별 결과). 검사 표의 R5 칸에 쓴다.

    Returns:
        검수 comment 본문(wiki markup).
    """
    parts = [f"검수 결과: *{VERDICT_KO[v]}*"] + [f"* {n}" for n in notes]
    if expected:
        parts += ["", deliverables_summary(expected, items, extra) + " (자세한 내용은 description의 결과 산출물 구역)"]
    if summary:
        parts += ["", "*결과 요약*", summary]
    if requests:
        parts += ["", "*요청*"] + [f"# {r}" for r in requests]
    parts += ["", "*검사 항목*", check_table(checks, feedback, attachments)]
    return "\n".join(parts)


def sanitize_mentions(text: str, allowed: set[str]) -> str:
    """allowed에 없는 accountId 멘션을 지운다.

    Args:
        text: comment 본문(wiki markup).
        allowed: 멘션을 남길 accountId 집합.

    Returns:
        멘션을 거른 본문.
    """

    def repl(m: re.Match[str]) -> str:
        return m.group(0) if m.group(1) in allowed else ""

    return MENTION_RE.sub(repl, text)


def agent_comment(body: str) -> str:
    """본문 첫 줄에 agent comment 표식(AGENT_MARK)이 없으면 붙인다."""
    body = body.strip()
    return body if body.startswith(AGENT_MARK) else f"{AGENT_MARK}\n{body}"
