"""scripts/dev/notion_baseline.py(문서화 Agent 도입 이전 현황 지표 집계)의 판정, 집계, API 호출.

Notion은 가짜 응답만 사용한다. 읽기 메서드만 가진 가짜 객체(FakeNotion)와, urlopen 대신 넘기는 가짜 opener(FakeHTTP)로
대신하고 실제 API를 호출하지 않는다.
"""

from __future__ import annotations

import csv
import datetime as dt
import http.client
import importlib.util
import io
import itertools
import json
import sys
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEMBER, MEMBER2, LEAD = "user-member-1", "user-member-2", "user-lead-0"
_ids = itertools.count()


@pytest.fixture(scope="module")
def nb():
    """scripts/dev/notion_baseline.py 모듈."""
    name = "notion_baseline"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "dev" / "notion_baseline.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


# ---- 가짜 블록, 페이지, comment ----


def rt(text: str, link: str | None = None) -> dict:
    return {
        "type": "text",
        "text": {"content": text, "link": {"url": link} if link else None},
        "plain_text": text,
        "href": link,
    }


def mention(kind: str, text: str = "@") -> dict:
    return {"type": "mention", "mention": {"type": kind, kind: {}}, "plain_text": text, "href": None}


def blk(type_: str, text: str = "", kids: list | None = None, rich: list | None = None, **data) -> dict:
    b = {
        "object": "block",
        "id": f"blk-{next(_ids)}",
        "type": type_,
        "has_children": bool(kids),
        type_: {"rich_text": rich if rich is not None else ([rt(text)] if text else []), **data},
    }
    if kids:
        b["children"] = kids
    return b


def h(text: str, kids: list | None = None) -> dict:
    return blk("heading_3", text, kids, color="blue")


def p(text: str = "", **kw) -> dict:
    return blk("paragraph", text, **kw)


def guide(text: str) -> dict:
    """회색 기울임 안내 문구 paragraph(따옴표 포함)."""
    return p(f"“{text}”")


def li(text: str = "", kids: list | None = None, **kw) -> dict:
    return blk("bulleted_list_item", text, kids, **kw)


def num(text: str, kids: list | None = None) -> dict:
    return blk("numbered_list_item", text, kids)


def todo(text: str, checked: bool, kids: list | None = None) -> dict:
    return blk("to_do", text, kids, checked=checked)


def media(type_: str) -> dict:
    return {"object": "block", "id": f"blk-{next(_ids)}", "type": type_, "has_children": False, type_: {"type": "file"}}


def bare(type_: str, kids: list | None = None) -> dict:
    """텍스트가 없는 블록(구분선, 열, 하위 페이지 등)."""
    b = {"object": "block", "id": f"blk-{next(_ids)}", "type": type_, "has_children": bool(kids), type_: {}}
    if kids:
        b["children"] = kids
    return b


def eng_blocks(background: list, criteria: list, result: list, links: list | None = None) -> list[dict]:
    """Engineering Template 꼴 페이지의 최상위 블록. 구역마다 heading, 안내 문구, 주어진 블록 순서."""
    return [
        h("관련 링크"),
        guide("본 task와 관련된 문서가 있으면 추가"),
        *(links or []),
        h("진행 배경"),
        guide("왜 이 task를 진행하게 되었는가 ?"),  # 물음표 앞 공백은 정규화로 무시
        *background,
        h("Task 완료 기준"),
        p("“Task를 달성했다는 것은 어떻게, 무엇으로 판단하는가?”, “예상 산출물은 무엇인가?”"),
        *criteria,
        h("Method(진행 내용)"),
        guide("어떻게 진행할 것인가?"),
        h("결과 산출물"),
        guide("Task 완료 기준과 1:1 대응해야 함, 완료는 ✅, 미완료는 사유와 함께 ❌ 표기"),
        *result,
        h("참고 자료(reference)"),
        guide("관련하여 참고한 자료가 있는가?"),
    ]


def research_blocks(purpose: list, criteria: list, result: list, refs: list | None = None) -> list[dict]:
    """Research Template 꼴 페이지의 최상위 블록."""
    return [
        h("본 task 목적"),
        guide("무엇을 위해, 무엇을 알아내기위해, 본 task를 진행하는가?"),
        *purpose,
        h("Task 완료 기준"),
        p("“무엇으로 본 task가 완료되었음을 판단하는가?”, “예상 산출물은 무엇인가?”"),
        *criteria,
        h("진행 사항"),
        guide("어떻게 진행했는가?"),
        h("결과(완료 기준 달성 여부)"),
        guide("Task 완료 기준과 1:1 대응해야 함, 완료는 ✅, 미완료는 사유와 함께 ❌ 표기"),
        *result,
        h("참고 자료"),
        guide("관련하여 참고한 자료가 있는가?"),
        *(refs or []),
    ]


