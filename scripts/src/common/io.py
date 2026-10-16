"""파일 읽기와 쓰기 함수."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def load_json(p: Path, default: Any = None) -> Any:
    """JSON 파일을 읽는다. 파일이 없거나 JSON이 아니면 default를 돌려준다.

    Args:
        p: 파일 경로.
        default: 읽을 수 없을 때 돌려줄 값.

    Returns:
        읽은 값 또는 default.
    """
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """CSV 파일을 쓴다.

    Args:
        path: 파일 경로.
        columns: 열 이름 목록.
        rows: 행 목록.
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
