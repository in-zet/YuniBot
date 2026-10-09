from pathlib import Path

from core.registry import SkillRegistry

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_valid_skill_loads() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok"])
    registry.load()

    names = {s.manifest.name for s in registry.enabled_skills()}
    assert "practice" in names
    assert registry.failures() == {}


def test_template_dir_is_skipped() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()

    names = {s.manifest.name for s in registry.enabled_skills()}
    assert "_template" not in names
    assert "my-skill" not in names  # _template 안의 placeholder manifest.name


def test_missing_skill_md_is_disabled_with_reason() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "missing_skill_md"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "SKILL.md" in reason


def test_bad_category_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "bad_category"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "category" in reason


def test_undeclared_service_usage_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "undeclared_service"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "songdb" in reason


def test_unknown_listen_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "unknown_listen"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "no.such.event" in reason


def test_undefined_tag_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "undefined_tag"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "no-such-tag" in reason


def test_slash_collision_disables_later_skill_only() -> None:
    registry = SkillRegistry(
        [
            FIXTURES / "skills_bad" / "slash_collision_standalone",
            FIXTURES / "skills_bad" / "slash_collision_group",
        ]
    )
    registry.load()

    enabled_names = {s.manifest.name for s in registry.enabled_skills()}
    assert enabled_names == {"slash-collision-standalone"}
    [reason] = registry.failures().values()
    assert "연습" in reason


def test_one_broken_skill_does_not_disable_valid_sibling() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok", FIXTURES / "skills_bad" / "bad_category"])
    registry.load()

    enabled_names = {s.manifest.name for s in registry.enabled_skills()}
    assert "practice" in enabled_names
    assert len(registry.failures()) == 1
