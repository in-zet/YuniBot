# core/prompt.py
from __future__ import annotations

from core.declarations import build_tool_declaration
from core.llm import ToolDeclaration
from core.permissions import TagRequirement, check_tags
from core.registry import LoadedSkill, SkillRegistry
from core.skill_loading import SkillLoadState


def _skill_tag_requirement(skill: LoadedSkill) -> TagRequirement:
    return TagRequirement(tags=list(skill.manifest.tags), tags_any=list(skill.manifest.tags_any))


class PromptAssembler:
    def __init__(self, *, rules_text: str, persona_text: str, registry: SkillRegistry) -> None:
        self._rules_text = rules_text
        self._persona_text = persona_text
        self._registry = registry

    def visible_skill_cards(self, *, player_tags: set[str]) -> list[LoadedSkill]:
        return [
            skill
            for skill in self._registry.enabled_skills()
            if check_tags(_skill_tag_requirement(skill), player_tags)
        ]

    def _card_text(self, skill: LoadedSkill) -> str:
        manifest = skill.manifest
        examples = "; ".join(manifest.examples)
        return f"- {manifest.name} ({manifest.category}): {manifest.description} (예: {examples})"

    def build_system_instruction(
        self,
        *,
        player_tags: set[str],
        skill_state: SkillLoadState,
        prompt_context_snippets: list[str],
    ) -> str:
        visible = self.visible_skill_cards(player_tags=player_tags)
        cards = "\n".join(self._card_text(s) for s in visible)

        instruction_bodies: list[str] = []
        for skill in visible:
            level = skill_state.level_for(skill.manifest.name)
            if level in ("instructions", "full"):
                instruction_bodies.append(f"## {skill.manifest.name}\n{_skill_body(skill)}")

        sections = [self._rules_text, self._persona_text, "## 스킬 카드\n" + cards]
        if instruction_bodies:
            sections.append("## 펼친 스킬 지침\n" + "\n\n".join(instruction_bodies))
        if prompt_context_snippets:
            sections.append("## 맥락\n" + "\n".join(prompt_context_snippets))
        return "\n\n".join(sections)

    def build_tool_declarations(
        self, *, player_tags: set[str], skill_state: SkillLoadState
    ) -> list[ToolDeclaration]:
        from core.core_tools import CORE_TOOL_SPECS

        declarations = [build_tool_declaration(spec, skill_name=None) for spec in CORE_TOOL_SPECS]
        for skill in self.visible_skill_cards(player_tags=player_tags):
            level = skill_state.level_for(skill.manifest.name)
            if level != "full" and level != "instructions":
                continue
            for spec in skill.code.tools:
                declarations.append(build_tool_declaration(spec, skill_name=skill.manifest.name))
        return declarations


def _skill_body(skill: LoadedSkill) -> str:
    """SKILL.md에서 프런트매터(YAML, '---'로 감싼 부분)를 뺀 본문만 돌려준다."""
    text = (skill.path / "SKILL.md").read_text()
    parts = text.lstrip().split("---", 2)
    return parts[2].strip() if len(parts) >= 3 else text
