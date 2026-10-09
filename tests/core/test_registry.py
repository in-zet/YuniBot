import shutil
from pathlib import Path

import pytest

from core.registry import SkillRegistry
from tests.core.skill_factory import slash_tool, write_skill

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


# --- 슬래시 경로 검증(레지스트리가 스킬 단위로 막는다) -------------------------------------


def _load(*roots: Path) -> SkillRegistry:
    registry = SkillRegistry(list(roots))
    registry.load()
    return registry


def _enabled(registry: SkillRegistry) -> set[str]:
    return {s.manifest.name for s in registry.enabled_skills()}


def test_same_skill_standalone_and_group_self_conflict_disables_that_skill(tmp_path: Path) -> None:
    # 한 스킬이 "foo"를 단독 명령으로, "foo bar"로 그룹으로 동시에 쓴다. 보호할 "먼저 온
    # 다른 스킬"이 없으므로 그 스킬 자신이 비활성화되어야 한다.
    _ = write_skill(tmp_path, "self-conflict", slash_tool("a", "foo") + slash_tool("b", "foo bar"))
    _ = write_skill(tmp_path, "zz-innocent", slash_tool("c", "other"))

    registry = _load(tmp_path)

    assert _enabled(registry) == {"zz-innocent"}
    assert "'foo'" in registry.failures()["self-conflict"]


def test_same_skill_nested_self_conflict_below_root_disables_that_skill(tmp_path: Path) -> None:
    _ = write_skill(tmp_path, "self-deep", slash_tool("a", "g x") + slash_tool("b", "g x y"))

    registry = _load(tmp_path)

    assert _enabled(registry) == set()
    assert "'g x'" in registry.failures()["self-deep"]


def test_same_skill_duplicate_path_disables_that_skill(tmp_path: Path) -> None:
    _ = write_skill(tmp_path, "self-dup", slash_tool("a", "foo bar") + slash_tool("b", "foo bar"))

    registry = _load(tmp_path)

    assert _enabled(registry) == set()
    assert "'foo bar'" in registry.failures()["self-dup"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("g x", "g x y"),  # 앞 스킬의 하위 명령을 뒤 스킬이 그룹으로 쓴다(루트 아래 단계 충돌)
        ("g x y", "g x"),  # 반대 방향
        ("g x", "g x"),  # 루트 아래 단계에서 완전히 같은 경로
        ("g x y", "g x y"),  # 3단계에서 완전히 같은 경로
        ("g", "g x y"),  # 루트 단독 명령 vs 3단계 그룹
    ],
)
def test_cross_skill_collision_at_any_depth_disables_later_skill(
    tmp_path: Path, first: str, second: str
) -> None:
    _ = write_skill(tmp_path, "a-first", slash_tool("a", first))
    _ = write_skill(tmp_path, "b-second", slash_tool("b", second))

    registry = _load(tmp_path)

    assert _enabled(registry) == {"a-first"}
    assert set(registry.failures()) == {"b-second"}
    assert "a-first" in registry.failures()["b-second"]


def test_skills_sharing_a_group_without_collision_both_load(tmp_path: Path) -> None:
    _ = write_skill(tmp_path, "a-first", slash_tool("a", "g x") + slash_tool("a2", "g sub one"))
    _ = write_skill(tmp_path, "b-second", slash_tool("b", "g y") + slash_tool("b2", "g sub two"))

    registry = _load(tmp_path)

    assert _enabled(registry) == {"a-first", "b-second"}
    assert registry.failures() == {}


def test_slash_path_deeper_than_three_levels_disables_skill(tmp_path: Path) -> None:
    _ = write_skill(tmp_path, "too-deep", slash_tool("a", "a b c d"))

    registry = _load(tmp_path)

    assert _enabled(registry) == set()
    assert "3단계" in registry.failures()["too-deep"]


@pytest.mark.parametrize("bad_slash", ["Foo", "foo Bar", "", "   ", "a" * 33, "foo/bar"])
def test_invalid_slash_token_disables_skill(tmp_path: Path, bad_slash: str) -> None:
    _ = write_skill(tmp_path, "bad-token", slash_tool("a", bad_slash))

    registry = _load(tmp_path)

    assert _enabled(registry) == set()
    assert "슬래시" in registry.failures()["bad-token"]


@pytest.mark.parametrize("bad_arg", ["__options", "Song"])
def test_invalid_tool_argument_name_disables_skill(tmp_path: Path, bad_arg: str) -> None:
    _ = write_skill(tmp_path, "bad-arg", slash_tool("a", "foo", args=f"{bad_arg}: str"))

    registry = _load(tmp_path)

    assert _enabled(registry) == set()
    assert bad_arg in registry.failures()["bad-arg"]


def test_invalid_manifest_name_disables_only_that_skill(tmp_path: Path) -> None:
    _ = write_skill(tmp_path, "bad-name", slash_tool("a", "foo"), manifest_name="Bad_Name")

    registry = _load(FIXTURES / "skills_ok", tmp_path)

    assert _enabled(registry) == {"practice"}
    assert "Bad_Name" in registry.failures()["bad-name"]


def test_template_copy_loads_with_zero_failures(tmp_path: Path) -> None:
    # "새 스킬은 _template을 복사해 만든다" — 복사본이 아무것도 고치지 않은 상태에서 그대로
    # 로드되어야 한다. _로 시작하는 폴더는 스캔하지 않으므로 다른 이름으로 복사해 직접 가리킨다.
    copied = tmp_path / "my-skill"
    _ = shutil.copytree(Path("skills/_template"), copied, ignore=shutil.ignore_patterns("__pycache__"))

    registry = _load(copied)

    assert registry.failures() == {}
    assert _enabled(registry) == {"my-skill"}
