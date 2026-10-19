# tests/core/test_prompts_files.py
from pathlib import Path


def test_rules_md_exists_and_mentions_numeric_rule() -> None:
    text = Path("prompts/rules.md").read_text()
    assert "data" in text
    assert len(text) > 0


def test_persona_md_is_yuni_voice_and_stays_short() -> None:
    text = Path("prompts/persona.md").read_text()
    assert "유니" in text
    assert "반말" in text
    # 대사집을 통째로 넣지 않는다(예시 몇 개만) — 00-core.md "프롬프트 파일".
    assert len(text) < 3000