def info(nb, status: str = "Done", assignees: tuple = (MEMBER,)):
    return nb.PageInfo(
        id="page-x",
        url="https://www.notion.so/page-x",
        title="실험 task",
        created=dt.datetime(2026, 4, 1, tzinfo=nb.KST),
        status=status,
        assignees={nb.norm_id(a) for a in assignees},
    )


def analyze(nb, blocks: list, status: str = "Done", comments: list | None = None):
    return nb.analyze_page(info(nb, status), blocks, comments or [], nb.SCOPE_ALL)


def comment(cid: str, user: str, parent: str = "page-x") -> dict:
    return {
        "object": "comment",
        "id": cid,
        "discussion_id": f"d-{cid}",
        "parent": {"type": "page_id", "page_id": parent},
        "created_by": {"object": "user", "id": user},
        "rich_text": [rt("확인 부탁")],
    }


def page(pid: str, title: str, created: str, status: str = "Done", people: tuple = (MEMBER,)) -> dict:
    return {
        "object": "page",
        "id": pid,
        "url": f"https://www.notion.so/{pid}",
        "created_time": created,
        "properties": {
            "Name": {"type": "title", "title": [rt(title)]},
            "상태": {"type": "status", "status": {"name": status}},
            "담당자": {"type": "people", "people": [{"object": "user", "id": u} for u in people]},
        },
    }


def flatten(parent: str, tree: list[dict], out: dict[str, list[dict]] | None = None) -> dict[str, list[dict]]:
    """중첩 블록을 API 응답 꼴(블록 ID -> 첫 단계 하위 블록, children 키 없음)로 바꾼다."""
    out = {} if out is None else out
    out[parent] = [{k: v for k, v in b.items() if k != "children"} for b in tree]
    for b in tree:
        if b.get("children"):
            flatten(b["id"], b["children"], out)
    return out


class FakeNotion:
    """run이 부르는 Notion 읽기 메서드만 흉내 낸다."""

    def __init__(self, pages: list[dict], blocks: dict[str, list[dict]], comments: dict[str, list[dict]]):
        self.pages, self.blocks, self.comment_map = pages, blocks, comments
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], Exception] = {}  # (메서드, ID) -> 그 호출에서 낼 예외

    def _call(self, kind: str, block_id: str) -> None:
        self.calls.append((kind, block_id))
        if (kind, block_id) in self.fail:
            raise self.fail[(kind, block_id)]

    def query_pages(self, data_source_id: str) -> list[dict]:
        self._call("query", data_source_id)
        return self.pages

    def block_children(self, block_id: str) -> list[dict]:
        self._call("children", block_id)
        return self.blocks.get(block_id, [])

    def comments(self, block_id: str) -> list[dict]:
        self._call("comments", block_id)
        return self.comment_map.get(block_id, [])


def run(nb, fake, tmp_path, *extra: str) -> Path:
    out = tmp_path / "out"
    argv = ["--data-source", "ds-1", "--assignees", f"{MEMBER},{MEMBER2}", "--since", "2026-03-01"]
    nb.run(fake, nb.parse_args([*argv, "--until", "2026-08-31", "--out", str(out), *extra]))
    return out


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---- 구역 찾기와 템플릿 종류 ----


def test_engineering_sections_and_written(nb):
    bg, crit, res = p("기존 파이프라인이 느림"), li("학습 원격 제출"), li("✅ Slurm 학습 원격 제출 기능 개발")
    blocks = eng_blocks([bg], [crit], [res])
    assert nb.template_kind(blocks) == "engineering"
    sections = nb.find_sections(blocks, "engineering")
    assert sections["background"][1:] == [bg]  # 첫 블록은 안내 문구
    assert sections["criteria"][1:] == [crit]
    assert sections["result"][1:] == [res]
    r = analyze(nb, blocks)
    assert r.kind == "engineering"
    assert r.written == {"background": True, "criteria": True, "result": True}


