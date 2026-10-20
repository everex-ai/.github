"""CI agent의 구조화 출력을 out/<KEY>/ 판정 파일로 저장하는 함수(save_output.py가 부름)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.constant.files import TASK_FILES

VERDICT_KEYS = (
    "verdict",
    "checks",
    "items",
    "extra",
    "requests",
    "feedback",
    "slack",
)  # 구조화 출력의 verdict 객체에서 덮어쓰는 키
CHECK_RESULTS = ("pass", "fail", "n/a")
FAILURE_TEXT_LIMIT = 300  # 실패한 실행의 result 문구를 옮기는 길이


def load_json(path: Path) -> object:
    """JSON 파일을 읽는다. 없거나 읽을 수 없으면 None을 돌려준다.

    Args:
        path: 파일 경로.

    Returns:
        JSON으로 읽은 값 또는 None.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def one_line(text: str, limit: int | None = None) -> str:
    """줄바꿈을 공백으로 바꿔 한 줄로 만든다.

    Args:
        text: 원문.
        limit: 앞에서 자를 글자 수. None이면 자르지 않는다.

    Returns:
        한 줄 문장.
    """
    line = re.sub(r"\s*[\r\n]+\s*", " ", text).strip()
    return line if limit is None else line[:limit]


def no_output_reason(read_ok: bool, result: dict | None, claude_outcome: str) -> str:
    """구조화 출력이 없을 때의 원인 한 줄. 성공한 실행의 result는 Jira 기록이 담길 수 있어 옮기지 않는다.

    Args:
        read_ok: 실행 결과 파일을 읽었는지.
        result: 마지막 result 메시지.
        claude_outcome: Claude 문서화 단계(Actions 워크플로 3단계)의 결과.

    Returns:
        원인 한 줄.
    """
    outcome = claude_outcome or "알 수 없음"
    if not read_ok:
        return f"실행 결과 파일 없음(Claude 문서화 단계(Actions 워크플로 3단계) 결과: {outcome})"
    if result is None:
        return f"실행 결과 파일에 result 메시지 없음(Claude 문서화 단계(Actions 워크플로 3단계) 결과: {outcome})"
    reason = f"Claude 문서화 단계 종료 종류(subtype): {result.get('subtype')}"
    if result.get("is_error") is True:
        reason += f"; 실패 문구: {one_line(str(result.get('result') or ''), FAILURE_TEXT_LIMIT)}"
    return reason


def build_verdict(d: Path, key: str, mode: str, agent: dict | None) -> dict:
    """판정 없음 기본값을 만들고, 구조화 출력의 verdict 객체에 있는 키만 덮어쓴다.

    기본값은 issueKey(폴더 이름), mode, verdict(검수 모드 "no_verdict", 정리 모드 null), checks(precheck.json의 스크립트 검사 결과),
    items(예상 산출물마다 done null), extra, requests, feedback, slack(빈 목록)이다. 구조화 출력의 issueKey, mode는 무시한다.

    Args:
        d: ctx/<KEY>/ 폴더.
        key: task 키(폴더 이름).
        mode: 실행 모드(review, digest).
        agent: 구조화 출력의 tasks에서 찾은 이 Task의 항목. 없으면 None.

    Returns:
        verdict.json 내용.
    """
    pre = load_json(d / "precheck.json")
    pre = pre if isinstance(pre, dict) else {}
    checks = pre.get("checks") if isinstance(pre.get("checks"), dict) else {}
    expected = pre.get("expected") if isinstance(pre.get("expected"), list) else []
    verdict: dict = {
        "issueKey": key,
        "mode": mode,
        "verdict": "no_verdict" if mode == "review" else None,
        "checks": [
            {"id": cid, **c} for cid, c in checks.items() if isinstance(c, dict) and c.get("result") in CHECK_RESULTS
        ],
        "items": [{"id": e["id"], "done": None, "result": ""} for e in expected if isinstance(e, dict) and "id" in e],
        "extra": [],
        "requests": [],
        "feedback": [],
        "slack": [],
    }
    given = (agent or {}).get("verdict")
    if isinstance(given, dict):
        verdict.update({k: given[k] for k in VERDICT_KEYS if k in given})
    return verdict


