# skills/chunithm-notes/tests/test_chunithm_notes.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
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
    assert section.data["mine"]["body"] == "내 메모"


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
    assert section.data["mine"] is None
    assert len(section.data["others"]) == 1
    assert section.data["others"][0]["player_id"] == "p1"


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
        "chunithm.plan.requested", owner_skill="chunithm-notes", fn=skill.on_plan_requested,
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
