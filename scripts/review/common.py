"""PR 검수 스크립트(collect, classify, precheck, apply)가 공유하는 도우미.

- 작업 디렉터리는 대상 repo 안의 `.everex-review/` 하나이며 그 아래 `ctx/`(스크립트 출력, Claude 입력)와
  `out/`(Claude 출력)을 둔다. 모든 스크립트는 `--repo`, `--work`를 인자로 받고 현재 디렉터리를 가정하지 않는다.
- 검사 항목 번호(1..7)는 팀의 검수 절차 번호를 그대로 쓴다. 이름은 PR comment 표에 나가는 표시 이름이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

WORK_DIR_NAME = ".everex-review"
PY_SUFFIXES = (".py", ".pyi")
TEST_DIR_NAMES = {"test", "tests"}
COMMENT_MARKER = "<!-- everex-review -->"

# 검사 항목: 번호 -> (표시 이름, 계산 주체, 실패 시 영향)
CHECK_NAMES: dict[str, str] = {
    "1": "변경 범위 수집",
    "2": "추가/수정 분류",
    "3-1": "형식, 타입 힌트, docstring 규칙 (추가 코드)",
    "3-2": "테스트 필요 여부 (추가 코드)",
    "3-3": "테스트 존재 여부 (추가 코드)",
    "4-1": "테스트 필요 여부 (수정 코드)",
    "4-2": "테스트 존재 여부 (수정 코드)",
    "5": "테스트 실행",
    "6": "미사용 코드",
    "7": "비효율, 확장성",
}
CHECK_ORDER: dict[str, int] = {cid: i for i, cid in enumerate(CHECK_NAMES)}
SCRIPT_CHECKS = ("1", "2", "3-1", "5")  # 스크립트가 판정. agent 출력에 있으면 거부한다
AGENT_CHECKS = ("3-2", "3-3", "4-1", "4-2", "6", "7")
REJECT_CHECKS = ("1", "2", "3-1", "3-3", "4-2", "5")  # 실패하면 반려. 6, 7은 comment만
RESULT_MARK = {"pass": "✅ 통과", "fail": "❌ 실패", "n/a": "➖ 해당 없음"}


@dataclass
class Result:
    """subprocess 실행 결과."""

    code: int
    out: str
    err: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """종료 코드 0이고 시간 초과가 아니면 True."""
        return self.code == 0 and not self.timed_out


def run(args: list[str], cwd: Path | str | None = None, timeout: int | None = None, env: dict | None = None) -> Result:
    """명령을 실행하고 stdout/stderr를 문자열로 돌려준다. 실패해도 예외를 내지 않는다."""
    try:
        p = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return Result(
            code=-1,
            out=(e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            err="timeout",
            timed_out=True,
        )
    except FileNotFoundError as e:
        return Result(code=127, out="", err=str(e))
    return Result(code=p.returncode, out=p.stdout, err=p.stderr)


def git(repo: Path, *args: str) -> Result:
    """대상 repo에서 git 명령을 실행한다."""
    return run(["git", "-c", "core.quotepath=off", *args], cwd=repo)


def write_json(path: Path, data: object) -> None:
    """JSON을 UTF-8, 들여쓰기 2로 쓴다. 상위 디렉터리는 만든다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path, default: object = None) -> object:
    """JSON 파일을 읽는다. 없거나 깨졌으면 default."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def log(tag: str, msg: str) -> None:
    """stderr에 [tag] 접두어로 한 줄 남긴다."""
    print(f"[{tag}] {msg}", file=sys.stderr, flush=True)


def is_python_path(path: str) -> bool:
    """.py 또는 .pyi 파일인가."""
    return PurePosixPath(path).suffix in PY_SUFFIXES


def is_test_path(path: str) -> bool:
    """tests/ 아래이거나 test_*.py, *_test.py, conftest.py이면 테스트 파일."""
    p = PurePosixPath(path)
    if any(part in TEST_DIR_NAMES for part in p.parts[:-1]):
        return True
    name = p.name
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def in_ranges(line: int, ranges: list[list[int]] | list[tuple[int, int]]) -> bool:
    """줄 번호가 [start, end] 범위 목록 중 하나에 들어가는가."""
    return any(a <= line <= b for a, b in ranges)


def ranges_overlap(a: tuple[int, int], ranges: list[list[int]] | list[tuple[int, int]]) -> bool:
    """범위 a가 범위 목록 중 하나와 겹치는가."""
    return any(not (a[1] < s or a[0] > e) for s, e in ranges)


def tree_state(repo: Path, work: Path) -> dict[str, str]:
    """작업 트리에서 바뀌었거나 추적되지 않는 파일의 {경로: 내용 해시}. <work> 아래는 제외한다.

    precheck가 Claude 단계 전에 기준선으로 저장하고, apply가 Claude 단계 뒤의 상태와 비교한다.
    pytest가 남긴 파일처럼 Claude 이전부터 있던 변경은 기준선에 들어 있으므로 문제 삼지 않는다.
    """
    r = git(repo, "status", "--porcelain", "--untracked-files=all", "-z")
    try:
        prefix = str(work.relative_to(repo)).replace(os.sep, "/") + "/"
    except ValueError:
        prefix = None
    state: dict[str, str] = {}
    entries = r.out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        code, path = e[:2], e[3:]
        if code[0] in ("R", "C"):
            i += 1  # 다음 칸은 원래 경로
        if prefix and path.startswith(prefix):
            continue
        f = repo / path
        try:
            state[path] = hashlib.sha1(f.read_bytes()).hexdigest() if f.is_file() else "absent"  # noqa: S324
        except OSError:
            state[path] = "unreadable"
    return state


def resolve_work(repo: Path, work: str | None) -> Path:
    """--work 인자가 없으면 <repo>/.everex-review 를 쓴다."""
    return Path(work).resolve() if work else (repo / WORK_DIR_NAME).resolve()


def github_output(key: str, value: str) -> None:
    """GITHUB_OUTPUT이 있으면 스텝 출력으로 기록한다 (로컬에서는 아무것도 하지 않는다)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def run_url() -> str:
    """GitHub Actions 실행 로그 URL. Actions 밖이면 빈 문자열."""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    rid = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{rid}" if repo and rid else ""


class Summary:
    """GITHUB_STEP_SUMMARY(또는 지정 파일)에 붙일 markdown을 모은다. 경로가 비어 있으면 stdout에 찍는다."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.lines: list[str] = []

    def add(self, line: str) -> None:
        """줄 하나를 추가한다."""
        self.lines.append(line)

    def flush(self) -> None:
        """모아 둔 줄을 파일 끝에 붙인다."""
        text = "\n".join(self.lines) + "\n"
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text)
