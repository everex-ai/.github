#!/usr/bin/env python3
"""2단계: 변경된 python 파일의 전후를 AST로 비교해 심볼별 추가/수정/삭제를 분류한다 (검수 절차 2번).

- 심볼: 모듈 수준 함수/클래스/변수, 클래스 안의 메서드/중첩 클래스/속성. 함수 안의 중첩 함수는 부모 본문의 일부로 본다.
- 정규화 텍스트는 ast.unparse 결과다. 서식만 바뀐 것은 unchanged, 주석만 바뀐 것도 unchanged(diff에는 남는다),
  docstring은 AST의 일부라 바뀌면 modified다.
- after 파일이 파싱되지 않으면 parse_errors에 기록한다. precheck가 이것을 2번 실패(반려)로 바꾼다.

출력: ctx/symbols.json = {symbols: [...], parse_errors: [...], notes: [...], summary: {...}}
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import WORK_DIR_NAME, is_test_path, load_json, log, ranges_overlap, resolve_work, write_json  # noqa: E402

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_main_guard(node: ast.If) -> bool:
    t = node.test
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name)
        and t.left.id == "__name__"
        and len(t.comparators) == 1
        and isinstance(t.comparators[0], ast.Constant)
        and t.comparators[0].value == "__main__"
    )


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_target_names(elt))
        return out
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    return []


def _start_line(node: ast.AST) -> int:
    decos = getattr(node, "decorator_list", None) or []
    return min([node.lineno, *[d.lineno for d in decos]])


def _annotations(fn: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool) -> dict:
    args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    if fn.args.vararg:
        args.append(fn.args.vararg)
    if fn.args.kwarg:
        args.append(fn.args.kwarg)
    if is_method and args and args[0].arg in ("self", "cls"):
        args = args[1:]
    return {
        "args": len(args),
        "annotated": sum(1 for x in args if x.annotation is not None),
        "return": fn.returns is not None,
    }


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    decos = " ".join(ast.unparse(d) for d in fn.decorator_list)
    ret = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
    kind = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
    return f"{decos} {kind} {fn.name}({ast.unparse(fn.args)}){ret}".strip()


def _walk_body(body: list[ast.stmt], prefix: str, out: dict[str, dict], in_class: bool) -> None:
    """모듈 또는 클래스 본문을 한 단계 훑는다. if/try/with 블록은 같은 수준으로 취급한다."""
    for node in body:
        if isinstance(node, ast.If):
            if _is_main_guard(node):
                continue
            _walk_body(node.body, prefix, out, in_class)
            _walk_body(node.orelse, prefix, out, in_class)
        elif isinstance(node, ast.Try):
            _walk_body(node.body, prefix, out, in_class)
            for h in node.handlers:
                _walk_body(h.body, prefix, out, in_class)
            _walk_body(node.orelse, prefix, out, in_class)
            _walk_body(node.finalbody, prefix, out, in_class)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            _walk_body(node.body, prefix, out, in_class)
        elif isinstance(node, FUNC_TYPES):
            name = f"{prefix}{node.name}"
            key = _unique_key(name, out)
            out[key] = {
                "kind": "method"
                if in_class
                else ("async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"),
                "name": name,
                "lines": [_start_line(node), node.end_lineno or node.lineno],
                "text": ast.unparse(node),
                "signature": _signature(node),
                "has_docstring": ast.get_docstring(node) is not None,
                "annotations": _annotations(node, in_class),
                "decorators": [ast.unparse(d) for d in node.decorator_list],
            }
        elif isinstance(node, ast.ClassDef):
            name = f"{prefix}{node.name}"
            out[name] = {
                "kind": "class",
                "name": name,
                "lines": [_start_line(node), node.end_lineno or node.lineno],
                "text": ast.unparse(node),
                "signature": f"class {node.name}({', '.join(ast.unparse(b) for b in node.bases)})",
                "has_docstring": ast.get_docstring(node) is not None,
                "annotations": None,
                "decorators": [ast.unparse(d) for d in node.decorator_list],
            }
            _walk_body(node.body, f"{name}.", out, True)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names: list[str] = []
            for t in targets:
                names.extend(_target_names(t))
            for n in names:
                name = f"{prefix}{n}"
                out[name] = {
                    "kind": "attribute" if in_class else "variable",
                    "name": name,
                    "lines": [node.lineno, node.end_lineno or node.lineno],
                    "text": ast.unparse(node),
                    "signature": None,
                    "has_docstring": None,
                    "annotations": {"annotated": isinstance(node, ast.AnnAssign)},
                    "decorators": [],
                }


def _unique_key(name: str, out: dict[str, dict]) -> str:
    """@overload 처럼 같은 이름이 여러 번 나오면 name#2, name#3 으로 구분한다."""
    if name not in out:
        return name
    n = 2
    while f"{name}#{n}" in out:
        n += 1
    return f"{name}#{n}"


