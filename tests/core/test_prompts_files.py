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


def test_rules_md_tells_the_model_how_to_use_work_tables() -> None:
    text = Path("prompts/rules.md").read_text()
    for needle in ("table_peek", "table_query", "[살아 있는 표]", "(동기화 전 값)", "rows", "exclude_rows", '"t3:2,3"',
                   "답장한 메시지의 표", "마지막으로 보여 준 표"):
        assert needle in text, needle
