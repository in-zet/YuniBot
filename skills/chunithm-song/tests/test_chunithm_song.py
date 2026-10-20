# skills/chunithm-song/tests/test_chunithm_song.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from core.events import EventBus, Section
from core.tables import WorkTable
from core.tool_types import Display, ToolContext
from services.chunithm import table_columns as tc
from services.chunithm.songdb import SongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CTX_NO_GUILD = ToolContext(player_id="p1", tags=frozenset(), services={}, event_bus=EventBus())


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_song_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _titles(table: WorkTable | None) -> list[str]:
    assert table is not None
    return [str(row.cell(tc.SONG).raw) for row in table.rows]


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
    table = result.table
    assert table is not None and result.display is None
    assert [c.name for c in table.columns] == [
        tc.SONG, tc.DIFFICULTY, tc.LEVEL, tc.CONST, tc.NOTE_COUNT, tc.VERSION,
    ]
    assert table.unit == "채보"
    assert table.steps[0] == "12.0~13.0 상수 MAS"
    assert all(row.ref.kind == tc.CHART for row in table.rows)
    consts = [row.cell(tc.CONST).raw for row in table.rows]
    assert consts == sorted(consts)
    assert table.row_count == result.data["count"]


async def test_find_by_const_without_max_and_new_only_filters() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    ctx = _ctx_with_songdb(songdb)
    mas = _resolved_difficulty("MASTER")

    open_ended = await skill.find_by_const(ctx, min_const=12.0, difficulty=mas)
    bounded = await skill.find_by_const(ctx, min_const=12.0, max_const=13.0, difficulty=mas)
    new_only = await skill.find_by_const(ctx, min_const=12.0, difficulty=mas, new_only=True)

    assert open_ended.data["count"] > bounded.data["count"]
    assert new_only.data["count"] <= open_ended.data["count"]
    assert open_ended.table is not None and open_ended.table.steps[0] == "12.0 이상 상수 MAS"
    # 신곡 표시는 Chunimaru 덧씌우기가 있어야 켜지므로 시드만으론 결과가 없을 수 있다 — 없으면 표 없이 안내만 온다.
    if new_only.table is not None:
        assert new_only.table.steps[0] == "12.0 이상 상수 MAS 신곡만"
        assert set(_titles(new_only.table)) <= set(_titles(open_ended.table))
    else:
        assert new_only.data["count"] == 0


async def test_find_by_version_without_difficulty_returns_all_available_charts() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_version(_ctx_with_songdb(songdb), version="LUMINOUS")

    assert result.error is None
    assert result.data["count"] > 0
    assert "可愛くてごめん" in _titles(result.table)
    assert result.table is not None and result.table.steps[0] == "LUMINOUS 버전"


async def test_find_by_version_with_difficulty_filters_to_one_chart() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_version(
        _ctx_with_songdb(songdb), version="LUMINOUS", difficulty=_resolved_difficulty("MASTER"),
    )

    assert result.error is None
    assert result.data["count"] > 0
    assert result.table is not None
    assert {row.cell(tc.DIFFICULTY).text for row in result.table.rows} == {"MAS"}


async def test_find_by_artist_without_difficulty_returns_song_rows() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_artist(_ctx_with_songdb(songdb), artist="nora2r")

    assert result.error is None
    table = result.table
    assert table is not None
    assert "B.B.K.K.B.K.K." in _titles(table)
    assert table.unit == "곡"
    assert table.steps == ("nora2r 작곡",)
    assert [c.name for c in table.columns] == [tc.SONG, tc.DIFFICULTY, tc.ARTIST, tc.VERSION]
    assert all(row.ref.kind == tc.SONG_KIND for row in table.rows)
    assert all(not row.cell(tc.DIFFICULTY).filled for row in table.rows)


async def test_find_by_artist_with_difficulty_returns_chart_rows() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_artist(
        _ctx_with_songdb(songdb), artist="nora2r", difficulty=_resolved_difficulty("MASTER")
    )

    table = result.table
    assert table is not None
    assert table.unit == "채보"
    assert table.steps == ("nora2r 작곡 MAS",)
    assert "B.B.K.K.B.K.K." in _titles(table)
    assert all(row.ref.kind == tc.CHART for row in table.rows)
    assert all(row.cell(tc.DIFFICULTY).text == "MAS" for row in table.rows)
    assert table.column_by_name(tc.CONST) is not None


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
    assert "B.B.K.K.B.K.K." in _titles(result.table)
    assert result.table is not None
    assert result.table.steps[0] == f"12.5 상수 노트 {notes}개 이상" or "12.5~12.5 상수" in result.table.steps[0]


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


