# skills/notes/tests/test_chunithm_notes.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.tables import render_text
from core.tool_types import ToolContext
from services.chunithm.songdb import SongDbService
from services.chunithm.terms import ResolvedDifficulty

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_NOTES_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_notes_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_NOTES_SQL.read_text())
    await conn.executescript(_NOTES_SQL.with_name("002_chart_type.sql").read_text())
    try:
        yield conn
    finally:
        await conn.close()


def _ctx(*, player_id: str = "p1", db: aiosqlite.Connection, songdb: SongDbService | None = None) -> ToolContext:
    services: dict[str, object] = {"songdb": songdb or SongDbService()}
    return ToolContext(player_id=player_id, tags=frozenset(), services=services, db=db, event_bus=EventBus())


async def _song(songdb: SongDbService, raw: str, ctx: ToolContext) -> Any:
    return await songdb.resolve(raw, ctx)


def _mas() -> ResolvedDifficulty:
    return ResolvedDifficulty(canonical="MASTER")


# ---------------------------------------------------------------- Step 2: set_note


async def test_set_note_without_difficulty_stores_whole_song_note(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    result = await skill.set_note(ctx, song, "후반 패턴 주의")
    assert result.error is None
    assert result.data["difficulty"] == ""

    row = await (await db.execute(
        "SELECT body, difficulty, public FROM notes_notes WHERE player_id = ? AND song_title = ?",
        ("p1", song.title),
    )).fetchone()
    assert row is not None
    assert row["body"] == "후반 패턴 주의"
    assert row["difficulty"] == ""
    assert row["public"] == 0


async def test_set_note_with_difficulty_stores_short_code(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    result = await skill.set_note(ctx, song, "라스트 주의", difficulty=_mas())
    assert result.error is None
    assert result.data["difficulty"] == "MAS"


async def test_set_note_overwrites_existing_note(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    await skill.set_note(ctx, song, "첫 메모")
    result = await skill.set_note(ctx, song, "덮어쓴 메모")
    assert result.error is None
    assert result.data["body"] == "덮어쓴 메모"

    rows = list(await (await db.execute("SELECT body FROM notes_notes WHERE player_id = ?", ("p1",))).fetchall())
    assert len(rows) == 1
    assert rows[0]["body"] == "덮어쓴 메모"


async def test_set_note_append_joins_with_newline(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    await skill.set_note(ctx, song, "첫 줄")
    result = await skill.set_note(ctx, song, "둘째 줄", append=True)
    assert result.error is None
    assert result.data["body"] == "첫 줄\n둘째 줄"


async def test_set_note_without_public_keeps_existing_visibility(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """I9: 공개 메모에 public을 다시 말하지 않고 덧붙이거나 덮어써도 공개가 유지된다."""
    songdb = SongDbService()
    ctx = _ctx(db=db, songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    await skill.set_note(ctx, song, "첫 줄", public=True)
    appended = await skill.set_note(ctx, song, "둘째 줄", append=True)
    assert appended.data["public"] is True
    overwritten = await skill.set_note(ctx, song, "새로 씀")
    assert overwritten.data["public"] is True
    row = await (await db.execute("SELECT public FROM notes_notes WHERE player_id = 'p1'")).fetchone()
    assert row is not None and row["public"] == 1

    made_private = await skill.set_note(ctx, song, "이제 비공개", public=False)
    assert made_private.data["public"] is False


async def test_set_note_new_note_without_public_is_private(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    result = await skill.set_note(ctx, song, "메모")
    assert result.data["public"] is False


# ---------------------------------------------------------------- Step 3: get_note


async def test_get_note_returns_own_private_note(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    await skill.set_note(ctx, song, "내 메모", public=False)

    result = await skill.get_note(ctx, song)
    assert result.error is None
    assert result.data["mine"] is not None
    assert result.data["mine"]["body"] == "내 메모"
    assert result.data["others"] == []


async def test_get_note_hides_private_note_from_other_player(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    owner_ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", owner_ctx)
    await skill.set_note(owner_ctx, song, "비공개 메모", public=False)

    viewer_ctx = _ctx(db=db, player_id="p2", songdb=songdb)
    result = await skill.get_note(viewer_ctx, song)
    assert result.error is None
    assert result.data["mine"] is None
    assert result.data["others"] == []


async def test_get_note_shows_public_note_to_other_player(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    owner_ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", owner_ctx)
    await skill.set_note(owner_ctx, song, "공개 메모", public=True)

    viewer_ctx = _ctx(db=db, player_id="p2", songdb=songdb)
    result = await skill.get_note(viewer_ctx, song)
    assert result.error is None
    assert result.data["mine"] is None
    assert len(result.data["others"]) == 1
    assert result.data["others"][0]["player_id"] == "p1"
    assert result.data["others"][0]["body"] == "공개 메모"


async def test_get_note_limits_others_to_three_with_more_flag(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    for i in range(4):
        other_ctx = _ctx(db=db, player_id=f"other{i}", songdb=songdb)
        await skill.set_note(other_ctx, song, f"공개 메모 {i}", public=True)

    result = await skill.get_note(ctx, song)
    assert result.error is None
    assert len(result.data["others"]) == 3
    assert result.data["others_total"] == 4
    assert result.data["has_more"] is True


# ---------------------------------------------------------------- Step 4: list_notes/delete


async def test_list_notes_returns_only_own_notes(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song_a = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    await skill.set_note(ctx, song_a, "메모1")

    other_ctx = _ctx(db=db, player_id="p2", songdb=songdb)
    await skill.set_note(other_ctx, song_a, "남의 메모")

    result = await skill.list_notes(ctx)
    assert result.error is None
    assert len(result.data["notes"]) == 1
    assert result.data["notes"][0]["body"] == "메모1"
    assert result.table is not None and result.table.subject == "p1" and result.table.row_count == 1


async def test_list_notes_empty_is_a_message(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.list_notes(_ctx(db=db, player_id="p1"))

    assert result.table is None and result.display is not None and result.display.content == "메모가 없어."
    assert result.data["notes"] == []


async def test_whole_song_note_and_per_difficulty_note_coexist(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """같은 곡에 난이도 없는(곡 전체) 메모와 특정 난이도(MAS) 메모를 따로 남겨도 서로
    덮어쓰지 않고 둘 다 남는지, list_notes/get_note가 둘을 구분해 보여주는지(설계서
    "테스트" 절: "난이도 없는 메모와 난이도별 메모가 함께 있을 때의 표시")."""
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    whole_song = await skill.set_note(ctx, song, "곡 전체 메모")
    per_difficulty = await skill.set_note(ctx, song, "마스 전용 메모", difficulty=_mas())
    assert whole_song.error is None
    assert per_difficulty.error is None

    # list_notes: 같은 곡이라도 난이도가 다른 두 행이 각각 남아 있어야 한다(한쪽이
    # 다른 쪽을 덮어쓰지 않음).
    listed = await skill.list_notes(ctx)
    assert listed.error is None
    by_difficulty = {n["difficulty"]: n["body"] for n in listed.data["notes"]}
    assert by_difficulty == {None: "곡 전체 메모", "MAS": "마스 전용 메모"}
    # 표: 곡 전체 메모는 난이도가 빈 곡 단위 행이고, 두 행의 가리키는 것이 서로 다르다.
    assert listed.table is not None
    whole_row, mas_row = sorted(listed.table.rows, key=lambda r: r.cell("난이도").filled)
    assert not whole_row.cell("난이도").filled and mas_row.cell("난이도").text == "MAS"
    assert whole_row.ref != mas_row.ref and whole_row.ref.kind == "notes.note"
    lines = render_text(listed.table).content.splitlines()
    assert "B.B.K.K.B.K.K. MAS  (비공개)  마스 전용 메모" in "\n".join(lines)
    assert "B.B.K.K.B.K.K.  (비공개)  곡 전체 메모" in "\n".join(lines)
    assert "수정 시각" not in "\n".join(lines)

    # get_note: 난이도를 비우고 물으면 곡 전체 메모만, MAS로 물으면 MAS 메모만 나온다
    # (서로 섞이거나 한쪽이 안 보이면 안 된다).
    whole_result = await skill.get_note(ctx, song)
    assert whole_result.error is None
    assert whole_result.data["mine"]["body"] == "곡 전체 메모"

    mas_result = await skill.get_note(ctx, song, difficulty=_mas())
    assert mas_result.error is None
    assert mas_result.data["mine"]["body"] == "마스 전용 메모"


async def test_delete_removes_own_note(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    await skill.set_note(ctx, song, "지울 메모")

    result = await skill.delete(ctx, song)
    assert result.error is None
    rows = list(await (await db.execute("SELECT * FROM notes_notes WHERE player_id = ?", ("p1",))).fetchall())
    assert rows == []


async def test_delete_missing_note_errors(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)

    result = await skill.delete(ctx, song)
    assert result.error is not None


async def test_delete_does_not_remove_other_players_note(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    owner_ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", owner_ctx)
    await skill.set_note(owner_ctx, song, "p1의 메모")

    other_ctx = _ctx(db=db, player_id="p2", songdb=songdb)
    result = await skill.delete(other_ctx, song)
    assert result.error is not None
    rows = list(await (await db.execute("SELECT * FROM notes_notes WHERE player_id = ?", ("p1",))).fetchall())
    assert len(rows) == 1


# ---------------------------------------------------------------- Step 5: on_song_viewed


async def test_on_song_viewed_returns_none_without_any_note(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "곡A", "difficulty": None})
    assert section is None


async def test_on_song_viewed_returns_section_with_own_note(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    await skill.set_note(ctx, song, "내 메모")

    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": song.title, "difficulty": None})
    assert section is not None
    assert section.data["mine"][0]["body"] == "내 메모"


async def test_on_song_viewed_excludes_private_notes_from_other_player(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = SongDbService()
    owner_ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", owner_ctx)
    await skill.set_note(owner_ctx, song, "비공개 메모", public=False)

    viewer_ctx = _ctx(db=db, player_id="p2", songdb=songdb)
    section = await skill.on_song_viewed(
        viewer_ctx, {"player_id": "p2", "song_title": song.title, "difficulty": None}
    )
    assert section is None


async def test_on_song_viewed_includes_public_notes_from_other_player(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = SongDbService()
    owner_ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", owner_ctx)
    await skill.set_note(owner_ctx, song, "공개 메모", public=True)

    viewer_ctx = _ctx(db=db, player_id="p2", songdb=songdb)
    section = await skill.on_song_viewed(
        viewer_ctx, {"player_id": "p2", "song_title": song.title, "difficulty": None}
    )
    assert section is not None
    assert section.data["mine"] == []
    assert len(section.data["others"]) == 1
    assert section.data["others"][0]["player_id"] == "p1"


def _exp() -> ResolvedDifficulty:
    return ResolvedDifficulty(canonical="EXPERT")


async def test_on_song_viewed_whole_song_view_aggregates_all_difficulties(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """I3: 곡 전체 보기(difficulty None, `곡` 도구)는 그 곡의 모든 난이도 메모를 모은다."""
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    await skill.set_note(ctx, song, "곡 전체 메모")
    await skill.set_note(ctx, song, "마스 메모", difficulty=_mas())
    other_ctx = _ctx(db=db, player_id="p2", songdb=songdb)
    await skill.set_note(other_ctx, song, "남의 엑스퍼 메모", difficulty=_exp(), public=True)

    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": song.title, "difficulty": None})
    assert section is not None
    assert sorted(n["body"] for n in section.data["mine"]) == ["곡 전체 메모", "마스 메모"]
    assert [(o["difficulty"], o["body"]) for o in section.data["others"]] == [("EXP", "남의 엑스퍼 메모")]
    content = section.display.content or ""
    assert "마스 메모" in content and "곡 전체 메모" in content and "남의 엑스퍼 메모" in content


async def test_on_song_viewed_chart_view_shows_that_difficulty_and_whole_song_notes(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """I3: 채보 보기(difficulty "MAS", `chart` 도구)는 그 난이도 메모와 곡 전체 메모만 보인다."""
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    await skill.set_note(ctx, song, "곡 전체 메모")
    await skill.set_note(ctx, song, "마스 메모", difficulty=_mas())
    await skill.set_note(ctx, song, "엑스퍼 메모", difficulty=_exp())

    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": song.title, "difficulty": "MAS"})
    assert section is not None
    assert sorted(n["body"] for n in section.data["mine"]) == ["곡 전체 메모", "마스 메모"]


# ---------------------------------------------------------------- Step 6: on_plan_requested


async def test_on_plan_requested_returns_none_without_notes(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    section = await skill.on_plan_requested(ctx, {"player_id": "p1"})
    assert section is None


async def test_on_plan_requested_returns_my_notes_via_event_bus(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    song = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    await skill.set_note(ctx, song, "플레이 계획에 쓸 메모")

    bus = EventBus()
    bus.register_gather(
        "chunithm.plan.requested", owner_skill="notes", fn=skill.on_plan_requested,
        skill_services=["songdb", "players", "db"],
    )
    bus.bind_dispatch(services={"songdb": songdb}, tag_provider=_NoTags(), settings=None, db=db)

    sections = await bus.emit_gather("chunithm.plan.requested", {"player_id": "p1"}, player_id="p1", guild_id=None)
    assert len(sections) == 1
    notes = sections[0].data["notes"]
    assert notes[0]["song_title"] == song.title
    assert notes[0]["body"] == "플레이 계획에 쓸 메모"


class _NoTags:
    async def tags_for(self, player_id: str) -> set[str]:
        return set()


# ---------------------------------------------------------------- add_notes


def _title_table(*rows: tuple[str, str | None]):
    from core.tables import TableBuilder
    from services.game import table_columns as tc

    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.step("테스트 표")
    for title, difficulty in rows:
        ref = tc.chart_ref(title, difficulty) if difficulty else tc.song_ref(title)
        builder.row(ref, {tc.SONG: tc.song_cell(title), tc.DIFFICULTY: tc.difficulty_cell(difficulty)})
    return builder.build()


async def test_add_notes_prefers_chart_note_then_whole_song_note_and_marks_missing(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    both = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    whole_only = await _song(songdb, "Cthugha", ctx)
    await skill.set_note(ctx, both, "곡 전체")
    await skill.set_note(ctx, both, "마스 전용", difficulty=_mas())
    await skill.set_note(ctx, whole_only, "이 곡 전체")
    await skill.set_note(_ctx(db=db, player_id="p2", songdb=songdb), both, "남의 메모")
    table = _title_table(
        (both.title, "MAS"), (both.title, "EXP"), (both.title, None), (whole_only.title, "MAS"), ("메모 없는 곡", "MAS")
    )

    result = await skill.add_notes(ctx, table=table)

    assert result.error is None and result.table is not None
    assert result.table.steps == ("테스트 표", "내 메모")
    bodies = [(r.cell("내 메모").raw, r.cell("내 메모").text) for r in result.table.rows]
    assert bodies == [
        ("마스 전용", "마스 전용"), ("곡 전체", "곡 전체"), ("곡 전체", "곡 전체"),
        ("이 곡 전체", "이 곡 전체"), (None, "메모 없음"),
    ]
    assert result.data == {"count": 5, "with_note": 4}


async def test_delete_notes_removes_exact_notes_only_and_counts_missing(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = SongDbService()
    ctx = _ctx(db=db, player_id="p1", songdb=songdb)
    both = await _song(songdb, "B.B.K.K.B.K.K.", ctx)
    other = await _song(songdb, "Cthugha", ctx)
    await skill.set_note(ctx, both, "곡 전체")
    await skill.set_note(ctx, both, "마스 전용", difficulty=_mas())
    await skill.set_note(ctx, other, "남길 메모")
    await skill.set_note(_ctx(db=db, player_id="p2", songdb=songdb), both, "남의 메모", difficulty=_mas())
    # 채보 행(EXP)에는 그 채보 메모가 없다 — 곡 전체 메모를 대신 지우지 않는다. 마스 행과 곡 단위 행은 지운다.
    table = _title_table((both.title, "MAS"), (both.title, "EXP"), (both.title, None), (both.title, "MAS"))

    result = await skill.delete_notes(ctx, table=table)

    assert result.error is None
    assert result.data["deleted_count"] == 2 and result.data["missing"] == 1
    assert "1개는 메모가 없어서 뺐어" in result.display.content
    left = {
        (r["player_id"], r["song_title"], r["difficulty"])
        for r in await (await db.execute("SELECT player_id, song_title, difficulty FROM notes_notes")).fetchall()
    }
    assert left == {("p1", other.title, ""), ("p2", both.title, "MAS")}  # 남의 메모는 그대로


async def test_delete_notes_without_any_note_errors(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    result = await skill.delete_notes(ctx, table=_title_table(("메모 없는 곡", "MAS")))
    assert result.error is not None


def test_delete_notes_always_asks_for_confirmation(skill: ModuleType) -> None:
    assert skill.delete_notes.__yuni_tool__.confirm == "always"
