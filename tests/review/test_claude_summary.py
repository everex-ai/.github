from __future__ import annotations

import claude_summary

RESULT = {
    "type": "result",
    "result": "verdict: reject, symbols: 1",
    "num_turns": 8,
    "total_cost_usd": 0.456,
    "duration_ms": 45200,
    "permission_denials": [],
    "subagent_stats": {"by_type": {"everex-review:dead-code": 1}},
}


def test_cli_json_dict():
    lines = claude_summary.summarize(RESULT)
    assert lines[0] == "[claude] verdict: reject, symbols: 1"
    assert "turns=8" in lines[1] and "api_equiv_usd=0.46" in lines[1] and "duration_s=45" in lines[1]
    assert "denials=0" in lines[1] and "everex-review:dead-code" in lines[1]


def test_action_execution_file_list():
    data = [{"type": "system"}, {"type": "assistant"}, RESULT]
    assert claude_summary.find_result(data) is RESULT
    assert claude_summary.summarize(data)[0].startswith("[claude] verdict")


def test_missing_result():
    assert claude_summary.find_result([{"type": "assistant"}]) is None
    assert claude_summary.summarize("x") == ["[claude] 실행 결과 요약을 찾지 못함"]
