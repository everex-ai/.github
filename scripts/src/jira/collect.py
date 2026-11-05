"""Jira와 GitHub에서 입력을 모아 ctx/ 아래에 파일로 저장하는 함수(1단계 수집, scripts/collect.py가 부름)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from src.constant.jira import TARGET_TYPES, TYPE_ALIAS
from src.jira.client import Jira
from src.jira.wiki import is_agent_comment, section_body, sections

CTX = Path("ctx")
ISSUE_FIELDS = [
    "summary",
    "status",
    "assignee",
    "reporter",
    "created",
    "updated",
    "labels",
    "issuetype",
    "parent",
    "subtasks",
    "description",
    "attachment",
]
# 사람 구역(해시와 검사에 쓰는 구역). agent 구역은 제외한다.
HUMAN_SECTIONS = {
    "Task": ["진행 배경", "예상 산출물"],
    "Bug": ["기본 정보", "문제", "개선", "첨부"],
    "Issue": ["이슈 유형", "이슈 내용"],
}


def canon_type(name: str) -> str:
    """work type 이름을 Task, Bug, Issue 중 하나로 바꾼다. 별칭이 아니면 그대로 돌려준다."""
    return TYPE_ALIAS.get((name or "").strip().lower(), TYPE_ALIAS.get(name, name))


def now_iso() -> str:
    """지금 시각을 초 단위 ISO 형식(로컬 시간대)으로 돌려준다."""
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def write(path: Path, data: dict | list | str | None) -> None:
    """파일을 쓴다. dict나 list는 JSON으로, 그 밖에는 문자열로 쓰고, 상위 폴더가 없으면 만든다.

    Args:
        path: 쓸 파일 경로.
        data: 쓸 내용. None이면 빈 파일을 쓴다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(data or "", encoding="utf-8")


def gh(*args: str) -> list | dict | None:
    """gh CLI 호출. 토큰이 없거나 실패하면 None."""
    if not os.environ.get("GH_TOKEN"):
        return None
    try:
        res = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            print(f"[collect] gh {' '.join(args[:3])} failed: {res.stderr.strip()[:200]}", file=sys.stderr)
            return None
        return json.loads(res.stdout) if res.stdout.strip() else []
    except Exception as e:  # noqa: BLE001
        print(f"[collect] gh error: {e}", file=sys.stderr)
        return None


def key_pattern(key_re: str) -> re.Pattern:
    r"""task 키 일치 정규식을 만든다.

    INNO-1이 INNO-17에 걸리지 않게 앞뒤 경계를 본다.
    \b는 한글 앞에서 끊기지 않으므로('INNO-17에서') 영숫자 lookaround를 쓴다.

    Args:
        key_re: ticket 키의 정규식.

    Returns:
        앞뒤 경계를 붙인 정규식.
    """
    return re.compile(rf"(?<![A-Za-z0-9]){key_re}(?![0-9])")


def fetch_prs(key: str, org: str) -> list[dict]:
    """ticket 키가 제목, 브랜치, 본문에 있는 PR을 GitHub 조직에서 찾는다.

    Args:
        key: ticket 키.
        org: GitHub 조직 이름.

    Returns:
        PR 목록. 항목마다 title, url, repo, state, branch, mergedAt, matched_in, body_head를 담는다.
    """
    found = (
        gh("search", "prs", key, "--owner", org, "--json", "title,url,state,repository,number", "--limit", "20") or []
    )
    pat = key_pattern(re.escape(key))
    out = []
    for pr in found:
        detail = gh("pr", "view", pr["url"], "--json", "title,state,headRefName,mergedAt,body,url") or {}
        title = detail.get("title") or pr.get("title", "")
        branch = detail.get("headRefName", "")
        body = detail.get("body") or ""
        where = [w for w, txt in (("title", title), ("branch", branch), ("body", body)) if pat.search(txt)]
        if not where:
            continue
        out.append(
            {
                "title": title,
                "url": pr["url"],
                "repo": (pr.get("repository") or {}).get("nameWithOwner", ""),
                "state": detail.get("state") or pr.get("state"),
                "branch": branch,
                "mergedAt": detail.get("mergedAt"),
                "matched_in": where,
                "body_head": "\n".join(body.splitlines()[:40]),
            }
        )
    return out


