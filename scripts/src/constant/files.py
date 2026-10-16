"""ctx(수집 결과 폴더)와 out(CI agent 출력 폴더)의 파일 이름 상수."""

from __future__ import annotations

TASK_FILES = (
    "verdict.json",
    "tldr.wiki",
    "comment.wiki",
    "_agent_error.txt",
)  # 다시 실행할 때 이전 결과가 남지 않게 먼저 지우는 파일
USAGE_FILE = "_usage.json"  # out/ 아래의 Claude 사용량 목록 파일
