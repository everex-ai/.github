from __future__ import annotations

import json
from pathlib import Path

import classify


def cmp(before: str | None, after: str | None):
    syms, errors, notes = classify.compare_file("m.py", before, after, [[1, 999]])
    return {s["name"]: s for s in syms}, errors, notes


def test_added_modified_removed_unchanged():
    before = "def a():\n    return 1\n\ndef b():\n    return 2\n\ndef gone():\n    pass\n"
    after = "def a():\n    return 1\n\ndef b():\n    return 3\n\ndef new():\n    pass\n"
    syms, errors, _ = cmp(before, after)
    assert not errors
    assert "a" not in syms
    assert syms["b"]["change"] == "modified" and syms["b"]["signature_changed"] is False
    assert syms["new"]["change"] == "added"
    assert syms["gone"]["change"] == "removed" and syms["gone"]["lines"] is None


def test_formatting_only_is_unchanged_but_docstring_is_modified():
    before = "def f(a,b):\n    return a+b\n"
    after = "def f(a, b):\n    return a + b\n"
    assert cmp(before, after)[0] == {}
    after2 = 'def f(a, b):\n    """doc."""\n    return a + b\n'
    syms, _, _ = cmp(before, after2)
    assert syms["f"]["change"] == "modified" and syms["f"]["has_docstring"] is True


def test_comment_only_change_is_unchanged():
    assert cmp("x = 1  # a\n", "x = 1  # b\n")[0] == {}


def test_signature_change_and_annotations():
    before = "def f(a):\n    return a\n"
    after = "def f(a: int, b: int = 0) -> int:\n    return a + b\n"
    syms, _, _ = cmp(before, after)
    assert syms["f"]["signature_changed"] is True
    assert syms["f"]["annotations"] == {"args": 2, "annotated": 2, "return": True}


def test_methods_nested_classes_and_attributes():
    after = (
        "class A:\n    x: int = 1\n    def m(self, v: int) -> int:\n        return v\n"
        "    class Inner:\n        def z(self):\n            pass\n"
        "async def af():\n    pass\n"
        "a, b = 1, 2\n"
    )
    syms, _, _ = cmp(None, after)
    assert syms["A"]["kind"] == "class"
    assert syms["A.m"]["kind"] == "method" and syms["A.m"]["annotations"] == {"args": 1, "annotated": 1, "return": True}
    assert syms["A.x"]["kind"] == "attribute"
    assert syms["A.Inner.z"]["kind"] == "method" and syms["A.Inner"]["kind"] == "class"
    assert syms["af"]["kind"] == "async_function"
    assert syms["a"]["kind"] == "variable" and syms["b"]["kind"] == "variable"


def test_overload_keys_and_main_guard_and_nested_functions_skipped():
    after = (
        "from typing import overload\n"
        "@overload\ndef f(a: int) -> int: ...\n"
        "@overload\ndef f(a: str) -> str: ...\n"
        "def f(a):\n    def inner():\n        pass\n    return a\n"
        "if __name__ == '__main__':\n    def main_only():\n        pass\n"
    )
    syms, _, _ = cmp(None, after)
    assert {"f", "f#2", "f#3"} <= set(syms)
    assert "inner" not in syms and "f.inner" not in syms
    assert "main_only" not in syms
    assert syms["f"]["decorators"] == ["overload"]


def test_private_and_test_flags():
    syms, _, _ = cmp(
        None,
        "def _helper():\n    pass\n\nclass _P:\n    def m(self):\n        pass\n\ndef test_x():\n    pass\n\ndef __dunder__():\n    pass\n",
    )
    assert syms["_helper"]["is_private"] and syms["_P.m"]["is_private"]
    assert syms["__dunder__"]["is_private"] is False
    assert syms["test_x"]["is_test"] is True and syms["_helper"]["is_test"] is False


def test_syntax_error_after_and_before():
    _, errors, _ = cmp("x = 1\n", "def (:\n")
    assert errors and errors[0]["file"] == "m.py" and errors[0]["line"] == 1
    syms, errors, notes = cmp("def (:\n", "x = 1\n")
    assert not errors and syms["x"]["change"] == "added" and notes


def test_classify_on_fixture(collected: Path):
    data = json.loads((collected / "ctx" / "symbols.json").read_text())
    by = {(s["file"], s["name"]): s for s in data["symbols"]}
    assert by[("calc/ops.py", "divide")]["change"] == "added"
    assert by[("calc/ops.py", "scale")]["has_docstring"] is False
    assert by[("calc/ops.py", "scale")]["annotations"] == {"args": 2, "annotated": 0, "return": False}
    assert by[("calc/ops.py", "multiply")]["change"] == "modified"
    assert by[("calc/ops.py", "multiply")]["signature_changed"] is False
    assert by[("calc/ops.py", "PRECISION")]["kind"] == "variable"
    assert by[("tests/test_ops.py", "test_divide")]["is_test"] is True
    assert ("calc/ops.py", "Calculator.push") not in by  # 바뀌지 않음
    assert ("calc/compat.py", "old_add") not in by  # 이름만 바뀐 파일은 심볼 변경 없음
    assert data["summary"]["kind_of_change"] == "mixed" and data["parse_errors"] == []