def norm_comments(raw: list[dict], site: str, key: str) -> list[dict]:
    """Jira comment 응답을 저장 형식으로 바꾼다.

    Args:
        raw: Jira comment 응답 목록.
        site: Jira 사이트 주소. 비면 comment URL을 비운다.
        key: ticket 키.

    Returns:
        comment 목록. 항목마다 id, created, updated, author, kind(agent 또는 human), body, url을 담는다.
    """
    out = []
    for c in raw:
        a = c.get("author") or {}
        out.append(
            {
                "id": c.get("id"),
                "created": c.get("created"),
                "updated": c.get("updated"),
                "author": {
                    "accountId": a.get("accountId"),
                    "displayName": a.get("displayName"),
                    "accountType": a.get("accountType"),
                },
                "kind": "agent" if is_agent_comment(c) else "human",
                "body": c.get("body") or "",
                "url": f"{site}/browse/{key}?focusedCommentId={c.get('id')}" if site else "",
            }
        )
    return out


def norm_changelog(raw: list[dict]) -> list[dict]:
    """변경 이력 응답에서 상태, description, 담당자, 첨부 변경만 골라 시간순으로 정리한다.

    Args:
        raw: Jira 변경 이력 응답 목록.

    Returns:
        변경 목록. 항목마다 created, author, field, from, to를 담는다.
    """
    out = []
    for h in raw:
        a = h.get("author") or {}
        for it in h.get("items", []):
            if it.get("field") in ("status", "description", "assignee", "Attachment"):
                out.append(
                    {
                        "created": h.get("created"),
                        "author": {
                            "accountId": a.get("accountId"),
                            "displayName": a.get("displayName"),
                            "accountType": a.get("accountType"),
                        },
                        "field": it.get("field"),
                        "from": it.get("fromString"),
                        "to": it.get("toString"),
                    }
                )
    out.sort(key=lambda x: x["created"] or "")
    return out


