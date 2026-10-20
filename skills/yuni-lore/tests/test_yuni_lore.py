# skills/yuni-lore/tests/test_yuni_lore.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from core.manifest import parse_manifest
from core.tool_types import ToolContext

_SKILL_DIR = Path(__file__).parent.parent
_SKILL_PY = _SKILL_DIR / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_yuni_lore_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx() -> ToolContext:
    return ToolContext(player_id="p1", tags=frozenset(), services={})


def test_manifest_is_valid() -> None:
    manifest = parse_manifest((_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))

    assert manifest.name == "yuni-lore"
    assert manifest.category == "유니 설정"


async def test_without_section_returns_index_with_spoiler_flags() -> None:
    skill = _load_skill()

    result = await skill.read_lore(_ctx())

    assert result.error is None
    by_id = {s["id"]: s for s in result.data["sections"]}
    assert by_id["origin"]["spoiler"] is False
    assert by_id["ending"]["spoiler"] is True
    assert by_id["ending"]["title"] == "결말"


async def test_every_spoiler_section_is_blocked_without_spoiler_ok() -> None:
    skill = _load_skill()
    index = await skill.read_lore(_ctx())
    spoiler_ids = [s["id"] for s in index.data["sections"] if s["spoiler"]]

    assert spoiler_ids
    for section_id in spoiler_ids:
        result = await skill.read_lore(_ctx(), section=section_id)
        assert result.error is not None and "스포일러" in result.error
        assert "text" not in result.data


async def test_spoiler_section_is_readable_with_spoiler_ok() -> None:
    skill = _load_skill()

    result = await skill.read_lore(_ctx(), section="gate9", spoiler_ok=True)

    assert result.error is None
    assert result.data["id"] == "gate9"
    assert result.data["text"]


async def test_reads_only_the_requested_section() -> None:
    skill = _load_skill()

    result = await skill.read_lore(_ctx(), section="air")

    assert result.error is None
    assert "게임센터" in result.data["text"]
    assert "ORIGIN" not in result.data["text"]


async def test_unknown_section_lists_valid_ids() -> None:
    skill = _load_skill()

    result = await skill.read_lore(_ctx(), section="nope")

    assert result.error is not None and "origin" in result.error


async def test_missing_lore_file_is_an_error(monkeypatch, tmp_path: Path) -> None:
    skill = _load_skill()
    monkeypatch.setattr(skill, "_LORE_PATH", tmp_path / "missing.md")

    result = await skill.read_lore(_ctx())

    assert result.error is not None


def test_lore_text_has_no_future_uni_speaker_labels() -> None:
    # 10년 뒤의 유니는 설정집에서 다루지 않는다(2026-10-20 사용자 지시).
    text = (_SKILL_DIR / "lore.md").read_text(encoding="utf-8")

    for label in ("【ユニ？】", "【イクシア】", "イクシア", "RE:VERSE", "UNIVERSE"):
        assert label not in text
