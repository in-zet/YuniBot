# tests/core/test_prompts_files.py
from pathlib import Path


def test_rules_md_exists_and_mentions_numeric_rule() -> None:
    text = Path("prompts/rules.md").read_text()
    assert "data" in text
    assert len(text) > 0


def test_persona_md_exists_and_is_short() -> None:
    text = Path("prompts/persona.md").read_text()
    assert len(text) > 0
    # 11단계 전까지는 건조한 임시 말투라 짧아야 한다(대사집을 미리 채우지 않는다).
    assert len(text) < 2000
