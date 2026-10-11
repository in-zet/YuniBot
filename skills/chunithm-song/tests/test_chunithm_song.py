# skills/chunithm-song/tests/test_chunithm_song.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.songdb import SongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CTX_NO_GUILD = ToolContext(player_id="p1", tags=frozenset(), services={}, event_bus=EventBus())


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_song_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx_with_songdb(songdb: SongDbService, *, guild_id: str | None = None) -> ToolContext:
    return ToolContext(
        player_id="p1", tags=frozenset(), services={"songdb": songdb}, event_bus=EventBus(), guild_id=guild_id
    )


async def test_info_lists_available_charts_and_emits_viewed_event() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)

    result = await skill.info(_ctx_with_songdb(songdb), song=song)

    assert result.error is None
    assert result.data["title"] == "B.B.K.K.B.K.K."
    assert result.display is not None and "B.B.K.K.B.K.K." in (result.display.content or "")


async def test_chart_reports_const_and_note_count() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)
    chart = song.charts["MAS"]

    from services.chunithm.chart_ref import ResolvedChart

    result = await skill.chart(_ctx_with_songdb(songdb), chart=ResolvedChart(song=song, chart=chart))

    assert result.data["const"] == chart.const
    assert result.data["note_count"] == chart.note_count


async def test_find_by_const_returns_sorted_results() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_const(
        _ctx_with_songdb(songdb), min_const=12.0, max_const=13.0,
        difficulty=_resolved_difficulty("MASTER"),
    )

    assert result.error is None
    assert result.data["count"] >= 1


async def test_chart_link_prefers_sdvxin_mapping() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)
    chart = song.charts["MAS"]

    from services.chunithm.chart_ref import ResolvedChart

    result = await skill.chart_link(_ctx_with_songdb(songdb), chart=ResolvedChart(song=song, chart=chart))

    assert result.data["has_mapping"] is True
    assert "sdvx.in" in result.data["url"]


async def test_alias_add_requires_guild() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)

    result = await skill.alias_add(_ctx_with_songdb(songdb, guild_id=None), song=song, alias="크로노")

    assert result.error is not None


async def test_alias_add_then_resolve_by_alias(tmp_path) -> None:
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/011_songdb_aliases.sql").read_text())
    await conn.commit()
    songdb = SongDbService(conn=conn)
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)
    skill = _load_skill()

    result = await skill.alias_add(_ctx_with_songdb(songdb, guild_id="g1"), song=song, alias="크로노")
    assert result.error is None

    resolved_again = await songdb.resolve("크로노", ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1"))
    assert resolved_again.title == "B.B.K.K.B.K.K."
    await conn.close()


async def test_alias_remove_rejects_non_owner_non_admin() -> None:
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/011_songdb_aliases.sql").read_text())
    await conn.commit()
    songdb = SongDbService(conn=conn)
    await songdb.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="owner")
    skill = _load_skill()

    ctx = ToolContext(player_id="someone-else", tags=frozenset(), services={"songdb": songdb}, guild_id="g1")
    result = await skill.alias_remove(ctx, alias="크로노")

    assert result.error is not None
    assert await songdb.get_alias(guild_id="g1", alias="크로노") is not None
    await conn.close()


async def test_alias_remove_allows_admin() -> None:
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/011_songdb_aliases.sql").read_text())
    await conn.commit()
    songdb = SongDbService(conn=conn)
    await songdb.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="owner")
    skill = _load_skill()

    ctx = ToolContext(player_id="admin1", tags=frozenset({"admin"}), services={"songdb": songdb}, guild_id="g1")
    result = await skill.alias_remove(ctx, alias="크로노")

    assert result.error is None
    assert await songdb.get_alias(guild_id="g1", alias="크로노") is None
    await conn.close()


def _resolved_difficulty(canonical: str):
    from services.chunithm.terms import ResolvedDifficulty

    return ResolvedDifficulty(canonical=canonical)