def test_research_sections_and_written(nb):
    blocks = research_blocks([p("모델 비교")], [li("비교 표")], [li("✅ 비교 표 작성")])
    assert nb.template_kind(blocks) == "research"
    sections = nb.find_sections(blocks, "research")
    assert set(sections) == {"background", "criteria", "method", "result", "reference"}
    assert nb.block_text(sections["background"][1]) == "모델 비교"
    assert analyze(nb, blocks).written == {"background": True, "criteria": True, "result": True}


def test_template_bodies_are_not_written(nb):
    # "[] Engineering Template (James)" 꼴: 안내 문구, 예시 항목, 내용 없는 체크박스, 빈 paragraph만 있음
    james = eng_blocks(
        [p()],
        [todo("", False)],
        [li()],
        links=[li("어떤 문서인지 설명", [li("링크")])],
    )
    r = analyze(nb, james)
    assert r.kind == "engineering"
    assert r.written == {"background": False, "criteria": False, "result": False}
    assert (r.criteria_items, r.result_items) == (0, 0)
    assert "links" in nb.find_sections(james, "engineering")
    assert not any(nb.is_content(b) for b in nb.find_sections(james, "engineering")["links"])
    # Research Template 꼴: 참고 자료의 빈 목록 항목 "-"
    research = research_blocks([], [], [], refs=[li("-")])
    r = analyze(nb, research)
    assert r.kind == "research"
    assert r.written == {"background": False, "criteria": False, "result": False}
    assert not nb.is_content(nb.find_sections(research, "research")["reference"][1])
    # 예시 항목의 하위 항목에 실제 링크를 넣으면 작성으로 간주
    filled = li("어떤 문서인지 설명", [li("", rich=[rt("링크", "https://example.com/doc")])])
    assert nb.is_content(filled)


def test_toggle_heading_children_and_no_template(nb):
    blocks = eng_blocks([p("배경")], [li("A")], [])
    toggled = [b for b in blocks if nb.block_text(b) != "결과 산출물"]
    idx = next(i for i, b in enumerate(toggled) if nb.block_text(b) == "참고 자료(reference)")
    toggled.insert(idx, h("결과 산출물", [li("✅ A 완료")]))
    sections = nb.find_sections(toggled, "engineering")
    assert [nb.block_text(b) for b in sections["result"]] == ["✅ A 완료"]
    plain = [h("메모"), p("자유 형식으로 작성한 내용")]
    assert nb.template_kind(plain) == "none"
    assert nb.find_sections(plain, "none") == {}
    r = analyze(nb, plain)
    assert r.kind == "none" and r.written == {"background": False, "criteria": False, "result": False}


def with_block(nb, blocks: list[dict], phrase: str, new: dict) -> list[dict]:
    """텍스트에 phrase가 들어 있는 블록을 new로 바꾼 목록."""
    return [new if phrase in nb.block_text(b) else b for b in blocks]


def test_guide_block_with_written_text_or_children(nb):
    base = eng_blocks([], [], [])
    assert analyze(nb, base).written == {"background": False, "criteria": False, "result": False}
    # (a) 안내 문구 뒤에 같은 paragraph로 이어 적음
    a = with_block(nb, base, "왜 이 task", p("“왜 이 task를 진행하게 되었는가?” 기존 파이프라인이 느려서 개선함"))
    assert analyze(nb, a).written["background"] is True
    # (b) 안내 문구 paragraph 아래에 들여 적음
    b = with_block(nb, base, "왜 이 task", p("“왜 이 task를 진행하게 되었는가?”", kids=[p("기존 파이프라인이 느림")]))
    assert analyze(nb, b).written["background"] is True
    # (c) Task 완료 기준 안내 문구 뒤에 붙여 적음
    joined = "“Task를 달성했다는 것은 어떻게, 무엇으로 판단하는가?”, “예상 산출물은 무엇인가?” 학습 코드와 리포트"
    c = with_block(nb, base, "Task를 달성했다는", p(joined))
    assert analyze(nb, c).written["criteria"] is True


def test_non_template_heading_stays_in_section(nb):
    result = [h("실험 결과"), li("✅ A"), li("❌ B", [p("데이터 부족")])]
    blocks = eng_blocks([p("배경")], [li("A"), li("B")], result)
    sections = nb.find_sections(blocks, "engineering")
    assert sections["result"][1:] == result  # 첫 블록은 안내 문구. 다음 템플릿 heading 전까지 이어짐
    assert [nb.block_text(b) for b in sections["reference"]] == ["“관련하여 참고한 자료가 있는가?”"]
    r = analyze(nb, blocks)
    assert (r.criteria_items, r.result_items, r.n3, r.n4, r.n5) == (2, 2, True, True, True)