def extract_symbols(source: str) -> dict[str, dict]:
    """소스 하나를 파싱해 qualified 이름 -> 심볼 정보 사전을 만든다. SyntaxError는 호출자가 처리한다."""
    tree = ast.parse(source)
    out: dict[str, dict] = {}
    _walk_body(tree.body, "", out, False)
    return out


def _is_private(name: str) -> bool:
    for part in name.split("#")[0].split("."):
        if part.startswith("_") and not (part.startswith("__") and part.endswith("__")):
            return True
    return False


def compare_file(
    path: str, before: str | None, after: str | None, changed_lines: list[list[int]]
) -> tuple[list[dict], list[dict], list[str]]:
    """한 파일의 전후 소스를 비교해 (심볼 목록, parse_errors, notes)를 돌려준다."""
    errors: list[dict] = []
    notes: list[str] = []
    try:
        b = extract_symbols(before) if before is not None else {}
    except SyntaxError as e:
        b = {}
        notes.append(f"{path}: 변경 전 파일이 파싱되지 않아 모든 심볼을 추가로 봄 ({e.msg} line {e.lineno})")
    try:
        a = extract_symbols(after) if after is not None else {}
    except SyntaxError as e:
        errors.append({"file": path, "line": e.lineno, "message": e.msg})
        return [], errors, notes

    symbols: list[dict] = []
    test_file = is_test_path(path)
    for key, s in a.items():
        old = b.get(key)
        if old is None:
            change = "added"
        elif old["text"] == s["text"]:
            continue
        else:
            change = "modified"
        symbols.append(
            {
                "file": path,
                "name": key,
                "kind": s["kind"],
                "change": change,
                "lines": s["lines"],
                "signature_changed": bool(old) and old["signature"] != s["signature"],
                "has_docstring": s["has_docstring"],
                "annotations": s["annotations"],
                "decorators": s["decorators"],
                "is_private": _is_private(key),
                "is_test": test_file or key.split(".")[-1].startswith("test_"),
                "overlaps_diff": ranges_overlap((s["lines"][0], s["lines"][1]), changed_lines),
            }
        )
    for key, s in b.items():
        if key not in a:
            symbols.append(
                {
                    "file": path,
                    "name": key,
                    "kind": s["kind"],
                    "change": "removed",
                    "lines": None,
                    "signature_changed": False,
                    "has_docstring": s["has_docstring"],
                    "annotations": s["annotations"],
                    "decorators": s["decorators"],
                    "is_private": _is_private(key),
                    "is_test": test_file or key.split(".")[-1].startswith("test_"),
                    "overlaps_diff": True,
                }
            )
    return symbols, errors, notes


def classify(work: Path) -> dict:
    """<work>/ctx/files.json 의 python 파일마다 전후를 비교해 ctx/symbols.json 을 쓴다."""
    ctx = work / "ctx"
    files = load_json(ctx / "files.json")
    if not isinstance(files, list):
        raise SystemExit(f"[classify] {ctx / 'files.json'} 이 없음. collect.py를 먼저 실행할 것")

    symbols: list[dict] = []
    errors: list[dict] = []
    notes: list[str] = []
    for f in files:
        if not f["is_python"] or f["is_binary"] or f["too_large"]:
            continue
        before = (work / f["before_path"]).read_text(encoding="utf-8") if f.get("before_path") else None
        after = (work / f["after_path"]).read_text(encoding="utf-8") if f.get("after_path") else None
        s, e, n = compare_file(f["path"], before, after, f["changed_lines"])
        symbols.extend(s)
        errors.extend(e)
        notes.extend(n)

    counts = {c: sum(1 for s in symbols if s["change"] == c) for c in ("added", "modified", "removed")}
    non_test = [s for s in symbols if not s["is_test"]]
    added = any(s["change"] == "added" for s in non_test)
    modified = any(s["change"] in ("modified", "removed") for s in non_test)
    kind = (
        "mixed" if added and modified else "additions_only" if added else "modifications_only" if modified else "none"
    )
    result = {
        "symbols": symbols,
        "parse_errors": errors,
        "notes": notes,
        "summary": {**counts, "kind_of_change": kind},
    }
    write_json(ctx / "symbols.json", result)
    return result


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="대상 repo 경로 (--work 기본값 계산용)")
    ap.add_argument("--work", default=None, help=f"작업 디렉터리 (기본 <repo>/{WORK_DIR_NAME})")
    a = ap.parse_args()
    work = resolve_work(Path(a.repo).resolve(), a.work)
    r = classify(work)
    s = r["summary"]
    log(
        "classify",
        f"추가 {s['added']}, 수정 {s['modified']}, 삭제 {s['removed']} ({s['kind_of_change']}), "
        f"파싱 오류 {len(r['parse_errors'])}건",
    )


if __name__ == "__main__":
    main()
