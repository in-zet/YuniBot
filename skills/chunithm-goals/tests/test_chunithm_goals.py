# skills/chunithm-goals/tests/test_chunithm_goals.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator, Awaitable, Callable
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
from core.tool_types import DifficultyRef, RankRef, ScoreRef, ToolContext
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.records import (
    ChartUpdate,
    PbRow,
    RatingDeltas,
    SessionInfo,
    SyncDiff,
    UnchangedPlay,
)
from services.chunithm.records_calc import remaining_to_target
from services.chunithm.songdb import ChartRecord, SongRecord
from services.chunithm.terms import ResolvedDifficulty, ResolvedRank, TermsService
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
_MAS = ResolvedDifficulty(canonical="MASTER")
_SSS = ResolvedRank(canonical="SSS", min_score=1_007_500)


def _chart(title: str, difficulty: str = "MAS", *, note_count: int | None = None, const: float | None = None) -> ResolvedChart:
    """ChartRef 해석 결과. 테스트는 해석기를 거치지 않고 해석된 값을 바로 넘긴다
    (chunithm-border/chunithm-song 테스트와 같은 방식)."""
    return ResolvedChart(
        song=SongRecord(title=title, aliases=(), artist=""),
        chart=ChartRecord(difficulty=difficulty, const=const, note_count=note_count),
    )


# ---------------------------------------------------------------- Step 2: set() 실패/성공