def test_non_template_heading_text_is_not_content(nb):
    assert analyze(nb, eng_blocks([h("메모")], [], [])).written["background"] is False
    assert analyze(nb, eng_blocks([h("메모"), p("기존 파이프라인이 느림")], [], [])).written["background"] is True


def test_divider_only_section_is_not_written(nb):
    assert analyze(nb, eng_blocks([bare("divider"), bare("table_of_contents")], [], [])).written["background"] is False


def test_container_needs_content_inside(nb):
    empty = bare("column_list", [bare("column", [p()]), bare("column", [li()])])
    assert analyze(nb, eng_blocks([empty], [], [])).written["background"] is False
    filled = bare("column_list", [bare("column", [p("기존 파이프라인이 느림")])])
    assert analyze(nb, eng_blocks([filled], [], [])).written["background"] is True


def test_toggle_text_or_children_is_content(nb):
    assert nb.is_content(blk("toggle", "실험 상세"))
    assert not nb.is_content(blk("toggle", "", [p()]))
    assert nb.is_content(blk("toggle", "“어떻게 진행할 것인가?”", [p("데이터 수집 뒤 학습")]))


# ---- 최상위 목록 항목과 N3 ----


def test_top_items_and_n3(nb):
    criteria = [li("A", [li("A-1"), li("A-2")]), p("<Due: 08/25>"), num("B"), todo("C", False), li()]
    assert len(nb.top_items(criteria)) == 3  # 하위 항목, paragraph, 빈 항목은 세지 않음
    same = analyze(nb, eng_blocks([p("배경")], criteria, [li("✅ A"), li("✅ B"), li("❌ C", [p("일정 부족")])]))
    assert (same.criteria_items, same.result_items, same.n3) == (3, 3, True)
    diff = analyze(nb, eng_blocks([p("배경")], criteria, [li("✅ A"), li("✅ B")]))
    assert (diff.criteria_items, diff.result_items, diff.n3) == (3, 2, False)
    zero = analyze(nb, eng_blocks([p("배경")], [p("목록 없이 작성")], [p("목록 없이 작성")]))
    assert (zero.criteria_items, zero.result_items, zero.n3) == (0, 0, False)


# ---- N4 ✅/❌ 표기, N5 미달성 사유 ----


def test_n4_marks(nb):
    marked = analyze(nb, eng_blocks([], [], [li("✅ A"), li("B ❌ 데이터 부족"), todo("C", True), todo("D", False)]))
    assert (marked.n4, marked.ok_items, marked.fail_items) == (True, 2, 2)
    mixed = analyze(nb, eng_blocks([], [], [li("✅ A"), li("B 진행함")]))
    assert (mixed.n4, mixed.ok_items, mixed.fail_items) == (False, 1, 0)
    empty = analyze(nb, eng_blocks([], [], [p("결과는 아래 링크")]))
    assert (empty.result_items, empty.n4) == (0, False)
    holding = analyze(nb, eng_blocks([], [], [li("✅ A")]), status="Holding")
    assert holding.n4 is None  # Done이 아니면 계산 대상이 아님


def test_n5_fail_reason(nb):
    with_reason = analyze(nb, eng_blocks([], [], [li("✅ A"), li("❌ B", [p("GPU 부족으로 보류")])]))
    assert with_reason.n5 is True
    no_reason = analyze(nb, eng_blocks([], [], [li("❌ A", [p("사유")]), todo("B", False)]))
    assert no_reason.n5 is False
    no_fail = analyze(nb, eng_blocks([], [], [li("✅ A")]))
    assert no_fail.n5 is None


def test_n5_empty_child_is_not_reason(nb):
    assert analyze(nb, eng_blocks([], [], [li("❌ A", [p(), li()])])).n5 is False


# ---- N6 근거 링크 ----


