"""Notion 페이지 템플릿 판정. 템플릿 종류, 구역, 안내 문구, 예시 항목, 작성한 내용, 근거를 블록에서 읽는다."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator

TEMPLATE_TITLES = {"Engineering Template", "Research Template", "[] Engineering Template (James)"}

# 템플릿별 구역 heading 문구 -> 구역 키. "[] Engineering Template (James)"는 Engineering Template과 heading이 같다
TEMPLATE_HEADINGS = {
    "engineering": {
        "관련 링크": "links",
        "진행 배경": "background",
        "Task 완료 기준": "criteria",
        "Method(진행 내용)": "method",
        "결과 산출물": "result",
        "참고 자료(reference)": "reference",
    },
    "research": {
        "본 task 목적": "background",
        "Task 완료 기준": "criteria",
        "진행 사항": "method",
        "결과(완료 기준 달성 여부)": "result",
        "참고 자료": "reference",
    },
}
KIND_NONE = "none"  # 템플릿 구역 heading을 찾지 못한 페이지
SECTION_LABELS = {  # N2에서 작성 여부를 판정하는 구역
    "background": "진행 배경(Research Template은 본 task 목적)",
    "criteria": "Task 완료 기준",
    "result": "결과(Engineering Template은 결과 산출물, Research Template은 결과(완료 기준 달성 여부))",
}
GUIDE_PHRASES = (  # 각 구역 heading 바로 아래의 안내 문구(따옴표 안 문자열)
    "본 task와 관련된 문서가 있으면 추가",
    "왜 이 task를 진행하게 되었는가?",
    "Task를 달성했다는 것은 어떻게, 무엇으로 판단하는가?",
    "예상 산출물은 무엇인가?",
    "어떻게 진행할 것인가?",
    "Task 완료 기준과 1:1 대응해야 함",
    "완료는 ✅, 미완료는 사유와 함께 ❌ 표기",
    "관련하여 참고한 자료가 있는가?",
    "무엇을 위해, 무엇을 알아내기위해, 본 task를 진행하는가?",
    "무엇으로 본 task가 완료되었음을 판단하는가?",
    "어떻게 진행했는가?",
)
EXAMPLE_TEXTS = (  # 템플릿 예시 항목. 내용 없는 체크박스와 빈 목록 항목은 빈 블록으로 따로 판정한다
    "어떤 문서인지 설명",  # "[] Engineering Template (James)"의 관련 링크 목록 항목
    "링크",  # 위 목록 항목의 하위 항목
    "-",  # Research Template 참고 자료의 빈 목록 항목
)
HEADING_TYPES = {"heading_1", "heading_2", "heading_3"}
LIST_TYPES = {"bulleted_list_item", "numbered_list_item", "to_do"}
EMPTY_TYPES = {"paragraph", "quote", "callout", "toggle", "table_row", *LIST_TYPES}  # 텍스트가 없으면 빈 블록
NON_CONTENT_TYPES = {"divider", "table_of_contents", "breadcrumb"}  # 작성한 내용으로 세지 않는 블록 종류
CONTAINER_TYPES = {"column_list", "column", "synced_block", "table"}  # 하위 블록에 내용이 있을 때만 작성
NO_DESCEND = {"child_page", "child_database"}  # 하위 블록을 조회하지 않는 블록 종류(다른 페이지, 데이터베이스)
EVIDENCE_BLOCKS = {  # 근거로 세는 블록 종류 -> summary.md에 적는 이름
    "file": "파일",
    "pdf": "PDF 파일",
    "image": "이미지",
    "video": "동영상",
    "bookmark": "bookmark",
    "embed": "embed",
    "link_preview": "링크 미리보기",
    "link_to_page": "페이지 링크",
    "child_page": "하위 페이지",
}
EVIDENCE_MENTIONS = {  # 근거로 세는 mention 종류 -> summary.md에 적는 이름. 날짜, 사용자 mention은 근거가 아니다
    "page": "페이지 mention",
    "database": "데이터베이스 mention",
    "link_preview": "링크 미리보기 mention",
    "link_mention": "링크 mention",
}
OK_MARK, FAIL_MARK = "✅", "❌"


def _rt_text(rt: dict) -> str:
    return rt.get("plain_text") or (rt.get("text") or {}).get("content") or ""


def children(block: dict) -> list[dict]:
    """fetch_tree가 붙인 하위 블록 목록.

    Args:
        block: 블록 객체.

    Returns:
        하위 블록 목록. 없으면 빈 목록.
    """
    return block.get("children") or []


def walk(blocks: list[dict]) -> Iterator[dict]:
    """블록과 그 하위 블록을 깊이 우선으로 모두 돌려준다.

    Args:
        blocks: 블록 목록.

    Yields:
        블록 객체.
    """
    for b in blocks:
        yield b
        yield from walk(children(b))


def rich_texts(block: dict) -> list[dict]:
    """블록의 rich_text 목록. 표 행(table_row)은 모든 칸의 rich_text를 이어 붙인다.

    Args:
        block: 블록 객체.

    Returns:
        rich_text 객체 목록.
    """
    data = block.get(block.get("type") or "") or {}
    out = list(data.get("rich_text") or [])
    for cell in data.get("cells") or []:
        out += cell
    return out


def block_text(block: dict) -> str:
    """블록의 텍스트(rich_text의 plain_text를 이어 붙인 것).

    Args:
        block: 블록 객체.

    Returns:
        텍스트.
    """
    return "".join(_rt_text(rt) for rt in rich_texts(block))


def _norm(s: str) -> str:
    """비교용 문자열. 따옴표와 모든 공백(물음표 주변 공백 포함)을 지우고 대소문자를 무시한다."""
    s = unicodedata.normalize("NFC", s)
    s = "".join(ch for ch in s if not ch.isspace() and ch not in "\"'“”‘’")
    return s.casefold()


GUIDE_NORMS = tuple(_norm(p) for p in GUIDE_PHRASES)
EXAMPLE_NORMS = {_norm(t) for t in EXAMPLE_TEXTS}
ALL_HEADING_NORMS = {_norm(h) for heads in TEMPLATE_HEADINGS.values() for h in heads}


def _is_link(rt: dict) -> bool:
    if rt.get("type") == "mention":
        return (rt.get("mention") or {}).get("type") in EVIDENCE_MENTIONS
    return bool(rt.get("href") or (rt.get("text") or {}).get("link"))


def is_guide(block: dict) -> bool:
    """안내 문구가 들어 있는 블록인지 판정한다. 정규화한 텍스트에 안내 문구가 들어 있으면 True다.

    Args:
        block: 블록 객체.

    Returns:
        안내 문구가 들어 있으면 True.
    """
    text = _norm(block_text(block))
    return bool(text) and any(g in text for g in GUIDE_NORMS)


def guide_residual(block: dict) -> str:
    """정규화한 텍스트에서 안내 문구를 모두 지우고 문장부호와 공백을 뺀 나머지 글자.

    Args:
        block: 블록 객체.

    Returns:
        남은 글자. 안내 문구 외에 작성한 글자가 없으면 빈 문자열.
    """
    text = _norm(block_text(block))
    for g in GUIDE_NORMS:
        text = text.replace(g, "")
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))


def is_empty(block: dict) -> bool:
    """빈 블록(텍스트 없는 paragraph, 목록 항목, to_do, 인용, callout, toggle, 표 행)인지 판정한다.

    Args:
        block: 블록 객체.

    Returns:
        빈 블록이면 True.
    """
    return block.get("type") in EMPTY_TYPES and not block_text(block).strip()


def is_example(block: dict) -> bool:
    """템플릿 예시 항목인지 판정한다. 텍스트가 예시 문구와 같고 링크가 없어야 한다.

    Args:
        block: 블록 객체.

    Returns:
        템플릿 예시 항목이면 True.
    """
    if block.get("type") not in EMPTY_TYPES or _norm(block_text(block)) not in EXAMPLE_NORMS:
        return False
    return not any(_is_link(rt) for rt in rich_texts(block))


def is_content(block: dict) -> bool:
    """작성한 내용인지 판정한다.

    - 구분선, 목차, 상위 경로 블록(NON_CONTENT_TYPES)은 내용이 아니다.
    - 다른 블록을 담는 블록(CONTAINER_TYPES), heading, 빈 블록, 템플릿 예시 항목은 하위 블록에 내용이 있을 때만
      내용이다. 구역 안의 heading은 템플릿에 없는 heading이므로 heading 문구로는 세지 않는다.
    - 안내 문구가 들어 있는 블록은 안내 문구를 지운 뒤 글자가 남거나, 하위 블록에 내용이 있을 때만 내용이다.
    - toggle은 다른 텍스트 블록과 같다. 제목이 내용이거나 하위 블록에 내용이 있으면 내용이다.

    Args:
        block: 블록 객체.

    Returns:
        작성한 내용이면 True.
    """
    kind = block.get("type")
    if kind in NON_CONTENT_TYPES:
        return False
    if kind in CONTAINER_TYPES or kind in HEADING_TYPES or is_empty(block) or is_example(block):
        return any(is_content(c) for c in children(block))
    if is_guide(block):
        return bool(guide_residual(block)) or any(is_content(c) for c in children(block))
    return True


def template_kind(blocks: list[dict]) -> str:
    """최상위 heading 문구의 집합으로 템플릿 종류를 판정한다.

    Args:
        blocks: 페이지의 최상위 블록.

    Returns:
        heading이 더 많이 겹치는 템플릿(engineering, research). 같으면 engineering, 하나도 없으면 none.
    """
    found = {_norm(block_text(b)) for b in blocks if b.get("type") in HEADING_TYPES}
    scores = {kind: len(found & {_norm(h) for h in heads}) for kind, heads in TEMPLATE_HEADINGS.items()}
    best = max(scores, key=lambda kind: scores[kind])  # 같은 점수면 앞의 engineering
    return best if scores[best] else KIND_NONE


def find_sections(blocks: list[dict], kind: str) -> dict[str, list[dict]]:
    """템플릿 heading부터 다음 템플릿 heading 전까지의 최상위 블록을 구역으로 묶는다.

    - 구역은 TEMPLATE_HEADINGS의 heading을 만날 때만 바뀐다. 다른 템플릿의 heading이면 구역 밖이 된다.
    - 템플릿 heading이 toggle heading(has_children이 참)이면 그 하위 블록도 구역에 넣는다.
    - 템플릿에 없는 heading은 현재 구역의 블록으로 남긴다. toggle heading이면 하위 블록을 구역의 최상위 블록으로
      넣는다.

    Args:
        blocks: 페이지의 최상위 블록.
        kind: template_kind의 결과.

    Returns:
        구역 키(TEMPLATE_HEADINGS의 값) -> 구역 블록 목록. kind가 none이면 빈 dict.
    """
    names = {_norm(h): key for h, key in TEMPLATE_HEADINGS.get(kind, {}).items()}
    sections: dict[str, list[dict]] = {}
    current = None
    for b in blocks:
        heading = _norm(block_text(b)) if b.get("type") in HEADING_TYPES else None
        if heading in ALL_HEADING_NORMS:
            current = names.get(heading)
            if current is not None:
                sections.setdefault(current, []).extend(children(b))
        elif current is not None:
            if heading is not None and children(b):
                sections[current] += [{**b, "children": []}, *children(b)]
            else:
                sections[current].append(b)
    return sections


def top_items(blocks: list[dict]) -> list[dict]:
    """구역의 최상위 목록 항목(bulleted_list_item, numbered_list_item, to_do 중 텍스트가 있는 것).

    Args:
        blocks: 구역 블록 목록.

    Returns:
        목록 항목 블록. 하위 항목과 paragraph는 넣지 않는다.
    """
    return [b for b in blocks if b.get("type") in LIST_TYPES and block_text(b).strip()]


def item_mark(block: dict) -> str | None:
    """결과 항목의 표기. to_do는 체크를 ✅, 미체크를 ❌로 간주한다. ❌와 ✅가 함께 있으면 ❌로 간주한다.

    Args:
        block: 목록 항목 블록.

    Returns:
        OK_MARK, FAIL_MARK 중 하나. 표기가 없으면 None.
    """
    if block.get("type") == "to_do":
        return OK_MARK if (block.get("to_do") or {}).get("checked") else FAIL_MARK
    text = block_text(block)
    if FAIL_MARK in text:
        return FAIL_MARK
    return OK_MARK if OK_MARK in text else None


def has_evidence(blocks: list[dict]) -> bool:
    """근거(링크가 걸린 텍스트, EVIDENCE_MENTIONS의 mention, EVIDENCE_BLOCKS의 블록)가 하위 블록까지 하나라도 있는지 확인한다.

    Args:
        blocks: 구역 블록 목록.

    Returns:
        근거가 있으면 True.
    """
    return any(b.get("type") in EVIDENCE_BLOCKS or any(_is_link(rt) for rt in rich_texts(b)) for b in walk(blocks))