async def test_set_rating_requires_synced_tag(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    assert result.error is not None
    assert "동기화" in result.error


async def test_set_song_goal_allowed_without_sync(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(
        ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_007_500
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
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3, folder_difficulty=_MAS,
        folder_condition=skill.FolderCondition.RANK, folder_rank=_SSS,
    )
    assert result.error is None
    assert result.data["target_value"] == 3.0


async def test_set_folder_goal_no_matching_charts_errors(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, songdb=_FakeSongDb())
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3, folder_difficulty=_MAS,
    )
    assert result.error is not None


async def test_set_folder_goal_by_level_succeeds_and_counts_total(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(level_hits=["곡A", "곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_level="14+", folder_difficulty=_MAS,
        folder_condition=skill.FolderCondition.CLEARED,
    )
    assert result.error is None
    assert result.data["target_value"] == 2.0
    assert result.data["target_ref"] == {
        "selector": "level", "level": "14+", "difficulty": "MAS", "condition": "cleared", "rank": None,
        "rank_min_score": None,
    }


async def test_set_folder_goal_by_version_succeeds_and_counts_total(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(version_hits=["곡A", "곡B", "곡C"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_version="LUMINOUS", folder_difficulty=_MAS,
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
        folder_difficulty=_MAS,
    )
    assert result.error is not None


async def test_set_folder_goal_rejects_no_selector(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, songdb=_FakeSongDb())
    result = await skill.set_goal(ctx, skill.GoalKind.FOLDER, folder_difficulty=_MAS)
    assert result.error is not None


async def test_on_sync_completed_folder_goal_by_level_progress(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(level_hits=["곡A", "곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    created = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_level="14+", folder_difficulty=_MAS,
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
        ctx, skill.GoalKind.FOLDER, folder_version="LUMINOUS", folder_difficulty=_MAS,
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
    first = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000)
    assert first.error is None
    second = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_005_000)
    assert second.error is not None


# ---------------------------------------------------------------- Step 3: list/update/progress/delete


async def test_list_returns_own_goals(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    me = await players.create_player()
    ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000)
    result = await skill.list_goals(ctx)
    assert result.error is None
    assert len(result.data["goals"]) == 1


async def test_list_for_other_player_only_shows_public_goals(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """I4: 플레이어 단위 goals_public 플래그 없이도, 목표마다의 public으로 거른다."""
    players = PlayersService(db)
    other = await players.create_player()
    assert other.goals_public is False  # 아무도 이 값을 켜지 않는다 — 막는 데 쓰면 안 된다

    other_ctx = _ctx(db=db, player_id=other.id, tags=frozenset(), players=players)
    await skill.set_goal(other_ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000, public=False)
    await skill.set_goal(other_ctx, skill.GoalKind.SONG, chart=_chart("곡B"), target_score=1_000_000, public=True)

    me = await players.create_player()
    me_ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    result = await skill.list_goals(me_ctx, player=other)
    assert result.error is None
    assert len(result.data["goals"]) == 1
    assert result.data["goals"][0]["target_ref"]["song_title"] == "곡B"


async def test_list_own_goals_shows_private_and_public(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    me = await players.create_player()
    ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000, public=False)
    await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡B"), target_score=1_000_000, public=True)
    result = await skill.list_goals(ctx)
    assert len(result.data["goals"]) == 2


async def test_list_for_other_player_without_public_goals_is_empty(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    other = await players.create_player()
    other_ctx = _ctx(db=db, player_id=other.id, tags=frozenset(), players=players)
    await skill.set_goal(other_ctx, skill.GoalKind.FREEFORM, description="비밀 연습")
    me = await players.create_player()
    me_ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    result = await skill.list_goals(me_ctx, player=other)
    assert result.error is None
    assert result.data["goals"] == []


async def test_progress_of_other_players_goal_follows_goal_public_flag(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    other = await players.create_player()
    other_ctx = _ctx(db=db, player_id=other.id, tags=frozenset(), players=players)
    public = await skill.set_goal(other_ctx, skill.GoalKind.FREEFORM, description="공개", public=True)
    private = await skill.set_goal(other_ctx, skill.GoalKind.FREEFORM, description="비공개")
    me = await players.create_player()
    me_ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    assert (await skill.progress(me_ctx, public.data["goal_id"])).error is None
    assert (await skill.progress(me_ctx, private.data["goal_id"])).error is not None


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
    created = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000)
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
    result = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    assert result.error is None
    assert result.data["kind"] == "song"


async def test_todo_add_rejects_already_met_target(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_008_000, "SSS", 15.0, "15.0")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    assert result.error is not None
    rows = await skill.models.list_goals_for_player(db, "p1", kind="song")
    assert rows == []


async def test_set_goal_song_rejects_already_met_target(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_008_000, "SSS", 15.0, "15.0")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.set_goal(
        ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_007_500
    )
    assert result.error is not None


async def test_todo_add_allows_not_yet_met_target(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    assert result.error is None


async def test_todo_bulk_requires_sync_for_rank_condition(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.todo_bulk(ctx, 15.0, 15.3, _MAS, 1_007_500, rank=_SSS)
    assert result.error is not None


async def test_todo_bulk_counts_targets_and_skips_already_met(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡A", "곡B", "곡C"])
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_009_000, "SSS+", 15.1, "15.1")]})
    ctx = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)

    result = await skill.todo_bulk(ctx, 15.0, 15.3, _MAS, 1_007_500, condition=skill.FolderCondition.RANK, rank=_SSS)
    assert result.error is None
    # 곡A는 이미 SSS를 넘었으니 빠지고, 곡B/곡C만 투두로 들어간다 — confirm="bulk"로 보여줄 개수다.
    assert result.data["count"] == 2
    assert "곡A" not in result.data["song_titles"]


@dataclass(slots=True)
class _TermsAdapter:
    """core/bot.py의 _RankRefAdapter 등과 같은 일 — 진짜 TermsService 메서드 하나를 해석기로 감싼다."""

    fn: Callable[[str, ToolContext], Awaitable[Any]]

    async def resolve(self, raw: str, ctx: ToolContext) -> Any:
        return await self.fn(raw, ctx)


def _executor() -> ToolExecutor:
    terms = TermsService()
    resolvers = ResolverRegistry()
    resolvers.register(DifficultyRef, _TermsAdapter(terms.resolve_difficulty))
    resolvers.register(RankRef, _TermsAdapter(terms.resolve_rank))
    resolvers.register(ScoreRef, _TermsAdapter(terms.resolve_score))
    return ToolExecutor(resolvers=resolvers, tag_provider=InMemoryTagProvider(), tag_definitions={})


async def test_todo_bulk_via_executor_resolves_lowercase_rank_and_skips_already_met(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """C2: rank="sss"(소문자 은어)도 해석기가 SSS로 바꿔, 이미 SSS인 곡A는 빠진다."""
    songdb = _FakeSongDb(const_range_hits=["곡A", "곡B"])
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_008_000, "SSS", 15.1, "15.1")]})
    ctx = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)
    outcome = await _executor().invoke(
        skill.todo_bulk.__yuni_tool__, ctx,
        {"min_const": 15.0, "max_const": 15.3, "difficulty": "마스", "target_score": "SSS+", "rank": "sss"},
    )
    assert outcome.result is not None and outcome.result.error is None
    assert outcome.result.data["song_titles"] == ["곡B"]
    rows = await skill.models.list_goals_for_player(db, "p1", kind="song")
    assert [r.target_ref for r in rows] == [{"song_title": "곡B", "difficulty": "MAS"}]
    assert rows[0].target_value == 1_009_000.0  # "SSS+"를 해석기가 점수로 바꿨다


def test_all_justice_condition_checks_combo_lamp_not_chain_lamp(skill: ModuleType) -> None:
    """I1: 올 저스티스는 combo_lamp(ALL JUSTICE/AJC)다. FULL CHAIN(chain_lamp)은 AJ가 아니다."""
    full_chain_only = _pb("곡A", "MAS", 1_005_000, "SS+", 15.0, "15.0", combo="FULL COMBO", chain="FULL CHAIN")
    aj = _pb("곡B", "MAS", 1_009_000, "SSS+", 15.0, "15.0", combo="ALL JUSTICE")
    ajc = _pb("곡C", "MAS", 1_010_000, "SSS+", 15.0, "15.0", combo="AJC")
    cond = skill.FolderCondition.ALL_JUSTICE
    assert skill._row_matches_condition(full_chain_only, cond, None) is False
    assert skill._row_matches_condition(aj, cond, None) is True
    assert skill._row_matches_condition(ajc, cond, None) is True


async def test_todo_bulk_excludes_chart_that_already_meets_target_score(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """I7: 조건(풀콤보)은 못 채웠어도 이미 목표 점수(SSS)를 넘긴 채보는 투두로 넣지 않는다."""
    songdb = _FakeSongDb(const_range_hits=["곡A", "곡B"])
    records = _FakeRecords(pbs={"p1": [
        _pb("곡A", "MAS", 1_008_000, "SSS", 15.1, "15.1", combo="NONE"),
        _pb("곡B", "MAS", 1_000_000, "SS", 15.1, "14.1", combo="NONE"),
    ]})
    ctx = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)
    result = await skill.todo_bulk(
        ctx, 15.0, 15.3, _MAS, 1_007_500, condition=skill.FolderCondition.FULL_COMBO
    )
    assert result.error is None
    assert result.data["song_titles"] == ["곡B"]


async def test_todo_add_stores_canonical_chart_identity(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """C2: 해석된 채보의 정식 제목·난이도 코드로 저장한다(원문 "크로노 마스"가 아니라)."""
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.todo_add(ctx, _chart("Chrono Diver -PENDULUMs-", "MAS"), 1_007_500)
    assert result.error is None
    assert result.data["target_ref"] == {"song_title": "Chrono Diver -PENDULUMs-", "difficulty": "MAS"}


async def test_todo_bulk_via_executor_with_one_target_runs_without_confirm(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = _FakeSongDb(const_range_hits=["곡B"])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    outcome = await _executor().invoke(
        skill.todo_bulk.__yuni_tool__, ctx,
        {"min_const": 15.0, "max_const": 15.3, "difficulty": "마스", "target_score": "SSS", "rank": "sss"},
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
        {"min_const": 15.0, "max_const": 15.3, "difficulty": "마스", "target_score": "SSS", "rank": "sss"},
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
    await skill.todo_add(ctx, _chart("곡A"), 1_007_500)

    result = await skill.todo_list(ctx, sort=skill.TodoSort.REMAINING)
    assert result.error is None
    assert len(result.data["todos"]) == 1
    assert result.data["todos"][0]["remaining_attacks"] is not None


async def test_todo_done_marks_achieved_and_emits_event(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
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
    created = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
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
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3, folder_difficulty=_MAS,
        folder_condition=skill.FolderCondition.CLEARED,
    )
    goal_id = created.data["goal_id"]

    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.1, "14.0", clear="CLEAR")]})
    ctx2 = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)
    await skill.on_sync_completed(ctx2, {"player_id": "p1", "sync_id": "sync1"})

    history = await skill.models.list_progress(db, goal_id)
    assert len(history) == 1
    assert history[0].value == 1.0


