"""jira-doc 기록(issue property)의 실행 기록(runs) 계산."""

from __future__ import annotations

import json
from pathlib import Path

from src.common.io import load_json
from src.constant.jira import READY_STATUS

RUNS_MAX_BYTES = 30000  # property 값 크기 상한(Jira 상한 32KB보다 작게). 넘으면 오래된 runs부터 지운다
RUNS_LIMIT = 100  # jira-doc 기록(issue property)의 runs에 남기는 최근 실행 수


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
    return {
        "retry": any(e.get("retry") is True for e in entries),
        "costUsd": sum(costs) if costs else None,
        "tokens": tokens,
        "models": models,
    }


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
    return sum(
        1
        for c in changes
        if isinstance(c, dict) and c.get("field") == "status" and str(c.get("to") or "").strip().lower() == READY_STATUS
    )


def add_run(prop: dict, entry: dict) -> None:
    """property의 runs 목록 끝에 항목을 추가하고 최근 RUNS_LIMIT건만 남긴다.

    추가한 뒤 property를 JSON으로 만든 크기가 RUNS_MAX_BYTES(바이트)를 넘으면 오래된 runs부터 지운다.
    방금 추가한 항목은 지우지 않는다.

    Args:
        prop: jira-doc 기록(issue property). 이 dict를 고친다.
        entry: run_entry의 결과.
    """
    runs = prop.get("runs")
    prop["runs"] = ([r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else [])[-(RUNS_LIMIT - 1) :] + [
        entry
    ]
    while len(prop["runs"]) > 1 and len(json.dumps(prop, ensure_ascii=False).encode("utf-8")) > RUNS_MAX_BYTES:
        del prop["runs"][0]
