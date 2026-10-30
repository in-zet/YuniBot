# skills/report/tests/test_maimai_report.py
"""maimai 판 `report`: 진짜 RecordsService·MaimaiSongDbService로 동기화 → 도구를 부른다."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from core.events import EventBus
from core.tables import render_text
from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.game.chart_ref import ResolvedChart
from services.game.records import RecordsService
from services.game.sync_types import ChartPlay, ProfileSnapshot, SyncSnapshot
from services.game.terms import TermsService
from services.maimai.profile import MAIMAI
from services.maimai.songdb import MaimaiSongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_MIGRATIONS = "".join(
    Path(f"migrations/core/{name}").read_text()
    for name in (
        "001_players.sql", "002_player_tags.sql", "008_records.sql", "013_records_overpower.sql",
        "014_records_sync_log_snapshot_averages.sql", "018_records_profile.sql",
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
_T1 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_report_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(_MIGRATIONS)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


def _play(title: str, chart_type: str, score: int, **extra: object) -> ChartPlay:
    return ChartPlay(song_title=title, difficulty="MAS", chart_type=chart_type, score=score, rank="SSS+", **extra)  # pyright: ignore[reportArgumentType]


async def _synced(conn: aiosqlite.Connection, tmp_path: Path, *, fresh_score: int = 1_005_000) -> ToolContext:
    path = tmp_path / "music-ex-intl.json"
    path.write_text(json.dumps(_SAMPLE, ensure_ascii=False), encoding="utf-8")
    songdb = MaimaiSongDbService(path)
    players = PlayersService(conn)
    player = await players.create_player()
    records = RecordsService(conn=conn, songdb=songdb, players=players, game="maimai", profile=MAIMAI)
    terms = TermsService(MAIMAI.terms_path, game="maimai", percent_scores=True)
    ctx = ToolContext(
        player_id=player.id, tags=frozenset(), event_bus=EventBus(), game="maimai",
        services={"records": records, "songdb": songdb, "players": players, "terms": terms},
    )
    snapshot = SyncSnapshot(
        profile=ProfileSnapshot(username="유니", displayed_rating=Decimal(909)),
        all_plays=[
            _play("Old Std", "STD", 1_005_000, combo_lamp="FC", chain_lamp="FS", dx_score=1000),
            _play("Old Both", "DX", 1_000_000),
            _play("Fresh", "DX", fresh_score),
        ],
    )
    await records.save_sync(player.id, snapshot, ctx)
    return ctx


async def test_profile_uses_the_frame_names_and_integer_rating(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path)

    result = await skill.profile(ctx)

    assert result.error is None and result.display is not None
    content = result.display.content or ""
    assert result.data["computed_rating"] == "909"
    assert "구곡 35" in content and "신곡 15" in content and "베스트 30" not in content
    assert result.data["best30_average"] == "287.0000"


async def test_best_table_shows_chart_type_percent_scores_and_integer_ratings(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path)

    result = await skill.best(ctx)

    assert result.table is not None
    text = render_text(result.table).content
    assert "── 구곡 35 · 2채보 · 평균 287.0000 ──" in text
    assert "── 신곡 15 · 1채보 · 평균 335.0000 ──" in text
    assert "Old Std STD MAS 12.4  100.5000% SSS+  곡 레이팅 279" in text
    assert "Fresh DX MAS 14.9  100.5000% SSS+  곡 레이팅 335" in text


async def test_record_names_the_chart_type_lamps_and_dx_score(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path)
    song = ctx.songdb.song_by_title("Old Std")
    assert song is not None
    [std_chart] = [c for c in song.charts.values() if c.difficulty == "MAS"]
    chart = ResolvedChart(song=song, chart=std_chart)

    result = await skill.record(ctx, chart=chart)

    assert result.error is None and result.display is not None
    content = result.display.content or ""
    assert "Old Std STD MAS 12.4" in content
    assert "100.5000% SSS+  FC / FS" in content
    assert "DX 점수 1,000" in content
    assert result.data["chart_type"] == "STD" and result.data["dx_score"] == 1000


async def test_candidates_have_no_remaining_score_column_for_percent_games(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path)

    result = await skill.candidates(ctx)

    # 후보가 있든 없든 "남은 점수"는 maimai에 없다.
    if result.table is not None:
        assert all("남은 점수" not in column.name for column in result.table.columns)
    else:
        assert result.display is not None


async def test_sync_section_uses_the_maimai_records_and_shows_integer_rating(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path, fresh_score=1_001_000)
    # 두 번째 동기화: Fresh의 달성률이 올랐다.
    improved = _play("Fresh", "DX", 1_005_000)
    snapshot = SyncSnapshot(
        profile=ProfileSnapshot(username="유니", displayed_rating=Decimal(922)),
        all_plays=[_play("Old Std", "STD", 1_005_000), _play("Old Both", "DX", 1_000_000), improved],
        recent=[replace(improved, achieved_at=_T1)],
    )
    result = await ctx.records.save_sync(ctx.player_id, snapshot, ctx)
    assert result.has_new_plays

    section = await skill.on_maimai_sync_completed(
        ctx,
        {"player_id": ctx.player_id, "sync_id": result.sync_id, "has_new_plays": result.has_new_plays, "is_first_sync": False},
    )

    content = section.display.content
    assert "구곡 35" in content and "신곡 15" in content
    assert "Fresh DX MAS 14.9  100.1000% → 100.5000%" in content
    assert "곡 레이팅 322 → 335" in content
    assert section.data["current"]["computed_rating"] == "909"
    assert section.data["change"]["computed_rating"] == "+13"
    assert section.data["chart_updates"][0]["chart_type"] == "DX"
