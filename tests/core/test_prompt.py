from pathlib import Path

from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from core.skill_loading import SkillLoadState


def _registry() -> SkillRegistry:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    return registry


def test_visible_skill_cards_hides_tagged_skill_without_tag() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    names_without_admin_tag = {s.manifest.name for s in assembler.visible_skill_cards(player_tags=set())}
    assert "admin" not in names_without_admin_tag
    assert "chunithm-border" in names_without_admin_tag


def test_visible_skill_cards_shows_admin_with_tag() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    names_with_admin_tag = {s.manifest.name for s in assembler.visible_skill_cards(player_tags={"admin"})}
    assert "admin" in names_with_admin_tag


def test_build_system_instruction_includes_rules_before_persona() -> None:
    assembler = PromptAssembler(rules_text="규칙 본문", persona_text="말투 본문", registry=_registry())
    state = SkillLoadState(mode="instructions", known_skills=set())
    text = assembler.build_system_instruction(player_tags=set(), skill_state=state, prompt_context_snippets=[])
    assert text.index("규칙 본문") < text.index("말투 본문")


def test_build_system_instruction_includes_opened_skill_body() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    state = SkillLoadState(mode="full", known_skills={"chunithm-border"})
    state.open("chunithm-border")
    text = assembler.build_system_instruction(player_tags=set(), skill_state=state, prompt_context_snippets=[])
    assert "보더는 저스티스 0" in text  # chunithm-border/SKILL.md 본문 일부


def test_build_tool_declarations_includes_core_tools_always() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    state = SkillLoadState(mode="full", known_skills={s.manifest.name for s in _registry().enabled_skills()})
    names = {d.name for d in assembler.build_tool_declarations(player_tags=set(), skill_state=state)}
    assert {"open_skill", "remember", "list_capabilities"} <= names


def test_build_tool_declarations_excludes_unopened_skill_in_full_mode() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    known = {s.manifest.name for s in _registry().enabled_skills()}
    state = SkillLoadState(mode="full", known_skills=known)
    names = {d.name for d in assembler.build_tool_declarations(player_tags=set(), skill_state=state)}
    assert not any(name.startswith("chunithm-border_") for name in names)


def test_build_tool_declarations_includes_instructions_mode_skill_without_opening() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    known = {s.manifest.name for s in _registry().enabled_skills()}
    state = SkillLoadState(mode="instructions", known_skills=known)
    names = {d.name for d in assembler.build_tool_declarations(player_tags=set(), skill_state=state)}
    assert "chunithm-border_lookup" in names


def test_build_tool_declarations_hides_tagged_skill_tools() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    known = {s.manifest.name for s in _registry().enabled_skills()}
    state = SkillLoadState(mode="instructions", known_skills=known)
    names = {d.name for d in assembler.build_tool_declarations(player_tags=set(), skill_state=state)}
    assert not any(name.startswith("admin_") for name in names)


def test_triggered_skills_matches_lowercased_trigger_words_in_message() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    names = assembler.triggered_skills(
        "즐겨찾기에 HERA 넣고 Linked VERSE 스토리도 알려줘", player_tags={"sega-linked"}
    )
    assert "settings" in names
    assert "yuni-lore" in names
    assert "chunithm-border" not in names


def test_triggered_skills_skips_skills_hidden_by_tags() -> None:
    assembler = PromptAssembler(rules_text="규칙", persona_text="말투", registry=_registry())
    assert "admin" not in assembler.triggered_skills("이번 달 비용 얼마야", player_tags=set())
    assert "admin" in assembler.triggered_skills("이번 달 비용 얼마야", player_tags={"admin"})


def test_every_shipped_skill_declares_triggers() -> None:
    registry = _registry()
    missing = [s.manifest.name for s in registry.enabled_skills() if not s.manifest.triggers]
    assert missing == []