def test_n6_evidence(nb):
    def n6(result: list) -> bool:
        return analyze(nb, eng_blocks([], [], result)).n6

    assert n6([li("", rich=[rt("✅ 리포트 "), rt("링크", "https://example.com/r")])]) is True
    assert n6([li("", rich=[rt("✅ "), mention("page", "실험 페이지")])]) is True
    image_in_columns = blk("column_list", kids=[blk("column", kids=[media("image")]), blk("column", kids=[p("설명")])])
    assert n6([li("✅ 결과 그래프", [image_in_columns])]) is True
    assert not n6([li("", rich=[rt("✅ 완료 "), mention("date", "2026-08-25")])])
    assert not n6([li("", rich=[rt("✅ 완료 "), mention("date", "2026-08-25"), mention("user", "@팀원")])])
    assert n6([media("bookmark")]) is True
    assert n6([li("✅ 데이터 정리", [media("file")])]) is True
    link_to_page = {
        "object": "block",
        "id": f"blk-{next(_ids)}",
        "type": "link_to_page",
        "has_children": False,
        "link_to_page": {"type": "page_id", "page_id": "page-report"},
    }
    assert n6([li("✅ 리포트 작성"), link_to_page]) is True


def test_n6_child_page_is_evidence(nb):
    assert analyze(nb, eng_blocks([], [], [li("✅ 리포트 작성"), bare("child_page")])).n6 is True


# ---- N1 리뷰 흔적, N8 Holding 사유 comment ----


def test_n1_review_trace_and_n8(nb):
    blocks = eng_blocks([], [], [li("✅ A")])
    own = analyze(nb, blocks, comments=[comment("c1", MEMBER), comment("c2", MEMBER)])
    assert (own.n1, own.comment_count) == (False, 2)
    other = analyze(nb, blocks, comments=[comment("c1", MEMBER), comment("c2", LEAD)])
    assert other.n1 is True
    assert other.n8 is None
    assert analyze(nb, blocks, status="Holding", comments=[comment("c1", MEMBER)]).n8 is True
    holding = analyze(nb, blocks, status="Holding")
    assert (holding.n8, holding.n1) == (False, None)


# ---- 대상 필터, pages.csv, summary.md ----


def build_fake(nb) -> FakeNotion:
    """페이지 6개 중 대상은 p-start(Done), p-end(Holding) 두 개.

    p-after: 2026-09-01 00:00(한국 시간)이라 기간 밖. p-before: 2026-02-28 23:59(한국 시간)라 기간 밖.
    p-lead: 담당자가 팀장뿐. p-tpl: 제목이 템플릿 페이지.
    """
    pages = [
        page("p-start", "시작 경계", "2026-02-28T15:00:00.000Z"),  # 2026-03-01T00:00+09:00
        page("p-end", "끝 경계", "2026-08-31T14:59:00.000Z", "Holding", (MEMBER2, LEAD)),  # 2026-08-31T23:59+09:00
        page("p-after", "기간 뒤", "2026-08-31T15:00:00.000Z"),  # 2026-09-01T00:00+09:00
        page("p-before", "기간 앞", "2026-02-28T14:59:00.000Z"),
        page("p-lead", "팀장 담당", "2026-05-01T00:00:00.000Z", people=(LEAD,)),
        page("p-tpl", "Engineering Template", "2026-05-01T00:00:00.000Z"),
    ]
    reason = p("데이터 수집 지연")
    start_tree = eng_blocks([p("배경")], [li("A"), li("B")], [li("✅ A"), li("❌ B", [reason])])
    blocks = flatten("p-start", start_tree)
    blocks.update(flatten("p-end", research_blocks([p("목적")], [li("A")], [])))
    comments = {
        "p-start": [comment("c1", MEMBER, "p-start")],
        reason["id"]: [comment("c2", LEAD, reason["id"]), comment("c1", MEMBER, "p-start")],  # c1은 중복
        "p-end": [comment("c3", MEMBER2, "p-end")],
    }
    return FakeNotion(pages, blocks, comments)


def test_run_filters_pages_and_writes_csv(nb, tmp_path):
    fake = build_fake(nb)
    out = run(nb, fake, tmp_path)
    rows = read_csv(out / "pages.csv")
    assert list(rows[0]) == nb.PAGE_COLUMNS
    assert [r["page_id"] for r in rows] == ["p-start", "p-end"]
    start, end = rows
    assert (start["created_month"], start["template"], start["status"]) == ("2026-03", "engineering", "Done")
    assert [start[k] for k in ("criteria_items", "result_items", "ok_items", "fail_items")] == ["2", "2", "1", "1"]
    assert [start[k] for k in ("n1_review", "n3_one_to_one", "n4_marked", "n5_fail_reason")] == ["1", "1", "1", "1"]
    assert (start["n6_evidence"], start["n7_complete"], start["n8_holding_comment"]) == ("0", "0", "")
    assert (start["comment_count"], start["comment_scope"]) == ("2", "page+blocks")  # 중복 comment는 한 번
    assert (end["created_month"], end["template"], end["n8_holding_comment"], end["n1_review"]) == (
        "2026-08",
        "research",
        "1",
        "",
    )
    assert not {"p-after", "p-before", "p-lead", "p-tpl"} & {bid for _, bid in fake.calls}


