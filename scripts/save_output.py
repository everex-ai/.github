#!/usr/bin/env python3
"""3-2단계: CI agent의 구조화 출력(JSON)을 out/<KEY>/ 파일로 저장한다. 표준 라이브러리만 쓴다.

구조화 출력은 claude-code-action의 실행 결과 파일(execution_file)에서 읽는다. 그 파일은 메시지 JSON 배열이고, 마지막
type이 "result"인 메시지의 structured_output(객체)이 구조화 출력이다. 형식은 schemas/agent-output.json에 있다.

ctx/<KEY>/마다 판정 없음 기본값을 만들고, 구조화 출력에 있는 키만 덮어쓴 뒤 아래 파일을 쓴다. 기본값과 덮어쓰는 범위는 build_verdict에 있다.
- out/<KEY>/verdict.json: 항상 쓴다.
- out/<KEY>/tldr.wiki: 내용이 있을 때만 쓴다.
- out/<KEY>/comment.wiki: 검수 모드이고 내용이 있을 때만 쓴다.
- out/<KEY>/_agent_error.txt: 구조화 출력이 없거나 그 Task가 구조화 출력에 없을 때 한 줄 원인을 쓴다. apply.py가 실패 이유 끝에 붙인다.
- out/_failed.txt: 구조화 출력의 failed 목록을 KEY와 이유를 탭으로 이은 줄로 쓴다. 없으면 이전 파일을 지운다.

검수 모드에서 어느 Task든 판정(verdict)이 "no_verdict"이거나 items에 done이 null인 항목이 있거나 tldr, comment가 비어 있으면
--github-output 파일에 retry=true를 덧붙인다(워크플로의 3-R단계가 3단계를 1회 다시 실행함).

실행 결과 파일의 Claude 사용량(total_cost_usd, modelUsage 또는 usage)은 out/_usage.json(목록)에 실행마다 항목 하나로 추가한다.
항목은 {"retry", "costUsd", "tokens": {"input", "output", "cacheWrite", "cacheRead"}, "models"}이다. apply.py가 이 파일을 합산해 jira-doc 기록에 남긴다.
`--exec-summary <경로>`만 주면 저장 없이 실행 결과 요약 줄(exec_summary)만 출력한다(워크플로 3-1단계).

표준 출력에는 Jira 기록이 담길 수 있는 내용(tldr, comment, 실행 결과의 result 문구)을 남기지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

VERDICT_KEYS = (
    "verdict",
    "checks",
    "items",
    "extra",
    "requests",
    "feedback",
)  # 구조화 출력의 verdict 객체에서 덮어쓰는 키
CHECK_RESULTS = ("pass", "fail", "n/a")
TASK_FILES = (
    "verdict.json",
    "tldr.wiki",
    "comment.wiki",
    "_agent_error.txt",
)  # 다시 실행할 때 이전 결과가 남지 않게 먼저 지우는 파일
FAILURE_TEXT_LIMIT = 300  # 실패한 실행의 result 문구를 옮기는 길이
USAGE_FILE = "_usage.json"  # out/ 아래의 Claude 사용량 목록 파일
MODEL_USAGE_KEYS = {  # modelUsage의 모델별 항목 키 -> 토큰 종류
    "input": "inputTokens",
    "output": "outputTokens",
    "cacheWrite": "cacheCreationInputTokens",
    "cacheRead": "cacheReadInputTokens",
}
USAGE_KEYS = {  # usage의 키 -> 토큰 종류
    "input": "input_tokens",
    "output": "output_tokens",
    "cacheWrite": "cache_creation_input_tokens",
    "cacheRead": "cache_read_input_tokens",
}


def build_parser() -> argparse.ArgumentParser:
    """명령줄 인자 파서를 만든다. 워크플로(.github/workflows/jira-doc.yml)의 3-2단계와 3-R단계가 이 인자로 부른다.

    Returns:
        인자 파서.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ctx", default="ctx")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mode", choices=["review", "digest"], help="--exec-summary가 없으면 필수")
    ap.add_argument("--exec-file", default="", help="claude-code-action의 execution_file. 비었거나 없는 경로일 수 있음")
    ap.add_argument(
        "--claude-outcome",
        default="",
        help="Claude 문서화 단계(Actions 워크플로 3단계)의 결과(success, failure, skipped 등)",
    )
    ap.add_argument("--retry", action="store_true", help="3-R단계의 다시 실행이면 _usage.json 항목에 retry=true를 적음")
    ap.add_argument(
        "--exec-summary",
        default="",
        metavar="PATH",
        help="이 실행 결과 파일의 요약 줄만 출력하고 끝냄(워크플로 3-1단계). 파일이 없거나 비면 아무것도 출력하지 않음",
    )
    ap.add_argument("--github-output", default="", help="GITHUB_OUTPUT 파일 경로. 비어 있으면 retry를 쓰지 않음")
    return ap


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


