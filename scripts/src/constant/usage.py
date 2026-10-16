"""Claude 사용량의 token 종류별 키."""

from __future__ import annotations

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