def _diff(*, updates: list[ChartUpdate] | None = None, unchanged: list[UnchangedPlay] | None = None) -> SyncDiff:
    return SyncDiff(
        sync_id="sync1", sessions=[SessionInfo(id="s1", play_date="2026-10-17", estimated=False)],
        chart_updates=updates or [], unchanged_plays=unchanged or [], frame_exits=[],
        previous_computed_rating=None, previous_best30_average=None, previous_new20_average=None,
    )


async def test_on_sync_completed_returns_none_for_player_without_goals(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """I5: 목표가 하나도 없으면 이 스킬이 리포트에 낼 조각이 없다."""
    records = _FakeRecords(diffs={"sync1": _diff()})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    assert await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync1"}) is None


async def test_on_sync_completed_reports_approached_and_unchanged_song_goals(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """I5: 올랐지만 목표에 못 닿은 곡은 "접근", 쳤지만 기록이 그대로인 곡은 "미갱신"."""
    songdb = _FakeSongDb(note_counts={("곡C", "MAS"): 1000, ("곡D", "MAS"): 1000})
    records = _FakeRecords()
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(ctx, _chart("곡C"), 1_007_500)
    await skill.todo_add(ctx, _chart("곡D"), 1_007_500)

    records.diffs["sync1"] = _diff(
        updates=[ChartUpdate(
            song_title="곡C", difficulty="MAS", old_score=1_000_000, new_score=1_007_372, old_rank="SS",
            new_rank="SS+", old_rating=None, new_rating=None, session_id="s1", estimated_session=False,
            entered_frame=False,
        )],
        unchanged=[UnchangedPlay(song_title="곡D", difficulty="MAS", play_count=2, current_score=1_006_950, session_id="s1")],
    )
    section = await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync1"})
    assert section is not None
    content = section.display.content or ""
    expected = remaining_to_target(1000, 1_007_372, 1_007_500)
    assert expected is not None
    assert "곡별 목표" in content
    assert f"접근  곡C MAS  1,007,500점까지 128점 ({expected[1]}어택, 현재 1,007,372)" in content
    assert "미갱신  곡D MAS  플레이 2회, 최고 기록 유지 (현재 1,006,950)" in content
    assert section.data["approached"][0]["remaining_score"] == 128
    assert section.data["unchanged"][0]["play_count"] == 2
    assert section.stage is None


async def test_on_sync_completed_shows_rating_goal_remaining_and_change(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """I5: 최종 목표의 현재값, 남은 레이팅, 이번 동기화 전진량, 달성률."""
    records = _FakeRecords(deltas={"p1": _deltas("16.80")}, diffs={"sync1": _diff()})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    records.deltas["p1"] = _deltas("16.90", "16.80")

    section = await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync1"})
    assert section is not None
    content = section.display.content or ""
    assert "최종 목표 17.00" in content
    assert "현재 16.90 · 남은 레이팅 0.10 · 이번 동기화 +0.10 · 달성률 50.00%" in content
    assert section.data["rating_goal"]["remaining"] == "0.10"
    assert section.data["rating_goal"]["change"] == "0.10"


# ---------------------------------------------------------------- Step 6: on_song_viewed


async def test_on_song_viewed_returns_todo_status(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    songdb = _FakeSongDb(note_counts={("곡A", "MAS"): 1000})
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(ctx, _chart("곡A"), 1_007_500)

    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "곡A", "difficulty": "MAS"})
    assert section is not None
    assert len(section.data["todos"]) == 1
    assert section.data["todos"][0]["remaining_attacks"] is not None


async def test_on_song_viewed_chart_view_only_shows_that_difficulty(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    await skill.todo_add(ctx, _chart("곡A", "MAS"), 1_007_500)
    await skill.todo_add(ctx, _chart("곡A", "EXP"), 1_009_000)
    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "곡A", "difficulty": "EXP"})
    assert section is not None
    assert [t["difficulty"] for t in section.data["todos"]] == ["EXP"]


async def test_on_song_viewed_whole_song_view_aggregates_all_difficulties(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """I3: `곡` 도구는 difficulty None으로 낸다 — 그 곡의 모든 난이도 투두를 보여준다."""
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    songdb = _FakeSongDb(note_counts={("곡A", "MAS"): 1000})
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(ctx, _chart("곡A", "MAS"), 1_007_500)
    await skill.todo_add(ctx, _chart("곡A", "EXP"), 1_009_000)
    await skill.todo_add(ctx, _chart("곡B", "MAS"), 1_009_000)

    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "곡A", "difficulty": None})
    assert section is not None
    assert sorted(t["difficulty"] for t in section.data["todos"]) == ["EXP", "MAS"]
    content = section.display.content or ""
    assert "MAS" in content and "EXP" in content and "곡B" not in content
    exp = next(t for t in section.data["todos"] if t["difficulty"] == "EXP")
    assert exp["remaining_attacks"] is None  # EXP 노트 수를 모른다 → 계산 불가
    assert "계산 불가" in content


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
    await skill.todo_add(setup_ctx, _chart("곡A"), 1_007_500)
    await skill.todo_add(setup_ctx, _chart("곡B"), 1_007_500)

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


