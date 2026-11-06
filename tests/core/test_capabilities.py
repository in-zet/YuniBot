# tests/core/test_capabilities.py
from pathlib import Path

from core.capabilities import (
    build_capabilities_text,
    format_commands,
)
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


_ALL_TAGS = {"admin", "sega-linked", "chunithm-synced", "maimai-synced"}


def test_every_skill_has_a_player_facing_summary() -> None:
    # description은 모델에게 주는 선택 기준이라 플레이어 글이 아니다 — 할 수 있는 일 안내(list_capabilities)는 summary를 쓴다.
    for skill in _registry().enabled_skills():
        assert skill.manifest.summary, skill.manifest.name


def test_format_commands_merges_a_group_into_one_entry() -> None:
    assert format_commands(["폴더 생성", "폴더 삭제", "곡"]) == "`/폴더 생성·삭제` `/곡`"
    assert "외 2개" in format_commands([f"묶음 {i}" for i in range(10)])
