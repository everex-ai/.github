"""공통 검사 R5(첨부 설명)가 스키마, 검사 이름과 순서, 검수 결과 재결정, Slack 요점, 지시 파일에 같게 들어갔는지 확인한다."""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함

R5_FAIL = {"id": "R5", "result": "fail", "detail": "첨부 2건 중 설명 없음 1건, 내용 다름 1건"}
ATTACHMENTS = [
    {"file": "결과.png", "comment": "2026-11-03", "description": "fail", "content": "n/a", "note": "파일 이름만 있음"},
    {
        "file": "정량평가셋 확장 계획.pdf",
        "comment": "2026-11-04",
        "description": "pass",
        "content": "fail",
        "note": "설명은 정확도 92%, PDF 표는 89%",
    },
]
R5_CELL = (
    "결과.png: 설명 존재 ❌, 내용 일치 ➖ – 파일 이름만 있음"
    " \\\\ "
    "정량평가셋 확장 계획.pdf: 설명 존재 ✅, 내용 일치 ❌ – 설명은 정확도 92%, PDF 표는 89%"
)
DONE = {"id": "1", "done": True, "result": "재현 테스트 3건 추가"}
REASONED = {"id": "2", "done": False, "result": "수치만 있음", "why": "라벨 재수집이 다음 분기로 밀림"}


@pytest.fixture(scope="module")
def check():
    """scripts/src/constant/check.py (모듈 이름 src.constant.check)."""
    return importlib.import_module("src.constant.check")


@pytest.fixture(scope="module")
def jv():
    """scripts/src/jira/verdict.py (모듈 이름 src.jira.verdict)."""
    return importlib.import_module("src.jira.verdict")


@pytest.fixture(scope="module")
def jcm():
    """scripts/src/jira/comment.py (모듈 이름 src.jira.comment)."""
    return importlib.import_module("src.jira.comment")


@pytest.fixture(scope="module")
def jsl():
    """scripts/src/jira/slack.py (모듈 이름 src.jira.slack)."""
    return importlib.import_module("src.jira.slack")


def verdict_schemas() -> list[dict]:
    """두 스키마의 verdict 정의(agent-output.json의 tasks[].verdict, verdict.json)."""
    agent = json.loads((ROOT / "schemas" / "agent-output.json").read_text(encoding="utf-8"))
    verdict = json.loads((ROOT / "schemas" / "verdict.json").read_text(encoding="utf-8"))
    return [agent["properties"]["tasks"]["items"]["properties"]["verdict"], verdict]


def check_id_patterns() -> list[str]:
    return [v["properties"]["checks"]["items"]["properties"]["id"]["pattern"] for v in verdict_schemas()]


@pytest.mark.parametrize(("cid", "ok"), [("R2", True), ("R4", True), ("R5", True), ("R1", False), ("R6", False)])
def test_schema_check_id_pattern_allows_r5(cid, ok):
    for pattern in check_id_patterns():
        assert bool(re.fullmatch(pattern, cid)) is ok


def test_schema_attachments_definition():
    """verdict의 attachments는 선택 필드이고, 두 스키마의 정의(설명 문장 포함)가 같다."""
    agent, verdict = verdict_schemas()
    defs = [v["properties"]["attachments"] for v in (agent, verdict)]
    for v in (agent, verdict):
        assert "attachments" not in v["required"]
    for d in defs:
        assert d["type"] == "array"
        item = d["items"]
        assert item["type"] == "object" and item["additionalProperties"] is False
        assert item["required"] == ["file", "comment", "description", "content"]
        props = item["properties"]
        assert set(props) == {"file", "comment", "description", "content", "note"}
        assert props["description"]["enum"] == ["pass", "fail"]
        assert props["content"]["enum"] == ["pass", "fail", "n/a"]
        assert props["note"]["maxLength"] == 150 and props["file"]["type"] == "string"
    assert defs[0] == defs[1]


def test_r5_name_and_order(check):
    ids = list(check.CHECK_NAMES)
    assert check.CHECK_NAMES["R5"] == "첨부 파일에 대한 설명"
    assert ids.index("R5") == ids.index("R4") + 1
    assert check.CHECK_ORDER["R4"] < check.CHECK_ORDER["R5"] < check.CHECK_ORDER["A1"]


def test_check_table_puts_r5_after_r4(jcm):
    checks = [
        {"id": "A2", "result": "n/a", "detail": "변경 없음"},
        R5_FAIL,
        {"id": "R4", "result": "n/a", "detail": "stop 이력 없음"},
    ]
    rows = jcm.check_table(checks).splitlines()
    assert [r.split(" | ")[0] for r in rows[1:]] == [
        "| stop 사유 comment (R4)",
        "| 첨부 파일에 대한 설명 (R5)",
        "| 완료 기준의 사후 변경 (A2)",
    ]
    assert rows[2] == "| 첨부 파일에 대한 설명 (R5) | ❌ 보완 필요 | 첨부 2건 중 설명 없음 1건, 내용 다름 1건 |"