def read_result(exec_file: str) -> tuple[bool, dict | None]:
    """실행 결과 파일에서 마지막 result 메시지를 찾는다.

    파일은 메시지 JSON 배열이거나 메시지 하나(dict)일 수 있다.

    Args:
        exec_file: 실행 결과 파일 경로. 비었거나 없는 경로일 수 있다.

    Returns:
        (파일을 읽었는지, 마지막 type이 "result"인 메시지). 읽지 못했으면 (False, None), result 메시지가 없으면 (True, None).
    """
    if not exec_file or not Path(exec_file).is_file():
        return False, None
    data = load_json(Path(exec_file))
    if data is None:
        return False, None
    results = [
        r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict) and r.get("type") == "result"
    ]
    return True, (results[-1] if results else None)


def result_messages(exec_file: str) -> list[dict]:
    """실행 결과 파일에서 type이 "result"인 메시지를 모두 꺼낸다.

    Args:
        exec_file: 실행 결과 파일 경로(JSON 목록 또는 메시지 하나). 비었거나 없는 경로일 수 있다.

    Returns:
        result 메시지 목록. 파일을 읽지 못했으면 빈 목록.
    """
    if not exec_file or not Path(exec_file).is_file():
        return []
    data = load_json(Path(exec_file))
    return [
        r for r in (data if isinstance(data, list) else [data]) if isinstance(r, dict) and r.get("type") == "result"
    ]