def test_status_select_type(nb):
    obj = page("p-x", "select 상태", "2026-04-01T00:00:00.000Z")
    obj["properties"]["상태"] = {"type": "select", "select": {"name": "Done"}}
    assert nb.page_info(obj).status == "Done"


def test_assignee_ids_are_normalized(nb):
    info = nb.page_info(page("p-x", "ID 정규화", "2026-04-01T00:00:00.000Z", people=("1A2B-3C4D",)))
    assert nb.select_pages([info], {nb.norm_id("1a2b3c4d")}, None, None) == [info]


def test_in_progress_page_skips_comments(nb, tmp_path):
    pages = [page("p-run", "진행 중", "2026-04-01T00:00:00.000Z", "In progress")]
    fake = FakeNotion(pages, flatten("p-run", eng_blocks([p("배경")], [], [])), {"p-run": [comment("c1", LEAD)]})
    rows = read_csv(run(nb, fake, tmp_path) / "pages.csv")
    assert [kind for kind, _ in fake.calls if kind == "comments"] == []
    assert (rows[0]["comment_count"], rows[0]["comment_scope"]) == ("", "")


def test_limit_takes_earliest_pages(nb, tmp_path):
    fake = build_fake(nb)
    fake.pages.reverse()  # 조회 순서와 관계없이 최초 생성 일자 순으로 처리
    out = run(nb, fake, tmp_path, "--limit", "1")
    assert [r["page_id"] for r in read_csv(out / "pages.csv")] == ["p-start"]
    assert "`--limit 1`을 적용해 최초 생성 일자 순으로 앞의 1개만 처리함" in (out / "summary.md").read_text("utf-8")


def test_run_continues_after_connection_reset(nb, tmp_path):
    fake = build_fake(nb)
    fake.pages.append(page("p-mid", "중간", "2026-05-01T00:00:00.000Z"))  # 최초 생성 순으로 2번째 페이지
    fake.fail[("children", "p-mid")] = ConnectionResetError("connection reset by peer")
    out = run(nb, fake, tmp_path)
    rows = [(r["page_id"], r["error"].split(":")[0]) for r in read_csv(out / "pages.csv")]
    assert rows == [("p-start", ""), ("p-mid", "ConnectionResetError"), ("p-end", "")]
    assert "- 집계한 페이지는 2개임" in (out / "summary.md").read_text(encoding="utf-8")


def test_period_bounds_kst(nb):
    start, end = nb.period_bounds(dt.date(2026, 3, 1), dt.date(2026, 8, 31))
    kst = dt.timezone(dt.timedelta(hours=9))
    assert nb.in_period(dt.datetime(2026, 3, 1, 0, 0, tzinfo=kst), start, end)
    assert nb.in_period(dt.datetime(2026, 8, 31, 23, 59, tzinfo=kst), start, end)
    assert not nb.in_period(dt.datetime(2026, 9, 1, 0, 0, tzinfo=kst), start, end)


def test_page_comments_only_skips_block_comments(nb, tmp_path):
    fake = build_fake(nb)
    out = run(nb, fake, tmp_path, "--page-comments-only")
    assert [bid for kind, bid in fake.calls if kind == "comments"] == ["p-start", "p-end"]
    rows = read_csv(out / "pages.csv")
    assert (rows[0]["comment_count"], rows[0]["comment_scope"], rows[0]["n1_review"]) == ("1", "page", "0")
    default = build_fake(nb)
    run(nb, default, tmp_path)
    assert len([1 for kind, _ in default.calls if kind == "comments"]) > 2