def text_of(agent: dict | None, name: str) -> str:
    """구조화 출력의 Task 항목에서 문자열 값을 꺼내 앞뒤 공백을 뗀다.

    Args:
        agent: 구조화 출력의 tasks에서 찾은 이 Task의 항목. 없으면 None.
        name: 키 이름(tldr, comment).

    Returns:
        문자열 값. 없거나 문자열이 아니면 빈 문자열.
    """
    value = (agent or {}).get(name)
    return value.strip() if isinstance(value, str) else ""


def missing_parts(verdict: dict, tldr: str, comment: str, mode: str) -> list[str]:
    """판정 없음 기본값이 남았거나 비어 있는 부분 이름.

    Args:
        verdict: verdict.json 내용.
        tldr: TL;DR 본문.
        comment: 검수 comment의 결과 요약.
        mode: 실행 모드(review, digest).

    Returns:
        빠진 부분 이름 목록(verdict, items, tldr, comment). 정리 모드는 verdict와 comment를 보지 않는다.
    """
    items = verdict.get("items")
    parts: list[str] = []
    if mode == "review" and verdict.get("verdict") == "no_verdict":
        parts.append("verdict")
    if isinstance(items, list) and any(isinstance(a, dict) and "done" in a and a["done"] is None for a in items):
        parts.append("items")
    if not tldr:
        parts.append("tldr")
    if mode == "review" and not comment:
        parts.append("comment")
    return parts


def failed_reasons(structured: dict) -> dict[str, str]:
    """구조화 출력의 failed 목록을 issueKey별 이유로 바꾼다.

    Args:
        structured: 구조화 출력.

    Returns:
        issueKey -> 이유(한 줄).
    """
    failed = structured.get("failed")
    out: dict[str, str] = {}
    for f in failed if isinstance(failed, list) else []:
        if isinstance(f, dict) and isinstance(f.get("issueKey"), str):
            out[f["issueKey"]] = one_line(str(f.get("reason") or ""), FAILURE_TEXT_LIMIT)
    return out


def save_task(d: Path, out: Path, mode: str, structured: dict | None, error: str, failed: dict[str, str]) -> list[str]:
    """Task 하나의 out/<KEY>/ 파일을 쓴다.

    Args:
        d: ctx/<KEY>/ 폴더.
        out: out 폴더.
        mode: 실행 모드(review, digest).
        structured: 구조화 출력. 없으면 None.
        error: 구조화 출력이 없을 때의 원인 한 줄.
        failed: 구조화 출력의 failed 목록(issueKey -> 이유).

    Returns:
        빠진 부분 이름 목록(missing_parts의 결과).
    """
    key = d.name
    o = out / key
    o.mkdir(parents=True, exist_ok=True)
    for name in TASK_FILES:
        (o / name).unlink(missing_ok=True)
    agent = None
    tasks = structured.get("tasks") if structured else None
    for t in tasks if isinstance(tasks, list) else []:
        if isinstance(t, dict) and t.get("issueKey") == key:
            agent = t
    verdict = build_verdict(d, key, mode, agent)
    tldr, comment = text_of(agent, "tldr"), text_of(agent, "comment")
    (o / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    if tldr:
        (o / "tldr.wiki").write_text(tldr + "\n", encoding="utf-8")
    if mode == "review" and comment:
        (o / "comment.wiki").write_text(comment + "\n", encoding="utf-8")
    if structured is None:
        reason = error
    elif agent is None:
        reason = f"CI agent가 처리하지 못함: {failed[key]}" if key in failed else "구조화 출력에 이 ticket 결과 없음"
    else:
        reason = ""
    if reason:
        (o / "_agent_error.txt").write_text(reason + "\n", encoding="utf-8")
    parts = missing_parts(verdict, tldr, comment, mode)
    print(f"[save] {key}: verdict={verdict['verdict']} 없음={','.join(parts) or '-'}")
    return parts
