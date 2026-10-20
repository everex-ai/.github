"""scripts/save_output.py(CI agent의 구조화 출력을 out/<KEY>/ 파일로 저장)와 schemas/agent-output.json의 동작.

save_output.py가 만든 out/을 src/jira/issue.py의 apply_issue에 넘겨 실패와 통과가 갈리는지도 확인한다. 가짜 Jira는
scripts/src/dev/samples/fake_jira.py의 FakeJira이다.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함(test_render.py 참고)
EXPECTED = [{"id": "1", "text": "재현 테스트 추가"}, {"id": "2", "text": "오류율 1% 미만"}]
PRE_CHECKS = {"T1": {"result": "pass", "detail": "예상 산출물 있음"}, "A2": {"result": "n/a"}, "T2": {"result": "x"}}
DONE_ITEMS = [{"id": "1", "done": True, "result": "테스트 3건 추가"}, {"id": "2", "done": True, "result": "0.4%"}]
SECRET = "비밀 Jira 기록 문장"
TLDR = "h2. Summary\n* 결과 요약\n"


def _load(name: str, path: Path):
    if name not in sys.modules:
        saved = list(sys.path)  # 스크립트가 scripts/를 sys.path 맨 앞에 넣으므로 불러온 뒤 되돌린다
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved
    return sys.modules[name]


@pytest.fixture(scope="module")
def render():
    """scripts/src/dev/samples/render.py (모듈 이름 src.dev.samples.render)."""
    return importlib.import_module("src.dev.samples.render")


@pytest.fixture(scope="module")
def ja():
    """scripts/apply.py (모듈 이름 jira_apply). main을 실행하는 테스트가 사용한다."""
    return _load("jira_apply", ROOT / "scripts" / "apply.py")


@pytest.fixture(scope="module")
def so():
    """scripts/save_output.py."""
    return _load("save_output", ROOT / "scripts" / "save_output.py")


@pytest.fixture(scope="module")
def output():
    """scripts/src/jira/output/result.py (모듈 이름 src.jira.output.result)."""
    return importlib.import_module("src.jira.output.result")


@pytest.fixture(scope="module")
def jrn():
    """scripts/src/jira/runs.py (모듈 이름 src.jira.runs)."""
    return importlib.import_module("src.jira.runs")


@pytest.fixture(scope="module")
def jsl():
    """scripts/src/jira/slack.py (모듈 이름 src.jira.slack)."""
    return importlib.import_module("src.jira.slack")


@pytest.fixture(autouse=True)
def slack(jsl, monkeypatch):
    """apply_issue가 Slack에 보내려던 본문을 모은다. 실제 Slack에는 보내지 않는다."""
    sent: list[str] = []
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("SLACK_USERS_JSON", "{}")
    monkeypatch.setattr(jsl, "slack_post", lambda text: sent.append(text) or "전송 완료")
    return sent


def make_ctx(render, work: Path, keys=("INNO-17",)) -> Path:
    """ctx/<KEY>/(issue.json, precheck.json, meta.json)를 만든다."""
    ctx = work / "ctx"
    for key in keys:
        render.write_issue(ctx, key, "Task", EXPECTED, PRE_CHECKS, summary="제목")
    return ctx


def agent_verdict(render, verdict: str | None = "pass", mode: str = "review", **over) -> dict:
    """구조화 출력의 verdict 객체(issueKey, mode 없음)."""
    v = render.task_verdict("INNO-17", mode, verdict) | {"items": DONE_ITEMS, "extra": []}
    v.pop("issueKey")
    v.pop("mode")
    return v | over


def task(key: str, verdict: dict, tldr: str = TLDR, comment: str = "* 요약") -> dict:
    """구조화 출력의 tasks 항목."""
    return {"issueKey": key, "verdict": verdict, "tldr": tldr, "comment": comment}


def write_exec(work: Path, structured, *, subtype="success", is_error=False, result=SECRET, as_list=True) -> str:
    """claude-code-action 실행 결과 파일을 만들고 경로를 돌려준다."""
    msg = {"type": "result", "subtype": subtype, "is_error": is_error, "result": result}
    if structured is not None:
        msg["structured_output"] = structured
    data = [{"type": "system", "subtype": "init"}, msg] if as_list else msg
    path = work / "exec.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def run_save(so, work: Path, mode="review", exec_file="", outcome="success", github_output=None) -> Path:
    """save_output.py의 main을 실행하고 out 폴더를 돌려준다."""
    out = work / "out"
    argv = ["--ctx", str(work / "ctx"), "--out", str(out), "--mode", mode, "--exec-file", exec_file]
    argv += ["--claude-outcome", outcome, "--github-output", str(github_output) if github_output else ""]
    so.main(argv)
    return out


def apply_one(ja, render, work: Path, out: Path, mode="review", key="INNO-17"):
    """out/<KEY>/를 apply_issue로 가짜 Jira에 반영하고 (FakeJira, Summary, 실패 dict 또는 None)을 돌려준다."""
    j = render.FakeJira({key: ""})
    summ = ja.Summary(str(work / "summary.md"))
    failure = ja.apply_issue(j, work / "ctx" / key, out, mode, False, "acc-lead", summ)
    return j, summ, failure


def test_all_outputs_match_write_out_format(render, so, tmp_path, capsys):
    make_ctx(render, tmp_path)
    v = agent_verdict(render)
    agent = {
        "tasks": [task("INNO-17", v, "h2. Summary\n* (CI agent가 작성하는 부분. 샘플에서 다루지 않음)\n")],
        "failed": [],
    }
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, agent))
    sample = tmp_path / "sample"
    full = {"issueKey": "INNO-17", "mode": "review"} | v
    render.write_out(sample, "INNO-17", full, "* 요약")
    o = out / "INNO-17"
    assert (o / "verdict.json").read_text(encoding="utf-8") == (sample / "INNO-17" / "verdict.json").read_text(
        encoding="utf-8"
    )
    assert (o / "tldr.wiki").read_text(encoding="utf-8") == (sample / "INNO-17" / "tldr.wiki").read_text(
        encoding="utf-8"
    )
    assert (o / "comment.wiki").read_text(encoding="utf-8").strip() == "* 요약"
    assert not (o / "_agent_error.txt").exists() and not (out / "_failed.txt").exists()
    assert capsys.readouterr().out.strip() == "[save] INNO-17: verdict=pass 없음=-"


def test_missing_task_gets_no_verdict_defaults_and_apply_fails(render, so, ja, tmp_path):
    make_ctx(render, tmp_path, ("INNO-17", "INNO-18"))
    agent = {"tasks": [task("INNO-18", agent_verdict(render))], "failed": []}
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, agent))
    default = json.loads((out / "INNO-17" / "verdict.json").read_text(encoding="utf-8"))
    assert default["issueKey"] == "INNO-17" and default["mode"] == "review" and default["verdict"] == "no_verdict"
    assert default["items"] == [{"id": "1", "done": None, "result": ""}, {"id": "2", "done": None, "result": ""}]
    assert default["extra"] == default["requests"] == default["feedback"] == default["slack"] == []
    assert [c["id"] for c in default["checks"]] == ["T1", "A2"]  # result가 pass, fail, n/a가 아닌 T2는 뺌
    assert not (out / "INNO-17" / "tldr.wiki").exists() and not (out / "INNO-17" / "comment.wiki").exists()
    assert (out / "INNO-17" / "_agent_error.txt").read_text(
        encoding="utf-8"
    ).strip() == "구조화 출력에 이 ticket 결과 없음"
    j, summ, failure = apply_one(ja, render, tmp_path, out)
    assert j.comments == [] and j.fields == {}
    assert [(k, set(p), p["runs"][0]["verdict"]) for k, p in j.props_set] == [("INNO-17", {"runs"}, "failed")]
    assert failure["reason"] == "CI agent 판정 없음. 원인: 구조화 출력에 이 ticket 결과 없음"
    assert failure["key"] == "INNO-17" and failure["title"] == "제목" and failure["url"].endswith("/browse/INNO-17")
    assert summ.rows[0].startswith("| INNO-17 | Task | review | 실패 |")


def test_review_without_tldr_fails_and_digest_keeps_memo(render, so, ja, tmp_path):
    agent = {"tasks": [task("INNO-17", agent_verdict(render), tldr="")], "failed": []}
    review_dir = tmp_path / "review"
    make_ctx(render, review_dir)
    out = run_save(so, review_dir, exec_file=write_exec(review_dir, agent))
    assert not (out / "INNO-17" / "tldr.wiki").exists()
    _, _, failure = apply_one(ja, render, review_dir, out)
    assert failure["reason"] == "CI agent 출력에 TL;DR 본문 없음 또는 비어 있음"

    digest_dir = tmp_path / "digest"
    make_ctx(render, digest_dir)
    digest = agent_verdict(render, None, "digest")
    agent = {"tasks": [task("INNO-17", digest, tldr="", comment="")], "failed": []}
    out = run_save(so, digest_dir, "digest", write_exec(digest_dir, agent))
    assert not (out / "INNO-17" / "comment.wiki").exists()
    j, summ, failure = apply_one(ja, render, digest_dir, out, "digest")
    assert failure is None and "TL;DR 출력 없음" in summ.rows[0]
    assert "결과 산출물" in j.fields["INNO-17"]["description"]


def test_missing_requests_extra_feedback_become_empty_and_pass(render, so, ja, tmp_path):
    make_ctx(render, tmp_path)
    v = {"verdict": "pass", "checks": [], "items": DONE_ITEMS}  # requests, extra, feedback 없음
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, {"tasks": [task("INNO-17", v)], "failed": []}))
    saved = json.loads((out / "INNO-17" / "verdict.json").read_text(encoding="utf-8"))
    assert saved["requests"] == [] and saved["extra"] == [] and saved["feedback"] == []
    assert saved["checks"] == []  # 구조화 출력의 checks가 기본값을 덮어씀
    j, _, failure = apply_one(ja, render, tmp_path, out)
    assert failure is None and j.comments  # 통과하면 검수 comment를 남김


def test_missing_slack_becomes_empty_list(render, so, tmp_path):
    make_ctx(render, tmp_path)
    v = agent_verdict(render)
    v.pop("slack", None)
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, {"tasks": [task("INNO-17", v)], "failed": []}))
    saved = json.loads((out / "INNO-17" / "verdict.json").read_text(encoding="utf-8"))
    assert "slack" not in v and saved["slack"] == []


def test_slack_points_are_saved_as_given(render, so, tmp_path):
    make_ctx(render, tmp_path)
    points = ["첨부 로그 파일 없음", "재현 불가로 종료하려 함"]
    v = agent_verdict(render, slack=points)
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, {"tasks": [task("INNO-17", v)], "failed": []}))
    saved = json.loads((out / "INNO-17" / "verdict.json").read_text(encoding="utf-8"))
    assert saved["slack"] == points


def test_structured_issue_key_and_mode_are_ignored(render, so, tmp_path):
    make_ctx(render, tmp_path)
    v = agent_verdict(render) | {"issueKey": "INNO-99", "mode": "digest"}
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, {"tasks": [task("INNO-17", v)], "failed": []}))
    saved = json.loads((out / "INNO-17" / "verdict.json").read_text(encoding="utf-8"))
    assert saved["issueKey"] == "INNO-17" and saved["mode"] == "review"


def test_digest_two_tasks_and_failed_list(render, so, ja, tmp_path):
    make_ctx(render, tmp_path, ("INNO-1", "INNO-2", "INNO-3"))
    digest = agent_verdict(render, None, "digest")
    agent = {
        "tasks": [task("INNO-1", digest, comment=""), task("INNO-2", digest, comment="")],
        "failed": [{"issueKey": "INNO-3", "reason": "기록이 너무 길어\n처리하지 못함"}],
    }
    out = run_save(so, tmp_path, "digest", write_exec(tmp_path, agent))
    for key in ("INNO-1", "INNO-2"):
        assert (out / key / "tldr.wiki").read_text(encoding="utf-8") == TLDR
        assert not (out / key / "_agent_error.txt").exists()
    assert (out / "_failed.txt").read_text(encoding="utf-8") == "INNO-3\t기록이 너무 길어 처리하지 못함\n"
    assert (out / "INNO-3" / "_agent_error.txt").read_text(encoding="utf-8").strip() == (
        "CI agent가 처리하지 못함: 기록이 너무 길어 처리하지 못함"
    )
    _, _, failure = apply_one(ja, render, tmp_path, out, "digest", "INNO-3")
    assert failure["reason"] == "CI agent 판정 없음. 원인: CI agent가 처리하지 못함: 기록이 너무 길어 처리하지 못함"


def test_error_result_text_is_copied_only_when_is_error(render, so, tmp_path):
    make_ctx(render, tmp_path)
    long_text = "API 오류\n" + "가" * 400
    path = write_exec(tmp_path, None, subtype="error_during_execution", is_error=True, result=long_text)
    out = run_save(so, tmp_path, exec_file=path)
    err = (out / "INNO-17" / "_agent_error.txt").read_text(encoding="utf-8").strip()
    assert err.startswith("Claude 문서화 단계 종료 종류(subtype): error_during_execution; 실패 문구: API 오류 가가")
    assert "\n" not in err and err.count("가") == 300 - len("API 오류 ")  # result 앞 300자만 옮김

    path = write_exec(tmp_path, None, subtype="error_max_turns", is_error=False, result=SECRET)
    out = run_save(so, tmp_path, exec_file=path)
    err = (out / "INNO-17" / "_agent_error.txt").read_text(encoding="utf-8").strip()
    assert err == "Claude 문서화 단계 종료 종류(subtype): error_max_turns" and SECRET not in err


@pytest.mark.parametrize("exec_file", ["", "missing"])
def test_missing_exec_file_reason(render, so, tmp_path, exec_file):
    make_ctx(render, tmp_path)
    path = str(tmp_path / "nope.json") if exec_file else ""
    out = run_save(so, tmp_path, exec_file=path, outcome="skipped" if exec_file else "")
    err = (out / "INNO-17" / "_agent_error.txt").read_text(encoding="utf-8").strip()
    assert (
        err
        == f"실행 결과 파일 없음(Claude 문서화 단계(Actions 워크플로 3단계) 결과: {'skipped' if exec_file else '알 수 없음'})"
    )


def test_exec_file_may_be_one_dict_and_structured_output_may_be_a_json_string(render, so, tmp_path):
    make_ctx(render, tmp_path)
    agent = json.dumps({"tasks": [task("INNO-17", agent_verdict(render))], "failed": []}, ensure_ascii=False)
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, agent, as_list=False))
    assert json.loads((out / "INNO-17" / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "pass"
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, "[1, 2]"))  # 객체가 아니면 없는 것으로 다룸
    assert (out / "INNO-17" / "_agent_error.txt").read_text(encoding="utf-8").strip() == (
        "Claude 문서화 단계 종료 종류(subtype): success"
    )


def test_retry_output(render, so, tmp_path):
    make_ctx(render, tmp_path)
    complete = {"tasks": [task("INNO-17", agent_verdict(render))], "failed": []}
    cases = {
        "complete": (complete, False),
        "no_verdict": ({"tasks": [task("INNO-17", agent_verdict(render, "no_verdict"))], "failed": []}, True),
        "done_null": (
            {
                "tasks": [task("INNO-17", agent_verdict(render, items=[{"id": "1", "done": None, "result": ""}]))],
                "failed": [],
            },
            True,
        ),
        "tldr_empty": ({"tasks": [task("INNO-17", agent_verdict(render), tldr="")], "failed": []}, True),
        "comment_empty": ({"tasks": [task("INNO-17", agent_verdict(render), comment="")], "failed": []}, True),
        "no_task": ({"tasks": [], "failed": []}, True),
    }
    for name, (agent, retry) in cases.items():
        gh = tmp_path / f"gh-{name}.txt"
        gh.write_text("", encoding="utf-8")
        run_save(so, tmp_path, exec_file=write_exec(tmp_path, agent), github_output=gh)
        assert (gh.read_text(encoding="utf-8") == "retry=true\n") is retry, name
    gh = tmp_path / "gh-no-exec.txt"
    gh.write_text("", encoding="utf-8")
    run_save(so, tmp_path, exec_file="", github_output=gh)
    assert gh.read_text(encoding="utf-8") == "retry=true\n"


def test_digest_never_asks_for_retry(render, so, tmp_path):
    make_ctx(render, tmp_path)
    gh = tmp_path / "gh.txt"
    gh.write_text("", encoding="utf-8")
    run_save(so, tmp_path, "digest", exec_file="", github_output=gh)
    assert gh.read_text(encoding="utf-8") == ""


def test_stdout_has_no_jira_text_and_names_only_unknown_keys(render, so, tmp_path, capsys):
    make_ctx(render, tmp_path)
    agent = {
        "tasks": [task("INNO-17", agent_verdict(render), tldr=f"h2. Summary\n* {SECRET}"), task("INNO-99", {})],
        "failed": [],
    }
    run_save(so, tmp_path, exec_file=write_exec(tmp_path, agent, result=SECRET))
    out = capsys.readouterr().out
    assert SECRET not in out and "INNO-99" in out
    run_save(so, tmp_path, exec_file=write_exec(tmp_path, None, subtype="error_during_execution", is_error=True))
    assert SECRET not in capsys.readouterr().out  # 실패 문구는 파일에만 쓰고 표준 출력에는 내지 않음


def test_second_run_clears_previous_results(render, so, ja, tmp_path):
    make_ctx(render, tmp_path)
    first = {"tasks": [task("INNO-17", agent_verdict(render))], "failed": [{"issueKey": "INNO-17", "reason": "x"}]}
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, first))
    assert (out / "INNO-17" / "tldr.wiki").exists() and (out / "_failed.txt").exists()
    (out / "INNO-17" / "_agent_error.txt").write_text("이전 원인\n", encoding="utf-8")
    second = {"tasks": [task("INNO-17", agent_verdict(render), tldr="")], "failed": []}
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, second))
    assert not (out / "INNO-17" / "tldr.wiki").exists()
    assert not (out / "INNO-17" / "_agent_error.txt").exists()
    assert not (out / "_failed.txt").exists()
    out = run_save(so, tmp_path, exec_file="")  # 구조화 출력이 없으면 이전 결과 대신 기본값과 원인
    assert not (out / "INNO-17" / "tldr.wiki").exists()
    assert json.loads((out / "INNO-17" / "verdict.json").read_text(encoding="utf-8"))["verdict"] == "no_verdict"


def _strip_descriptions(node):
    if isinstance(node, dict):
        return {k: _strip_descriptions(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_strip_descriptions(v) for v in node]
    return node


def test_schema_verdict_part_matches_verdict_json():
    agent_schema = json.loads((ROOT / "schemas" / "agent-output.json").read_text(encoding="utf-8"))
    verdict_schema = json.loads((ROOT / "schemas" / "verdict.json").read_text(encoding="utf-8"))
    part = agent_schema["properties"]["tasks"]["items"]["properties"]["verdict"]
    props = {k: v for k, v in verdict_schema["properties"].items() if k not in ("issueKey", "mode")}
    assert part["type"] == "object" and part["additionalProperties"] is False
    assert part["required"] == [r for r in verdict_schema["required"] if r not in ("issueKey", "mode")]
    assert _strip_descriptions(part["properties"]) == _strip_descriptions(props)
    item = agent_schema["properties"]["tasks"]["items"]
    assert item["required"] == ["issueKey", "verdict", "tldr"] and item["additionalProperties"] is False
    assert set(item["properties"]) == {"issueKey", "verdict", "tldr", "comment"}
    assert item["properties"]["issueKey"]["pattern"] == verdict_schema["properties"]["issueKey"]["pattern"]
    assert agent_schema["required"] == ["tasks", "failed"] and agent_schema["additionalProperties"] is False
    failed = agent_schema["properties"]["failed"]["items"]
    assert failed["required"] == ["issueKey", "reason"] and failed["additionalProperties"] is False
    assert agent_schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert "'" not in (ROOT / "schemas" / "agent-output.json").read_text(encoding="utf-8")


def workflow_steps() -> dict[str, dict]:
    data = yaml.safe_load((ROOT / ".github" / "workflows" / "jira-doc.yml").read_text(encoding="utf-8"))
    return {s["id"]: s for s in data["jobs"]["run"]["steps"] if "id" in s}


def test_workflow_has_structured_output_steps():
    steps = workflow_steps()
    assert steps["schema"]["name"].startswith("3-0.")
    assert steps["save"]["name"].startswith("3-2.")
    assert steps["claude_retry"]["name"].startswith("3-R.") and steps["save_retry"]["name"].startswith("3-R.")
    assert steps["claude"]["continue-on-error"] is True and steps["claude_retry"]["continue-on-error"] is True


def test_workflow_has_failure_notification_step():
    """5단계는 앞 스텝이 실패하면 apply.py failure 모드로 연동 채널에 알린다."""
    step = workflow_steps()["notify_failure"]
    assert step["name"].startswith("5.") and step["if"] == "failure()"
    assert "--mode failure" in step["run"] and '--failed-mode "$MODE"' in step["run"]
    assert set(step["env"]) == {"SLACK_WEBHOOK_URL", "SLACK_USERS_JSON"}


@pytest.mark.parametrize("step_id", ["save", "save_retry"])
def test_workflow_save_command_is_accepted_by_parser(so, step_id):
    run = workflow_steps()[step_id]["run"]
    for var, value in (
        ("MODE", "review"),
        ("EXEC_FILE", "/x/exec.json"),
        ("CLAUDE_OUTCOME", "failure"),
        ("GITHUB_OUTPUT", "/x/gh.txt"),
    ):
        run = run.replace(f"${var}", value)
    tokens = shlex.split(run)
    assert tokens[:2] == ["python3", "scripts/save_output.py"]
    args = so.build_parser().parse_args(tokens[2:])
    assert (args.ctx, args.out, args.mode) == ("ctx/", "out/", "review")
    assert (args.exec_file, args.claude_outcome, args.github_output) == ("/x/exec.json", "failure", "/x/gh.txt")


def test_prompts_have_no_file_writing_instructions():
    pattern = re.compile(r"out/|tldr\.wiki|comment\.wiki|deliverables\.wiki|_failed\.txt|verdict\.json")
    hits = [
        f"{p.relative_to(ROOT)}:{n}"
        for p in sorted((ROOT / "prompts").rglob("*.md"))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


def test_failed_reason_is_cut_to_300_characters(render, so, tmp_path):
    make_ctx(render, tmp_path, ("INNO-1", "INNO-3"))
    agent = {"tasks": [task("INNO-1", agent_verdict(render))], "failed": [{"issueKey": "INNO-3", "reason": "가" * 500}]}
    out = run_save(so, tmp_path, exec_file=write_exec(tmp_path, agent))
    assert (out / "_failed.txt").read_text(encoding="utf-8") == "INNO-3\t" + "가" * 300 + "\n"
    assert "가" * 300 in (out / "INNO-3" / "_agent_error.txt").read_text(encoding="utf-8")
    assert "가" * 301 not in (out / "INNO-3" / "_agent_error.txt").read_text(encoding="utf-8")
    schema = json.loads((ROOT / "schemas" / "agent-output.json").read_text(encoding="utf-8"))
    assert schema["properties"]["failed"]["items"]["properties"]["reason"]["maxLength"] == 300


def test_digest_task_without_expected_items_fails_when_exec_file_is_missing(render, so, ja, tmp_path, monkeypatch):
    ctx = tmp_path / "ctx"
    render.write_issue(ctx, "INNO-17", "Task", [], {}, summary="제목")  # 예상 산출물 없음: 기본값 items가 빈 목록
    out = run_save(so, tmp_path, "digest", exec_file="", outcome="failure")
    err = (out / "INNO-17" / "_agent_error.txt").read_text(encoding="utf-8").strip()
    assert err == "실행 결과 파일 없음(Claude 문서화 단계(Actions 워크플로 3단계) 결과: failure)"
    j, _, failure = apply_one(ja, render, tmp_path, out, "digest")
    assert failure["reason"] == f"CI agent 판정 없음. 원인: {err}"
    assert j.fields == {} and j.comments == []
    jira = render.FakeJira({"INNO-17": ""})
    monkeypatch.setattr(ja, "Jira", lambda: jira)
    monkeypatch.setattr(
        sys,
        "argv",
        ["apply.py", "--ctx", str(ctx), "--out", str(out), "--mode", "digest", "--summary", str(tmp_path / "s.md")],
    )
    with pytest.raises(SystemExit) as e:
        ja.main()
    assert e.value.code == 1


# ---------------------------------------------------------------------- Claude 사용량
MODEL_USAGE = {
    "claude-sonnet-5-5": {
        "inputTokens": 100,
        "outputTokens": 200,
        "cacheReadInputTokens": 3000,
        "cacheCreationInputTokens": 400,
    },
    "claude-haiku-5": {
        "inputTokens": 1,
        "outputTokens": 2,
        "cacheReadInputTokens": 3,
        "cacheCreationInputTokens": 4,
    },
}
USAGE = {"input_tokens": 7, "output_tokens": 8, "cache_creation_input_tokens": 9, "cache_read_input_tokens": 10}


def write_usage_exec(work: Path, name: str = "usage-exec.json", **fields) -> str:
    """result 메시지에 사용량 필드를 넣은 실행 결과 파일을 만들고 경로를 돌려준다."""
    msg = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 5} | fields
    path = work / name
    path.write_text(json.dumps([{"type": "system"}, msg]), encoding="utf-8")
    return str(path)


def test_usage_is_read_from_model_usage(output):
    got = output.usage_of([{"type": "result", "total_cost_usd": 0.5, "modelUsage": MODEL_USAGE, "usage": USAGE}])
    assert got == {
        "costUsd": 0.5,
        "tokens": {"input": 101, "output": 202, "cacheWrite": 404, "cacheRead": 3003},
        "models": ["claude-sonnet-5-5", "claude-haiku-5"],
    }


def test_usage_falls_back_to_usage_without_model_usage(output):
    for msg in ({"usage": USAGE}, {"modelUsage": {}, "usage": USAGE}):
        got = output.usage_of([{"type": "result", **msg}])
        assert got == {
            "costUsd": None,
            "tokens": {"input": 7, "output": 8, "cacheWrite": 9, "cacheRead": 10},
            "models": [],
        }


def test_usage_sums_result_messages_and_ignores_bad_values(output):
    results = [
        {"total_cost_usd": 0.25, "usage": USAGE},
        {"total_cost_usd": "비용", "usage": {"input_tokens": "많음", "output_tokens": 1}},
    ]
    got = output.usage_of(results)
    assert got["costUsd"] == 0.25 and got["tokens"] == {"input": 7, "output": 9, "cacheWrite": 9, "cacheRead": 10}
    assert output.usage_of([]) == {
        "costUsd": None,
        "tokens": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0},
        "models": [],
    }


def test_exec_summary_keeps_workflow_lines_and_adds_token_line(output, tmp_path):
    denials = [
        {"tool_name": "Write", "tool_input": {"file_path": "out/INNO-17/tldr.wiki"}},
        {"tool_name": "Bash", "tool_input": {"command": SECRET}},
        {"tool_name": "Edit", "tool_input": {"file_path": "ctx/x"}},
    ]
    path = write_usage_exec(
        tmp_path, is_error=True, subtype="error_max_turns", total_cost_usd=1.5, result="실패 문구",
        permission_denials=denials, modelUsage=MODEL_USAGE,
    )  # fmt: skip
    assert output.exec_summary(path) == [
        "is_error=True subtype=error_max_turns num_turns=5 total_cost_usd=1.5",
        "permission_denials=Write,Bash,Edit",
        "denied Write: out/INNO-17/tldr.wiki",
        "denied Edit: ctx/x",
        "result=실패 문구",
        "tokens input=101 output=202 cache_write=404 cache_read=3003",
    ]
    ok = write_usage_exec(tmp_path, "ok.json", total_cost_usd=0.1, result=SECRET, usage=USAGE)
    assert output.exec_summary(ok) == [
        "is_error=False subtype=success num_turns=5 total_cost_usd=0.1",
        "permission_denials=",
        "tokens input=7 output=8 cache_write=9 cache_read=10",
    ]


def test_exec_summary_cli_prints_lines_without_other_arguments(so, tmp_path, capsys):
    path = write_usage_exec(tmp_path, total_cost_usd=0.1, result=SECRET, usage=USAGE)
    so.main(["--exec-summary", path])  # --mode 없이 동작함
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "tokens input=7 output=8 cache_write=9 cache_read=10" and SECRET not in out
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", ["missing", "empty", "no_result"])
def test_exec_summary_cli_prints_nothing_for_missing_or_empty_file(so, tmp_path, capsys, kind):
    path = tmp_path / "exec.json"
    if kind == "empty":
        path.write_text("", encoding="utf-8")
    elif kind == "no_result":
        path.write_text(json.dumps([{"type": "system"}]), encoding="utf-8")
    so.main(["--exec-summary", str(path)])
    assert capsys.readouterr().out == ""


def test_exec_summary_runs_as_script_with_exit_code_0(tmp_path):
    import subprocess

    path = write_usage_exec(tmp_path, usage=USAGE)
    for target in (path, str(tmp_path / "none.json")):
        res = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "save_output.py"), "--exec-summary", target],
            capture_output=True, text=True, cwd=tmp_path,
        )  # fmt: skip
        assert res.returncode == 0
    assert "tokens input=7" in subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "save_output.py"), "--exec-summary", path],
        capture_output=True, text=True, cwd=tmp_path,
    ).stdout  # fmt: skip


def test_save_appends_usage_entry_and_retry_marks_it(render, so, tmp_path):
    make_ctx(render, tmp_path)
    agent = {"tasks": [task("INNO-17", agent_verdict(render))], "failed": []}
    first = write_usage_exec(
        tmp_path, "first.json", structured_output=agent, total_cost_usd=0.5, modelUsage=MODEL_USAGE
    )
    out = run_save(so, tmp_path, exec_file=first)
    entries = json.loads((out / "_usage.json").read_text(encoding="utf-8"))
    assert entries == [
        {
            "retry": False,
            "costUsd": 0.5,
            "tokens": {"input": 101, "output": 202, "cacheWrite": 404, "cacheRead": 3003},
            "models": ["claude-sonnet-5-5", "claude-haiku-5"],
        }
    ]
    second = write_usage_exec(tmp_path, "second.json", structured_output=agent, total_cost_usd=0.25, usage=USAGE)
    so.main(["--ctx", str(tmp_path / "ctx"), "--out", str(out), "--mode", "review", "--exec-file", second, "--retry"])
    entries = json.loads((out / "_usage.json").read_text(encoding="utf-8"))
    assert [e["retry"] for e in entries] == [False, True]
    assert entries[1] == {
        "retry": True,
        "costUsd": 0.25,
        "tokens": {"input": 7, "output": 8, "cacheWrite": 9, "cacheRead": 10},
        "models": [],
    }


def test_save_without_exec_file_adds_no_usage_entry(render, so, tmp_path):
    make_ctx(render, tmp_path)
    out = run_save(so, tmp_path, exec_file=str(tmp_path / "none.json"))
    assert not (out / "_usage.json").exists()


def test_apply_sums_usage_file(jrn, tmp_path):
    (tmp_path / "_usage.json").write_text(json.dumps(USAGE_ENTRIES), encoding="utf-8")
    assert jrn.usage_total(tmp_path) == {
        "retry": True,
        "costUsd": 0.75,
        "tokens": {"input": 11, "output": 22, "cacheWrite": 33, "cacheRead": 44},
        "models": ["a", "b"],
    }
    assert jrn.usage_total(tmp_path / "none") == {
        "retry": False,
        "costUsd": None,
        "tokens": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0},
        "models": [],
    }


USAGE_ENTRIES = [
    {"retry": False, "costUsd": 0.25, "tokens": {"input": 10, "output": 20, "cacheWrite": 30, "cacheRead": 40}, "models": ["a"]},
    {"retry": True, "costUsd": 0.5, "tokens": {"input": 1, "output": 2, "cacheWrite": 3, "cacheRead": 4}, "models": ["a", "b"]},
]  # fmt: skip