async def test_find_songs_orders_by_const_then_title_with_unknown_last() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_songs(_ctx_with_songdb(songdb), min_const=12.0, max_const=13.0)

    table = result.table
    assert table is not None
    keys = [(row.cell(tc.CONST).raw, row.cell(tc.SONG).raw) for row in table.rows]
    assert keys == sorted(keys)


def test_find_songs_has_no_sort_arguments() -> None:
    import inspect

    skill = _load_skill()
    params = inspect.signature(skill.find_songs).parameters
    assert "sort_by" not in params and "descending" not in params


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


# ---------------------------------------------------------------- charts_of


def _song_table(titles: list[str], *, extra_memo: dict[str, str] | None = None) -> WorkTable:
    from core.tables import TableBuilder, cell

    builder = TableBuilder(unit="곡")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.column("메모", "메모")
    builder.step("테스트 곡 표")
    for title in titles:
        memo = (extra_memo or {}).get(title)
        builder.row(tc.song_ref(title), {
            tc.SONG: tc.song_cell(title), tc.DIFFICULTY: tc.difficulty_cell(None),
            "메모": cell(memo) if memo else cell(None, "-"),
        })
    return builder.build()


def _titles_with(songdb: SongDbService, code: str, *, want: bool, count: int) -> list[str]:
    titles: list[str] = []
    for hit in songdb.find_songs(difficulty="MAS"):
        has = code in hit.song.charts and hit.song.charts[code].is_available
        if has == want and hit.song.title not in titles:
            titles.append(hit.song.title)
        if len(titles) == count:
            break
    return titles


async def test_charts_of_expands_song_rows_to_mas_by_default() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    titles = _titles_with(songdb, "MAS", want=True, count=2)

    result = await skill.charts_of(
        _ctx_with_songdb(songdb), table=_song_table(titles, extra_memo={titles[0]: "메모 있음"})
    )

    assert result.error is None
    table = result.table
    assert table is not None
    assert table.unit == "채보"
    assert table.steps == ("테스트 곡 표", "MAS로 펼침")
    assert [c.name for c in table.columns] == [tc.SONG, tc.DIFFICULTY, "메모", tc.LEVEL, tc.CONST, tc.NOTE_COUNT]
    first = table.rows[0]
    assert first.ref == tc.chart_ref(titles[0], "MAS")
    assert first.cell(tc.DIFFICULTY).raw == "MASTER" and first.cell(tc.DIFFICULTY).text == "MAS"
    song = songdb.song_by_title(titles[0])
    assert song is not None
    chart = song.charts["MAS"]
    assert first.cell(tc.CONST).raw == chart.const
    assert first.cell(tc.NOTE_COUNT).raw == chart.note_count
    assert first.cell("메모").raw == "메모 있음"  # 원래 행의 다른 칸은 그대로 옮긴다
    assert result.data == {"count": 2, "difficulty": "MAS", "expanded": 2}


async def test_charts_of_drops_songs_without_that_difficulty() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    has = _titles_with(songdb, "ULT", want=True, count=1)
    lacks = _titles_with(songdb, "ULT", want=False, count=2)
    difficulty = await _resolve_difficulty("ULT")

    result = await skill.charts_of(_ctx_with_songdb(songdb), table=_song_table([*has, *lacks]), difficulty=difficulty)

    assert result.error is None and result.table is not None
    assert _titles(result.table) == has
    assert result.table.steps[-2:] == ("ULT로 펼침", "ULT 없는 곡 2개 뺌")


async def test_charts_of_errors_when_no_song_has_the_difficulty() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    lacks = _titles_with(songdb, "ULT", want=False, count=2)
    difficulty = await _resolve_difficulty("ULT")

    result = await skill.charts_of(_ctx_with_songdb(songdb), table=_song_table(lacks), difficulty=difficulty)

    assert result.table is None
    assert result.error is not None and "ULT" in result.error


async def test_charts_of_leaves_chart_rows_alone() -> None:
    from core.tables import TableBuilder

    skill = _load_skill()
    songdb = SongDbService()
    title = _titles_with(songdb, "MAS", want=True, count=1)[0]
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.row(tc.chart_ref(title, "EXP"), {tc.SONG: tc.song_cell(title), tc.DIFFICULTY: tc.difficulty_cell("EXP")})
    source = builder.build()

    result = await skill.charts_of(_ctx_with_songdb(songdb), table=source)

    assert result.table is not None
    assert result.table.rows[0].ref == tc.chart_ref(title, "EXP")
    assert result.table.steps == ("펼칠 곡 단위 행 없음",)


async def _resolve_difficulty(name: str) -> object:
    from services.chunithm.terms import TermsService

    return await TermsService().resolve_difficulty(name, _CTX_NO_GUILD)
