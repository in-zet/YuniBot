# skills/memory/tests/test_memory_skill.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from core.executor import ToolExecutor
from core.permissions import InMemoryTagProvider
from core.resolvers import ResolverRegistry
from core.tool_types import ToolContext
from services.common.memory import MemoryMerge, MemoryService, PendingMessage

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE_MIGRATIONS = Path("migrations/core")


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_memory_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


@pytest.fixture
async def memory() -> AsyncGenerator[MemoryService]:
    conn = await aiosqlite.connect(":memory:")
    for name in ("004_memory.sql", "015_memory_nickname_pending.sql"):
        await conn.executescript((_CORE_MIGRATIONS / name).read_text())
    await conn.commit()
    try:
        yield MemoryService(conn)
    finally:
        await conn.close()


def _ctx(memory: MemoryService, player_id: str = "p1") -> ToolContext:
    return ToolContext(player_id=player_id, tags=frozenset(), services={"memory": memory})


def _executor(memory: MemoryService) -> ToolExecutor:
    return ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={},
        services={"memory": memory},
    )


async def test_view_shows_everything_privately_and_keeps_the_text_out_of_model_data(
    skill: ModuleType, memory: MemoryService
) -> None:
    await memory.set("p1", "왼손이 약하다\n짧은 답을 좋아한다")
    await memory.set_nickname("p1", "디짐")
    result = await skill.view(_ctx(memory))

    assert result.error is None and result.display is not None
    assert result.display.private is True and result.display.ephemeral is True
    assert result.display.content is not None
    for expected in ("호칭: 디짐", "- 왼손이 약하다", "- 짧은 답을 좋아한다", "자동 저장: 켜짐"):
        assert expected in result.display.content
    assert result.data["lines"] == 2 and result.data["has_nickname"] is True
    assert "왼손" not in str(result.data)


async def test_view_of_empty_memory(skill: ModuleType, memory: MemoryService) -> None:
    result = await skill.view(_ctx(memory))
    assert result.display is not None and result.display.content is not None
    assert "기억하고 있는 게 없어" in result.display.content
    assert result.data["lines"] == 0


async def test_view_never_shows_another_players_memory(skill: ModuleType, memory: MemoryService) -> None:
    await memory.set("p2", "남의 비밀")
    result = await skill.view(_ctx(memory, "p1"))
    assert result.display is not None and "남의 비밀" not in (result.display.content or "")


async def test_forget_one_line_runs_without_confirm_and_keeps_the_rest(
    skill: ModuleType, memory: MemoryService
) -> None:
    await memory.set("p1", "왼손이 약하다\n짧은 답을 좋아한다")
    outcome = await _executor(memory).invoke(skill.forget.__yuni_tool__, _ctx(memory), {"what": "왼손이 약하다"})
    assert outcome.needs_confirm is False
    assert outcome.result is not None and outcome.result.data["items"] == ["왼손이 약하다"]
    assert await memory.get("p1") == "짧은 답을 좋아한다"


async def test_forget_everything_needs_confirm_before_deleting(skill: ModuleType, memory: MemoryService) -> None:
    await memory.set("p1", "a\nb")
    executor = _executor(memory)
    outcome = await executor.invoke(skill.forget.__yuni_tool__, _ctx(memory), {})
    assert outcome.needs_confirm is True and outcome.confirm_token is not None
    assert outcome.preview is not None and outcome.preview.data["count"] == 2
    assert await memory.get("p1") == "a\nb"  # 아직 안 지웠다

    result = await executor.confirm(outcome.confirm_token, confirmed=True, player_id="p1")
    assert result.data["deleted_count"] == 2
    assert await memory.get("p1") == ""


async def test_forget_cannot_touch_another_players_memory(skill: ModuleType, memory: MemoryService) -> None:
    await memory.set("p2", "남의 메모리")
    result = await skill.forget(_ctx(memory, "p1"), what="남의 메모리")
    assert result.error is not None
    assert await memory.get("p2") == "남의 메모리"


async def test_forget_without_match_lists_current_lines_for_the_model_to_retry(
    skill: ModuleType, memory: MemoryService
) -> None:
    await memory.set("p1", "왼손이 약하다")
    await memory.set_nickname("p1", "디짐")
    result = await skill.forget(_ctx(memory), what="오른손")
    assert result.error is not None
    assert "왼손이 약하다" in result.error and "호칭: 디짐" in result.error


async def test_forget_on_empty_memory_is_an_error(skill: ModuleType, memory: MemoryService) -> None:
    assert (await skill.forget(_ctx(memory))).error is not None


async def test_forget_nickname_by_keyword(skill: ModuleType, memory: MemoryService) -> None:
    await memory.set_nickname("p1", "디짐")
    result = await skill.forget(_ctx(memory), what="호칭")
    assert result.error is None and result.data["items"] == ["호칭: 디짐"]
    assert await memory.get_nickname("p1") == ""


async def test_forget_dry_run_writes_nothing(skill: ModuleType, memory: MemoryService) -> None:
    await memory.set("p1", "a\nb")
    result = await skill.forget(_ctx(memory), dry_run=True)
    assert result.data["count"] == 2
    assert await memory.get("p1") == "a\nb"


async def test_forgotten_item_is_not_revived_by_the_next_summary(skill: ModuleType, memory: MemoryService) -> None:
    await memory.set("p1", "왼손이 약하다\n짧은 답을 좋아한다")
    await skill.forget(_ctx(memory), what="왼손이 약하다")
    assert await memory.enqueue(
        "p1", messages=[PendingMessage(id="1", text="아무 말")], guild_id=None, channel_id=None, message_id=None,
        now=0.0,
    )

    async def merge(state, pending):
        return MemoryMerge(content="왼손이 약하다\n짧은 답을 좋아한다")

    await memory.flush_due(now=10**6, merge=merge)
    assert await memory.get("p1") == "짧은 답을 좋아한다"


async def test_set_auto_off_stops_summaries_but_keeps_memory(skill: ModuleType, memory: MemoryService) -> None:
    await memory.set("p1", "기존")
    assert await memory.enqueue(
        "p1", messages=[PendingMessage(id="1", text="글")], guild_id=None, channel_id=None, message_id=None, now=0.0,
    )
    result = await skill.set_auto(_ctx(memory), on=False)
    assert result.data == {"auto_save": False}
    assert await memory.get("p1") == "기존"
    assert await memory.due_pending(now=10**9) == []
    assert not await memory.enqueue(
        "p1", messages=[PendingMessage(id="2", text="글")], guild_id=None, channel_id=None, message_id=None, now=0.0,
    )

    await skill.set_auto(_ctx(memory), on=True)
    assert await memory.get_auto_save("p1") is True
