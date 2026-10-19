from pathlib import Path

import aiosqlite

from core.core_tools import CORE_TOOL_SPECS, list_capabilities, open_skill, remember
from core.skill_loading import SkillLoadState
from core.tool_types import ToolContext
from services.common.memory import MemoryService


def test_core_tool_specs_contains_all_three() -> None:
    names = {spec.name for spec in CORE_TOOL_SPECS}
    assert names == {"open_skill", "remember", "list_capabilities"}


def test_list_capabilities_importable_without_core_capabilities_module() -> None:
    # list_capabilities는 core/capabilities.py를 함수 안에서 늦게 import한다. 이 모듈을
    # import하는 것만으로는(= 이 함수를 직접 부르지 않는 한) 깨지지 않아야 한다.
    assert list_capabilities.__name__ == "list_capabilities"


async def test_open_skill_marks_skill_open_in_state() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border"})
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"_skill_load_state": state})
    result = await open_skill(ctx, skill_name="chunithm-border")
    assert result.error is None
    assert state.level_for("chunithm-border") == "full"


async def test_open_skill_rejects_unknown_skill() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border"})
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"_skill_load_state": state})
    result = await open_skill(ctx, skill_name="does-not-exist")
    assert result.error is not None


async def _memory_ctx() -> tuple[ToolContext, MemoryService, aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    for name in ("004_memory.sql", "015_memory_nickname_pending.sql"):
        await conn.executescript(Path("migrations/core", name).read_text())
    memory = MemoryService(conn)
    return ToolContext(player_id="p1", tags=frozenset(), services={"memory": memory}), memory, conn


async def test_remember_appends_to_memory() -> None:
    ctx, memory, conn = await _memory_ctx()
    try:
        result = await remember(ctx, content="왼손이 약하다")
        assert result.error is None
        assert result.data == {"saved": "왼손이 약하다", "nickname": None}
        assert await memory.get("p1") == "왼손이 약하다"
    finally:
        await conn.close()


async def test_remember_nickname_goes_to_the_nickname_slot() -> None:
    ctx, memory, conn = await _memory_ctx()
    try:
        result = await remember(ctx, nickname="디짐")
        assert result.error is None
        assert result.data == {"saved": None, "nickname": "디짐"}
        assert await memory.get_nickname("p1") == "디짐"
        assert await memory.get("p1") == ""
    finally:
        await conn.close()


async def test_remember_rejects_empty_input() -> None:
    ctx, _memory, conn = await _memory_ctx()
    try:
        assert (await remember(ctx)).error is not None
        assert (await remember(ctx, content="  ", nickname="@")).error is not None
    finally:
        await conn.close()
