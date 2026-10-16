"""work type별 description 템플릿 검사(Task T1, T2, Bug B1, B2, Issue I1, I2)."""

from __future__ import annotations

import re

from src.jira.wiki import section_body, strip_placeholders

LIST_ITEM_RE = re.compile(r"^\s*(?:([*#\-]+)|(\d+)[.)])\s+(.*)$")  # 글머리표(*, #, -), 번호(1. 1))
LEGACY_AC_RE = re.compile(r"^AC-\d+\s*[:：]\s*")  # 예전 형식 'AC-1:'은 접두어만 떼고 본문을 쓴다
# Bug의 기본 정보 구역 항목. 키는 줄을 찾는 접두어, 값은 내용 칸에 적는 템플릿의 항목 이름
BUG_INFO_FIELDS = {
    "발생 기기": "발생 기기/서비스",
    "발생 일자": "발생 일자",
    "발생 장비": "발생 장비",
    "발생 계정": "발생 계정",
}
CHECKBOX_CHECKED = re.compile(r"\[\s*[xX✓✔]\s*\]|\(\s*[xX]\s*\)|☑|✅")
CHECKBOX_ANY = re.compile(r"\[\s*[xX✓✔ ]?\s*\]|\(\s*[xX ]?\s*\)|☐|☑")
# Jira 체크 목록(ADF taskList)에서 선택한 항목은 REST API v2의 wiki markup에 취소선 "-제안-"으로 나온다(INNO-36 검수, 2026-10-08)
STRUCK_ITEM = re.compile(r"^\s*(?:[*#-]+\s+)?-(?=[^\s-])[^\n]*[^\s-]-\s*$", re.M)
LINES_NOTE = "(안내문과 빈 줄을 뺀 줄 수)"


def expected_items(desc: str) -> list[dict]:
    """예상 산출물 구역의 목록을 항목별로 나눈다. 형식은 강제하지 않는다.

    - 최상위 글머리표(*, #, -)나 번호(1. 1))로 시작하는 줄이 한 항목이다.
    - 들여쓴 하위 항목(**, ##, -- 또는 글머리표 없는 이어지는 줄)은 바로 위 항목의 본문에 붙인다.
    - 괄호로만 된 템플릿 안내문 줄은 항목으로 세지 않는다.
    - id는 1부터 순서대로 붙인다. Claude 출력(items)과 결과 산출물 구역은 이 번호로 대응시킨다(T3).
    """
    body = section_body(desc, "예상 산출물")
    items: list[dict] = []
    for raw in body.splitlines():
        if not raw.strip():
            continue
        m = LIST_ITEM_RE.match(raw)
        if m:
            marker, text = m.group(1) or m.group(2), m.group(3).strip()
            nested = bool(m.group(1)) and len(m.group(1)) > 1
        else:
            marker, text, nested = None, raw.strip(), True  # 글머리표 없는 줄은 이어지는 줄로 본다
        text = LEGACY_AC_RE.sub("", text)
        if not strip_placeholders(text):  # 안내문 또는 빈 항목
            continue
        if nested and items:
            items[-1]["text"] += ", " + text
        elif marker is None and not items:
            items.append({"id": "1", "text": text})  # 목록 없이 문장만 쓴 경우도 한 항목으로 인정
        else:
            items.append({"id": str(len(items) + 1), "text": text})
    return items


def check_task_template(desc: str) -> dict:
    """Task의 description 구역(예상 산출물, 진행 배경)을 검사한다.

    Args:
        desc: description(wiki markup).

    Returns:
        expected(예상 산출물 목록)와 T1(예상 산출물 있음), T2(진행 배경 작성)의 결과와 내용 칸 문구.
    """
    expected = expected_items(desc)
    t1 = "pass" if expected else "fail"
    t1_detail = f"예상 산출물 {len(expected)}개" if expected else "예상 산출물 항목 없음"
    bg = strip_placeholders(section_body(desc, "진행 배경"))
    t2 = "pass" if len(bg) >= 2 else "fail"
    return {
        "expected": expected,
        "T1": {"result": t1, "detail": t1_detail},
        "T2": {"result": t2, "detail": f"진행 배경 내용 {len(bg)}줄{LINES_NOTE}"},
    }


def check_bug_template(desc: str, attachment_count: int) -> dict:
    """Bug의 description field 구성(기본 정보, 문제(As-Is), 개선(To-Be), 첨부 자료(필수))을 검사한다.

    Args:
        desc: description(wiki markup).
        attachment_count: Jira task의 첨부 파일 개수.

    Returns:
        B1(기본 정보 항목, 문제(As-Is) 내용, Jira 첨부 파일)과 B2(개선(To-Be) 내용)의 결과와 내용 칸 문구.
    """
    info = section_body(desc, "기본 정보")
    missing = []
    for prefix, name in BUG_INFO_FIELDS.items():
        m = re.search(rf"{re.escape(prefix)}[^:：\n]*[:：][ \t]*(.*)", info)
        if not m or not strip_placeholders(m.group(1)):
            missing.append(name)
    problem = strip_placeholders(section_body(desc, "문제"))
    b1_ok = not missing and bool(problem) and attachment_count >= 1
    if missing:
        info_detail = f"기본 정보 중 비어 있는 항목 {len(missing)}개({', '.join(missing)})"
    else:
        info_detail = f"기본 정보 항목 {len(BUG_INFO_FIELDS)}개({', '.join(BUG_INFO_FIELDS.values())}) 모두 채워짐"
    b1_detail = ", ".join(
        [
            info_detail,
            f"문제(As-Is) 내용 {len(problem)}줄{LINES_NOTE}",
            f"첨부 파일 {attachment_count}개",
        ]
    )
    tobe = strip_placeholders(section_body(desc, "개선"))
    return {
        "B1": {"result": "pass" if b1_ok else "fail", "detail": b1_detail},
        "B2": {"result": "pass" if tobe else "fail", "detail": f"개선(To-Be) 내용 {len(tobe)}줄{LINES_NOTE}"},
    }


def check_issue_template(desc: str) -> dict:
    """Issue의 description 구역(이슈 유형, 이슈 내용)을 검사한다.

    이슈 유형은 체크 글자(CHECKBOX_CHECKED)나 Jira 체크 목록의 선택 항목(취소선 줄, STRUCK_ITEM)이 있으면 선택됨으로 본다.

    Args:
        desc: description(wiki markup).

    Returns:
        이슈 유형 선택(I1)과 이슈 내용 작성(I2)의 결과와 내용 칸 문구. I1은 체크 상태를 글자로 알 수 없으면 unknown이다.
    """
    kind = section_body(desc, "이슈 유형")
    if CHECKBOX_CHECKED.search(kind) or STRUCK_ITEM.search(kind):
        i1 = ("pass", "유형 선택됨")
    elif CHECKBOX_ANY.search(kind):
        i1 = ("fail", "체크된 유형 없음")
    else:
        i1 = ("unknown", "체크박스 상태를 텍스트로 확인할 수 없음. agent가 판단")
    body = strip_placeholders(section_body(desc, "이슈 내용"))
    i2_ok = len(body) >= 2 or (len(body) == 1 and len(body[0]) >= 40)
    return {
        "I1": {"result": i1[0], "detail": i1[1]},
        "I2": {"result": "pass" if i2_ok else "fail", "detail": f"이슈 내용 {len(body)}줄{LINES_NOTE}"},
    }
