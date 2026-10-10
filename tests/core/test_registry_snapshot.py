# tests/core/test_registry_snapshot.py
from pathlib import Path

from core.registry import SkillRegistry
from core.registry_snapshot import build_snapshot

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_build_snapshot_lists_enabled_and_failed_skills() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok" / "practice", FIXTURES / "skills_bad" / "bad_category"])
    registry.load()

    snapshot = build_snapshot(registry)

    assert any(s.name == "practice" and s.enabled for s in snapshot.skills)
    assert snapshot.failures  # bad_category는 실패 이유가 있다