def test_check_table_r5_cell_lists_attachments(jcm):
    """attachments가 있으면 R5 칸은 첨부마다 한 줄이고, detail은 쓰지 않는다."""
    table = jcm.check_table([R5_FAIL, {"id": "R4", "result": "n/a", "detail": "x"}], None, ATTACHMENTS)
    row = next(r for r in table.splitlines() if r.startswith("| 첨부 파일에 대한 설명 (R5)"))
    assert row == f"| 첨부 파일에 대한 설명 (R5) | ❌ 보완 필요 | {R5_CELL} |"
    assert row.count(" \\\\ ") == len(ATTACHMENTS) - 1
    assert "| stop 사유 comment (R4) | ➖ 해당 없음 | x |" in table  # 다른 행은 그대로


@pytest.mark.parametrize("attachments", [None, [], [{"file": ""}, "x", {"comment": "2026-11-03"}]])
def test_check_table_r5_falls_back_to_detail(jcm, attachments):
    row = jcm.check_table([R5_FAIL], None, attachments).splitlines()[1]
    assert row == "| 첨부 파일에 대한 설명 (R5) | ❌ 보완 필요 | 첨부 2건 중 설명 없음 1건, 내용 다름 1건 |"


def test_attachment_lines_omit_empty_comment_and_note(jcm):
    got = jcm.attachment_lines([{"file": "a.mp4", "comment": "", "description": "pass", "content": "pass"}])
    assert got == ["a.mp4: 설명 존재 ✅, 내용 일치 ✅"]
    got = jcm.attachment_lines([{"file": "a|b.md", "comment": "description", "description": "pass", "content": "n/a"}])
    assert got == ["a|b.md: 설명 존재 ✅, 내용 일치 ➖"]
    assert jcm.check_table([R5_FAIL], None, [{"file": "a|b.md", "description": "pass", "content": "n/a"}]).endswith(
        "| a/b.md: 설명 존재 ✅, 내용 일치 ➖ |"
    )  # 링크 밖의 "|"는 표가 깨지지 않게 "/"로 바뀜


def test_review_comment_passes_attachments_to_table(jcm):
    text = jcm.review_comment("fix", [R5_FAIL], [], [], [], "* 요약", ["설명 추가 필요"], [], None, ATTACHMENTS)
    assert f"| 첨부 파일에 대한 설명 (R5) | ❌ 보완 필요 | {R5_CELL} |" in text


def test_save_keeps_attachments(tmp_path):
    save = importlib.import_module("src.jira.output.save")
    assert "attachments" in save.VERDICT_KEYS
    d = tmp_path / "ctx" / "INNO-17"
    d.mkdir(parents=True)
    agent = {"verdict": {"verdict": "fix", "checks": [R5_FAIL], "attachments": ATTACHMENTS}}
    assert save.build_verdict(d, "INNO-17", "review", agent)["attachments"] == ATTACHMENTS
    assert "attachments" not in save.build_verdict(d, "INNO-17", "review", {"verdict": {"verdict": "fix"}})


def test_r5_is_owner_fixable(check, jv):
    assert "R5" in check.OWNER_FIXABLE_CHECKS
    assert jv.owner_fails([R5_FAIL, {"id": "R3", "result": "fail"}]) == [R5_FAIL]


@pytest.mark.parametrize(
    ("v", "want"),
    [
        ("fix", ("fix", None)),
        ("pass", ("fix", "미달성 항목이 있어 통과에서 보류로 바꿈")),
        ("escalate", ("escalate", None)),
    ],
    ids=["설명 없음 보류는 사유가 있어도 보류 유지", "통과면 보류로", "내용이 달라 검토 요청이면 그대로"],
)
def test_recheck_verdict_with_r5_fail(jv, v, want):
    assert jv.recheck_verdict(v, "Task", [DONE, REASONED], [R5_FAIL], False, []) == want


def test_recheck_verdict_without_r5_fail_escalates(jv):
    r5_pass = {"id": "R5", "result": "pass", "detail": "첨부 2건 모두 설명 있음"}
    got = jv.recheck_verdict("fix", "Task", [DONE, REASONED], [r5_pass], False, [])
    assert got == ("escalate", "미달성 항목마다 담당자가 남긴 사유가 있어 보류에서 검토 요청으로 바꿈")


def test_slack_hold_names_r5_as_fix(jsl):
    assert jsl.review_slack_reasons("fix", "Bug", [], [], [], [R5_FAIL], [], []) == ["첨부 파일에 대한 설명 보완 필요"]


@pytest.mark.parametrize("agent", [[], ["설명의 정확도와 첨부 수치가 다름"]], ids=["요점 없음", "요점 있음"])
def test_slack_escalate_does_not_name_r5_as_fix(jsl, agent):
    """내용 기준 실패로 검토 요청이면 R5 fail이 Slack에 "보완 필요"로 나가지 않는다."""
    got = jsl.review_slack_reasons("escalate", "Bug", [], [], [], [R5_FAIL], [], agent)
    assert got == agent and "첨부 파일에 대한 설명 보완 필요" not in got


