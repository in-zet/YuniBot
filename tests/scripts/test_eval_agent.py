from pathlib import Path

from core.registry import SkillRegistry
from scripts.eval_agent import collect_eval_cases, score_tool_choice


def test_collect_eval_cases_includes_all_examples() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    cases = collect_eval_cases(registry)
    border_cases = [c for c in cases if c.skill_name == "chunithm-border"]
    assert len(border_cases) == 3  # chunithm-border/SKILL.md의 examples 3개


def test_score_tool_choice_full_mode_requires_open_skill_first() -> None:
    assert score_tool_choice(expected_skill="chunithm-border", first_tool_name="open_skill", mode="full") is True
    assert score_tool_choice(expected_skill="chunithm-border", first_tool_name="chunithm-song_info", mode="full") is False


def test_score_tool_choice_instructions_mode_checks_skill_prefix() -> None:
    assert score_tool_choice(expected_skill="chunithm-border", first_tool_name="chunithm-border_lookup", mode="instructions") is True
    assert score_tool_choice(expected_skill="chunithm-border", first_tool_name="chunithm-song_info", mode="instructions") is False