def test_summary_has_no_tilde_average_or_user_ids(nb, tmp_path):
    out = run(nb, build_fake(nb), tmp_path)
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "~" not in text and "평균" not in text
    for user in (MEMBER, MEMBER2, LEAD):
        assert user not in text
    for title in ("## N1 ", "## N2 ", "## N3 ", "## N4 ", "## N5 ", "## N6 ", "## N7 ", "## N8 ", "## N9 "):
        assert title in text
    assert "2026-03-01–2026-08-31" in text
    assert "`--assignees`로 받은 담당자 2명 중 한 명 이상이 담당한 페이지" in text
    assert "100.0% (1/1), n=1" in text  # N1, N3 등 Done 페이지 1개
    assert "| 2026-03 | 1 | 1 | 100.0% (1/1) |" in text


# ---- API 클라이언트: 다음 페이지 조회, 재귀, 429 재시도 ----


class FakeHTTP:
    """urlopen 대신 넘기는 가짜 opener. (메서드, 경로, cursor)마다 정해 둔 응답이나 예외를 돌려준다."""

    def __init__(self, routes: dict[tuple[str, str, str | None], list]):
        self.routes = routes
        self.requests: list[dict] = []

    def __call__(self, req, timeout: float = 0):
        url = urllib.parse.urlsplit(req.full_url)
        query = dict(urllib.parse.parse_qsl(url.query))
        body = json.loads(req.data) if req.data else {}
        cursor = body.get("start_cursor") or query.get("start_cursor")
        path = url.path.removeprefix("/v1")
        self.requests.append({"method": req.get_method(), "path": path, "query": query, "body": body, "req": req})
        result = self.routes[(req.get_method(), path, cursor)].pop(0)
        if isinstance(result, Exception):
            raise result
        return io.BytesIO(json.dumps(result).encode("utf-8"))


def listing(results: list[dict], cursor: str | None = None) -> dict:
    """목록 API 응답. cursor가 있으면 다음 페이지가 있다."""
    return {"object": "list", "results": results, "has_more": cursor is not None, "next_cursor": cursor}


def client(nb, routes: dict):
    sleeps: list[float] = []
    http = FakeHTTP(routes)
    return nb.NotionClient("secret-token", opener=http, sleep=sleeps.append, interval=0), http, sleeps


def test_client_paginates_pages_blocks_and_comments(nb):
    routes = {
        ("POST", "/data_sources/ds-1/query", None): [listing([{"id": "p1"}], "c2")],
        ("POST", "/data_sources/ds-1/query", "c2"): [listing([{"id": "p2"}])],
        ("GET", "/blocks/p1/children", None): [listing([{"id": "b1", "type": "paragraph"}], "k")],
        ("GET", "/blocks/p1/children", "k"): [listing([{"id": "b2", "type": "to_do", "has_children": True}])],
        ("GET", "/blocks/b2/children", None): [listing([{"id": "b3", "type": "child_page", "has_children": True}])],
        ("GET", "/comments", None): [listing([{"id": "m1"}], "m")],
        ("GET", "/comments", "m"): [listing([{"id": "m2"}])],
    }
    c, http, _ = client(nb, routes)
    assert [x["id"] for x in c.query_pages("ds-1")] == ["p1", "p2"]
    assert http.requests[1]["body"] == {"page_size": 100, "start_cursor": "c2"}
    tree = nb.fetch_tree(c, "p1")
    assert [b["id"] for b in tree] == ["b1", "b2"]
    assert [b["id"] for b in tree[1]["children"]] == ["b3"]  # has_children 재귀. child_page는 더 내려가지 않음
    assert [x["id"] for x in c.comments("p1")] == ["m1", "m2"]
    assert http.requests[-1]["query"] == {"block_id": "p1", "page_size": "100", "start_cursor": "m"}
    req = http.requests[0]["req"]
    assert req.get_header("Authorization") == "Bearer secret-token"
    assert req.get_header("Notion-version") == "2026-03-11"


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    body = io.BytesIO(b'{"object": "error", "message": "fake"}')
    return urllib.error.HTTPError("https://api.notion.com/v1/x", code, "error", headers, body)


def test_client_retries_after_429(nb):
    routes = {("GET", "/blocks/p1/children", None): [http_error(429, "2"), listing([{"id": "b1"}])]}
    c, http, sleeps = client(nb, routes)
    assert [b["id"] for b in c.block_children("p1")] == ["b1"]
    assert sleeps == [2.0]
    assert len(http.requests) == 2