def _deltas(current: str, previous: str | None = None) -> RatingDeltas:
    return RatingDeltas(
        computed_rating=Decimal(current), best30_average=Decimal("17.0"), new20_average=Decimal("16.0"),
        has_previous=previous is not None,
        previous_computed_rating=Decimal(previous) if previous is not None else None,
        previous_best30_average=None, previous_new20_average=None,
    )


async def test_progress_projects_rating_with_todos(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """I2: 프레임 안의 채보(15.00)를 15.50으로 올리는 투두는 그 항목을 바꿔 +0.50만 더한다.
    (15.50 + 13.00 * 29) / 50 = 7.85."""
    best_rows = [
        _pb("높은곡", "MAS", 1_005_000, "SS+", 13.5, "15.00"),
        *[_pb(f"곡{i}", "MAS", 1_000_000, "SS", 11.0, "13.00") for i in range(29)],
    ]
    records = _FakeRecords(deltas={"p1": _deltas("7.84")}, frames={"p1": (best_rows, [])}, pbs={"p1": best_rows})
    songdb = _FakeSongDb(consts={("높은곡", "MAS"): 13.5})
    ctx = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)

    rating_goal = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=8.0)
    todo = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("높은곡"), target_score=1_007_500)
    assert todo.error is None

    result = await skill.progress(ctx, rating_goal.data["goal_id"])
    assert result.error is None
    assert result.data["projected_rating"] == "7.85"
    assert result.display is not None and "예상 레이팅 7.85" in (result.display.content or "")


