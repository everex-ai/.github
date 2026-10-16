"""숫자와 비율을 문구로 바꾸는 함수."""

from __future__ import annotations


def fmt(x: float) -> str:
    """숫자를 소수 첫째 자리까지 적고, 소수 부분이 0이면 정수로 적는다.

    Args:
        x: 숫자.

    Returns:
        숫자 문구. 예: 1.5, 3.
    """
    return f"{x:.1f}".removesuffix(".0")


def rate(a: int, b: int) -> str:
    """비율 문구를 만든다. 분모가 0이면 표본이 없다고 적는다.

    Args:
        a: 분자.
        b: 분모.

    Returns:
        비율 문구. 예: 50.0% (2/4).
    """
    return f"{a / b * 100:.1f}% ({a}/{b})" if b else "계산할 표본이 없음 (0/0)"
