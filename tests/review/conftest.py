"""scripts/review 테스트 공통 fixture."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "review"))
sys.path.insert(0, str(ROOT / "scripts" / "review" / "dev"))

import make_fixture  # noqa: E402


def has(tool: str) -> bool:
    return shutil.which(tool) is not None


requires_ruff = pytest.mark.skipif(not has("ruff"), reason="ruff 없음")


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """feature 브랜치가 checkout된 fixture repo."""
    return make_fixture.build(tmp_path_factory.mktemp("fx") / "repo")


@pytest.fixture(scope="session")
def collected(fixture_repo: Path) -> Path:
    """collect + classify 까지 돌린 작업 디렉터리."""
    import classify
    import collect

    work = fixture_repo / ".everex-review"
    collect.collect(fixture_repo, work, "main", "feature", None)
    classify.classify(work)
    return work
