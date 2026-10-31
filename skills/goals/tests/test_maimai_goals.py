# skills/goals/tests/test_maimai_goals.py
"""maimai 판 `goals`: 진짜 RecordsService·MaimaiSongDbService로 동기화한 기록 위에서 목표를 만들고 점검한다."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, time
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.scheduler import Scheduler
from core.tables import TableBuilder, render_text
from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.common.settings import SettingsService
from services.common.yuni import YuniService
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart
from services.game.records import RecordsService
from services.game.registry import profile_for
from services.game.sync_types import ChartPlay, ProfileSnapshot, SyncSnapshot
from services.game.terms import ResolvedDifficulty, ResolvedLamp, TermsService
from services.maimai.profile import MAIMAI
from services.maimai.songdb import MaimaiSongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE = Path("migrations/core")
_GOALS = Path(__file__).parent.parent / "migrations"
_CORE_SQL = "".join(
    (_CORE / name).read_text()
    for name in (
        "001_players.sql", "002_player_tags.sql", "003_settings.sql", "006_schedules.sql", "008_records.sql",
        "013_records_overpower.sql", "014_records_sync_log_snapshot_averages.sql", "018_records_profile.sql",
        "019_records_session_state.sql", "024_records_chart_type.sql",
    )
)
_SAMPLE = [
    {
        "title": "Old Both", "artist": "A", "version": "20000", "image_url": "a.png", "intl": "1",
        "lev_mas": "12+", "lev_mas_i": "12.6", "lev_mas_notes": "346",
        "dx_lev_mas": "13+", "dx_lev_mas_i": "13.7", "dx_lev_mas_notes": "631",
    },
    {
        "title": "Old Std", "artist": "B", "version": "10000", "image_url": "b.png", "intl": "1",
        "lev_mas": "12", "lev_mas_i": "12.4", "lev_mas_notes": "302",
    },
    {
        "title": "Fresh", "artist": "C", "version": "26511", "image_url": "c.png", "intl": "1",
        "dx_lev_mas": "14+", "dx_lev_mas_i": "14.9", "dx_lev_mas_notes": "900",
    },
]
_SYNCED = frozenset({"maimai-synced"})
_MAS = ResolvedDifficulty(canonical="MASTER")
_T1 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_goals_skill_under_test", _SKILL_PY)
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
    await conn.executescript(_CORE_SQL)
    await conn.executescript((_GOALS / "001_init.sql").read_text())
    await conn.executescript((_GOALS / "002_notify_schedule.sql").read_text())
    try:
        yield conn
    finally:
        await conn.close()


class _FakeNotify:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str]] = []

    async def dm(self, player_id: str, content: str, *, view: Any | None = None) -> None:
        self.dms.append((player_id, content))

    async def post(self, channel_id: str, content: str) -> None: ...

    async def mention(self, player_id: str, content: str) -> str:
        return content


def _play(title: str, chart_type: str, score: int, **extra: object) -> ChartPlay:
    return ChartPlay(song_title=title, difficulty="MAS", chart_type=chart_type, score=score, rank="SSS+", **extra)  # pyright: ignore[reportArgumentType]


class _World:
    """maimai·CHUNITHM 두 게임의 서비스 묶음을 가진 ctx 공장."""

    def __init__(self, db: aiosqlite.Connection, tmp_path: Path) -> None:
        path = tmp_path / "music-ex-intl.json"
        path.write_text(json.dumps(_SAMPLE, ensure_ascii=False), encoding="utf-8")
        self.db = db
        self.players = PlayersService(db)
        self.songdb = MaimaiSongDbService(path)
        self.records = RecordsService(
            conn=db, songdb=self.songdb, players=self.players, game="maimai", profile=MAIMAI
        )
        self.terms = TermsService(MAIMAI.terms_path, game="maimai", percent_scores=True)
        self.notify = _FakeNotify()
        self.player_id = ""

    async def setup(self, plays: list[ChartPlay]) -> None:
        self.player_id = (await self.players.create_player()).id
        await self.sync(plays)

    async def sync(self, plays: list[ChartPlay], *, recent: list[ChartPlay] | None = None) -> Any:
        snapshot = SyncSnapshot(profile=ProfileSnapshot(username="유니"), all_plays=plays, recent=recent or [])
        return await self.records.save_sync(self.player_id, snapshot, self.ctx())

    def ctx(self, *, game: str = "maimai", tags: frozenset[str] = _SYNCED) -> ToolContext:
        maimai_services: dict[str, object] = {"records": self.records, "songdb": self.songdb, "terms": self.terms}
        services: dict[str, object] = {
            **maimai_services, "players": self.players, "notify": self.notify,
            "settings": SettingsService(self.db), "yuni": YuniService(),
        }
        return ToolContext(
            player_id=self.player_id, tags=tags, services=services, db=self.db, event_bus=EventBus(),
            scheduler=Scheduler(self.db, quiet_start=time(2, 0), quiet_end=time(6, 0)),
            game=game, game_services={"maimai": maimai_services},
        )

    def chart(self, title: str, chart_type: str) -> ResolvedChart:
        song = self.songdb.song_by_title(title)
        assert song is not None
        [chart] = [c for c in song.charts.values() if c.difficulty == "MAS" and c.chart_type == chart_type]
        return ResolvedChart(song=song, chart=chart)


@pytest.fixture
async def world(db, tmp_path) -> _World:
    world = _World(db, tmp_path)
    await world.setup([
        _play("Old Std", "STD", 1_005_000, combo_lamp="FC", chain_lamp="FS"),
        _play("Old Both", "DX", 1_000_000),
        _play("Fresh", "DX", 1_001_000),
    ])
    return world


async def test_rating_goal_uses_integer_rating_and_the_games_tag(skill, world) -> None:
    no_tag = await skill.set_goal(world.ctx(tags=frozenset()), skill.GoalKind.RATING, target_value=950)
    assert no_tag.error is not None and "maimai DX NET 동기화" in no_tag.error

    result = await skill.set_goal(world.ctx(), skill.GoalKind.RATING, target_value=950)
    assert result.error is None and "레이팅 950" in (result.display.content if result.display else "")

    listed = await skill.list_goals(world.ctx())
    assert listed.table is not None
    text = render_text(listed.table).content
    assert "레이팅 950" in text and "현재 896" in text and "남은 54" in text
    [goal] = await skill.models.list_goals_for_player(world.db, world.player_id)
    assert goal.game == "maimai"


async def test_overpower_goal_is_rejected_for_maimai(skill, world) -> None:
    result = await skill.set_goal(world.ctx(), skill.GoalKind.OVERPOWER, target_value=90.0)
    assert result.error is not None and "오버파워" in result.error


async def test_song_goal_keeps_the_chart_type_and_shows_percent_scores(skill, world) -> None:
    ctx = world.ctx()
    result = await skill.todo_add(ctx, chart=world.chart("Fresh", "DX"), target_score=1_005_000)
    assert result.error is None
    again = await skill.todo_add(ctx, chart=world.chart("Fresh", "DX"), target_score=1_005_000)
    assert again.error is not None and "이미 진행 중인 투두" in again.error
    # 같은 곡의 다른 종류는 따로 만들 수 있다.
    other = await skill.todo_add(ctx, chart=world.chart("Old Both", "STD"), target_score=1_005_000)
    assert other.error is None

    listed = await skill.todo_list(ctx)
    assert listed.table is not None
    text = render_text(listed.table).content
    assert "Fresh DX MAS 14.9  100.1000% → 100.5000%  (남은 0.4000%)" in text
    refs = [g.target_ref for g in await skill.models.list_goals_for_player(world.db, world.player_id)]
    assert {"song_title": "Fresh", "difficulty": "MAS", "chart_type": "DX"} in refs
    assert {"song_title": "Old Both", "difficulty": "MAS", "chart_type": "STD"} in refs


async def test_goals_are_per_game(skill, world, db) -> None:
    await skill.todo_add(world.ctx(), chart=world.chart("Fresh", "DX"), target_score=1_005_000)

    chunithm_list = await skill.todo_list(replace(world.ctx(game="chunithm"), game_services={}))

    assert chunithm_list.table is None  # 츄니즘 투두는 없다


async def test_ap_lamp_goal_is_judged_with_the_maimai_lamp_order(skill, world) -> None:
    ctx = world.ctx()
    result = await skill.todo_add(ctx, chart=world.chart("Old Std", "STD"), lamp=ResolvedLamp(canonical="AP", min_score=None))
    assert result.error is None
    assert "올 퍼펙트" in (result.display.content if result.display else "")

    # FC뿐인 지금은 달성이 아니고, AP를 찍으면 동기화 점검이 완료 처리한다.
    improved = _play("Old Std", "STD", 1_005_000, combo_lamp="AP", chain_lamp="FS")
    sync = await world.sync(
        [improved, _play("Old Both", "DX", 1_000_000), _play("Fresh", "DX", 1_001_000)],
        recent=[replace(improved, achieved_at=_T1)],
    )
    section = await skill.on_maimai_sync_completed(
        world.ctx(), {"player_id": world.player_id, "sync_id": sync.sync_id}
    )

    assert section is not None
    assert "달성  Old Std STD MAS  올 퍼펙트" in (section.display.content if section.display else "")
    [goal] = await skill.models.list_goals_for_player(world.db, world.player_id)
    assert goal.status == "achieved"


async def test_song_goal_progress_in_sync_check_uses_percent(skill, world) -> None:
    await skill.todo_add(world.ctx(), chart=world.chart("Fresh", "DX"), target_score=1_005_000)
    improved = _play("Fresh", "DX", 1_003_000)
    sync = await world.sync(
        [_play("Old Std", "STD", 1_005_000), _play("Old Both", "DX", 1_000_000), improved],
        recent=[replace(improved, achieved_at=_T1)],
    )

    section = await skill.on_maimai_sync_completed(
        world.ctx(), {"player_id": world.player_id, "sync_id": sync.sync_id}
    )

    assert section is not None and section.display is not None
    assert "접근  Fresh DX MAS  100.5000%까지 0.2000% (현재 100.3000%)" in (section.display.content or "")
    assert section.data["approached"][0]["chart_type"] == "DX"


async def test_scheduled_notice_computes_with_the_goals_own_game(skill, world, db) -> None:
    """예약 이벤트의 ctx는 기본 게임(CHUNITHM) 서비스를 들고 있어도, 목표의 게임 기록으로 진행을 계산한다."""
    await skill.set_goal(world.ctx(), skill.GoalKind.RATING, target_value=950, due_at="2026-12-31")
    [goal] = await skill.models.list_goals_for_player(world.db, world.player_id)
    default_game_ctx = replace(world.ctx(), game="chunithm", services={**world.ctx().services, "records": object()})

    await skill.on_notify_due(default_game_ctx, {"goal_id": goal.id, "offset_days": 7})

    [(_, text)] = world.notify.dms
    assert "레이팅 950" in text and "현재 896" in text


# ---------------------------------------------------------------- 코드 검토(2026-10-31) 수정


async def test_slash_update_resolves_the_score_with_the_goals_own_game(skill, world) -> None:
    """주 게임이 CHUNITHM이어도 maimai 목표의 점수는 maimai 기준으로 해석한다(SSS+ = 100.5%)."""
    await skill.todo_add(world.ctx(), chart=world.chart("Fresh", "DX"), target_score=1_005_000)
    [goal] = await skill.models.list_goals_for_player(world.db, world.player_id)
    chunithm_terms = TermsService(profile_for("chunithm").terms_path, game="chunithm")
    ctx = replace(world.ctx(game="chunithm"), services={**world.ctx().services, "terms": chunithm_terms})

    result = await skill.update(ctx, goal.id, target_score="SSS+")

    assert result.error is None
    [updated] = await skill.models.list_goals_for_player(world.db, world.player_id)
    assert updated.target_value == 1_005_000


def test_song_rating_sum_multiplier_follows_the_metric(skill) -> None:
    chunithm = profile_for("chunithm")
    assert skill._song_rating_sum_multiplier(MAIMAI, "computed_rating") == 1
    assert skill._song_rating_sum_multiplier(MAIMAI, "best30_average") == 35
    assert skill._song_rating_sum_multiplier(MAIMAI, "new20_average") == 15
    assert skill._song_rating_sum_multiplier(chunithm, "computed_rating") == 50
    assert skill._song_rating_sum_multiplier(chunithm, "best30_average") == 30
    assert skill._song_rating_sum_multiplier(chunithm, "new20_average") == 20


async def test_folder_goal_defaults_to_played_without_a_clear_lamp(skill, world) -> None:
    result = await skill.set_goal(world.ctx(), skill.GoalKind.FOLDER, folder_level="13+")

    assert result.error is None
    [goal] = await skill.models.list_goals_for_player(world.db, world.player_id)
    assert goal.target_ref["condition"] == "played"


async def test_folder_goal_shows_the_games_own_lamp_name(skill, world) -> None:
    await skill.set_goal(
        world.ctx(), skill.GoalKind.FOLDER, folder_level="13+", folder_condition=skill.FolderCondition.ALL_JUSTICE
    )
    [goal] = await skill.models.list_goals_for_player(world.db, world.player_id)

    assert "올 퍼펙트" in skill._goal_label(goal)


async def test_overpower_is_refused_before_asking_for_a_sync(skill, world) -> None:
    result = await skill.set_goal(world.ctx(tags=frozenset()), skill.GoalKind.OVERPOWER, target_value=1)

    assert result.error is not None and "오버파워가 없어" in result.error


async def test_lamp_goal_only_takes_the_games_combo_lamps(skill, world) -> None:
    result = await skill.todo_add(
        world.ctx(), chart=world.chart("Fresh", "DX"), lamp=ResolvedLamp(canonical="AP+", min_score=None)
    )

    assert result.error is not None and "올 퍼펙트" in result.error


async def test_add_todos_needs_the_chart_type_in_a_typed_game(skill, world) -> None:
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.step("검색 결과")
    builder.row(
        tc.chart_ref("Fresh", "MAS"), {tc.SONG: tc.song_cell("Fresh"), tc.DIFFICULTY: tc.difficulty_cell("MAS")}
    )

    result = await skill.add_todos(world.ctx(), builder.build(), 1_005_000)

    assert result.error is not None and "종류" in result.error
    assert await skill.models.list_goals_for_player(world.db, world.player_id) == []


async def test_todo_remaining_cell_raw_value_is_in_the_shown_unit(skill, world) -> None:
    await skill.todo_add(world.ctx(), chart=world.chart("Fresh", "DX"), target_score=1_005_000)

    result = await skill.todo_list(world.ctx())

    assert result.table is not None
    [row] = result.table.rows
    remaining = row.cell("남은 점수")
    assert remaining.text == "0.4000%" and remaining.raw == 0.4  # 1.0%p 미만 같은 거르기가 보이는 숫자와 맞는다