def human_input_hash(
    itype: str, desc: str, comments: list[dict], subtasks: list[dict], prs: list[dict], changelog: list[dict]
) -> str:
    """사람이 쓴 입력의 해시를 만든다. 사람 구역, 사람 comment, sub-task, PR, 상태 변경이 바뀌면 값이 바뀐다.

    Args:
        itype: work type(Task, Bug, Issue).
        desc: description(wiki markup).
        comments: norm_comments의 결과.
        subtasks: sub-task 목록. 항목마다 key, summary, status, description, comments, prs를 담는다.
        prs: fetch_prs의 결과.
        changelog: norm_changelog의 결과.

    Returns:
        "sha256:"로 시작하는 해시 문자열.
    """
    h = hashlib.sha256()
    for sec in HUMAN_SECTIONS.get(itype, []):
        h.update(section_body(desc, sec).encode())
    for c in comments:
        if c["kind"] == "human":
            h.update(f"{c['id']}|{c['updated']}|{c['body']}".encode())
    for s in subtasks:
        h.update(
            json.dumps(
                {k: s.get(k) for k in ("key", "summary", "status", "description")}, ensure_ascii=False, sort_keys=True
            ).encode()
        )
        for c in s.get("comments", []):
            if c["kind"] == "human":
                h.update(f"{c['id']}|{c['updated']}".encode())
        for p in s.get("prs", []):
            h.update(f"{p['url']}|{p['state']}|{p['mergedAt']}".encode())
    for p in prs:
        h.update(f"{p['url']}|{p['state']}|{p['mergedAt']}".encode())
    for ch in changelog:
        if ch["field"] == "status":
            h.update(f"{ch['created']}|{ch['to']}".encode())
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------- 첨부
# 첨부 형식별 받는 크기 상한(bytes). 넘으면 받지 않고 attachments.json에 이유를 적는다
ATT_MAX_BYTES = {
    "text": 200 * 1024,
    "image": 20 * 1024 * 1024,
    "pdf": 32 * 1024 * 1024,
    "video": 200 * 1024 * 1024,
}
ATT_MAX_FILES = 20  # ticket당 저장하는 첨부 수 상한(saved, frames만 셈. 부모 ticket과 sub-task 합계, 생성 시각 최신순)
TEXT_MIMES = {"application/json"}
TEXT_EXTS = {"md", "txt", "csv", "json", "log", "yaml", "yml"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
FRAME_COUNT = 8  # 영상 1건에서 뽑는 프레임 수
FRAME_MAX_SIDE = 1280  # 프레임 긴 변의 상한(px). 작으면 그대로 둔다
MATCH_SECONDS = 10  # 본문 표기가 없을 때 같은 작성자의 comment와 짝짓는 생성 시각 차이 상한(초)
FRAME_TIMEOUT = 60  # 프레임 1장을 뽑는 ffmpeg 실행의 시간 상한(초)
# ticket 하나의 영상 처리(받기와 프레임 추출) 전체 시간 상한(초). 넘으면 남은 영상은 받지 않는다
VIDEO_TIME_BUDGET = 600
# GitHub Actions runner에 없는 첨부 처리 도구(apt 패키지 이름: 확인할 실행 파일). 영상은 ffmpeg로 프레임을 뽑고,
# 3단계 CI agent는 10쪽이 넘는 PDF를 poppler-utils(pdftoppm)로 읽는다
TOOL_PACKAGES = {"video": ("ffmpeg", ("ffmpeg", "ffprobe")), "pdf": ("poppler-utils", ("pdftoppm",))}
TOOL_INSTALL_TIMEOUT = 300  # apt-get update와 install 각각의 시간 상한(초)


def attachment_kind(mime: str | None, filename: str | None) -> str:
    """첨부 형식을 mimeType과 확장자로 분류한다.

    Args:
        mime: Jira 첨부의 mimeType.
        filename: 첨부 파일 이름.

    Returns:
        text, image, pdf, video, other 중 하나.
    """
    m = (mime or "").split(";")[0].strip().lower()
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if m.startswith("video/"):
        return "video"
    if m == "application/pdf":
        return "pdf"
    if m in IMAGE_MIMES or (not m.startswith("image/") and ext in IMAGE_EXTS):
        return "image"
    if m.startswith("text/") or m in TEXT_MIMES or ext in TEXT_EXTS:
        return "text"
    return "other"


def safe_filename(name: str, limit: int = 120) -> str:
    """파일 이름을 저장해도 안전한 이름으로 바꾼다.

    경로 구분자와 특수 문자는 _로 바꾸고 앞의 점을 지워 상위 폴더나 숨김 파일로 가지 못하게 한다.

    Args:
        name: 원래 파일 이름.
        limit: 이름 길이 상한. 넘으면 확장자를 남기고 앞부분을 자른다.

    Returns:
        안전한 파일 이름. 남는 글자가 없으면 "file".
    """
    safe = re.sub(r"[^\w.\-]+", "_", name or "").lstrip(".")
    if len(safe) > limit:
        suffix = Path(safe).suffix[:20]
        safe = safe[: limit - len(suffix)] + suffix
    return safe or "file"


def parse_time(value: str | None) -> dt.datetime | None:
    """Jira 시각 문자열(예: 2026-11-03T11:56:29.233+0900)을 datetime으로 바꾼다. 읽지 못하면 None."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(value or "", fmt)
        except (TypeError, ValueError):
            continue
    return None


def match_comments(att: dict, comments: list[dict], same_name: bool = False) -> tuple[list, str | None]:
    """첨부와 짝지을 comment를 찾는다.

    (a) comment 본문이 !파일이름 또는 [^파일이름] 표기로 첨부를 가리키면 그 comment와 짝짓는다(사람, agent 구분 없음).
        같은 ticket에 이름이 같은 첨부가 여럿이면(same_name) 그중 생성 시각이 첨부와 가장 가까운 comment 하나만 짝짓는다.
    (b) (a)가 없으면 같은 작성자(accountId)이고 생성 시각 차이가 MATCH_SECONDS 이내인 comment와 짝짓는다.

    Args:
        att: Jira 첨부 응답 항목. filename, author, created를 읽는다.
        comments: 첨부가 달린 ticket의 norm_comments 결과.
        same_name: 같은 ticket에 이 첨부와 이름이 같은 첨부가 더 있는지 여부.

    Returns:
        (짝지은 comment id 목록, 짝지은 방식). 방식은 "body", "time", 짝이 없으면 None.
    """
    created = parse_time(att.get("created"))
    name = re.escape(att.get("filename") or "")
    if name:
        pat = re.compile(rf"!{name}[|!]|\^{name}\]")
        hits = [c for c in comments if pat.search(c.get("body") or "")]
        if hits and same_name:

            def gap(c: dict) -> float:
                ct = parse_time(c.get("created"))
                return abs((ct - created).total_seconds()) if ct and created else float("inf")

            hits = [min(hits, key=gap)]  # 같은 gap이면 앞선 comment
        if hits:
            return [c["id"] for c in hits], "body"
    author = (att.get("author") or {}).get("accountId")
    if not author or created is None:
        return [], None
    ids = []
    for c in comments:
        ct = parse_time(c.get("created"))
        if (
            (c.get("author") or {}).get("accountId") == author
            and ct
            and abs((ct - created).total_seconds()) <= MATCH_SECONDS
        ):
            ids.append(c["id"])
    return (ids, "time") if ids else ([], None)


def _run_tool(args: list[str], timeout: int) -> tuple[bool, str]:
    """ffmpeg나 ffprobe를 실행한다. 실패해도 예외를 올리지 않는다.

    Returns:
        (성공 여부, 성공이면 stdout, 실패면 원인 문자열).
    """
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"[:300]
    if res.returncode != 0:
        return False, f"exit {res.returncode}: {(res.stderr or '').strip()[:300]}"
    return True, res.stdout or ""


def ensure_tools(kinds: set[str]) -> None:
    """GitHub Actions에서 첨부 처리에 필요한 도구가 없으면 apt-get으로 설치한다. 실패해도 예외를 올리지 않는다.

    워크플로 파일에 설치 스텝을 두지 않는 이유: claude-code-action은 워크플로 파일이 main과 같아야 3단계를
    실행하므로, 워크플로를 바꾸면 브랜치에서 시험할 수 없다(docs/jira-doc-agent/README.md의 "시험 실행" 절).
    GitHub Actions가 아니면(GITHUB_ACTIONS가 "true"가 아니면) 아무것도 하지 않는다.

    Args:
        kinds: 이 ticket 첨부의 형식(attachment_kind 결과) 집합. video, pdf가 있을 때만 해당 도구를 설치한다.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    packages = [
        pkg for kind, (pkg, exes) in TOOL_PACKAGES.items() if kind in kinds and not all(shutil.which(e) for e in exes)
    ]
    if not packages:
        return
    apt = ["sudo", "apt-get", "-o", "Acquire::Retries=3"]
    ok, msg = _run_tool([*apt, "update"], TOOL_INSTALL_TIMEOUT)
    if ok:
        ok, msg = _run_tool([*apt, "install", "-y", "--no-install-recommends", *packages], TOOL_INSTALL_TIMEOUT)
    if ok:
        print(f"[collect] 첨부 처리 도구 설치: {', '.join(packages)}")
    else:
        print(f"[collect] 첨부 처리 도구 설치 실패({', '.join(packages)}): {msg}", file=sys.stderr)


def extract_frames(src: Path, out_dir: Path) -> tuple[list[dict], str]:
    """영상에서 균등 간격 FRAME_COUNT개 시점의 프레임을 jpg로 뽑는다. 긴 변은 FRAME_MAX_SIDE 이하로 줄인다.

    Args:
        src: 영상 파일 경로.
        out_dir: 프레임을 저장할 폴더. frame-01.jpg부터 저장한다.

    Returns:
        (프레임 목록, 실패 원인). 프레임 항목은 path(저장 경로)와 seconds(영상 속 시점)를 담는다.
        프레임을 하나도 뽑지 못하면 빈 목록과 원인을 돌려준다.
        일부 프레임만 실패하면 뽑은 프레임 목록과, 실패한 프레임 수와 마지막 원인을 돌려준다.
    """
    probe = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"]
    ok, out = _run_tool([*probe, str(src)], 60)
    try:
        duration = float(out.strip()) if ok else 0.0
    except ValueError:
        duration = 0.0
    if duration <= 0:
        return [], f"ffprobe로 영상 길이를 구하지 못함({out.strip()[:200] if not ok else '길이 없음'})"
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = f"scale='min({FRAME_MAX_SIDE},iw)':'min({FRAME_MAX_SIDE},ih)':force_original_aspect_ratio=decrease"
    frames, error, failed = [], "", 0
    for i in range(FRAME_COUNT):
        sec = round(duration * (i + 0.5) / FRAME_COUNT, 3)  # 구간 가운데 시점. 영상 끝에서 프레임이 비는 것을 피한다
        path = out_dir / f"frame-{i + 1:02d}.jpg"
        args = ["ffmpeg", "-v", "error", "-y", "-ss", f"{sec:.3f}", "-i", str(src)]
        ok, msg = _run_tool([*args, "-frames:v", "1", "-vf", scale, "-q:v", "3", str(path)], FRAME_TIMEOUT)
        if ok and path.exists():
            frames.append({"path": path.as_posix(), "seconds": sec})
        else:
            failed += 1
            error = msg or "프레임 파일이 없음"
    if not frames:
        shutil.rmtree(out_dir, ignore_errors=True)
        return [], f"ffmpeg 프레임 추출 실패({error})"
    if failed:
        return frames, f"프레임 {FRAME_COUNT}장 중 {failed}장 추출 실패(마지막 원인: {error})"
    return frames, ""


def _save_attachment(j: Jira, att: dict, kind: str, att_dir: Path) -> dict:
    """첨부 하나를 받아 저장하고 status와 path 또는 frames, reason을 돌려준다. 받기 실패는 skipped로 돌려준다.

    받는 크기는 ATT_MAX_BYTES[kind]로 막는다. Jira의 size가 없거나 틀려도 상한을 넘게 읽지 않는다.
    """
    aid = safe_filename(str(att.get("id") or ""))
    name = f"{aid}-{safe_filename(att.get('filename') or '')}"
    limit = ATT_MAX_BYTES[kind]
    try:
        content = j.attachment_content(str(att.get("id")), max_bytes=limit)
    except Exception as e:  # noqa: BLE001  받기 실패는 그 첨부만 건너뛴다
        return {"status": "skipped", "reason": f"받기 실패({type(e).__name__}: {e})"[:300]}
    if len(content) > limit:
        return {"status": "skipped", "reason": f"받는 중 {kind} 기준 {limit} bytes를 넘어 저장하지 않음"}
    if kind != "video":
        path = att_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"status": "saved", "path": path.as_posix()}
    with tempfile.TemporaryDirectory() as tmp:  # 원본 영상은 ctx/에 남기지 않는다
        src = Path(tmp) / ("video" + Path(name).suffix)
        src.write_bytes(content)
        del content  # 프레임을 뽑는 동안 영상 bytes를 메모리에 두지 않는다
        frames, reason = extract_frames(src, att_dir / f"{aid}-frames")
    if not frames:
        return {"status": "skipped", "reason": reason}
    return {"status": "frames", "frames": frames, **({"reason": reason} if reason else {})}


