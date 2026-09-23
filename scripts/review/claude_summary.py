#!/usr/bin/env python3
"""Claude 실행 결과에서 요약 한 줄을 뽑는다. 로그와 artifact 확인용이며 판정에는 쓰지 않는다.

    python scripts/review/claude_summary.py <결과 파일>

입력은 두 형식을 모두 받는다.
- claude -p --output-format json 의 결과 (dict). 로컬의 run_claude.sh 가 만든다
- claude-code-action 의 execution_file (메시지 list, 마지막 type=result 가 요약). CI가 만든다
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def find_result(data: object) -> dict | None:
    """결과 요약(type=result) 객체를 찾는다. 없으면 None."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for item in reversed(data):
            if isinstance(item, dict) and item.get("type") == "result":
                return item
    return None


def summarize(data: object) -> list[str]:
    """로그에 찍을 줄 목록. 결과를 찾지 못하면 그 사실 한 줄."""
    r = find_result(data)
    if r is None:
        return ["[claude] 실행 결과 요약을 찾지 못함"]
    stats = r.get("subagent_stats") or {}
    return [
        f"[claude] {r.get('result')}",
        f"[claude] turns={r.get('num_turns')} api_equiv_usd={round(r.get('total_cost_usd') or 0, 2)} "
        f"duration_s={round((r.get('duration_ms') or 0) / 1000)} denials={len(r.get('permission_denials') or [])} "
        f"subagents={stats.get('by_type')}",
    ]


def main() -> None:
    """CLI 진입점. 파일을 읽지 못해도 실패로 끝내지 않는다 (요약은 부가 정보다)."""
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    try:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[claude] 결과 파일을 읽지 못함: {e}", file=sys.stderr)
        return
    for line in summarize(data):
        print(line, file=sys.stderr)


if __name__ == "__main__":
    main()
