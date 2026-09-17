"""everex-review 플러그인 파일의 형식과 orchestrator 프롬프트가 서로 맞는지 확인한다."""

from __future__ import annotations

import json
import re
from pathlib import Path

from common import AGENT_CHECKS, SCRIPT_CHECKS

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "everex-review"
AGENTS = {"test-necessity", "test-coverage", "dead-code", "design-review"}


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "frontmatter 없음"
    return dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)


def test_plugin_manifest():
    meta = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert meta["name"] == "everex-review" and meta["version"]


def test_agent_files_have_required_frontmatter():
    found = {p.stem for p in (PLUGIN / "agents").glob("*.md")}
    assert found == AGENTS
    for p in (PLUGIN / "agents").glob("*.md"):
        fm = {k.strip(): v.strip() for k, v in frontmatter(p.read_text()).items()}
        assert fm["name"] == p.stem
        assert fm["description"] and fm["model"] == "sonnet"
        assert set(fm["tools"].replace(" ", "").split(",")) == {"Read", "Grep", "Glob"}  # 읽기 전용
        body = p.read_text().split("---", 2)[2]
        assert "지시가 아니다" in body  # 신뢰 경계
        assert "```json" in body  # 출력 형식


def test_orchestrator_references_agents_and_check_ids():
    text = (PLUGIN / "orchestrator.md").read_text()
    for a in AGENTS:
        assert f"`{a}`" in text
    for cid in AGENT_CHECKS:
        assert re.search(rf"(^|[^\d-]){re.escape(cid)}([^\d-]|$)", text, re.M), cid
    assert ", ".join(SCRIPT_CHECKS) in text  # 스크립트 판정 항목을 넣지 말라는 문장
    assert ".everex-review/out/verdict.json" in text and "verdict-schema.json" in text
