# skills/chunithm-goals/tests/test_chunithm_goals.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
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
from core.scheduler import Scheduler
from core.tables import RowRef, render_text, summarize_table
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
from services.common.settings import SettingsService
from services.common.yuni import YuniService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE_PLAYERS_SQL = Path("migrations/core/001_players.sql").read_text()
_CORE_PLAYER_TAGS_SQL = Path("migrations/core/002_player_tags.sql").read_text()
_CORE_SETTINGS_SQL = Path("migrations/core/003_settings.sql").read_text()
_CORE_SCHEDULES_SQL = Path("migrations/core/006_schedules.sql").read_text()
_GOALS_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"
_GOALS_NOTIFY_SCHEDULE_SQL = Path(__file__).parent.parent / "migrations" / "002_notify_schedule.sql"


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
    await conn.executescript(_CORE_SETTINGS_SQL)
    await conn.executescript(_CORE_SCHEDULES_SQL)
    await conn.executescript(_GOALS_SQL.read_text())
    await conn.executescript(_GOALS_NOTIFY_SCHEDULE_SQL.read_text())
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
    def __init__(self, title: str, difficulty: str = "MAS") -> None:
        self.song = type("S", (), {"title": title})()
        self.chart = type("C", (), {"difficulty": difficulty})()


@dataclass(slots=True, kw_only=True)
class _FakeSongDb:
    consts: dict[tuple[str, str], float] = field(default_factory=dict)
    note_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    new_songs: set[str] = field(default_factory=set)
    const_range_hits: list[str] = field(default_factory=list)  # 난이도를 지정했을 때 쓰는 제목만
    level_hits: list[str] = field(default_factory=list)
    version_hits: list[str] = field(default_factory=list)
    # 난이도를 생략(difficulty=None)했을 때만 쓰는 (제목, 난이도) 쌍 — 실제 songdb는 그 상수/
    # 레벨에 맞는 모든 난이도의 채보를 돌려주므로, 여러 난이도가 섞인 경우를 흉내 낸다.
    const_range_hits_any_difficulty: list[tuple[str, str]] = field(default_factory=list)

    def const_for(self, title: str, difficulty: str) -> float | None:
        return self.consts.get((title, difficulty))

    def note_count_for(self, title: str, difficulty: str) -> int | None:
        return self.note_counts.get((title, difficulty))

    def is_new_for(self, title: str) -> bool:
        return title in self.new_songs

    def find_by_const(self, *, min_const: float, max_const: float, difficulty: str | None = None) -> list[Any]:
        if difficulty is None:
            return [_Hit(title, dif) for title, dif in self.const_range_hits_any_difficulty]
        return [_Hit(title, difficulty) for title in self.const_range_hits]

    def find_by_level(self, *, level: str, difficulty: str | None = None) -> list[Any]:
        return [_Hit(title, difficulty or "MAS") for title in self.level_hits]

    def find_by_version(self, *, version: str, difficulty: str | None = None) -> list[Any]:
        return [_Hit(title, difficulty or "MAS") for title in self.version_hits]


@dataclass(slots=True, kw_only=True)
class _FakeNotify:
    posts: list[tuple[str, str]] = field(default_factory=list)
    dms: list[tuple[str, str]] = field(default_factory=list)

    async def post(self, channel_id: str, content: str) -> None:
        self.posts.append((channel_id, content))

    async def dm(self, player_id: str, content: str, *, view: Any | None = None) -> None:
        self.dms.append((player_id, content))

    async def mention(self, player_id: str, content: str) -> str:
        return f"<@{player_id}> {content}"


def _ctx(
    *, player_id: str = "p1", tags: frozenset[str] = frozenset(), db: aiosqlite.Connection,
    records: _FakeRecords | None = None, songdb: _FakeSongDb | None = None,
    players: PlayersService | None = None, notify: _FakeNotify | None = None,
    guild_id: str | None = None,
) -> ToolContext:
    services: dict[str, object] = {
        "records": records or _FakeRecords(), "songdb": songdb or _FakeSongDb(),
        "players": players or PlayersService(db), "notify": notify or _FakeNotify(),
        "settings": SettingsService(db), "yuni": YuniService(),
    }
    return ToolContext(
        player_id=player_id, tags=tags, services=services, db=db, event_bus=EventBus(),
        scheduler=Scheduler(db, quiet_start=time(2, 0), quiet_end=time(6, 0)), guild_id=guild_id,
    )