def collect_attachments(j: Jira, key: str, sources: list[tuple[str, list[dict], list[dict]]]) -> list[dict]:
    """부모 ticket과 sub-task의 첨부를 받아 ctx/<KEY>/attachments/에 저장하고 첨부별 기록을 돌려준다.

    생성 시각 최신순으로 형식(attachment_kind)과 크기 기준(ATT_MAX_BYTES)을 통과한 첨부를 받는다.
    저장에 성공한 첨부(saved, frames)가 ATT_MAX_FILES개가 되면 나머지는 받지 않는다.
    영상은 받은 뒤 프레임만 저장한다. GitHub Actions에서는 먼저 필요한 도구를 설치하고(ensure_tools),
    그래도 ffmpeg나 ffprobe가 없으면 영상을 받지 않는다.
    영상 처리에 쓴 시간이 VIDEO_TIME_BUDGET초를 넘으면 남은 영상은 받지 않는다.
    어떤 첨부의 오류도 수집 단계 전체를 멈추지 않는다.

    Args:
        j: Jira 클라이언트. attachment_content를 쓴다.
        key: 부모 ticket 키.
        sources: (첨부가 달린 ticket 키, Jira 첨부 응답 목록, 그 ticket의 norm_comments 결과) 목록.

    Returns:
        첨부별 기록 목록(생성 시각 최신순). 항목마다 id, source, filename, mimeType, size, author, created, kind,
        status(saved, frames, skipped), path 또는 frames, reason, commentIds, matchedBy를 담는다.
        reason은 skipped일 때와, 일부 프레임만 실패한 frames일 때 있다.
    """
    items = [(src, att, comments) for src, atts, comments in sources for att in atts]
    oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    items.sort(key=lambda x: parse_time(x[1].get("created")) or oldest, reverse=True)
    names: dict[tuple, int] = {}
    for src, att, _ in items:
        names[(src, att.get("filename"))] = names.get((src, att.get("filename")), 0) + 1
    ensure_tools({attachment_kind(a.get("mimeType"), a.get("filename")) for _, a, _ in items})
    has_ffmpeg = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    att_dir = CTX / key / "attachments"
    records, stored, video_spent = [], 0, 0.0
    for src, att, comments in items:
        kind = attachment_kind(att.get("mimeType"), att.get("filename"))
        size = att.get("size")
        author = att.get("author") or {}
        ids, matched_by = match_comments(att, comments, names[(src, att.get("filename"))] > 1)
        rec = {
            "id": str(att.get("id")),
            "source": src,
            "filename": att.get("filename"),
            "mimeType": att.get("mimeType"),
            "size": size,
            "author": {"accountId": author.get("accountId"), "displayName": author.get("displayName")},
            "created": att.get("created"),
            "kind": kind,
            "commentIds": ids,
            "matchedBy": matched_by,
        }
        if kind == "other":
            result = {"status": "skipped", "reason": f"읽을 수 없는 형식({att.get('mimeType')})이라 받지 않음"}
        elif isinstance(size, int) and size > ATT_MAX_BYTES[kind]:
            result = {
                "status": "skipped",
                "reason": f"크기 {size} bytes가 {kind} 기준 {ATT_MAX_BYTES[kind]} bytes를 넘어 받지 않음",
            }
        elif stored >= ATT_MAX_FILES:
            result = {
                "status": "skipped",
                "reason": f"첨부 수 상한 {ATT_MAX_FILES}개를 넘어 받지 않음(생성 시각 최신순)",
            }
        elif kind == "video" and not has_ffmpeg:
            result = {"status": "skipped", "reason": "ffmpeg 또는 ffprobe가 없어 영상을 받지 않음"}
        elif kind == "video" and video_spent >= VIDEO_TIME_BUDGET:
            result = {
                "status": "skipped",
                "reason": f"영상 처리 시간 예산 {VIDEO_TIME_BUDGET}초를 넘어 받지 않음",
            }
        else:
            started = time.monotonic() if kind == "video" else 0.0
            try:
                result = _save_attachment(j, att, kind, att_dir)
            except Exception as e:  # noqa: BLE001  예상하지 못한 오류도 그 첨부만 건너뛴다
                result = {"status": "skipped", "reason": f"처리 실패({type(e).__name__}: {e})"[:300]}
            if kind == "video":
                video_spent += time.monotonic() - started
            if result["status"] != "skipped":
                stored += 1
        if result["status"] == "skipped":
            print(f"[collect] {key}: 첨부 {rec['id']} 건너뜀: {result['reason']}", file=sys.stderr)
        records.append({**rec, **result})
    return records


