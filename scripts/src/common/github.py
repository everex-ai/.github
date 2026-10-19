"""GitHub Actions 실행 주소, Actions Summary(실행 결과 요약 탭) 기록, 실행 실패 기록."""

from __future__ import annotations

import json
import os
from pathlib import Path

# 실패한 스텝이 out/에 남기는 실패 기록. 5단계 실패 알림(apply.py failure 모드)이 읽는다
RUN_ERROR_FILE = "_run_error.json"
# ticket 실패 알림을 보낸 표시. 5단계가 같은 실패를 다시 알리지 않는 데 쓴다
FAILURE_NOTIFIED_FILE = "_failure_notified"


def run_url() -> str:
    """GitHub Actions 실행 로그 주소. 환경 변수 GITHUB_SERVER_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID 중 하나라도 없으면 빈 문자열."""
    s, r, i = (
        os.environ.get("GITHUB_SERVER_URL", ""),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )
    return f"{s}/{r}/actions/runs/{i}" if s and r and i else ""


def write_run_error(out_dir: Path, step: str, exc: BaseException) -> None:
    """실패한 스텝의 단계와 원인을 out_dir/_run_error.json에 JSON으로 쓴다.

    out_dir가 없으면 만든다. 원인은 "예외 이름: 메시지" 꼴이고 메시지는 300자까지만 적는다.

    Args:
        out_dir: 실패 기록을 둘 폴더(out/).
        step: 실패한 스텝 이름(예: "1. 입력 수집").
        exc: 스텝을 실패시킨 예외.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    error = {"step": step, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    (out_dir / RUN_ERROR_FILE).write_text(json.dumps(error, ensure_ascii=False), encoding="utf-8")


class Summary:
    """Actions Summary(실행 결과 요약 탭)에 남길 표의 행과 추가 줄을 모은다."""

    def __init__(self, path: str):
        self.path = path
        self.rows: list[str] = []
        self.extra: list[str] = []

    def row(self, key: str, itype: str, mode: str, result: str, note: str = "") -> None:
        """표에 ticket 한 행을 추가하고 같은 내용을 출력한다.

        Args:
            key: ticket 키.
            itype: work type.
            mode: 실행 모드.
            result: 처리 결과.
            note: 비고.
        """
        self.rows.append(f"| {key} | {itype} | {mode} | {result} | {note} |")
        print(f"[apply] {key}: {result} {note}")

    def flush(self) -> None:
        """모은 표와 추가 줄을 path 파일 끝에 쓴다. path가 비어 있으면 출력한다."""
        lines = [
            "## jira-doc 실행 결과",
            "",
            "| ticket | type | mode | 결과 | 비고 |",
            "|---|---|---|---|---|",
            *self.rows,
            "",
            *self.extra,
        ]
        text = "\n".join(lines) + "\n"
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text)
