#!/usr/bin/env python3
"""1단계: PR의 변경 범위를 git에서 모아 <work>/ctx/ 에 저장한다 (검수 절차 1번).

- 비교 기준은 `git merge-base base head`..head (= base...head). CI는 fetch-depth: 0 으로 checkout해야 한다.
- 파일 내용은 작업 트리가 아니라 git 객체(`git show`)에서 읽는다. dirty checkout이 섞이지 않게 하기 위함.
- 변경 줄 번호(changed_lines)는 -U0 hunk에서 계산한다. 뒤 단계(precheck)는 이 줄에 있는 위반만 판정에 쓴다.
- 이름이 바뀐 파일은 before 사본을 새 경로로 저장해 classify가 짝을 맞추게 한다.

출력: ctx/pr.json, ctx/files.json, ctx/diff/<path>.diff, ctx/before/<path>, ctx/after/<path>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    WORK_DIR_NAME,
    Result,
    git,
    is_python_path,
    is_test_path,
    log,
    resolve_work,
    run,
    write_json,
)

MAX_FILE_BYTES = 200 * 1024
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_hunk_ranges(diff_text: str) -> tuple[list[list[int]], list[list[int]]]:
    """-U0 diff의 hunk 헤더에서 (after 쪽 변경 줄 범위, before 쪽 삭제 줄 범위)를 만든다."""
    changed: list[list[int]] = []
    removed: list[list[int]] = []
    for line in diff_text.splitlines():
        m = HUNK_RE.match(line)
        if not m:
            continue
        a, b, c, d = m.groups()
        b = 1 if b is None else int(b)
        d = 1 if d is None else int(d)
        if d > 0:
            changed.append([int(c), int(c) + d - 1])
        if b > 0:
            removed.append([int(a), int(a) + b - 1])
    return changed, removed


def parse_name_status(text: str) -> list[dict]:
    """`git diff --name-status -z` 출력을 파일 목록으로 바꾼다. R/C는 old_path를 가진다."""
    parts = text.split("\0")
    files: list[dict] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if not status:
            i += 1
            continue
        code = status[0]
        if code in ("R", "C"):
            old, new = parts[i + 1], parts[i + 2]
            files.append({"path": new, "old_path": old, "status": code, "similarity": int(status[1:] or 0)})
            i += 3
        else:
            files.append({"path": parts[i + 1], "old_path": None, "status": code, "similarity": None})
            i += 2
    return files


def parse_numstat(text: str) -> dict[str, tuple[int | None, int | None]]:
    """`git diff --numstat -z` 출력. binary는 (None, None). rename은 새 경로를 키로 쓴다."""
    stats: dict[str, tuple[int | None, int | None]] = {}
    parts = text.split("\0")
    i = 0
    while i < len(parts):
        rec = parts[i]
        if not rec:
            i += 1
            continue
        add, dele, rest = rec.split("\t", 2)
        if rest == "":  # rename/copy: 다음 두 칸이 old, new
            path = parts[i + 2]
            i += 3
        else:
            path = rest
            i += 1
        stats[path] = (None, None) if add == "-" else (int(add), int(dele))
    return stats


def pr_meta_from_gh(repo: Path, number: int) -> dict:
    """gh CLI로 PR 메타데이터(번호, 제목, 본문, base/head SHA 등)를 가져온다."""
    fields = "number,title,body,author,url,isDraft,baseRefName,headRefName,baseRefOid,headRefOid"
    r = run(["gh", "pr", "view", str(number), "--json", fields], cwd=repo)
    if not r.ok:
        raise SystemExit(f"[collect] gh pr view 실패: {r.err.strip()}")
    d = json.loads(r.out)
    return {
        "number": d["number"],
        "title": d.get("title"),
        "body": d.get("body") or "",
        "author": (d.get("author") or {}).get("login"),
        "url": d.get("url"),
        "is_draft": d.get("isDraft", False),
        "base_ref": d.get("baseRefName"),
        "head_ref": d.get("headRefName"),
        "base_sha": d.get("baseRefOid"),
        "head_sha": d.get("headRefOid"),
    }


def pr_meta_from_event(path: Path) -> dict:
    """GitHub Actions 이벤트 payload(GITHUB_EVENT_PATH)의 pull_request 에서 PR 메타데이터를 읽는다. 네트워크와 토큰이 필요 없다."""
    ev = json.loads(path.read_text(encoding="utf-8"))
    pr = ev.get("pull_request")
    if not isinstance(pr, dict):
        raise SystemExit(f"[collect] {path} 에 pull_request 가 없음 (pull_request 이벤트가 아님)")
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "body": pr.get("body") or "",
        "author": (pr.get("user") or {}).get("login"),
        "url": pr.get("html_url"),
        "is_draft": bool(pr.get("draft")),
        "base_ref": (pr.get("base") or {}).get("ref"),
        "head_ref": (pr.get("head") or {}).get("ref"),
        "base_sha": (pr.get("base") or {}).get("sha"),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "repo_slug": ((pr.get("base") or {}).get("repo") or {}).get("full_name"),
    }


def repo_slug(repo: Path) -> str | None:
    """origin remote URL에서 owner/name 을 뽑는다. 없으면 None."""
    r = git(repo, "remote", "get-url", "origin")
    if not r.ok:
        return None
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", r.out.strip())
    return m.group(1) if m else None


def show(repo: Path, rev: str, path: str) -> Result:
    """git 객체에서 특정 rev의 파일 내용을 읽는다."""
    return git(repo, "show", f"{rev}:{path}")


def blob_size(repo: Path, rev: str, path: str) -> int:
    """특정 rev의 파일 크기(바이트). 없으면 0."""
    r = git(repo, "cat-file", "-s", f"{rev}:{path}")
    return int(r.out.strip()) if r.ok else 0


def collect(repo: Path, work: Path, base: str, head: str, pr: dict | None) -> list[dict]:
    """base...head 의 변경 파일을 모아 <work>/ctx/ 에 쓰고 파일 목록을 돌려준다."""
    ctx = work / "ctx"
    if ctx.exists():
        shutil.rmtree(ctx)
    ctx.mkdir(parents=True)

    mb = git(repo, "merge-base", base, head)
    if not mb.ok:
        raise SystemExit(f"[collect] merge-base 실패 ({base}, {head}): {mb.err.strip()}")
    merge_base = mb.out.strip()
    head_sha = git(repo, "rev-parse", head).out.strip()
    base_sha = git(repo, "rev-parse", base).out.strip()

    ns = git(repo, "diff", "-M", "--name-status", "-z", merge_base, head)
    if not ns.ok:
        raise SystemExit(f"[collect] git diff 실패: {ns.err.strip()}")
    files = parse_name_status(ns.out)
    stats = parse_numstat(git(repo, "diff", "-M", "--numstat", "-z", merge_base, head).out)

    for f in files:
        path, status = f["path"], f["status"]
        src_path = f["old_path"] or path
        add, dele = stats.get(path, (0, 0))
        f["is_binary"] = add is None
        f["additions"] = add or 0
        f["deletions"] = dele or 0
        f["is_python"] = is_python_path(path)
        f["is_test"] = is_test_path(path)
        f["changed_lines"] = []
        f["removed_lines"] = []
        f["too_large"] = False
        f["before_path"] = None
        f["after_path"] = None
        f["diff_path"] = None
        if f["is_binary"]:
            continue

        size = max(
            blob_size(repo, head, path) if status != "D" else 0,
            blob_size(repo, merge_base, src_path) if status != "A" else 0,
        )
        if size > MAX_FILE_BYTES:
            f["too_large"] = True
            log("collect", f"{path}: {size} bytes, 크기 초과로 내용을 저장하지 않음")
            continue

        u0 = git(repo, "diff", "-M", "-U0", merge_base, head, "--", src_path, path)
        f["changed_lines"], f["removed_lines"] = parse_hunk_ranges(u0.out)
        u3 = git(repo, "diff", "-M", "-U3", merge_base, head, "--", src_path, path)
        dp = ctx / "diff" / f"{path}.diff"
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(u3.out, encoding="utf-8")
        f["diff_path"] = str(dp.relative_to(work))

        if not f["is_python"]:
            continue
        if status != "A":
            r = show(repo, merge_base, src_path)
            if r.ok:
                bp = ctx / "before" / path
                bp.parent.mkdir(parents=True, exist_ok=True)
                bp.write_text(r.out, encoding="utf-8")
                f["before_path"] = str(bp.relative_to(work))
        if status != "D":
            r = show(repo, head, path)
            if r.ok:
                ap = ctx / "after" / path
                ap.parent.mkdir(parents=True, exist_ok=True)
                ap.write_text(r.out, encoding="utf-8")
                f["after_path"] = str(ap.relative_to(work))

    meta = {
        "number": None,
        "title": None,
        "body": "",
        "author": None,
        "url": None,
        "is_draft": False,
        "base_ref": base,
        "head_ref": head,
        **{k: v for k, v in (pr or {}).items() if k != "repo_slug"},
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_base": merge_base,
        "repo_slug": (pr or {}).get("repo_slug") or repo_slug(repo),
        "repo_path": str(repo),
    }
    write_json(ctx / "pr.json", meta)
    write_json(ctx / "files.json", files)
    return files


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="대상 repo 경로")
    ap.add_argument("--work", default=None, help=f"작업 디렉터리 (기본 <repo>/{WORK_DIR_NAME})")
    ap.add_argument("--base", default=None, help="기준 ref (예: main, origin/main, SHA)")
    ap.add_argument("--head", default="HEAD", help="PR head ref (기본 HEAD)")
    ap.add_argument("--pr", type=int, default=None, help="PR 번호. gh CLI로 base/head SHA를 가져온다")
    ap.add_argument("--event", default=None, help="GitHub Actions 이벤트 payload 경로 (GITHUB_EVENT_PATH). CI에서 쓴다")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    work = resolve_work(repo, a.work)
    if git(repo, "rev-parse", "--is-inside-work-tree").out.strip() != "true":
        raise SystemExit(f"[collect] git repo가 아님: {repo}")
    try:
        rel = work.relative_to(repo)
        if git(repo, "ls-files", "--error-unmatch", str(rel)).ok:
            raise SystemExit(f"[collect] 작업 디렉터리 {rel}가 git에 추적되고 있음. .gitignore에 추가할 것")
    except ValueError:
        pass

    pr = None
    if a.event:
        pr = pr_meta_from_event(Path(a.event))
        base, head = pr["base_sha"], pr["head_sha"]
    elif a.pr is not None:
        pr = pr_meta_from_gh(repo, a.pr)
        base, head = pr["base_sha"], pr["head_sha"]
    else:
        if not a.base:
            raise SystemExit("[collect] --base, --pr, --event 중 하나가 필요함")
        base, head = a.base, a.head

    files = collect(repo, work, base, head, pr)
    py = sum(1 for f in files if f["is_python"])
    log("collect", f"변경 파일 {len(files)}개 (python {py}개) -> {work / 'ctx'}")


if __name__ == "__main__":
    main()