def _count(value: object) -> int:
    """토큰 수 값을 정수로 읽는다. 정수가 아니면 0이다."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def usage_of(results: list[dict]) -> dict:
    """result 메시지들의 Claude 사용량을 합산한다.

    modelUsage(모델 이름 -> 모델별 토큰 수)가 있으면 그것을, 없으면 usage를 읽는다.

    Args:
        results: result 메시지 목록.

    Returns:
        {"costUsd": float 또는 None, "tokens": {"input", "output", "cacheWrite", "cacheRead"}, "models": [모델 이름]}.
        costUsd는 total_cost_usd의 합이고, 숫자인 값이 없으면 None이다. models는 modelUsage의 키이고 없으면 빈 목록이다.
    """
    tokens = {k: 0 for k in USAGE_KEYS}
    models: list[str] = []
    costs = [
        r["total_cost_usd"]
        for r in results
        if isinstance(r.get("total_cost_usd"), (int, float)) and not isinstance(r["total_cost_usd"], bool)
    ]
    for r in results:
        by_model = r.get("modelUsage")
        if isinstance(by_model, dict) and by_model:
            for name, u in by_model.items():
                if name not in models:
                    models.append(name)
                for kind, key in MODEL_USAGE_KEYS.items():
                    tokens[kind] += _count((u or {}).get(key)) if isinstance(u, dict) else 0
        elif isinstance(r.get("usage"), dict):
            for kind, key in USAGE_KEYS.items():
                tokens[kind] += _count(r["usage"].get(key))
    return {"costUsd": float(sum(costs)) if costs else None, "tokens": tokens, "models": models}


def exec_summary(path: str) -> list[str]:
    """실행 결과 파일의 요약 줄을 만든다. 워크플로 3-1단계가 남기던 줄에 결과 메시지마다 토큰 줄을 더한다.

    Jira 기록이 담길 수 있어, 실패한 실행일 때만 result 문구를 옮기고 Bash 명령은 옮기지 않는다.

    Args:
        path: 실행 결과 파일 경로. 비었거나 없는 경로일 수 있다.

    Returns:
        출력할 줄 목록. result 메시지마다 아래 줄이 차례로 들어간다.
        is_error, subtype, num_turns, total_cost_usd 줄 / permission_denials 줄 / 거부된 Write, Edit 경로 줄 /
        실패했을 때의 result 줄 / `tokens input=<n> output=<n> cache_write=<n> cache_read=<n>` 줄.
    """
    lines: list[str] = []
    for r in result_messages(path):
        lines.append(
            f"is_error={r.get('is_error')} subtype={r.get('subtype')} num_turns={r.get('num_turns')} "
            f"total_cost_usd={r.get('total_cost_usd')}"
        )
        denials = [d for d in r.get("permission_denials") or [] if isinstance(d, dict)]
        lines.append("permission_denials=" + ",".join(d.get("tool_name", "?") for d in denials))
        for d in denials:  # 파일 쓰기 거부는 경로만 남긴다. Bash 명령은 Jira 기록이 담길 수 있어 남기지 않는다
            if d.get("tool_name") in ("Write", "Edit"):
                lines.append(f"denied {d.get('tool_name')}: {(d.get('tool_input') or {}).get('file_path')}")
        if r.get("is_error"):
            lines.append(f"result={r.get('result')}")
        t = usage_of([r])["tokens"]
        lines.append(
            f"tokens input={t['input']} output={t['output']} cache_write={t['cacheWrite']} cache_read={t['cacheRead']}"
        )
    return lines


def append_usage(out: Path, exec_file: str, retry: bool) -> None:
    """실행 결과 파일의 Claude 사용량을 out/_usage.json에 항목 하나로 추가한다.

    Args:
        out: out 폴더.
        exec_file: 실행 결과 파일 경로. 읽을 수 없으면 아무것도 추가하지 않는다.
        retry: 3-R단계의 다시 실행인지.
    """
    if not exec_file or not Path(exec_file).is_file() or load_json(Path(exec_file)) is None:
        return
    entries = load_json(out / USAGE_FILE)
    entries = entries if isinstance(entries, list) else []
    entries.append({"retry": retry, **usage_of(result_messages(exec_file))})
    (out / USAGE_FILE).write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def structured_output(result: dict | None) -> dict | None:
    """result 메시지에서 구조화 출력(객체)을 꺼낸다. 문자열이면 JSON으로 읽어 본다.

    Args:
        result: 마지막 result 메시지.

    Returns:
        구조화 출력. 없거나 객체가 아니면 None.
    """
    value = (result or {}).get("structured_output")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


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
    items(예상 산출물마다 done null), extra, requests, feedback(빈 목록)이다. 구조화 출력의 issueKey, mode는 무시한다.

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


def main(argv: list[str] | None = None) -> None:
    """구조화 출력을 읽어 out/ 파일을 쓰고, 검수 모드에서 다시 실행이 필요하면 retry=true를 출력한다.

    Args:
        argv: 명령줄 인자. None이면 sys.argv를 쓴다.
    """
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.exec_summary:  # 저장하지 않고 요약 줄만 출력한다. 파일이 없거나 비면 아무것도 출력하지 않는다
        for line in exec_summary(args.exec_summary):
            print(line)
        return
    if not args.mode:
        ap.error("--mode가 필요하다(--exec-summary를 쓰지 않을 때)")
    ctx, out = Path(args.ctx), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    append_usage(out, args.exec_file, args.retry)
    read_ok, result = read_result(args.exec_file)
    structured = structured_output(result)
    error = "" if structured is not None else no_output_reason(read_ok, result, args.claude_outcome)
    failed = failed_reasons(structured) if structured is not None else {}

    retry = False
    keys: list[str] = []
    for d in sorted(ctx.glob("[A-Z]*-[0-9]*/")):
        if (d / "issue.json").exists():
            keys.append(d.name)
            retry = bool(save_task(d, out, args.mode, structured, error, failed)) or retry
    tasks = structured.get("tasks") if structured else None
    for t in tasks if isinstance(tasks, list) else []:  # ctx에 없는 issueKey는 키만 남긴다(내용은 남기지 않음)
        if isinstance(t, dict) and isinstance(t.get("issueKey"), str) and t["issueKey"] not in keys:
            print(f"[save] ctx에 없는 issueKey: {t['issueKey']}")

    failed_file = out / "_failed.txt"
    if failed:
        failed_file.write_text("".join(f"{k}\t{r}\n" for k, r in failed.items()), encoding="utf-8")
    else:
        failed_file.unlink(missing_ok=True)

    if args.mode == "review" and retry and args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write("retry=true\n")
        print("[save] retry=true")


if __name__ == "__main__":
    main()
