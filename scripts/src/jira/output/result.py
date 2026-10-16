"""claude-code-action 실행 결과 파일에서 구조화 출력과 Claude 사용량을 읽는 함수(save_output.py가 부름)."""

from __future__ import annotations

import json
from pathlib import Path

from src.constant.files import USAGE_FILE
from src.constant.usage import MODEL_USAGE_KEYS, USAGE_KEYS
from src.jira.output.save import load_json


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
