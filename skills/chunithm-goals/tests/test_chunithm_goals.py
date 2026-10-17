# skills/chunithm-goals/tests/test_chunithm_goals.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.executor import ToolExecutor
from core.permissions import InMemoryTagProvider
from core.resolvers import ResolverRegistry
from core.tool_types import ToolContext
from services.chunithm.records import (
    ChartUpdate,
    PbRow,
    RatingDeltas,
    SessionInfo,
    SyncDiff,
)
from services.chunithm.records_calc import remaining_to_target
from services.common.players import PlayersService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE_PLAYERS_SQL = Path("migrations/core/001_players.sql").read_text()
_CORE_PLAYER_TAGS_SQL = Path("migrations/core/002_player_tags.sql").read_text()
_GOALS_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_goals_skill_under_test", _SKILL_PY)
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
    await conn.executescript(_CORE_PLAYERS_SQL)
    await conn.executescript(_CORE_PLAYER_TAGS_SQL)
    await conn.executescript(_GOALS_SQL.read_text())
    try:
        yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------- 가짜 서비스


def _pb(title: str, difficulty: str, score: int, rank: str, const: float | None, rating: str | None,
        *, clear: str = "CLEAR", combo: str = "NONE", chain: str = "NONE") -> PbRow:
    return PbRow(
        song_title=title, difficulty=difficulty, score=score, rank=rank, clear_lamp=clear,
        combo_lamp=combo, chain_lamp=chain, const=const,
        rating=Decimal(rating) if rating is not None else None, session_id=None,
        updated_at="2026-10-16T12:00:00+00:00",
    )


@dataclass(slots=True, kw_only=True)
class _FakeRecords:
    pbs: dict[str, list[PbRow]] = field(default_factory=dict)
    deltas: dict[str, RatingDeltas] = field(default_factory=dict)
    overpower: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    frames: dict[str, tuple[list[PbRow], list[PbRow]]] = field(default_factory=dict)
    diffs: dict[str, SyncDiff] = field(default_factory=dict)

    async def all_pb_rows(self, player_id: str) -> list[PbRow]:
        return list(self.pbs.get(player_id, []))

    async def pb_row(self, player_id: str, *, song_title: str, difficulty: str) -> PbRow | None:
        for row in self.pbs.get(player_id, []):
            if (row.song_title, row.difficulty) == (song_title, difficulty):
                return row
        return None

    async def rating_deltas(self, player_id: str) -> RatingDeltas | None:
        return self.deltas.get(player_id)

    async def latest_overpower(self, player_id: str) -> tuple[float | None, float | None] | None:
        return self.overpower.get(player_id)

    async def current_frames(self, player_id: str) -> tuple[list[PbRow], list[PbRow]]:
        return self.frames.get(player_id, ([], []))

    async def sync_diff(self, sync_id: str) -> SyncDiff | None:
        return self.diffs.get(sync_id)

    @staticmethod
    def remaining_to_target(note_count: int | None, current_score: int, target_score: int) -> tuple[int, Decimal] | None:
        return remaining_to_target(note_count, current_score, target_score)


class _Hit:
    def __init__(self, title: str) -> None:
        self.song = type("S", (), {"title": title})()


@dataclass(slots=True, kw_only=True)
class _FakeSongDb:
    consts: dict[tuple[str, str], float] = field(default_factory=dict)
    note_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    new_songs: set[str] = field(default_factory=set)
    const_range_hits: list[str] = field(default_factory=list)  # 제목만
    level_hits: list[str] = field(default_factory=list)
    version_hits: list[str] = field(default_factory=list)

    def const_for(self, title: str, difficulty: str) -> float | None:
        return self.consts.get((title, difficulty))

    def note_count_for(self, title: str, difficulty: str) -> int | None:
        return self.note_counts.get((title, difficulty))

    def is_new_for(self, title: str) -> bool:
        return title in self.new_songs

    def find_by_const(self, *, min_const: float, max_const: float, difficulty: str) -> list[Any]:
        return [_Hit(title) for title in self.const_range_hits]

    def find_by_level(self, *, level: str, difficulty: str) -> list[Any]:
        return [_Hit(title) for title in self.level_hits]

    def find_by_version(self, *, version: str, difficulty: str | None = None) -> list[Any]:
        return [_Hit(title) for title in self.version_hits]