def collect_issue(j: Jira, key: str, site: str, org: str, mode: str) -> dict | None:
    """task 하나의 입력을 ctx/<KEY>/ 아래에 저장한다. 사람 입력이 지난 실행과 같아도 건너뛰지 않는다.

    첨부는 collect_attachments로 받아 attachments/와 attachments.json에 저장한다.

    Args:
        j: Jira 클라이언트.
        key: ticket 키.
        site: Jira 사이트 주소. 비면 링크를 비운다.
        org: GitHub 조직 이름. 비면 PR을 찾지 않는다.
        mode: 실행 모드(review, digest). meta.json에 적는다.

    Returns:
        {"key": 키}. work type이 대상이 아니면 None.
    """
    fields = ISSUE_FIELDS + ([j.tldr_field] if j.tldr_field else [])
    data = j.issue(key, fields)
    f = data.get("fields", {})
    itype = canon_type((f.get("issuetype") or {}).get("name", ""))
    if itype not in TARGET_TYPES or (f.get("issuetype") or {}).get("subtask"):
        print(f"[collect] {key}: work type '{itype}'은 대상이 아님. 건너뜀")
        return None
    desc = f.get("description") or ""
    comments = norm_comments(j.comments(key), site, key)
    changelog = norm_changelog(j.changelog(key))
    subtasks = []
    # (첨부가 달린 ticket 키, 첨부 응답, 그 ticket의 comment)
    att_sources = [(key, f.get("attachment") or [], comments)]
    for st in f.get("subtasks", []) or []:
        sk = st.get("key")
        sd = j.issue(sk, ["summary", "status", "description", "updated", "assignee", "attachment"])
        sf = sd.get("fields", {})
        sub_atts = sf.get("attachment") or []
        sub_comments = norm_comments(j.comments(sk), site, sk)
        att_sources.append((sk, sub_atts, sub_comments))
        subtasks.append(
            {
                "key": sk,
                "summary": sf.get("summary"),
                "status": (sf.get("status") or {}).get("name"),
                "updated": sf.get("updated"),
                "description": sf.get("description") or "",
                "comments": sub_comments,
                "prs": fetch_prs(sk, org) if org else [],  # sub-task 키로만 연결된 PR
                "attachments": [
                    {"filename": a.get("filename"), "created": a.get("created"), "mimeType": a.get("mimeType")}
                    for a in sub_atts
                ],
            }
        )
    prs = fetch_prs(key, org) if org else []
    state = j.prop_get(key)
    ihash = human_input_hash(itype, desc, comments, subtasks, prs, changelog)

    d = CTX / key
    if d.exists():
        shutil.rmtree(d)
    write(
        d / "issue.json",
        {
            "key": key,
            "type": itype,
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "statusCategory": ((f.get("status") or {}).get("statusCategory") or {}).get("key"),
            "assignee": {
                "accountId": (f.get("assignee") or {}).get("accountId"),
                "displayName": (f.get("assignee") or {}).get("displayName"),
            },
            "reporter": {
                "accountId": (f.get("reporter") or {}).get("accountId"),
                "displayName": (f.get("reporter") or {}).get("displayName"),
            },
            "created": f.get("created"),
            "updated": f.get("updated"),
            "labels": f.get("labels", []),
            "attachments": [
                {"filename": a.get("filename"), "created": a.get("created"), "mimeType": a.get("mimeType")}
                for a in (f.get("attachment") or [])
            ],
            "subtaskKeys": [s["key"] for s in subtasks],
            "url": f"{site}/browse/{key}" if site else "",
            "sectionTitles": [t for t, *_ in sections(desc)],
        },
    )
    write(d / "description.wiki", desc)
    write(d / "tldr.txt", (f.get(j.tldr_field) or "") if j.tldr_field else "")
    write(d / "comments.json", comments)
    write(d / "changelog.json", changelog)
    for s in subtasks:
        write(d / "subtasks" / f"{s['key']}.json", s)
    write(d / "prs.json", prs)
    write(d / "state.json", state)
    write(d / "meta.json", {"mode": mode, "inputHash": ihash, "collectedAt": now_iso(), "site": site})
    try:
        attachments = collect_attachments(j, key, att_sources)
    except Exception as e:  # noqa: BLE001  첨부 처리 오류로 수집 단계를 멈추지 않는다
        print(f"[collect] {key}: 첨부 처리 실패, attachments.json을 빈 목록으로 저장함: {e}", file=sys.stderr)
        attachments = []
    write(d / "attachments.json", attachments)
    sub_prs = sum(len(s["prs"]) for s in subtasks)
    n_att = {st: sum(a["status"] == st for a in attachments) for st in ("saved", "frames", "skipped")}
    print(
        f"[collect] {key}: 수집 완료 (comment {len(comments)}, sub-task {len(subtasks)}, PR {len(prs)}, sub-task PR {sub_prs}, "
        f"첨부 저장 {n_att['saved']}, 프레임 {n_att['frames']}, 건너뜀 {n_att['skipped']})"
    )
    return {"key": key}


