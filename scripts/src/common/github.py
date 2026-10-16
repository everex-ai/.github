"""GitHub Actions 실행 주소와 Actions Summary(실행 결과 요약 탭) 기록."""

from __future__ import annotations

import os


def run_url() -> str:
    """GitHub Actions 실행 로그 주소. 환경 변수 GITHUB_SERVER_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID 중 하나라도 없으면 빈 문자열."""
    s, r, i = (
        os.environ.get("GITHUB_SERVER_URL", ""),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )
    return f"{s}/{r}/actions/runs/{i}" if s and r and i else ""


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