def _ctx(
    *, player_id: str = "p1", tags: frozenset[str] = frozenset(), db: aiosqlite.Connection,
    records: _FakeRecords | None = None, songdb: _FakeSongDb | None = None,
    players: PlayersService | None = None,
) -> ToolContext:
    services: dict[str, object] = {
        "records": records or _FakeRecords(), "songdb": songdb or _FakeSongDb(),
        "players": players or PlayersService(db),
    }
    return ToolContext(player_id=player_id, tags=tags, services=services, db=db, event_bus=EventBus())


_SYNCED = frozenset({"chunithm-synced"})


# ---------------------------------------------------------------- Step 2: set() 실패/성공


async def test_set_rating_requires_synced_tag(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    assert result.error is not None
    assert "동기화" in result.error


async def test_set_song_goal_allowed_without_sync(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(
        ctx, skill.GoalKind.SONG, song_title="곡A", difficulty="MAS", target_value=1_007_500
    )
    assert result.error is None
    assert result.data["kind"] == "song"


async def test_set_freeform_goal_allowed_without_sync(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="손배치 연습 주 2회")
    assert result.error is None
    assert result.data["kind"] == "freeform"


async def test_set_rating_goal_once_per_player(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(deltas={"p1": RatingDeltas(
        computed_rating=Decimal("16.80"), best30_average=Decimal("17.0"), new20_average=Decimal("16.0"),
        has_previous=False, previous_computed_rating=None, previous_best30_average=None, previous_new20_average=None,
    )})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    first = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    assert first.error is None

    second = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.5)
    assert second.error is not None
    assert "목표 수정" in second.error


async def test_set_rating_goal_without_sync_history_errors(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, records=_FakeRecords())
    result = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    assert result.error is not None


async def test_set_overpower_folder_scope_not_supported(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(overpower={"p1": (90.0, 95.0)})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    result = await skill.set_goal(
        ctx, skill.GoalKind.OVERPOWER, target_value=99.0, overpower_scope=skill.OverpowerScope.FOLDER
    )
    assert result.error is not None
    assert "폴더별 오버파워" in result.error


async def test_set_overpower_goal_succeeds(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(overpower={"p1": (90.0, 95.0)})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    result = await skill.set_goal(ctx, skill.GoalKind.OVERPOWER, target_value=99.0)
    assert result.error is None
    assert result.data["target_ref"] == {"mode": "percentage"}


async def test_set_folder_goal_succeeds_and_counts_total(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡A", "곡B", "곡C"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3, folder_difficulty="MAS",
        folder_condition=skill.FolderCondition.RANK, folder_rank="SSS",
    )
    assert result.error is None
    assert result.data["target_value"] == 3.0


async def test_set_folder_goal_no_matching_charts_errors(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, songdb=_FakeSongDb())
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3, folder_difficulty="MAS",
    )
    assert result.error is not None


async def test_set_folder_goal_by_level_succeeds_and_counts_total(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(level_hits=["곡A", "곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_level="14+", folder_difficulty="MAS",
        folder_condition=skill.FolderCondition.CLEARED,
    )
    assert result.error is None
    assert result.data["target_value"] == 2.0
    assert result.data["target_ref"] == {
        "selector": "level", "level": "14+", "difficulty": "MAS", "condition": "cleared", "rank": None,
    }


async def test_set_folder_goal_by_version_succeeds_and_counts_total(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(version_hits=["곡A", "곡B", "곡C"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_version="LUMINOUS", folder_difficulty="MAS",
        folder_condition=skill.FolderCondition.CLEARED,
    )
    assert result.error is None
    assert result.data["target_value"] == 3.0
    assert result.data["target_ref"]["selector"] == "version"
    assert result.data["target_ref"]["version"] == "LUMINOUS"


async def test_set_folder_goal_rejects_more_than_one_selector(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡A"], level_hits=["곡A"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3, folder_level="14+",
        folder_difficulty="MAS",
    )
    assert result.error is not None


async def test_set_folder_goal_rejects_no_selector(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, songdb=_FakeSongDb())
    result = await skill.set_goal(ctx, skill.GoalKind.FOLDER, folder_difficulty="MAS")
    assert result.error is not None


async def test_on_sync_completed_folder_goal_by_level_progress(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(level_hits=["곡A", "곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    created = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_level="14+", folder_difficulty="MAS",
        folder_condition=skill.FolderCondition.CLEARED,
    )
    goal_id = created.data["goal_id"]

    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 14.5, "14.0", clear="CLEAR")]})
    ctx2 = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)
    await skill.on_sync_completed(ctx2, {"player_id": "p1", "sync_id": "sync1"})

    history = await skill.models.list_progress(db, goal_id)
    assert len(history) == 1
    assert history[0].value == 1.0  # 곡A만 매치, 곡B는 아직 플레이 기록 없음


async def test_on_sync_completed_folder_goal_by_version_progress(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(version_hits=["곡A", "곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    created = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_version="LUMINOUS", folder_difficulty="MAS",
        folder_condition=skill.FolderCondition.CLEARED,
    )
    goal_id = created.data["goal_id"]

    records = _FakeRecords(pbs={"p1": [
        _pb("곡A", "MAS", 1_000_000, "SS", 14.5, "14.0", clear="CLEAR"),
        _pb("곡B", "MAS", 1_000_000, "SS", 14.5, "14.0", clear="CLEAR"),
    ]})
    ctx2 = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)
    await skill.on_sync_completed(ctx2, {"player_id": "p1", "sync_id": "sync1"})

    history = await skill.models.list_progress(db, goal_id)
    assert len(history) == 1
    assert history[0].value == 2.0


async def test_set_song_goal_only_one_per_chart(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    first = await skill.set_goal(ctx, skill.GoalKind.SONG, song_title="곡A", difficulty="MAS", target_value=1_000_000)
    assert first.error is None
    second = await skill.set_goal(ctx, skill.GoalKind.SONG, song_title="곡A", difficulty="MAS", target_value=1_005_000)
    assert second.error is not None


# ---------------------------------------------------------------- Step 3: list/update/progress/delete


async def test_list_returns_own_goals(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    me = await players.create_player()
    ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    await skill.set_goal(ctx, skill.GoalKind.SONG, song_title="곡A", difficulty="MAS", target_value=1_000_000)
    result = await skill.list_goals(ctx)
    assert result.error is None
    assert len(result.data["goals"]) == 1


async def test_list_for_other_player_only_shows_public_goals(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    other = await players.create_player()
    await players.set_visibility(other.id, "goals_public", True)
    other = await players.get_player(other.id)
    assert other is not None

    other_ctx = _ctx(db=db, player_id=other.id, tags=frozenset(), players=players)
    await skill.set_goal(other_ctx, skill.GoalKind.SONG, song_title="곡A", difficulty="MAS", target_value=1_000_000, public=False)
    await skill.set_goal(other_ctx, skill.GoalKind.SONG, song_title="곡B", difficulty="MAS", target_value=1_000_000, public=True)

    me = await players.create_player()
    me_ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    result = await skill.list_goals(me_ctx, player=other)
    assert result.error is None
    assert len(result.data["goals"]) == 1
    assert result.data["goals"][0]["target_ref"]["song_title"] == "곡B"


async def test_list_for_private_other_player_errors(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    other = await players.create_player()
    me = await players.create_player()
    me_ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    result = await skill.list_goals(me_ctx, player=other)
    assert result.error is not None


async def test_update_changes_status_and_due_at(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습")
    goal_id = created.data["goal_id"]

    result = await skill.update(ctx, goal_id, status=skill.GoalStatus.STOPPED, due_at="2026-12-31")
    assert result.error is None
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "stopped"
    assert goal.due_at == "2026-12-31"


async def test_update_rejects_other_players_goal(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1", tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습")
    goal_id = created.data["goal_id"]

    other_ctx = _ctx(db=db, player_id="p2", tags=frozenset())
    result = await skill.update(other_ctx, goal_id, status=skill.GoalStatus.STOPPED)
    assert result.error is not None


async def test_progress_shows_history(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.SONG, song_title="곡A", difficulty="MAS", target_value=1_000_000)
    goal_id = created.data["goal_id"]
    await skill.models.insert_progress(
        db, id="prog1", goal_id=goal_id, sync_id="sync1", recorded_at="2026-10-17T00:00:00+00:00",
        value=990_000.0, achievement_ratio=0.5,
    )
    result = await skill.progress(ctx, goal_id)
    assert result.error is None
    assert len(result.data["history"]) == 1
    assert result.data["history"][0]["value"] == 990_000.0


async def test_delete_removes_own_goals_only(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1", tags=frozenset())
    mine = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습")
    other_ctx = _ctx(db=db, player_id="p2", tags=frozenset())
    theirs = await skill.set_goal(other_ctx, skill.GoalKind.FREEFORM, description="연습2")

    result = await skill.delete(ctx, f"{mine.data['goal_id']},{theirs.data['goal_id']}")
    assert result.error is None
    assert result.data["deleted_count"] == 1
    assert await skill.models.fetch_goal(db, mine.data["goal_id"]) is None
    assert await skill.models.fetch_goal(db, theirs.data["goal_id"]) is not None


# ---------------------------------------------------------------- Step 4: todo_add/todo_bulk/todo_list/todo_done


async def test_todo_add_creates_song_goal(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.todo_add(ctx, "곡A", "MAS", 1_007_500)
    assert result.error is None
    assert result.data["kind"] == "song"


async def test_todo_add_rejects_already_met_target(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_008_000, "SSS", 15.0, "15.0")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.todo_add(ctx, "곡A", "MAS", 1_007_500)
    assert result.error is not None
    rows = await skill.models.list_goals_for_player(db, "p1", kind="song")
    assert rows == []


async def test_set_goal_song_rejects_already_met_target(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_008_000, "SSS", 15.0, "15.0")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.set_goal(
        ctx, skill.GoalKind.SONG, song_title="곡A", difficulty="MAS", target_value=1_007_500
    )
    assert result.error is not None


async def test_todo_add_allows_not_yet_met_target(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.todo_add(ctx, "곡A", "MAS", 1_007_500)
    assert result.error is None


async def test_todo_bulk_requires_sync_for_rank_condition(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.todo_bulk(ctx, 15.0, 15.3, "MAS", 1_007_500, rank="SSS")
    assert result.error is not None


async def test_todo_bulk_counts_targets_and_skips_already_met(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡A", "곡B", "곡C"])
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_009_000, "SSS+", 15.1, "15.1")]})
    ctx = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)

    result = await skill.todo_bulk(ctx, 15.0, 15.3, "MAS", 1_007_500, condition=skill.FolderCondition.RANK, rank="SSS")
    assert result.error is None
    # 곡A는 이미 SSS를 넘었으니 빠지고, 곡B/곡C만 투두로 들어간다 — confirm="bulk"로 보여줄 개수다.
    assert result.data["count"] == 2
    assert "곡A" not in result.data["song_titles"]


def _executor() -> ToolExecutor:
    return ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={})


async def test_todo_bulk_via_executor_with_one_target_runs_without_confirm(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    outcome = await _executor().invoke(
        skill.todo_bulk.__yuni_tool__, ctx,
        {"min_const": 15.0, "max_const": 15.3, "difficulty": "MAS", "target_score": 1_007_500, "rank": "SSS"},
    )
    assert outcome.needs_confirm is False
    assert outcome.result is not None and outcome.result.error is None
    assert outcome.result.data["count"] == 1
    assert len(await skill.models.list_goals_for_player(db, "p1", kind="song")) == 1


async def test_todo_bulk_via_executor_with_many_targets_needs_confirm_before_writing(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡B", "곡C"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    executor = _executor()
    outcome = await executor.invoke(
        skill.todo_bulk.__yuni_tool__, ctx,
        {"min_const": 15.0, "max_const": 15.3, "difficulty": "MAS", "target_score": 1_007_500, "rank": "SSS"},
    )
    assert outcome.needs_confirm is True
    assert outcome.confirm_token is not None
    assert outcome.preview is not None and outcome.preview.data["count"] == 2
    assert await skill.models.list_goals_for_player(db, "p1", kind="song") == []  # 아직 안 썼다

    result = await executor.confirm(outcome.confirm_token, confirmed=True, player_id="p1")
    assert result.error is None
    assert result.data["count"] == 2
    assert len(await skill.models.list_goals_for_player(db, "p1", kind="song")) == 2


async def test_delete_via_executor_single_goal_runs_without_confirm(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습")
    outcome = await _executor().invoke(skill.delete.__yuni_tool__, ctx, {"goal_ids": created.data["goal_id"]})
    assert outcome.needs_confirm is False
    assert outcome.result is not None and outcome.result.data["deleted_count"] == 1
    assert await skill.models.fetch_goal(db, created.data["goal_id"]) is None


async def test_delete_via_executor_many_goals_needs_confirm_before_deleting(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    a = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습1")
    b = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습2")
    executor = _executor()
    outcome = await executor.invoke(
        skill.delete.__yuni_tool__, ctx, {"goal_ids": f"{a.data['goal_id']},{b.data['goal_id']}"}
    )
    assert outcome.needs_confirm is True
    assert outcome.confirm_token is not None
    assert await skill.models.fetch_goal(db, a.data["goal_id"]) is not None  # 아직 안 지웠다

    result = await executor.confirm(outcome.confirm_token, confirmed=True, player_id="p1")
    assert result.data["deleted_count"] == 2
    assert await skill.models.fetch_goal(db, a.data["goal_id"]) is None
    assert await skill.models.fetch_goal(db, b.data["goal_id"]) is None


async def test_todo_list_sorts_by_remaining(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    songdb = _FakeSongDb(note_counts={("곡A", "MAS"): 1000}, consts={("곡A", "MAS"): 15.0})
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(ctx, "곡A", "MAS", 1_007_500)

    result = await skill.todo_list(ctx, sort="remaining")
    assert result.error is None
    assert len(result.data["todos"]) == 1
    assert result.data["todos"][0]["remaining_attacks"] is not None


async def test_todo_done_marks_achieved_and_emits_event(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, "곡A", "MAS", 1_007_500)
    goal_id = created.data["goal_id"]

    received: list[dict[str, Any]] = []

    async def _on_completed(_ctx: ToolContext, payload: dict[str, Any]) -> None:
        received.append(payload)

    ctx.event_bus.register_notify("chunithm.goal.completed", owner_skill="chunithm-goals", fn=_on_completed, skill_services=[])

    result = await skill.todo_done(ctx, goal_id)
    assert result.error is None
    await ctx.event_bus.drain()
    assert len(received) == 1
    assert received[0]["goal_id"] == goal_id
    assert received[0]["kind"] == "song"


# ---------------------------------------------------------------- Step 5: on_sync_completed(order=30)


async def test_on_sync_completed_auto_completes_song_goal(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, "곡A", "MAS", 1_007_500)
    goal_id = created.data["goal_id"]

    diff = SyncDiff(
        sync_id="sync1", sessions=[SessionInfo(id="s1", play_date="2026-10-17", estimated=False)],
        chart_updates=[
            ChartUpdate(
                song_title="곡A", difficulty="MAS", old_score=1_000_000, new_score=1_008_000,
                old_rank="SS", new_rank="SSS", old_rating=None, new_rating=None, session_id="s1",
                estimated_session=False, entered_frame=False,
            )
        ],
        unchanged_plays=[], frame_exits=[], previous_computed_rating=None,
        previous_best30_average=None, previous_new20_average=None,
    )
    records = _FakeRecords(diffs={"sync1": diff})
    ctx2 = _ctx(db=db, tags=frozenset(), records=records)

    section = await skill.on_sync_completed(ctx2, {"player_id": "p1", "sync_id": "sync1"})
    assert section.order == 30
    assert section.stage == "great"
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "achieved"


async def test_on_sync_completed_records_rating_goal_progress(skill: ModuleType, db: aiosqlite.Connection) -> None:
    deltas_first = RatingDeltas(
        computed_rating=Decimal("16.80"), best30_average=Decimal("17.0"), new20_average=Decimal("16.0"),
        has_previous=False, previous_computed_rating=None, previous_best30_average=None, previous_new20_average=None,
    )
    records = _FakeRecords(deltas={"p1": deltas_first})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    goal_id = created.data["goal_id"]

    deltas_second = RatingDeltas(
        computed_rating=Decimal("16.90"), best30_average=Decimal("17.1"), new20_average=Decimal("16.1"),
        has_previous=True, previous_computed_rating=Decimal("16.80"), previous_best30_average=Decimal("17.0"),
        previous_new20_average=Decimal("16.0"),
    )
    records.deltas["p1"] = deltas_second
    section = await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync2"})
    assert section.order == 30
    history = await skill.models.list_progress(db, goal_id)
    assert len(history) == 1
    assert history[0].value == pytest.approx(16.90)


async def test_on_sync_completed_rating_goal_achieved(skill: ModuleType, db: aiosqlite.Connection) -> None:
    deltas_first = RatingDeltas(
        computed_rating=Decimal("16.80"), best30_average=Decimal("17.0"), new20_average=Decimal("16.0"),
        has_previous=False, previous_computed_rating=None, previous_best30_average=None, previous_new20_average=None,
    )
    records = _FakeRecords(deltas={"p1": deltas_first})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    goal_id = created.data["goal_id"]

    records.deltas["p1"] = RatingDeltas(
        computed_rating=Decimal("17.00"), best30_average=Decimal("17.2"), new20_average=Decimal("16.6"),
        has_previous=True, previous_computed_rating=Decimal("16.80"), previous_best30_average=Decimal("17.0"),
        previous_new20_average=Decimal("16.0"),
    )
    section = await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync2"})
    assert section.stage == "great"
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "achieved"


async def test_on_sync_completed_folder_goal_progress(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡A", "곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    created = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3, folder_difficulty="MAS",
        folder_condition=skill.FolderCondition.CLEARED,
    )
    goal_id = created.data["goal_id"]

    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.1, "14.0", clear="CLEAR")]})
    ctx2 = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)
    await skill.on_sync_completed(ctx2, {"player_id": "p1", "sync_id": "sync1"})

    history = await skill.models.list_progress(db, goal_id)
    assert len(history) == 1
    assert history[0].value == 1.0


# ---------------------------------------------------------------- Step 6: on_song_viewed


async def test_on_song_viewed_returns_todo_status(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    songdb = _FakeSongDb(note_counts={("곡A", "MAS"): 1000})
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(ctx, "곡A", "MAS", 1_007_500)

    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "곡A", "difficulty": "MAS"})
    assert section is not None
    assert section.data["remaining_attacks"] is not None


async def test_on_song_viewed_returns_none_without_todo(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "곡B", "difficulty": "MAS"})
    assert section is None


# ---------------------------------------------------------------- Step 7: on_plan_requested (직접 emit_gather)


async def test_on_plan_requested_returns_nearest_todos_via_event_bus(skill: ModuleType, db: aiosqlite.Connection) -> None:
    bus = EventBus()
    records = _FakeRecords(pbs={"p1": [
        _pb("곡A", "MAS", 1_006_000, "SS+", 15.0, "14.0"),
        _pb("곡B", "MAS", 1_000_000, "SS", 15.0, "14.0"),
    ]})
    songdb = _FakeSongDb(note_counts={("곡A", "MAS"): 1000, ("곡B", "MAS"): 1000})
    setup_ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(setup_ctx, "곡A", "MAS", 1_007_500)
    await skill.todo_add(setup_ctx, "곡B", "MAS", 1_007_500)

    bus.register_gather(
        "chunithm.plan.requested", owner_skill="chunithm-goals", fn=skill.on_plan_requested,
        skill_services=["records", "songdb", "players", "db"],
    )
    bus.bind_dispatch(
        services={"records": records, "songdb": songdb, "players": PlayersService(db)},
        tag_provider=PlayersService(db), settings=None, db=db,
    )

    sections = await bus.emit_gather("chunithm.plan.requested", {"player_id": "p1"}, player_id="p1", guild_id=None)
    assert len(sections) == 1
    todos = sections[0].data["todos"]
    assert todos[0]["song_title"] == "곡A"  # 남은 어택이 더 적은 쪽이 먼저다


# ---------------------------------------------------------------- Step 8: 레이팅 목표의 예상 레이팅


async def test_progress_projects_rating_with_todos(skill: ModuleType, db: aiosqlite.Connection) -> None:
    deltas = RatingDeltas(
        computed_rating=Decimal("16.80"), best30_average=Decimal("17.0"), new20_average=Decimal("16.0"),
        has_previous=False, previous_computed_rating=None, previous_best30_average=None, previous_new20_average=None,
    )
    best_rows = [_pb(f"곡{i}", "MAS", 1_000_000, "SS", 15.0, "15.0") for i in range(30)]
    records = _FakeRecords(deltas={"p1": deltas}, frames={"p1": (best_rows, [])})
    songdb = _FakeSongDb(consts={("투두곡", "MAS"): 15.0})
    ctx = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)

    rating_goal = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    await skill.set_goal(ctx, skill.GoalKind.SONG, song_title="투두곡", difficulty="MAS", target_value=1_009_000)

    result = await skill.progress(ctx, rating_goal.data["goal_id"])
    assert result.error is None
    assert result.data["projected_rating"] is not None