def test_prompt_tables_name_r5_like_check_names(check):
    """공통 검사 절의 표 행 ID와 이름이 CHECK_NAMES와 같다. 문장은 문서 검토에 맡기고 표 행만 확인한다."""
    review = (ROOT / "prompts" / "review.md").read_text(encoding="utf-8")
    section = review.split("## 공통 검사 항목", 1)[1].split("\n---", 1)[0]
    rows = [line.split(" | ")[:2] for line in section.splitlines() if line.startswith("| R")]
    assert rows == [[f"| {cid}", check.CHECK_NAMES[cid]] for cid in ("R2", "R3", "R4", "R5")]


@pytest.mark.parametrize("itype", ["task", "bug", "issue"])
def test_type_review_files_include_r5(itype):
    """work type 검수 파일의 검수 결과 절이 R5를 다룬다. 문구는 확인하지 않는다."""
    text = (ROOT / "prompts" / "types" / itype / "review.md").read_text(encoding="utf-8")
    result = text.split("## 검수 결과", 1)[1].split("\n---", 1)[0]
    assert "R5" in result


@pytest.fixture(scope="module")
def render():
    """scripts/src/dev/samples/render.py (모듈 이름 src.dev.samples.render)."""
    return importlib.import_module("src.dev.samples.render")


@pytest.fixture(scope="module")
def ja():
    """scripts/apply.py (모듈 이름 jira_apply). tests/jira/test_render.py와 같은 이름으로 한 번만 불러온다."""
    name = "jira_apply"
    if name not in sys.modules:
        saved = list(sys.path)  # apply.py가 scripts/를 sys.path 맨 앞에 넣으므로 불러온 뒤 되돌린다
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "apply.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved
    return sys.modules[name]


def test_apply_issue_renders_r5_cell_from_verdict_attachments(render, ja, jsl, monkeypatch, tmp_path):
    """검수 모드의 apply_issue가 verdict.json의 attachments로 검수 comment의 R5 칸을 만든다."""
    monkeypatch.setenv("JIRA_LEAD_NAME", "Kai")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(jsl, "slack_post", lambda text: "전송 완료")
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    expected = [{"id": "1", "text": "재현 테스트 추가"}]
    render.write_issue(ctx, "INNO-17", "Task", expected, {}, status="ready-to-done")
    v = render.task_verdict("INNO-17", "review", "fix") | {
        "checks": [R5_FAIL],
        "items": [{"id": "1", "done": True, "result": "재현 테스트 3건 추가"}],
        "extra": [],
        "slack": [],
        "attachments": ATTACHMENTS,
    }
    render.write_out(out, "INNO-17", v, "* 요약")
    j = render.FakeJira({"INNO-17": ""})
    ja.apply_issue(j, ctx / "INNO-17", out, "review", False, "acc-lead", ja.Summary(str(tmp_path / "summary.md")))
    assert f"| 첨부 파일에 대한 설명 (R5) | ❌ 보완 필요 | {R5_CELL} |" in j.comments[0][1]


@pytest.mark.parametrize("bad", [5, True, "abc", {"file": "a.png"}], ids=["숫자", "불리언", "문자열", "dict"])
def test_attachment_lines_ignores_malformed_attachments(jcm, bad):
    """목록이 아니거나 기준 값이 빠진 attachments는 줄을 만들지 않는다(검수 comment 게시가 멈추지 않게)."""
    assert jcm.attachment_lines(bad) == []


def test_attachment_lines_skips_item_without_valid_results(jcm):
    """기준 값이 정해진 값이 아닌 항목만 건너뛰고 나머지는 그대로 만든다."""
    items = [
        {"file": "a.png", "comment": "", "description": "pass"},
        {"file": "b.png", "comment": "", "description": "pass", "content": "maybe"},
        {"file": "c.png", "comment": "", "description": "pass", "content": "pass"},
    ]
    assert jcm.attachment_lines(items) == ["c.png: 설명 존재 ✅, 내용 일치 ✅"]


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("smpl_to_skel (5a5e8928-3218-4da6-8a1c-807272e4fd6c).md", "smpl_to_skel.md"),
        ("README (5a5e8928-3218-4da6-8a1c-807272e4fd6c)", "README"),
        ("plan (v2).pdf", "plan (v2).pdf"),
    ],
    ids=["확장자 앞 media id", "확장자 없음", "media id 아님"],
)
def test_attachment_lines_strip_jira_media_id(jcm, name, shown):
    """Jira가 같은 이름의 첨부에 붙인 media id는 R5 칸에서 지우고, 사람이 붙인 괄호는 둔다."""
    got = jcm.attachment_lines([{"file": name, "comment": "2026-11-05", "description": "pass", "content": "pass"}])
    assert got == [f"{shown}: 설명 존재 ✅, 내용 일치 ✅"]