def test_client_retries_5xx_urlerror_timeout(nb):
    failures = [http_error(503), urllib.error.URLError("connection reset"), TimeoutError("timed out")]
    routes = {("GET", "/blocks/p1/children", None): [*failures, listing([{"id": "b1"}])]}
    c, http, sleeps = client(nb, routes)
    assert [b["id"] for b in c.block_children("p1")] == ["b1"]
    assert sleeps == [1.0, 2.0, 4.0]  # BACKOFF_BASE에서 시작해 시도마다 두 배
    assert len(http.requests) == 4


def test_client_retries_response_read_errors(nb):
    failures = [
        http.client.RemoteDisconnected("Remote end closed connection without response"),
        ConnectionResetError("connection reset by peer"),
        http.client.IncompleteRead(b"{"),
    ]
    routes = {("GET", "/blocks/p1/children", None): [*failures, listing([{"id": "b1"}])]}
    c, http_fake, sleeps = client(nb, routes)
    assert [b["id"] for b in c.block_children("p1")] == ["b1"]
    assert (sleeps, len(http_fake.requests)) == ([1.0, 2.0, 4.0], 4)


def test_client_gives_up_after_max_attempts(nb):
    routes = {("GET", "/blocks/p1/children", None): [http_error(502) for _ in range(nb.MAX_ATTEMPTS)]}
    c, http, sleeps = client(nb, routes)
    with pytest.raises(nb.NotionError, match="5번 모두 실패"):
        c.block_children("p1")
    assert sleeps == [1.0, 2.0, 4.0, 8.0]  # 마지막 시도 뒤에는 기다리지 않음
    assert len(http.requests) == nb.MAX_ATTEMPTS


def test_client_comment_403_is_permission_error(nb):
    routes = {("GET", "/comments", None): [http_error(403)], ("GET", "/blocks/p1/children", None): [http_error(403)]}
    c, http, sleeps = client(nb, routes)
    with pytest.raises(nb.CommentPermissionError, match="integration에 comment 읽기 권한 필요"):
        c.comments("p1")
    with pytest.raises(nb.NotionError) as other:
        c.block_children("p1")
    assert not isinstance(other.value, nb.CommentPermissionError)  # comment 조회가 아닌 403은 일반 오류
    assert (len(http.requests), sleeps) == (2, [])  # 403은 다시 시도하지 않음


def test_run_records_page_error_and_continues(nb, tmp_path):
    fake = build_fake(nb)
    fake.fail[("children", "p-start")] = nb.NotionError("GET /blocks/p-start/children -> HTTP 404: not found")
    out = run(nb, fake, tmp_path)
    rows = read_csv(out / "pages.csv")
    assert [r["page_id"] for r in rows] == ["p-start", "p-end"]
    failed, ok = rows
    assert "HTTP 404" in failed["error"]
    assert (failed["created_month"], failed["status"], failed["template"], failed["n1_review"]) == (
        "2026-03",
        "Done",
        "",
        "",
    )
    assert (ok["error"], ok["n8_holding_comment"]) == ("", "1")
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "집계에서 뺀 페이지는 1개임(pages.csv의 error 열)" in text
    assert "- 집계한 페이지는 1개임" in text
    assert "리뷰 흔적 비율은 계산할 표본이 없음 (0/0), n=0임" in text  # Done 페이지가 error라 빠짐
    assert "| 2026-03 |" not in text


def test_comment_403_stops_run_with_guidance(nb, tmp_path, monkeypatch):
    pages = [
        page("p-1", "첫 페이지", "2026-04-01T00:00:00.000Z"),
        page("p-2", "둘째 페이지", "2026-04-02T00:00:00.000Z"),
    ]
    routes = {
        ("POST", "/data_sources/ds-1/query", None): [listing(pages)],
        ("GET", "/blocks/p-1/children", None): [listing([])],
        ("GET", "/comments", None): [http_error(403)],
    }
    http = FakeHTTP(routes)
    real = nb.NotionClient
    monkeypatch.setattr(nb, "NotionClient", lambda token: real(token, opener=http, sleep=lambda s: None, interval=0))
    monkeypatch.setenv("NOTION_TOKEN", "secret-token")
    out = tmp_path / "out"
    argv = ["notion_baseline.py", "--data-source", "ds-1", "--assignees", MEMBER, "--out", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as stop:
        nb.main()
    assert "integration에 comment 읽기 권한 필요" in str(stop.value.code)
    assert [r["path"] for r in http.requests] == ["/data_sources/ds-1/query", "/blocks/p-1/children", "/comments"]
    assert not out.exists()  # 둘째 페이지로 넘어가지 않고 바로 멈춤
