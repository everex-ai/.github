"""description(위키 마크업)의 h2 구역 처리와 agent comment 판별."""

from __future__ import annotations

import re

from src.constant.jira import AGENT_MARK

H2 = re.compile(r"^h2\.\s*(.+?)\s*$", re.M)
MENTION_RE = re.compile(r"\[~(?:accountid:)?([^\]]+)\]")
WIKI_LINK_RE = re.compile(r"\[([^|\]]+)\|[^\]]*\]")  # [제목|URL]
# [제목|URL]에서 제목과 URL을 함께 잡는다 (Slack 링크로 바꿀 때)
WIKI_LINK_PARTS_RE = re.compile(r"\[([^|\]]+)\|([^\]]*)\]")
WIKI_COLOR_RE = re.compile(r"\{color(?::[^}]*)?\}")  # {color:#6b778c}, {color}
LIST_MARK_RE = re.compile(r"^[*#-]+\s+")  # wiki 목록 표식("* ", "# ", "#* ")
CELL_BREAK = " \\\\ "  # Jira wiki 표 칸 안의 줄바꿈


# ---------------------------------------------------------------------- 공통 유틸
def is_agent_comment(c: dict) -> bool:
    """agent 또는 Automation이 쓴 comment인지. 사람이 쓴 comment만 근거로 삼기 위해 쓴다."""
    body = (c.get("body") or "").lstrip()
    if body.startswith(AGENT_MARK):
        return True
    author = c.get("author") or {}
    if author.get("accountType") == "app":
        return True
    name = (author.get("displayName") or "").lower()
    return "automation for jira" in name


def sections(desc: str) -> list[tuple[str, int, int, int]]:
    """description(wiki markup)의 h2 구역 목록: (제목, 제목 시작, 본문 시작, 본문 끝)."""
    desc = desc or ""
    heads = list(H2.finditer(desc))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(desc)
        out.append((m.group(1), m.start(), m.end(), end))
    return out


def norm_title(title: str) -> str:
    """구역 제목 비교용. 굵게(*), 기울임(_), 밑줄(+), 색 표식과 공백을 뗀다. 'h2. *진행 배경*'도 잡기 위해."""
    return re.sub(r"\{color[^}]*\}|[*_+\s]", "", title or "").lower()


def find_section(desc: str, prefix: str) -> tuple[str, int, int, int] | None:
    """제목이 prefix로 시작하는 첫 구역. 괄호 설명이 붙은 제목('예상 산출물 (Task 완료 기준)')도 잡는다."""
    p = norm_title(prefix)
    for title, hs, bs, be in sections(desc):
        if norm_title(title).startswith(p):
            return title, hs, bs, be
    return None


def section_body(desc: str, prefix: str) -> str:
    """제목이 prefix로 시작하는 첫 구역의 본문. 구역이 없으면 빈 문자열."""
    hit = find_section(desc, prefix)
    return (desc or "")[hit[2] : hit[3]].strip() if hit else ""


def set_section(desc: str, prefix: str, body: str, stamp: str, note: str) -> str:
    """prefix 구역의 본문만 body로 바꾼다. 다른 구역은 건드리지 않는다. 구역이 없으면 맨 뒤에 추가."""
    desc = desc or ""
    block = f"\n{{color:#6b778c}}_{note} 마지막 갱신 {stamp}_{{color}}\n{body.strip()}\n\n"
    hit = find_section(desc, prefix)
    if hit:
        title, hs, bs, be = hit
        return desc[:bs] + block + desc[be:]
    return desc.rstrip() + f"\n\nh2. {prefix}" + block


def strip_placeholders(text: str) -> list[str]:
    """템플릿 안내문(괄호로만 된 줄, 빈 글머리표)을 뺀 실제 내용 줄 목록."""
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        core = re.sub(r"^[*#\-\s]+", "", s).strip()
        if not core:
            continue
        if re.fullmatch(r"[\(（].*[\)）]", core):  # (안내문)
            continue
        if core.startswith("{color:#6b778c}"):  # agent 안내 줄
            continue
        lines.append(core)
    return lines


def cell_text(text: str) -> str:
    """검사 표 칸에 넣을 문장. 칸 구분자와 겹치지 않게 Jira wiki 링크 밖의 "|"만 "/"로 바꾼다.

    링크 [제목|URL] 안의 "|"는 링크를 이루므로 그대로 둔다.

    Args:
        text: 검사 결과의 detail 또는 문서화 리뷰 points의 한 줄(wiki markup).

    Returns:
        링크 밖의 "|"를 "/"로 바꾼 문장.
    """
    parts: list[str] = []
    pos = 0
    for m in WIKI_LINK_RE.finditer(text):
        parts += [text[pos : m.start()].replace("|", "/"), m.group(0)]
        pos = m.end()
    parts.append(text[pos:].replace("|", "/"))
    return "".join(parts)


def short_name(text: str, limit: int = 40) -> str:
    """T8 detail과 요청에서 초과 달성 항목을 가리키는 이름. wiki 링크는 제목만 남기고 limit자에서 자른다."""
    s = WIKI_LINK_RE.sub(r"\1", text).replace("|", "/").strip()
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def strip_claude_header(text: str) -> str:
    """Claude가 comment.wiki에 판정 줄이나 검사 표를 넣었더라도 스크립트가 만든 것과 겹치지 않게 뗀다."""
    kept = []
    for line in text.strip().splitlines():
        s = line.strip()
        if s.startswith("검수 결과") or s.startswith("||") or (s.startswith("|") and s.endswith("|")):
            continue
        kept.append(line)
    return "\n".join(kept).strip()
