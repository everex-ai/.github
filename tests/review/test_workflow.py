"""pr-review 재사용 워크플로와 호출 템플릿의 구조를 확인한다 (CI를 직접 돌릴 수 없으므로 정적 검사)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-review.yml").read_text())
CALLER = yaml.safe_load((ROOT / "workflow-templates" / "pr-review.yml").read_text())
ON = True  # PyYAML은 'on' 키를 True로 읽는다


def steps() -> list[dict]:
    return WF["jobs"]["review"]["steps"]


def step(name_part: str) -> dict:
    return next(s for s in steps() if name_part in s.get("name", ""))


def test_workflow_call_interface():
    call = WF[ON]["workflow_call"]
    assert call["inputs"]["gate"]["default"] is False  # 관찰 모드로 시작
    assert "claude_version" not in call["inputs"]  # claude-code-action이 CLI를 설치한다
    assert call["secrets"]["CLAUDE_CODE_OAUTH_TOKEN"]["required"] is True


def test_caller_template_matches_interface():
    job = CALLER["jobs"]["review"]
    assert job["uses"] == "everex-ai/.github/.github/workflows/pr-review.yml@main"
    assert set(job["with"]) <= set(WF[ON]["workflow_call"]["inputs"])
    assert set(job["secrets"]) == set(WF[ON]["workflow_call"]["secrets"])
    assert "pull_request" in CALLER[ON]
    # 호출자가 준 권한이 재사용 워크플로의 job 권한을 덮는다. 같아야 한다
    assert CALLER["permissions"] == WF["jobs"]["review"]["permissions"]
    props = json.loads((ROOT / "workflow-templates" / "pr-review.properties.json").read_text())
    assert props["name"] and props["description"]


def test_checkout_full_history_at_pr_head():
    co = steps()[0]
    assert co["uses"].startswith("actions/checkout@")
    assert co["with"]["fetch-depth"] == 0
    assert "pull_request.head.sha" in co["with"]["ref"]


def test_stage_order_and_conditions():
    names = [s.get("name", s.get("uses", "")) for s in steps()]
    order = [next(i for i, n in enumerate(names) if part in n) for part in ("1. 변경 수집", "2. Claude", "3. PR 반영")]
    assert order == sorted(order)
    pre, claude, apply_ = step("1. 변경 수집"), step("2. Claude"), step("3. PR 반영")
    assert pre["id"] == "precheck"
    for script in ("collect.py", "classify.py", "precheck.py"):
        assert script in pre["run"]
    assert "--event" in pre["run"]
    assert claude["if"] == "steps.precheck.outputs.precheck == 'continue'"  # 반려면 Claude를 부르지 않는다
    assert claude["continue-on-error"] is True
    assert claude["uses"] == "anthropics/claude-code-action@v1"
    w = claude["with"]
    assert "secrets.CLAUDE_CODE_OAUTH_TOKEN" in w["claude_code_oauth_token"]  # 구독 토큰. API 키가 아니다
    assert "anthropic_api_key" not in w
    assert w["github_token"] == "${{ github.token }}"
    assert w["prompt"] == "${{ steps.prompt.outputs.text }}"
    args = w["claude_args"]
    assert "--plugin-dir ${{ env.TOOLS }}/plugins/everex-review" in args
    assert '--allowedTools "Read,Grep,Glob,Agent,Write"' in args
    assert "mcp__github" not in args and "Bash" not in args  # GitHub 도구와 명령 실행을 주지 않는다
    prompt = step("orchestrator 프롬프트 읽기")
    assert prompt["id"] == "prompt" and "plugins/everex-review/orchestrator.md" in prompt["run"]
    assert not any("npm install" in s.get("run", "") for s in steps())
    assert apply_["if"].startswith("always()")
    assert "--require-clean" in apply_["run"] and "--gate" in apply_["run"]


def test_secrets_are_step_scoped():
    job = WF["jobs"]["review"]
    assert "secrets." not in json.dumps(job.get("env", {}))
    for s in steps():
        scoped = json.dumps({**s.get("env", {}), **s.get("with", {})})
        if "CLAUDE_CODE_OAUTH_TOKEN" in scoped:
            assert "2. Claude" in s["name"]
        if "github.token" in scoped:
            assert "2. Claude" in s["name"] or "3. PR 반영" in s["name"]


def test_scripts_referenced_by_workflow_exist():
    for rel in (
        "scripts/review/collect.py",
        "scripts/review/classify.py",
        "scripts/review/precheck.py",
        "scripts/review/apply.py",
        "scripts/review/run_claude.sh",
        "requirements-dev.txt",
        "plugins/everex-review/orchestrator.md",
    ):
        assert (ROOT / rel).exists(), rel
    text = (ROOT / ".github" / "workflows" / "pr-review.yml").read_text()
    for rel in ("scripts/review/collect.py", "scripts/review/claude_summary.py", "scripts/review/apply.py"):
        assert rel in text
