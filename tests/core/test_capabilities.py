# tests/core/test_capabilities.py
from pathlib import Path

from core.capabilities import (
    CATEGORY_BLURBS,
    build_capabilities_text,
    build_help_category,
    build_help_overview,
    collect_capabilities,
    format_commands,
    help_categories,
)
from core.manifest import VALID_CATEGORIES
from core.registry import SkillRegistry


def _registry() -> SkillRegistry:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    return registry


def test_build_capabilities_text_groups_by_category() -> None:
    registry = _registry()
    text = build_capabilities_text(
        registry=registry, player_tags=set(), tag_definitions=registry.tag_definitions(), category=None,
    )
    assert "곡 정보" in text
    assert "chunithm-border" in text


def test_build_capabilities_text_hides_admin_without_tag() -> None:
    registry = _registry()
    text = build_capabilities_text(
        registry=registry, player_tags=set(), tag_definitions=registry.tag_definitions(), category=None,
    )
    assert "admin" not in text


def test_build_capabilities_text_shows_admin_with_tag() -> None:
    registry = _registry()
    text = build_capabilities_text(
        registry=registry, player_tags={"admin"}, tag_definitions=registry.tag_definitions(), category=None,
    )
    assert "admin" in text


def test_build_capabilities_text_filters_by_category() -> None:
    registry = _registry()
    text = build_capabilities_text(
        registry=registry, player_tags=set(), tag_definitions=registry.tag_definitions(), category="곡 정보",
    )
    assert "song" in text
    assert "account-link" not in text


def test_build_capabilities_text_describes_locked_status_tags() -> None:
    registry = _registry()
    text = build_capabilities_text(
        registry=registry, player_tags=set(), tag_definitions=registry.tag_definitions(), category=None,
    )
    # chunithm-border의 '남은어택' 도구는 chunithm-synced 태그가 필요하다 — 잠긴 기능 안내에 포함
    assert "CHUNITHM-NET" in text


_ALL_TAGS = {"admin", "sega-linked", "chunithm-synced"}
_DISCORD_MESSAGE_LIMIT = 2000


def test_every_category_has_a_blurb() -> None:
    assert set(CATEGORY_BLURBS) == set(VALID_CATEGORIES)


def test_every_skill_has_a_player_facing_summary() -> None:
    # description은 모델에게 주는 선택 기준이라 플레이어 글이 아니다 — 도움말은 summary를 쓴다.
    for skill in _registry().enabled_skills():
        assert skill.manifest.summary, skill.manifest.name


def test_help_overview_speaks_as_yuni_without_internal_skill_names() -> None:
    found = collect_capabilities(registry=_registry(), player_tags=set(), category=None)
    text = build_help_overview(found)
    assert "유니" in text
    assert "chunithm-" not in text
    assert "곡 정보" in text
    assert "관리" not in help_categories(found)


def test_help_overview_guides_login_until_linked() -> None:
    registry = _registry()
    new = build_help_overview(collect_capabilities(registry=registry, player_tags=set(), category=None))
    assert "/로그인" in new
    linked = build_help_overview(
        collect_capabilities(registry=registry, player_tags=_ALL_TAGS - {"admin"}, category=None)
    )
    assert "더 쓰고 싶다면" not in linked


def test_help_category_shows_examples_and_slash_commands() -> None:
    found = collect_capabilities(registry=_registry(), player_tags=set(), category=None)
    text = build_help_category(found, "곡 정보")
    assert "이렇게 말해 봐" in text
    assert "`/곡`" in text
    assert "chunithm-" not in text


def test_help_category_hides_commands_the_player_cannot_use_yet() -> None:
    registry = _registry()
    before = build_help_category(
        collect_capabilities(registry=registry, player_tags=set(), category=None), "곡 정보"
    )
    after = build_help_category(
        collect_capabilities(registry=registry, player_tags={"chunithm-synced"}, category=None), "곡 정보"
    )
    assert "남은어택" not in before
    assert "남은어택" in after


def test_help_messages_fit_in_one_discord_message_even_with_every_tag() -> None:
    found = collect_capabilities(registry=_registry(), player_tags=_ALL_TAGS, category=None)
    assert len(build_help_overview(found)) <= _DISCORD_MESSAGE_LIMIT
    for category in help_categories(found):
        assert len(build_help_category(found, category)) <= _DISCORD_MESSAGE_LIMIT, category


def test_format_commands_merges_a_group_into_one_entry() -> None:
    assert format_commands(["폴더 생성", "폴더 삭제", "곡"]) == "`/폴더 생성·삭제` `/곡`"
    assert "외 2개" in format_commands([f"묶음 {i}" for i in range(10)])
