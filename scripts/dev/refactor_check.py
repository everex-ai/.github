#!/usr/bin/env python3
"""리팩터링 전후의 top-level 정의를 비교해 빠진 이름과 본문이 바뀐 정의를 찾는다.

    python scripts/dev/refactor_check.py snapshot --out reports/x/before.json scripts/apply.py scripts/dev
    python scripts/dev/refactor_check.py compare reports/x/before.json reports/x/after.json

- snapshot: 파일과 폴더(하위 .py 전체)의 top-level 함수, 클래스, 클래스 메서드, 대입 상수를 모은다.
  - 함수와 메서드는 docstring을 뺀 본문과 인자 이름을 비교 대상으로 둔다. type-hint는 비교하지 않는다.
  - 상수는 대입한 값의 식을 비교 대상으로 둔다.
- compare: 이름별로 이전 정의와 같은 본문이 이후에 하나라도 있으면 같음으로 센다.
  - 빠짐: 이후에 이름이 없음
  - 바뀜: 이름은 있으나 같은 본문이 없음. 가장 가까운 이후 정의와의 차이를 unified diff로 출력한다.
- 이름은 파일과 관계없이 비교한다. 정의를 다른 파일로 옮겨도 같음으로 센다.
- 본문 글자만 비교하므로 import 대상이 바뀌어도 본문 글자가 같으면 같음으로 센다. import 대상은 테스트와 결과 비교로 확인한다.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import sys
from pathlib import Path


def _strip_doc(body: list[ast.stmt]) -> list[ast.stmt]:
    """본문 첫 문장이 docstring이면 뺀 목록을 돌려준다.

    Args:
        body: 함수나 클래스의 본문 문장 목록.

    Returns:
        docstring을 뺀 문장 목록.
    """
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            return body[1:]
    return body


def _func_text(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """함수의 인자 이름과 docstring을 뺀 본문을 소스 문자열로 만든다.

    Args:
        node: 함수 정의 노드.

    Returns:
        인자 이름 줄과 본문 소스.
    """
    a = node.args
    names = [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    defaults = [ast.unparse(d) for d in [*a.defaults, *[d for d in a.kw_defaults if d is not None]]]
    body = "\n".join(ast.unparse(s) for s in _strip_doc(node.body))
    decos = "".join(f"@{ast.unparse(d)}\n" for d in node.decorator_list)
    return f"{decos}args({', '.join(names)}) defaults({', '.join(defaults)})\n{body}"


def _class_text(node: ast.ClassDef) -> str:
    """클래스의 메서드를 뺀 본문(필드, 상수)을 소스 문자열로 만든다.

    Args:
        node: 클래스 정의 노드.

    Returns:
        기반 클래스 줄과 메서드를 뺀 본문 소스.
    """
    bases = ", ".join(ast.unparse(b) for b in node.bases)
    decos = "".join(f"@{ast.unparse(d)}\n" for d in node.decorator_list)
    rest = [s for s in _strip_doc(node.body) if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return f"{decos}bases({bases})\n" + "\n".join(ast.unparse(s) for s in rest)


def definitions(path: Path) -> list[dict]:
    """파일 하나의 top-level 정의 목록을 만든다.

    Args:
        path: Python 파일 경로.

    Returns:
        정의마다 name, kind, file, text를 담은 dict 목록.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[dict] = []

    def add(name: str, kind: str, text: str) -> None:
        out.append({"name": name, "kind": kind, "file": str(path), "text": text})

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, "function", _func_text(node))
        elif isinstance(node, ast.ClassDef):
            add(node.name, "class", _class_text(node))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{node.name}.{sub.name}", "method", _func_text(sub))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    add(t.id, "constant", ast.unparse(node.value))
                elif (
                    isinstance(t, ast.Tuple)
                    and isinstance(node.value, ast.Tuple)
                    and len(t.elts) == len(node.value.elts)
                ):
                    for name, value in zip(t.elts, node.value.elts):
                        if isinstance(name, ast.Name):
                            add(name.id, "constant", ast.unparse(value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            add(node.target.id, "constant", ast.unparse(node.value))
    return out


def py_files(paths: list[str]) -> list[Path]:
    """파일과 폴더 목록을 .py 파일 목록으로 펼친다.

    Args:
        paths: 파일이나 폴더 경로 목록. 폴더는 하위 .py 파일 전체를 포함한다.

    Returns:
        정렬된 .py 파일 목록.
    """
    files: set[Path] = set()
    for p in map(Path, paths):
        if p.is_dir():
            files.update(f for f in p.rglob("*.py") if "__pycache__" not in f.parts)
        elif p.suffix == ".py" and p.exists():
            files.add(p)
    return sorted(files)


def snapshot(paths: list[str], out: Path) -> int:
    """정의 목록을 JSON 파일로 저장한다.

    Args:
        paths: 대상 파일과 폴더.
        out: 저장할 JSON 경로.

    Returns:
        저장한 정의 수.
    """
    defs = [d for f in py_files(paths) for d in definitions(f)]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(defs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(defs)


def compare(before: list[dict], after: list[dict]) -> tuple[list[dict], list[tuple[dict, dict]], int]:
    """이전 정의마다 이후에 같은 정의가 있는지 비교한다.

    Args:
        before: 이전 정의 목록.
        after: 이후 정의 목록.

    Returns:
        (빠진 정의 목록, (바뀐 이전 정의, 가장 가까운 이후 정의) 목록, 같은 정의 수).
    """
    by_name: dict[str, list[dict]] = {}
    for d in after:
        by_name.setdefault(d["name"], []).append(d)
    missing: list[dict] = []
    changed: list[tuple[dict, dict]] = []
    same = 0
    for d in before:
        cands = by_name.get(d["name"], [])
        if not cands:
            missing.append(d)
        elif any(c["text"] == d["text"] for c in cands):
            same += 1
        else:
            best = max(cands, key=lambda c: difflib.SequenceMatcher(None, d["text"], c["text"]).ratio())
            changed.append((d, best))
    return missing, changed, same


def render(missing: list[dict], changed: list[tuple[dict, dict]], same: int) -> str:
    """비교 결과를 markdown 문자열로 만든다.

    Args:
        missing: 빠진 정의 목록.
        changed: 바뀐 정의와 가장 가까운 이후 정의의 쌍 목록.
        same: 같은 정의 수.

    Returns:
        markdown 본문.
    """
    lines = [f"- 같음: {same}개", f"- 빠짐: {len(missing)}개", f"- 바뀜: {len(changed)}개", "", "## 빠짐", ""]
    lines += [f"- `{d['name']}` ({d['kind']}, {d['file']})" for d in missing]
    lines += ["", "## 바뀜", ""]
    for d, c in changed:
        lines.append(f"### `{d['name']}` ({d['file']} -> {c['file'] if c else '-'})")
        diff = difflib.unified_diff(
            d["text"].splitlines(), (c["text"] if c else "").splitlines(), "before", "after", lineterm=""
        )
        lines += ["", "```diff", *diff, "```", ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="정의 목록을 JSON으로 저장")
    s.add_argument("--out", required=True, type=Path, help="저장할 JSON 경로")
    s.add_argument("paths", nargs="+", help="대상 파일이나 폴더")
    c = sub.add_parser("compare", help="두 정의 목록을 비교")
    c.add_argument("before", type=Path, help="이전 JSON")
    c.add_argument("after", type=Path, help="이후 JSON")
    c.add_argument("--out", type=Path, help="결과 markdown 경로. 없으면 표준 출력")
    a = ap.parse_args()
    if a.cmd == "snapshot":
        print(f"{snapshot(a.paths, a.out)}개 정의를 {a.out}에 저장함")
        return
    load = lambda p: json.loads(p.read_text(encoding="utf-8"))  # noqa: E731
    text = render(*compare(load(a.before), load(a.after)))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
        print(text.split("\n## ")[0], file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
