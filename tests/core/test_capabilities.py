# tests/core/test_capabilities.py
from pathlib import Path

from core.capabilities import build_capabilities_text
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
    assert "chunithm-song" in text
    assert "account-link" not in text


def test_build_capabilities_text_describes_locked_status_tags() -> None:
    registry = _registry()
    text = build_capabilities_text(
        registry=registry, player_tags=set(), tag_definitions=registry.tag_definitions(), category=None,
    )
    # chunithm-border의 '남은어택' 도구는 chunithm-synced 태그가 필요하다 — 잠긴 기능 안내에 포함
    assert "CHUNITHM-NET" in text
