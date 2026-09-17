#!/usr/bin/env python3
"""로컬 검증용 대상 repo를 만든다. collect/classify/precheck/apply의 모든 분기를 한 PR로 건드리게 짜여 있다.

    python scripts/review/dev/make_fixture.py <dir> [--failing-test]

main: calc/ops.py(add, multiply, Calculator), calc/legacy.py, tests/test_ops.py, requirements.txt
feature 브랜치:
  (a) divide 추가: docstring + 타입 힌트 + test_divide          -> 3-1 통과, 테스트 후보 있음
  (b) scale 추가: docstring/타입 없음, 서식 나쁨, 테스트 없음    -> 3-1 실패 (D103, ANN, format)
  (c) multiply 본문만 수정                                      -> modified, signature_changed=False
  (d) calc/legacy.py -> calc/compat.py 이름 변경                 -> status R
  (e) unused_helper 함수와 미사용 import os                     -> 6번 후보 (vulture, ruff F401)
  (f) PRECISION: int = 4 모듈 변수 추가                          -> variable added
  (g) requirements.txt 변경                                     -> 비 python 파일
  --failing-test: test_multiply 가 실패하게 만든다                -> 5번 실패
  --clean: scale 에 docstring/타입 힌트/서식을 갖춘다 (테스트는 여전히 없음) -> 3-1 통과, precheck continue.
           Claude 단계 검증용. 기대 verdict: 3-3 fail(scale), 6 confirmed(unused_helper, os), 4-2 pass(multiply)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0900",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0900",
}

OPS_BASE = '''"""기본 연산."""

from __future__ import annotations


def add(a: float, b: float) -> float:
    """두 수를 더한다.

    Args:
        a: 첫 번째 수.
        b: 두 번째 수.

    Returns:
        합.
    """
    return a + b


def multiply(a: float, b: float) -> float:
    """두 수를 곱한다.

    Args:
        a: 첫 번째 수.
        b: 두 번째 수.

    Returns:
        곱.
    """
    return a * b


class Calculator:
    """누적 계산기."""

    def __init__(self) -> None:
        self.total: float = 0.0

    def push(self, value: float) -> float:
        """값을 누적한다.

        Args:
            value: 더할 값.

        Returns:
            누적 합.
        """
        self.total = add(self.total, value)
        return self.total
'''

OPS_FEATURE = '''"""기본 연산."""

from __future__ import annotations

import os

PRECISION: int = 4


def add(a: float, b: float) -> float:
    """두 수를 더한다.

    Args:
        a: 첫 번째 수.
        b: 두 번째 수.

    Returns:
        합.
    """
    return a + b


def multiply(a: float, b: float) -> float:
    """두 수를 곱한다.

    Args:
        a: 첫 번째 수.
        b: 두 번째 수.

    Returns:
        곱.
    """
    result = a * b
    return round(result, PRECISION)


def divide(a: float, b: float) -> float:
    """두 수를 나눈다.

    Args:
        a: 나뉘는 수.
        b: 나누는 수. 0이면 안 된다.

    Returns:
        몫.

    Raises:
        ZeroDivisionError: b가 0일 때.
    """
    if b == 0:
        raise ZeroDivisionError("b must not be zero")
    return a / b


{scale}

def unused_helper() -> int:
    """아무도 부르지 않는 함수."""
    return 42


class Calculator:
    """누적 계산기."""

    def __init__(self) -> None:
        self.total: float = 0.0

    def push(self, value: float) -> float:
        """값을 누적한다.

        Args:
            value: 더할 값.

        Returns:
            누적 합.
        """
        self.total = add(self.total, value)
        return self.total
'''

SCALE_BAD = """def scale(x,k):
    return x*k
"""

SCALE_CLEAN = """def scale(x: float, k: float) -> float:
    \"\"\"x를 k배 한다.

    Args:
        x: 값.
        k: 배율.

    Returns:
        x * k.
    \"\"\"
    return x * k
"""

LEGACY = '''"""예전 API."""

from __future__ import annotations


def old_add(a: float, b: float) -> float:
    """add의 예전 이름."""
    return a + b
'''

TEST_BASE = """from calc.ops import Calculator, add, multiply


def test_add():
    assert add(1, 2) == 3


def test_multiply():
    assert multiply(2, 3) == 6


def test_calculator_push():
    c = Calculator()
    c.push(1)
    assert c.push(2) == 3
"""

TEST_FEATURE = """import pytest

from calc.ops import Calculator, add, divide, multiply


def test_add():
    assert add(1, 2) == 3


def test_multiply():
    assert multiply(2, 3) == {expected}


def test_calculator_push():
    c = Calculator()
    c.push(1)
    assert c.push(2) == 3


def test_divide():
    assert divide(6, 3) == 2
    with pytest.raises(ZeroDivisionError):
        divide(1, 0)
"""


def sh(*args: str, cwd: Path) -> None:
    """fixture 디렉터리에서 명령을 실행한다. 실패하면 예외."""
    subprocess.run(args, cwd=cwd, env=ENV, check=True, capture_output=True, text=True)


def build(root: Path, failing_test: bool = False, clean: bool = False) -> Path:
    """주어진 디렉터리에 fixture repo를 만들고 그 경로를 돌려준다. 이미 있으면 지우고 다시 만든다."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    sh("git", "init", "-q", "-b", "main", cwd=root)
    (root / "calc").mkdir()
    (root / "tests").mkdir()
    (root / "calc" / "__init__.py").write_text('"""calc 패키지."""\n', encoding="utf-8")
    (root / "calc" / "ops.py").write_text(OPS_BASE, encoding="utf-8")
    (root / "calc" / "legacy.py").write_text(LEGACY, encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_ops.py").write_text(TEST_BASE, encoding="utf-8")
    (root / "requirements.txt").write_text("pytest>=8\n", encoding="utf-8")
    (root / ".gitignore").write_text(".everex-review/\n__pycache__/\n", encoding="utf-8")
    sh("git", "add", "-A", cwd=root)
    sh("git", "commit", "-q", "-m", "base", cwd=root)

    sh("git", "checkout", "-q", "-b", "feature", cwd=root)
    (root / "calc" / "ops.py").write_text(
        OPS_FEATURE.replace("{scale}", SCALE_CLEAN if clean else SCALE_BAD), encoding="utf-8"
    )
    sh("git", "mv", "calc/legacy.py", "calc/compat.py", cwd=root)
    (root / "tests" / "test_ops.py").write_text(
        TEST_FEATURE.format(expected=7 if failing_test else 6), encoding="utf-8"
    )
    (root / "requirements.txt").write_text("pytest>=8\nnumpy>=2\n", encoding="utf-8")
    sh("git", "add", "-A", cwd=root)
    sh("git", "commit", "-q", "-m", "feature: divide, scale, compat rename", cwd=root)
    # CI의 checkout과 같게 PR head(feature)를 작업 트리에 둔다. precheck는 작업 트리가 head와 같은지 확인한다.
    return root


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--failing-test", action="store_true")
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    p = build(Path(a.dir).resolve(), a.failing_test, a.clean)
    print(f"fixture repo: {p} (branches: main, feature; checked out: feature)")


if __name__ == "__main__":
    main()