# ---------------------------------------------------------------- I6: 달성률·필요량·페이스


async def test_list_goals_shows_rating_goal_achievement_ratio_and_remaining(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    players = PlayersService(db)
    me = await players.create_player()
    records = _FakeRecords(deltas={me.id: _deltas("16.80")})
    ctx = _ctx(db=db, player_id=me.id, tags=_SYNCED, records=records, players=players)
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    records.deltas[me.id] = _deltas("16.90", "16.80")

    result = await skill.list_goals(ctx)
    goal = result.data["goals"][0]
    assert goal["goal_id"] == created.data["goal_id"]
    assert goal["current_value"] == "16.90"
    assert Decimal(goal["achievement_ratio"]) == Decimal("0.5")
    assert goal["remaining"] == "0.10"
    assert goal["song_rating_sum_needed"] == "5.00"
    content = result.display.content if result.display is not None else ""
    assert "달성률 50.00%" in content
    assert created.data["goal_id"] in content  # I8: 슬래시 사용자가 id를 볼 수 있다


async def test_progress_computes_ratio_for_fresh_rating_goal_without_progress_rows(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    records = _FakeRecords(deltas={"p1": _deltas("16.80")})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0, due_at="2099-12-31")
    records.deltas["p1"] = _deltas("16.85", "16.80")

    result = await skill.progress(ctx, created.data["goal_id"])
    assert result.data["history"] == []
    assert Decimal(result.data["achievement_ratio"]) == Decimal("0.25")
    assert result.data["remaining"] == "0.15"
    assert result.data["pace"] == "ahead"  # 기한이 아주 멀어 경과율은 거의 0이다
    content = result.display.content if result.display is not None else ""
    assert "달성률 25.00%" in content


async def test_progress_without_due_date_omits_pace(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(deltas={"p1": _deltas("16.80")})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    result = await skill.progress(ctx, created.data["goal_id"])
    assert result.data["pace"] is None


async def test_song_goal_summary_has_remaining_score_and_attacks(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    songdb = _FakeSongDb(note_counts={("곡A", "MAS"): 1000})
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    created = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    result = await skill.progress(ctx, created.data["goal_id"])
    expected = remaining_to_target(1000, 1_000_000, 1_007_500)
    assert expected is not None
    assert result.data["remaining_score"] == 7_500
    assert result.data["remaining_attacks"] == str(expected[1])
    assert result.data["current_value"] == "1000000"


# ---------------------------------------------------------------- I7: 이미 달성/달성 불가 목표


async def test_set_rating_goal_rejects_target_not_above_current(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(deltas={"p1": _deltas("16.80")})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    for target in (16.80, 16.50):
        result = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=target)
        assert result.error is not None
    assert await skill.models.list_goals_for_player(db, "p1") == []


async def test_set_overpower_goal_rejects_target_not_above_current(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(overpower={"p1": (90.0, 95.0)})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    result = await skill.set_goal(ctx, skill.GoalKind.OVERPOWER, target_value=95.0)
    assert result.error is not None


# ---------------------------------------------------------------- I8: id 보이기


async def test_todo_list_shows_goal_id(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    result = await skill.todo_list(ctx)
    assert result.data["todos"][0]["goal_id"] == created.data["goal_id"]
    assert result.display is not None and created.data["goal_id"] in (result.display.content or "")


# ---------------------------------------------------------------- 사소한 항목


async def test_delete_also_removes_progress_rows_and_detaches_children(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    parent = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="상위")
    child = await skill.set_goal(
        ctx, skill.GoalKind.FREEFORM, description="하위", parent_goal_id=parent.data["goal_id"]
    )
    await skill.models.insert_progress(
        db, id="prog1", goal_id=parent.data["goal_id"], sync_id=None, recorded_at="2026-10-17T00:00:00+00:00",
        value=None, achievement_ratio=None,
    )
    await skill.delete(ctx, parent.data["goal_id"])
    assert await skill.models.list_progress(db, parent.data["goal_id"]) == []
    child_row = await skill.models.fetch_goal(db, child.data["goal_id"])
    assert child_row is not None and child_row.parent_goal_id is None


async def test_set_goal_rejects_invalid_due_date(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습", due_at="다음 주")
    assert result.error is not None


async def test_set_goal_derives_duration_tag_from_due_date(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습", due_at="2099-12-31")
    goal = await skill.models.fetch_goal(db, created.data["goal_id"])
    assert goal.duration_tag == "long"
    assert goal.notify_offsets == [30, 7, 1, 0]


async def test_update_changes_notify_offsets_and_can_turn_them_off(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습")
    goal_id = created.data["goal_id"]
    assert (await skill.update(ctx, goal_id, notify_offsets="3,0")).error is None
    assert (await skill.models.fetch_goal(db, goal_id)).notify_offsets == [3, 0]
    assert (await skill.update(ctx, goal_id, notify_offsets="")).error is None
    assert (await skill.models.fetch_goal(db, goal_id)).notify_offsets == []
    assert (await skill.update(ctx, goal_id, notify_offsets="내일")).error is not None
