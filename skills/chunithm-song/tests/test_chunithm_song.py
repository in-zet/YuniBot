# skills/chunithm-song/tests/test_chunithm_song.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from core.events import EventBus, Section
from core.tool_types import Display, ToolContext
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


class _RecordingBus(EventBus):
    """emit_gather 호출을 기록하고, 정해 둔 조각을 돌려주는 버스(다른 스킬의 곡 조회 조각 대신)."""

    def __init__(self, sections: list[Section]) -> None:
        super().__init__()
        self.calls: list[tuple[str, Any]] = []
        self._sections = sections

    async def emit_gather(self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None) -> list[Section]:
        self.calls.append((event_name, payload))
        return list(self._sections)


def _ctx_with_bus(songdb: SongDbService, bus: EventBus) -> ToolContext:
    return ToolContext(player_id="p1", tags=frozenset(), services={"songdb": songdb}, event_bus=bus)


async def test_chart_emits_song_viewed_with_real_difficulty_and_renders_sections() -> None:
    """I3: chart는 실제 난이도 코드로 chunithm.song.viewed를 내고, 돌아온 조각을 표시에 붙인다."""
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)
    from services.chunithm.chart_ref import ResolvedChart

    bus = _RecordingBus([
        Section(order=10, display=Display(content="투두 목표 1,007,500점"), data={}),
        Section(order=20, display=Display(content="내 메모\n후반 주의"), data={}),
    ])
    result = await skill.chart(_ctx_with_bus(songdb, bus), chart=ResolvedChart(song=song, chart=song.charts["MAS"]))

    assert bus.calls == [
        ("chunithm.song.viewed", {"player_id": "p1", "song_title": song.title, "difficulty": "MAS"})
    ]
    content = result.display.content if result.display is not None else ""
    assert "투두 목표 1,007,500점" in content
    assert "후반 주의" in content
    assert content.index("투두 목표") < content.index("내 메모")


async def test_info_emits_whole_song_view_and_renders_sections() -> None:
    """I3: info는 곡 전체 보기(difficulty None)로 내고, 돌아온 조각을 표시에 붙인다."""
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)

    bus = _RecordingBus([Section(order=20, display=Display(content="내 메모\n곡 전체 메모"), data={})])
    result = await skill.info(_ctx_with_bus(songdb, bus), song=song)

    assert bus.calls == [("chunithm.song.viewed", {"player_id": "p1", "song_title": song.title, "difficulty": None})]
    content = result.display.content if result.display is not None else ""
    assert content.startswith("B.B.K.K.B.K.K.")
    assert "곡 전체 메모" in content


async def test_find_by_const_returns_sorted_results() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_const(
        _ctx_with_songdb(songdb), min_const=12.0, max_const=13.0,
        difficulty=_resolved_difficulty("MASTER"),
    )

    assert result.error is None
    assert result.data["count"] >= 1


async def test_find_by_version_without_difficulty_returns_all_available_charts() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_version(_ctx_with_songdb(songdb), version="LUMINOUS")

    assert result.error is None
    assert result.data["count"] > 0
    assert "可愛くてごめん" in result.data["titles"]


async def test_find_by_version_with_difficulty_filters_to_one_chart() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_version(
        _ctx_with_songdb(songdb), version="LUMINOUS", difficulty=_resolved_difficulty("MASTER"),
    )

    assert result.error is None
    assert result.data["count"] > 0


async def test_find_by_artist_returns_matching_songs() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_artist(_ctx_with_songdb(songdb), artist="nora2r")

    assert result.error is None
    assert result.data["count"] > 0
    assert "B.B.K.K.B.K.K." in result.data["titles"]


async def test_find_by_artist_no_match_shows_empty_message() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_artist(_ctx_with_songdb(songdb), artist="그런작곡가없음")

    assert result.error is None
    assert result.data["count"] == 0
    assert result.display is not None and "없다" in (result.display.content or "")


async def test_find_songs_combines_const_and_note_count() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    notes = songdb.note_count_for("B.B.K.K.B.K.K.", "MAS")
    assert notes is not None

    result = await skill.find_songs(
        _ctx_with_songdb(songdb), min_const=12.5, max_const=12.5, min_note_count=notes,
    )

    assert result.error is None
    assert "B.B.K.K.B.K.K." in result.data["titles"]


async def test_find_songs_without_any_filter_is_rejected() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_songs(_ctx_with_songdb(songdb))

    assert result.error is not None


async def test_find_songs_no_match_shows_empty_message() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_songs(_ctx_with_songdb(songdb), min_const=99.0, max_const=99.0)

    assert result.error is None
    assert result.data["count"] == 0
    assert result.display is not None and "없다" in (result.display.content or "")


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
