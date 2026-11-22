from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.tool_types import ToolContext
from services.common.players import PlayersService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_tutorial_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class _FakeNotify:
    opened: list[dict[str, Any]] = field(default_factory=list)
    open_threads: set[tuple[str, str]] = field(default_factory=set)
    can_open: bool = True
    raise_on_open: bool = False

    def has_open_thread(self, player_id: str, skill_name: str) -> bool:
        return (player_id, skill_name) in self.open_threads

    async def open_dm_thread(self, **kwargs: Any) -> object | None:
        if self.raise_on_open:
            raise RuntimeError("DM 차단")
        if not self.can_open:
            return None
        self.opened.append(kwargs)
        self.open_threads.add((kwargs["player_id"], kwargs["skill_name"]))
        return object()


class _FakeYuni:
    def __init__(self) -> None:
        self.hints: list[str] = []

    async def line(self, sections: list[Any], *, player_id: str, guild_id: str | None, fallback: str) -> str:
        self.hints.append(sections[0].hint)
        return "인사"


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    for name in ("001_players", "002_player_tags", "027_players_main_game"):
        sql = Path(f"migrations/core/{name}.sql")
        await conn.executescript(sql.read_text())
    try:
        yield conn
    finally:
        await conn.close()


async def _ctx(db: aiosqlite.Connection, notify: _FakeNotify, *, tags: frozenset[str] = frozenset()) -> ToolContext:
    players = PlayersService(db)
    player = await players.create_player()
    services: dict[str, object] = {"players": players, "notify": notify, "yuni": _FakeYuni()}
    return ToolContext(player_id=player.id, tags=tags, services=services, db=db)


async def test_start_opens_a_dm_thread_with_the_guided_steps(db: aiosqlite.Connection) -> None:
    skill, notify = _load_skill(), _FakeNotify()
    ctx = await _ctx(db, notify)

    result = await skill.tutorial_start(ctx)

    assert result.error is None
    opened = notify.opened[0]
    assert opened["skill_name"] == "tutorial" and opened["opening"] == "인사"
    guidance = opened["guidance"]
    for command in ("/로그인", "/동기화", "/설정"):
        assert command in guidance
    assert "remember" in guidance and "end_conversation" in guidance and "tutorial_status" in guidance
    # 로그인 → 동기화 → 레이팅표 최초 작성 → 기능 묻기 → 안내 → 마무리 순서(2026-11-22 사용자 요청).
    order = ["② 로그인", "③ 동기화", "④ 레이팅표 최초 작성", "⑤ 어떤 기능을", "⑥ 기본 명령 안내", "⑧ 플레이어가", "end_conversation"]
    positions = [guidance.index(step) for step in order]
    assert positions == sorted(positions)
    # 2026-11-22 실기: 애매한 답("다")에 안내를 건너뛰고 바로 닫았다 — 안내는 필수이고 같은 턴에 닫지 않는다.
    assert "⑥ 기본 명령 안내 — 반드시 한다" in guidance and "⑥과 같은 턴에 대화를 끝내지 않는다" in guidance


async def test_status_reads_the_current_main_game_login_and_sync_tags(db: aiosqlite.Connection) -> None:
    skill, notify = _load_skill(), _FakeNotify()
    ctx = await _ctx(db, notify)
    assert (await skill.tutorial_status(ctx)).data == {
        "main_game": None, "logged_in": False, "synced": {"chunithm": False, "maimai": False},
    }

    await ctx.players.set_main_game(ctx.player_id, "maimai")
    await ctx.players.set_status_tag(ctx.player_id, "sega-linked", source="test")
    await ctx.players.set_status_tag(ctx.player_id, "maimai-synced", source="test")

    assert (await skill.tutorial_status(ctx)).data == {
        "main_game": "maimai", "logged_in": True, "synced": {"chunithm": False, "maimai": True},
    }


async def test_start_refuses_a_second_open_thread_and_reports_dm_failures(db: aiosqlite.Connection) -> None:
    skill, notify = _load_skill(), _FakeNotify()
    ctx = await _ctx(db, notify)
    _ = await skill.tutorial_start(ctx)
    assert (await skill.tutorial_start(ctx)).error is not None

    notify.open_threads.clear()
    notify.can_open = False
    assert (await skill.tutorial_start(ctx)).error == skill._DM_FAILED
    notify.can_open, notify.raise_on_open = True, True
    assert (await skill.tutorial_start(ctx)).error == skill._DM_FAILED


async def test_set_main_game_changes_only_the_callers_player(db: aiosqlite.Connection) -> None:
    skill, notify = _load_skill(), _FakeNotify()
    ctx = await _ctx(db, notify)
    other = await ctx.players.create_player()

    result = await skill.tutorial_set_main_game(ctx, game="maimai")

    assert result.data == {"main_game": "maimai"}
    mine = await ctx.players.get_player(ctx.player_id)
    theirs = await ctx.players.get_player(other.id)
    assert mine is not None and mine.main_game == "maimai"
    assert theirs is not None and theirs.main_game == ""