_SYNCED = frozenset({"chunithm-synced"})
_MAS = ResolvedDifficulty(canonical="MASTER")
_SSS = ResolvedRank(canonical="SSS", min_score=1_007_500)


def _chart(title: str, difficulty: str = "MAS", *, note_count: int | None = None, const: float | None = None) -> ResolvedChart:
    """ChartRef 해석 결과. 테스트는 해석기를 거치지 않고 해석된 값을 바로 넘긴다
    (chunithm-border/chunithm-song 테스트와 같은 방식)."""
    return ResolvedChart(
        song=SongRecord(title=title, aliases=(), artist="", id=1),
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


async def test_set_song_goal_without_pb_uses_margin_below_target_as_baseline(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """사용자 확인 2026-10-18: 안 쳐본 채보는 baseline이 0이면 달성률이 raw 점수 비율과
    같아져(예: 990,000/1,007,500=98.3%) 실제보다 훨씬 더 달성한 것처럼 보인다. 목표 점수보다
    15,000점 낮은 점수를 기준점으로 삼는다."""
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_007_500)
    assert result.error is None
    goal = await skill.models.fetch_goal(db, result.data["goal_id"])
    assert goal.baseline_value == 1_007_500.0 - 15_000.0


async def test_set_song_goal_without_pb_clamps_baseline_at_zero_for_low_target(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=10_000)
    assert result.error is None
    goal = await skill.models.fetch_goal(db, result.data["goal_id"])
    assert goal.baseline_value == 0.0


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


async def test_set_folder_goal_without_difficulty_spans_all_difficulties(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """실기 2026-10-18: "15.0 폴더 더블플 목표"처럼 난이도 없이 상수만 주면, 그 상수를
    만족하는 모든 난이도(MAS·ULT 등)의 채보를 묶어야 한다 — 상수는 채보 단위로 이미
    유일하게 정해지는 값이라 난이도로 좁힐 이유가 없다는 사용자 결정."""
    songdb = _FakeSongDb(const_range_hits_any_difficulty=[("곡A", "MAS"), ("곡B", "ULT")])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    result = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3,
        folder_condition=skill.FolderCondition.CLEARED,
    )
    assert result.error is None
    assert result.data["target_value"] == 2.0
    assert result.data["target_ref"]["difficulty"] is None


async def test_folder_goal_without_difficulty_counts_progress_across_difficulties(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    songdb = _FakeSongDb(const_range_hits_any_difficulty=[("곡A", "MAS"), ("곡B", "ULT")])
    ctx = _ctx(db=db, tags=_SYNCED, songdb=songdb)
    created = await skill.set_goal(
        ctx, skill.GoalKind.FOLDER, folder_min_const=15.0, folder_max_const=15.3,
        folder_condition=skill.FolderCondition.CLEARED,
    )
    goal_id = created.data["goal_id"]

    records = _FakeRecords(pbs={"p1": [
        _pb("곡A", "MAS", 1_000_000, "SS", 15.0, "15.0", clear="CLEAR"),
        _pb("곡B", "ULT", 1_000_000, "SS", 15.0, "15.0", clear="CLEAR"),
    ]})
    ctx2 = _ctx(db=db, tags=_SYNCED, records=records, songdb=songdb)
    await skill.on_sync_completed(ctx2, {"player_id": "p1", "sync_id": "sync1"})

    history = await skill.models.list_progress(db, goal_id)
    assert len(history) == 1
    assert history[0].value == 2.0  # 곡A(MAS)·곡B(ULT) 둘 다 매치


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
    # 표로 나온다(코어가 마지막 표를 붙이므로 [[show]]를 빼먹어도 목록이 붙는다).
    assert result.table is not None and result.table.subject == ctx.player_id
    assert result.data == {"player_id": me.id, "count": 1}  # 목표 id·값은 data에 없고 표에만 있다
    assert result.table.rows[0].ref == RowRef("chunithm-goals.goal", result.table.rows[0].cell("id").raw)
    assert result.display is None


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
    assert result.table is not None
    assert [r.cell("곡").raw for r in result.table.rows] == ["곡B"]


async def test_list_own_goals_shows_private_and_public(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    me = await players.create_player()
    ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000, public=False)
    await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡B"), target_score=1_000_000, public=True)
    result = await skill.list_goals(ctx)
    assert result.table is not None and result.table.row_count == 2


async def test_list_for_other_player_without_public_goals_is_empty(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    other = await players.create_player()
    other_ctx = _ctx(db=db, player_id=other.id, tags=frozenset(), players=players)
    await skill.set_goal(other_ctx, skill.GoalKind.FREEFORM, description="비밀 연습")
    me = await players.create_player()
    me_ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)
    result = await skill.list_goals(me_ctx, player=other)
    assert result.error is None
    assert result.table is None and result.data["count"] == 0


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


async def test_update_via_executor_with_raw_string_enum_args(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """set_goal과 같은 종류의 버그: status/duration_tag도 LLM 경로에선 원문 문자열로 온다."""
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습")
    outcome = await _executor().invoke(
        skill.update.__yuni_tool__, ctx,
        {"goal_id": created.data["goal_id"], "status": "stopped", "duration_tag": "short"},
    )
    assert outcome.result is not None and outcome.result.error is None
    goal = await skill.models.fetch_goal(db, created.data["goal_id"])
    assert goal.status == "stopped"
    assert goal.duration_tag == "short"


async def test_update_target_score_resolves_rank_name_to_correct_score(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """실기 2026-10-18: "트리플(SSS)로 수정해줘"에 모델이 SSS를 1,000,000점(SS)으로 잘못
    기억해 넘겼다 — set_goal처럼 target_score(ScoreRef)로 해석기를 거치게 고쳤다."""
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000)
    goal_id = created.data["goal_id"]

    result = await skill.update(ctx, goal_id, target_score=1_007_500)
    assert result.error is None
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.target_value == 1_007_500.0


async def test_update_rejects_target_value_alone_on_song_goal(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """실기 2026-10-18: target_score를 추가한 뒤에도 target_value가 곡 목표에 여전히
    열려 있어서, 모델이 또 "트리플"을 1,000,000(SS)으로 직접 계산해 target_value로
    넘긴 사례가 재현됐다 — 곡 목표는 이제 target_value를 아예 거절해 target_score로만
    가게 한다."""
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000)
    goal_id = created.data["goal_id"]

    result = await skill.update(ctx, goal_id, target_value=1_000_000.0)

    assert result.error is not None
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.target_value == 1_000_000.0  # 안 바뀌었다(set_goal 때 값 그대로)


async def test_update_rejects_both_target_value_and_target_score(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_000_000)
    result = await skill.update(ctx, created.data["goal_id"], target_value=1_005_000, target_score=1_007_500)
    assert result.error is not None


async def test_update_rejects_target_score_on_non_song_goal(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습")
    result = await skill.update(ctx, created.data["goal_id"], target_score=1_007_500)
    assert result.error is not None


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


async def test_set_goal_via_executor_with_raw_string_enum_args(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """실기 2026-10-18: LLM 경로는 core/schema.py가 enum 인자를 원문 문자열로 넘기는데
    set_goal이 kind.value 등을 바로 불러 AttributeError('str' object has no attribute
    'value')로 터졌다. ToolExecutor.invoke는 enum 인자를 변환하지 않으므로(ref type만
    변환) 도구 자신이 되돌려야 한다 — 그 경로를 그대로 재현한다."""
    ctx = _ctx(db=db, tags=frozenset())
    outcome = await _executor().invoke(
        skill.set_goal.__yuni_tool__, ctx, {"kind": "freeform", "description": "손배치 연습"},
    )
    assert outcome.result is not None and outcome.result.error is None
    assert outcome.result.data["kind"] == "freeform"


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
    assert result.table is not None and result.table.row_count == 1
    assert result.table.rows[0].cell("남은 점수").raw == 7_500


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
    assert "곡별 목표" in content
    assert "접근  곡C MAS  1,007,500점까지 128점 (현재 1,007,372)" in content
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
    assert "현재 16.9000 · 남은 레이팅 0.1000 · 이번 동기화 +0.10 · 달성률 50.00%" in content
    assert section.data["rating_goal"]["remaining"] == "0.1000"
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
    assert section.data["todos"][0]["remaining_score"] == 7_500


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
    # EXP 노트 수를 몰라도(note_counts에 없음) 남은 점수는 계산된다 — 점수 차이만 쓰고
    # 어택 환산(노트 수 필요)은 더 이상 안 한다(사용자 확인 2026-10-18).
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
    assert exp["remaining_score"] == 1_009_000


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
    assert todos[0]["song_title"] == "곡A"  # 남은 점수가 더 적은 쪽이 먼저다


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
    assert result.table is not None
    assert result.table.rows[0].ref == RowRef("chunithm-goals.goal", created.data["goal_id"])
    content = render_text(result.table).content
    row = result.table.rows[0]
    assert row.cell("달성률").raw == 0.5 and row.cell("달성률").text == "50.00%"
    assert row.cell("남은 양").raw == 0.1
    assert content.splitlines()[-1] == (
        "1. [진행 중] 레이팅 17.0000  달성률 50.00% · 현재 16.9000 · 남은 0.1000 (곡 레이팅 합 5.0000)"
    )
    # I8: id는 슬래시 출력에만 있다(멘션 답에는 안 보인다).
    assert created.data["goal_id"] not in content
    assert created.data["goal_id"] in render_text(result.table, slash=True).content


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
    assert result.data["remaining"] == "0.1500"
    assert result.data["pace"] == "ahead"  # 기한이 아주 멀어 경과율은 거의 0이다
    content = result.display.content if result.display is not None else ""
    assert "달성률 25.00%" in content


async def test_progress_without_due_date_omits_pace(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(deltas={"p1": _deltas("16.80")})
    ctx = _ctx(db=db, tags=_SYNCED, records=records)
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0)
    result = await skill.progress(ctx, created.data["goal_id"])
    assert result.data["pace"] is None


async def test_song_goal_summary_has_remaining_score(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    created = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    result = await skill.progress(ctx, created.data["goal_id"])
    assert result.data["remaining_score"] == 7_500
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


async def test_todo_list_shows_goal_id_only_in_slash_content(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    result = await skill.todo_list(ctx)
    assert result.table is not None
    assert result.table.rows[0].ref == RowRef("chunithm-goals.goal", created.data["goal_id"])
    assert created.data["goal_id"] not in render_text(result.table).content
    assert created.data["goal_id"] in render_text(result.table, slash=True).content
    assert "id" not in summarize_table(result.table)["columns"]  # 모델 요약에도 id 칸이 없다


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


# ---------------------------------------------------------------- 2차: 램프 곡 목표(풀콤보/올 저스티스)


@pytest.mark.parametrize(("lamp", "label"), [("full_combo", "풀콤보"), ("all_justice", "올 저스티스")])
async def test_todo_add_lamp_goal_is_stored_in_target_ref(
    skill: ModuleType, db: aiosqlite.Connection, lamp: str, label: str
) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    result = await skill.todo_add(ctx, _chart("곡A"), lamp=skill.SongLamp(lamp))
    assert result.error is None
    assert result.data["target_ref"] == {"song_title": "곡A", "difficulty": "MAS", "lamp": lamp}
    assert result.data["target_value"] is None
    assert result.display is not None and label in (result.display.content or "")


async def test_song_goal_requires_exactly_one_of_score_or_lamp(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    both = await skill.todo_add(ctx, _chart("곡A"), 1_007_500, lamp=skill.SongLamp.FULL_COMBO)
    assert both.error is not None
    neither = await skill.todo_add(ctx, _chart("곡A"))
    assert neither.error is not None
    assert await skill.models.list_goals_for_player(db, "p1") == []


@pytest.mark.parametrize(
    ("lamp", "combo"), [("full_combo", "FULL COMBO"), ("full_combo", "AJC"), ("all_justice", "ALL JUSTICE")]
)
async def test_lamp_goal_rejected_when_chart_already_has_that_lamp(
    skill: ModuleType, db: aiosqlite.Connection, lamp: str, combo: str
) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0", combo=combo)]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.todo_add(ctx, _chart("곡A"), lamp=skill.SongLamp(lamp))
    assert result.error is not None
    assert await skill.models.list_goals_for_player(db, "p1") == []


async def test_aj_goal_allowed_when_chart_only_has_full_combo(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_009_500, "SSS+", 15.0, "17.0", combo="FULL COMBO")]})
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    result = await skill.todo_add(ctx, _chart("곡A"), lamp=skill.SongLamp.ALL_JUSTICE)
    assert result.error is None


@pytest.mark.parametrize(("lamp", "combo"), [("full_combo", "FULL COMBO"), ("all_justice", "ALL JUSTICE")])
async def test_on_sync_completed_auto_completes_lamp_goal_when_lamp_appears(
    skill: ModuleType, db: aiosqlite.Connection, lamp: str, combo: str
) -> None:
    records = _FakeRecords()
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    created = await skill.todo_add(ctx, _chart("곡A"), lamp=skill.SongLamp(lamp))
    goal_id = created.data["goal_id"]

    # 동기화가 records_pb를 갱신한 뒤의 상태: 점수는 그대로라도 램프가 올랐다.
    records.pbs["p1"] = [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0", combo=combo)]
    records.diffs["sync1"] = _diff(updates=[ChartUpdate(
        song_title="곡A", difficulty="MAS", old_score=999_000, new_score=1_000_000, old_rank="S+",
        new_rank="SS", old_rating=None, new_rating=None, session_id="s1", estimated_session=False,
        entered_frame=False,
    )])
    section = await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync1"})
    assert section is not None and section.stage == "great"
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "achieved"


async def test_on_sync_completed_lamp_goal_not_completed_by_score_alone(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """램프 목표는 점수 비교(target_value)로 완료되지 않는다 — 점수가 올라도 램프가 없으면 그대로다."""
    records = _FakeRecords()
    ctx = _ctx(db=db, tags=frozenset(), records=records)
    created = await skill.todo_add(ctx, _chart("곡A"), lamp=skill.SongLamp.FULL_COMBO)
    records.pbs["p1"] = [_pb("곡A", "MAS", 1_009_000, "SSS+", 15.0, "17.15", combo="NONE")]
    records.diffs["sync1"] = _diff(updates=[ChartUpdate(
        song_title="곡A", difficulty="MAS", old_score=1_000_000, new_score=1_009_000, old_rank="SS",
        new_rank="SSS+", old_rating=None, new_rating=None, session_id="s1", estimated_session=False,
        entered_frame=False,
    )])
    section = await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync1"})
    assert section is not None and section.stage is None
    assert (await skill.models.fetch_goal(db, created.data["goal_id"])).status == "in_progress"


async def test_lamp_goal_in_todo_list_and_song_view(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    await skill.todo_add(ctx, _chart("곡A"), lamp=skill.SongLamp.ALL_JUSTICE)
    listed = await skill.todo_list(ctx)
    assert listed.table is not None
    assert not listed.table.rows[0].cell("남은 점수").filled
    assert render_text(listed.table).content.splitlines()[-1] == "1. 곡A MAS  올 저스티스 목표  현재 램프 기록 없음"  # 상수를 몰라 3번 서식
    assert listed.table.rows[0].cell("목표 램프").raw == "ALL JUSTICE"
    assert listed.table.rows[0].cell("현재 램프").text == "기록 없음"
    section = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "곡A", "difficulty": None})
    assert section is not None and "올 저스티스" in (section.display.content or "")


# ---------------------------------------------------------------- 2차: 남의 목표의 진행값은 records_public을 따른다


async def _other_with_public_rating_goal(
    skill: ModuleType, db: aiosqlite.Connection, *, records_public: bool
) -> tuple[ToolContext, str]:
    players = PlayersService(db)
    other = await players.create_player()
    await players.set_visibility(other.id, "records_public", records_public)
    records = _FakeRecords(deltas={other.id: _deltas("16.80")})
    other_ctx = _ctx(db=db, player_id=other.id, tags=_SYNCED, records=records, players=players)
    created = await skill.set_goal(other_ctx, skill.GoalKind.RATING, target_value=17.0, public=True)
    records.deltas[other.id] = _deltas("16.90", "16.80")
    me = await players.create_player()
    me_ctx = _ctx(db=db, player_id=me.id, tags=frozenset(), records=records, players=players)
    return me_ctx, created.data["goal_id"]


async def test_other_players_public_goal_hides_progress_when_records_private(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    me_ctx, goal_id = await _other_with_public_rating_goal(skill, db, records_public=False)
    players = me_ctx.players
    owner_id = (await skill.models.fetch_goal(db, goal_id)).player_id
    owner = await players.get_player(owner_id)

    listed = await skill.list_goals(me_ctx, player=owner)
    assert listed.table is not None
    hidden = listed.table.rows[0]
    assert hidden.ref.key == goal_id and hidden.cell("유형").raw == "레이팅" and hidden.cell("상태").raw == "in_progress"
    content = render_text(listed.table).content
    assert "레이팅 17.00" in content
    assert "16.90" not in content and "달성률" not in content
    assert "진행 상황은 비공개야" in content
    row = listed.table.rows[0]
    assert row.cell("달성률").raw is None and row.cell("남은 양").raw is None  # 비공개면 원래 값 None

    progress = await skill.progress(me_ctx, goal_id)
    assert progress.error is None
    assert progress.data["current_value"] is None and progress.data["achievement_ratio"] is None
    assert progress.data["history"] == [] and progress.data.get("projected_rating") is None
    progress_content = progress.display.content if progress.display is not None else ""
    assert "16.90" not in progress_content and "달성률" not in progress_content
    assert "진행 상황은 비공개야" in progress_content


async def test_other_players_public_goal_shows_progress_when_records_public(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    me_ctx, goal_id = await _other_with_public_rating_goal(skill, db, records_public=True)
    owner = await me_ctx.players.get_player((await skill.models.fetch_goal(db, goal_id)).player_id)
    listed = await skill.list_goals(me_ctx, player=owner)
    assert listed.table is not None
    assert listed.table.rows[0].cell("달성률").raw == 0.5
    assert "현재 16.9000" in render_text(listed.table).content
    assert "달성률 50.00%" in render_text(listed.table).content
    progress = await skill.progress(me_ctx, goal_id)
    assert progress.data["current_value"] == "16.90"


# ---------------------------------------------------------------- 9단계 Part 3: 기한 알림 예약


def _rating_records(current: str, *, previous: str | None = None) -> _FakeRecords:
    return _FakeRecords(deltas={"p1": RatingDeltas(
        computed_rating=Decimal(current), best30_average=Decimal("17.0"), new20_average=Decimal("16.0"),
        has_previous=previous is not None, previous_computed_rating=Decimal(previous) if previous else None,
        previous_best30_average=None, previous_new20_average=None,
    )})


async def test_set_goal_schedules_reminders_and_final_deadline(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, records=_rating_records("16.80"))
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0, due_at="2099-12-31")
    goal_id = created.data["goal_id"]

    reminders = await skill.models.list_schedules(db, goal_id, kind="reminder")
    assert len(reminders) == 4  # 장기 목표(단기/중기/장기는 기한-만든날로 정해짐) 기본 알림: 30·7·1·0일 전

    finals = await skill.models.list_schedules(db, goal_id, kind="final_deadline")
    assert len(finals) == 1
    cursor = await db.execute("SELECT fire_at FROM schedule_once WHERE id = ?", (finals[0].schedule_id,))
    row = await cursor.fetchone()
    assert row is not None
    fire_at = datetime.fromisoformat(row[0])
    assert fire_at.date() == date(2100, 1, 4)  # 2099-12-31 + 4일(다음 날 + 3일 대기)
    assert fire_at.time() == time(10, 0)  # 기본 알림 시각


async def test_reminder_schedule_avoids_quiet_hours(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await SettingsService(db).set_value(
        scope="guild", scope_id="g1", skill="chunithm-goals", name="알림 시각", value="03:00", updated_by="p1",
    )
    ctx = _ctx(db=db, tags=_SYNCED, records=_rating_records("16.80"), guild_id="g1")
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0, due_at="2099-12-31")
    goal_id = created.data["goal_id"]

    reminders = await skill.models.list_schedules(db, goal_id, kind="reminder")
    cursor = await db.execute("SELECT fire_at FROM schedule_once WHERE id = ?", (reminders[0].schedule_id,))
    row = await cursor.fetchone()
    assert row is not None
    fire_at = datetime.fromisoformat(row[0])
    assert fire_at.time() == time(6, 0)  # 02~06시 정지 시간대 끝으로 밀림(03시 설정)


async def test_early_achievement_cancels_pending_schedules(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2099-12-31")
    goal_id = created.data["goal_id"]
    assert await skill.models.list_schedules(db, goal_id)

    await skill.todo_done(ctx, goal_id)
    assert await skill.models.list_schedules(db, goal_id) == []


async def test_update_due_at_reschedules_final_deadline(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2099-12-31")
    goal_id = created.data["goal_id"]
    old_final = (await skill.models.list_schedules(db, goal_id, kind="final_deadline"))[0]

    await skill.update(ctx, goal_id, due_at="2050-06-15")
    finals = await skill.models.list_schedules(db, goal_id, kind="final_deadline")
    assert len(finals) == 1
    assert finals[0].schedule_id != old_final.schedule_id  # 옛 예약은 취소되고 새로 걸렸다

    cursor = await db.execute("SELECT 1 FROM schedule_once WHERE id = ?", (old_final.schedule_id,))
    assert await cursor.fetchone() is None  # cancel_once가 지웠다

    cursor = await db.execute("SELECT fire_at FROM schedule_once WHERE id = ?", (finals[0].schedule_id,))
    row = await cursor.fetchone()
    assert row is not None
    fire_at = datetime.fromisoformat(row[0])
    assert fire_at.date() == date(2050, 6, 19)  # 2050-06-15 + 4일


async def test_update_status_stopped_cancels_schedules(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2099-12-31")
    goal_id = created.data["goal_id"]
    assert await skill.models.list_schedules(db, goal_id)

    await skill.update(ctx, goal_id, status=skill.GoalStatus.STOPPED)
    assert await skill.models.list_schedules(db, goal_id) == []


async def test_delete_cancels_schedules(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2099-12-31")
    goal_id = created.data["goal_id"]
    schedule_ids = [row.schedule_id for row in await skill.models.list_schedules(db, goal_id)]
    assert schedule_ids

    await skill.delete(ctx, goal_id)
    assert await skill.models.list_schedules(db, goal_id) == []
    for sid in schedule_ids:
        cursor = await db.execute("SELECT 1 FROM schedule_once WHERE id = ?", (sid,))
        assert await cursor.fetchone() is None


async def test_on_sync_completed_finalizes_expired_rating_goal_as_good(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, records=_rating_records("16.80"))
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0, due_at="2020-01-01")
    goal_id = created.data["goal_id"]

    ctx2 = _ctx(db=db, tags=_SYNCED, records=_rating_records("16.96", previous="16.80"))
    section = await skill.on_sync_completed(ctx2, {"player_id": "p1", "sync_id": "sync1"})
    assert section is not None and section.stage == "good"  # 달성률 (16.96-16.80)/(17.00-16.80)=80%
    assert any(e["goal_id"] == goal_id and e["stage"] == "good" for e in section.data["expired"])

    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "expired"
    assert await skill.models.list_schedules(db, goal_id) == []


async def test_on_sync_completed_finalizes_expired_rating_goal_as_rest(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx = _ctx(db=db, tags=_SYNCED, records=_rating_records("16.80"))
    created = await skill.set_goal(ctx, skill.GoalKind.RATING, target_value=17.0, due_at="2020-01-01")
    goal_id = created.data["goal_id"]

    section = await skill.on_sync_completed(ctx, {"player_id": "p1", "sync_id": "sync1"})
    assert section is not None and section.stage == "rest"  # 기준값에서 전혀 안 움직임
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "expired"


async def test_final_deadline_handler_finalizes_with_last_known_value(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    notify = _FakeNotify()
    ctx = _ctx(db=db, tags=frozenset(), notify=notify)
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2020-01-01")
    goal_id = created.data["goal_id"]

    await skill.on_final_deadline(ctx, {"goal_id": goal_id})

    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "expired"
    assert notify.dms  # 채널 설정이 없어 DM으로 간다
    assert await skill.models.list_schedules(db, goal_id) == []


async def test_final_deadline_handler_noop_if_already_resolved(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    notify = _FakeNotify()
    ctx = _ctx(db=db, tags=frozenset(), notify=notify)
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2020-01-01")
    goal_id = created.data["goal_id"]
    await skill.todo_done(ctx, goal_id)  # 기한 전에 이미 끝남

    await skill.on_final_deadline(ctx, {"goal_id": goal_id})

    assert notify.dms == [] and notify.posts == []
    goal = await skill.models.fetch_goal(db, goal_id)
    assert goal.status == "achieved"  # expired로 덮어쓰지 않는다


async def test_notify_due_handler_sends_due_day_message(skill: ModuleType, db: aiosqlite.Connection) -> None:
    notify = _FakeNotify()
    ctx = _ctx(db=db, tags=frozenset(), notify=notify)
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2099-12-31")
    goal_id = created.data["goal_id"]

    await skill.on_notify_due(ctx, {"goal_id": goal_id, "offset_days": 0})
    assert notify.dms
    assert "기한이야" in notify.dms[-1][1]


async def test_notify_due_handler_skips_already_resolved_goal(skill: ModuleType, db: aiosqlite.Connection) -> None:
    notify = _FakeNotify()
    ctx = _ctx(db=db, tags=frozenset(), notify=notify)
    created = await skill.todo_add(ctx, chart=_chart("곡A"), target_score=1_007_500, due_at="2099-12-31")
    goal_id = created.data["goal_id"]
    await skill.todo_done(ctx, goal_id)

    await skill.on_notify_due(ctx, {"goal_id": goal_id, "offset_days": 0})
    assert notify.dms == [] and notify.posts == []


# ---------------------------------------------------------------- 16단계 Part 4-2: 목표·투두를 표로

async def _goals_ctx(db: aiosqlite.Connection) -> ToolContext:
    players = PlayersService(db)
    me = await players.create_player()
    return _ctx(db=db, player_id=me.id, tags=frozenset(), players=players)



async def test_list_goals_table_columns_and_line_shapes(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = await _goals_ctx(db)
    await skill.set_goal(ctx, skill.GoalKind.SONG, chart=_chart("곡A"), target_score=1_005_000, due_at="2099-12-31")
    await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습하기", public=True)

    result = await skill.list_goals(ctx)

    table = result.table
    assert table is not None
    assert [c.label for c in table.columns] == [
        "유형", "곡", "난이도", "목표", "상태", "진행", "달성률", "남은 양", "기한", "공개", "id",
    ]
    song, free = table.rows
    assert song.cell("유형").text == "곡" and song.cell("곡").raw == "곡A" and song.cell("난이도").text == "MAS"
    assert free.cell("유형").text == "자유" and not free.cell("곡").filled and not free.cell("난이도").filled
    assert song.cell("공개").text == "비공개" and free.cell("공개").text == "공개"
    lines = render_text(table).content.splitlines()
    assert lines[-2].startswith("1. [진행 중] 곡A MAS  1,005,000점")
    assert lines[-2].endswith("기한 2099-12-31")
    assert lines[-1] == "2. [진행 중] 연습하기"
    assert table.steps == ("목표 목록",)


async def test_list_goals_kind_filter_is_in_the_steps(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = await _goals_ctx(db)
    await skill.set_goal(ctx, skill.GoalKind.FREEFORM, description="연습하기")

    result = await skill.list_goals(ctx, kind=skill.GoalKind.FREEFORM)

    assert result.table is not None and result.table.steps == ("목표 목록 (자유)",)


async def test_list_goals_empty_is_a_message(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = await _goals_ctx(db)

    result = await skill.list_goals(ctx)

    assert result.table is None and result.display is not None and result.display.content == "목표가 없어."
    assert result.data["count"] == 0


async def test_todo_list_score_goal_line_and_cells(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0")]})
    songdb = _FakeSongDb(note_counts={("곡A", "MAS"): 1000}, consts={("곡A", "MAS"): 15.0})
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(ctx, _chart("곡A"), 1_007_500)

    result = await skill.todo_list(ctx)

    table = result.table
    assert table is not None and table.subject == "p1"
    assert [c.label for c in table.columns] == [
        "곡", "난이도", "상수", "점수", "목표 점수", "남은 점수", "목표 램프", "현재 램프", "id",
    ]
    row = table.rows[0]
    assert row.cell("점수").raw == 1_000_000 and row.cell("목표 점수").raw == 1_007_500
    assert row.cell("남은 점수").raw == 7_500 and not row.cell("목표 램프").filled
    assert render_text(table).content.splitlines()[-1] == "1. 곡A MAS 15.0  1,000,000 → 1,007,500  (남은 7,500점)"


async def test_todo_list_lamp_goal_with_const_shows_current_lamp(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(pbs={"p1": [_pb("곡A", "MAS", 1_000_000, "SS", 15.0, "14.0", combo="FULL COMBO")]})
    songdb = _FakeSongDb(consts={("곡A", "MAS"): 15.0})
    ctx = _ctx(db=db, tags=frozenset(), records=records, songdb=songdb)
    await skill.todo_add(ctx, _chart("곡A"), lamp=skill.SongLamp.ALL_JUSTICE)

    result = await skill.todo_list(ctx)

    assert result.table is not None
    assert render_text(result.table).content.splitlines()[-1] == (
        "1. 곡A MAS 15.0  올 저스티스 목표  (현재 램프 FULL COMBO)"
    )


async def test_todo_list_empty_and_sort_step(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, tags=frozenset())
    empty = await skill.todo_list(ctx)
    assert empty.table is None and empty.display is not None and empty.display.content == "투두가 없어."

    await skill.todo_add(ctx, _chart("곡A"), 1_007_500)
    by_remaining = await skill.todo_list(ctx, sort=skill.TodoSort.REMAINING)
    assert by_remaining.table is not None and by_remaining.table.steps == ("투두 (남은 점수순)",)