def collect_scan(j: Jira, project: str, site: str) -> None:
    """누락 검사(stop 사유 comment, 활동 없음)용 요약. Done/Deleted가 아닌 모든 최상위 task.

    src/jira/precheck/run.py의 run_scan이 읽는다. 템플릿 누락은 Jira 전환 검증이 막으므로 description 구역과 첨부 수는 모으지 않는다.
    """
    types = ", ".join(TARGET_TYPES)
    jql = f"project = {project} AND issuetype in ({types}) AND statusCategory != Done AND status != Deleted ORDER BY created ASC"
    fields = ["summary", "status", "issuetype", "created", "updated", "assignee", "comment"]
    for it in j.search(jql, fields):
        key = it["key"]
        f = it.get("fields", {})
        itype = canon_type((f.get("issuetype") or {}).get("name", ""))
        raw_comments = (f.get("comment") or {}).get("comments", [])
        comments = norm_comments(raw_comments, site, key)
        changelog = norm_changelog(j.changelog(key))
        write(
            CTX / "_scan" / f"{key}.json",
            {
                "key": key,
                "type": itype,
                "status": (f.get("status") or {}).get("name"),
                "summary": f.get("summary"),
                "url": f"{site}/browse/{key}" if site else "",
                "created": f.get("created"),
                "updated": f.get("updated"),
                "assignee": {
                    "accountId": (f.get("assignee") or {}).get("accountId"),
                    "displayName": (f.get("assignee") or {}).get("displayName"),
                },
                # body: 본문 앞 100자. 늦게 남긴 stop 사유("stop 사유:"로 시작하는 comment)를 precheck가 찾는 데 쓴다
                "humanComments": [
                    {"created": c["created"], "url": c["url"], "body": c["body"][:100]}
                    for c in comments
                    if c["kind"] == "human"
                ],
                "statusChanges": [c for c in changelog if c["field"] == "status"],
            },
        )
