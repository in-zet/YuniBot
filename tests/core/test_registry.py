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


def test_decorator_time_error_disables_only_that_skill() -> None:
    # @tool이 적용되는 import 시점에 실패하는 스킬(docstring 없음)이 레지스트리 전체를
    # 죽이지 않고, 그 스킬만 비활성화해야 한다.
    registry = SkillRegistry([FIXTURES / "skills_ok", FIXTURES / "skills_bad" / "decorator_error"])
    registry.load()

    enabled_names = {s.manifest.name for s in registry.enabled_skills()}
    assert "practice" in enabled_names
    [reason] = registry.failures().values()
    assert "docstring" in reason


def test_orphan_listen_without_handler_is_disabled() -> None:
    # manifest.listens에 이벤트를 선언했지만 처리하는 @on 핸들러가 없으면 비활성화해야 한다
    # (현재는 "코드가 구독하는데 manifest에 없는" 방향만 보고, 반대 방향은 안 보는 구멍이 있었다).
    # orphan_listen_emitter가 some.event를 낸다고 선언해 두어, "아무도 내지 않는 이벤트" 교차
    # 검사가 아니라 "핸들러 없음" 검사 하나만으로 걸러지는지 확실히 한다.
    registry = SkillRegistry(
        [
            FIXTURES / "skills_bad" / "orphan_listen",
            FIXTURES / "skills_bad" / "orphan_listen_emitter",
        ]
    )
    registry.load()

    enabled_names = {s.manifest.name for s in registry.enabled_skills()}
    assert enabled_names == {"orphan-listen-emitter"}
    [reason] = registry.failures().values()
    assert "some.event" in reason


def test_disabled_skill_path_ownership_is_purged_so_later_skill_can_reuse_it() -> None:
    # B가 깨끗한 경로 하나와 충돌하는 경로 하나를 등록하고 충돌로 비활성화되면, 깨끗한 경로의
    # 소유권도 함께 지워져야 한다. 그렇지 않으면 나중에 로드되는 C가 "이미 존재하지 않는 B"와
    # 충돌한다고 잘못 판정된다.
    registry = SkillRegistry(
        [
            FIXTURES / "skills_bad" / "slash_stale_owner_group",
            FIXTURES / "skills_bad" / "slash_stale_owner_conflict",
            FIXTURES / "skills_bad" / "slash_stale_owner_reuse",
        ]
    )
    registry.load()

    enabled_names = {s.manifest.name for s in registry.enabled_skills()}
    assert enabled_names == {"slash-stale-owner-group", "slash-stale-owner-reuse"}
    assert len(registry.failures()) == 1
    [reason] = registry.failures().values()
    assert "stale-group" in reason
