from pathlib import Path

from core.help_slash import build_help_command
from core.registry import SkillRegistry


def test_build_help_command_has_korean_name() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    command = build_help_command(registry=registry)
    assert command.name == "도움말"
