# skills/report/tests/test_arcaea_report.py
"""Arcaea 판 `report`(19단계 3단계): 진짜 RecordsService·ArcaeaSongDbService로 동기화 → 도구를 부른다."""
from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from core.events import EventBus
from core.migrations import run_migrations
from core.tables import render_text
from core.tool_types import ToolContext
from services.arcaea.profile import ARCAEA
from services.arcaea.songdb import ArcaeaSongDbService
from services.common.players import PlayersService
from services.game.chart_ref import ResolvedChart
from services.game.play_counts import PlayCountService
from services.game.records import RecordsService
from services.game.sync_types import ChartPlay, ProfileSnapshot, SyncSnapshot
from services.game.terms import TermsService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
# 상수 10.0~11.1인 FTR 12곡. 모두 9,900,000점(EX+)이라 곡 포텐셜 = 상수 + 1.5 + 0.2.
_SONGS = {"songs": [
    {"title": f"Song {i:02d}", "artist": "A", "version": "6.0.0",
     "charts": [{"difficulty": "FTR", "level": "10", "constant": round(10.0 + i / 10, 1), "notes": 1000}]}
    for i in range(12)
]}


@pytest.fixture
def skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_arcaea_report_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await run_migrations(connection, roots=[("core", Path("migrations/core"))])
    try:
        yield connection
    finally:
        await connection.close()


async def _synced(conn: aiosqlite.Connection, tmp_path: Path) -> ToolContext:
    path = tmp_path / "songs.json"
    path.write_text(json.dumps(_SONGS), encoding="utf-8")
    songdb = ArcaeaSongDbService(path, conn=conn)
    players = PlayersService(conn)
    player = await players.create_player()
    records = RecordsService(conn=conn, songdb=songdb, players=players, game="arcaea")
    play_counts = PlayCountService(conn=conn, game="arcaea", records=records, songdb=songdb, net=None)
    terms = TermsService(ARCAEA.terms_path, game="arcaea")
    ctx = ToolContext(
        player_id=player.id, tags=frozenset(), event_bus=EventBus(), game="arcaea",
        services={"records": records, "songdb": songdb, "players": players, "terms": terms, "play_counts": play_counts},
    )
    plays = [ChartPlay(song_title=f"Song {i:02d}", difficulty="FTR", score=9_900_000, rank="EX+",
                       clear_lamp="HARD CLEAR") for i in range(12)]
    snapshot = SyncSnapshot(profile=ProfileSnapshot(username="Hikari", displayed_rating=Decimal("4.508")),
                            best=plays, new=[], all_plays=plays)
    result = await records.save_sync(player.id, snapshot, ctx)
    assert not result.rating_mismatch, result.computed_rating
    await play_counts.store_counts(player.id, {("Song 11", ""): {"FTR": 7}})
    return ctx


async def test_profile_says_potential_and_has_no_new_frame_line(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path)

    result = await skill.profile(ctx)

    content = result.display.content if result.display else ""
    assert "포텐셜" in content and "베스트 50" in content and "신곡" not in content
    assert result.data["computed_rating"] == "4.508"  # 12곡뿐이라 나머지 38칸은 0이다 and "new20_average" not in result.data


async def test_best_is_one_frame_and_the_top_ten_count_twice(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path)

    result = await skill.best(ctx)

    assert result.table is not None
    text = render_text(result.table).content
    assert "베스트 50" in text and "신곡" not in text
    contribution = [row.cell("기여").raw for row in result.table.rows]
    assert contribution[0] == pytest.approx(2 * 12.8 / 60)  # Song 11: 11.1 + 1.5 + 0.2, 상위 10곡이라 두 번
    assert contribution[10] == pytest.approx(11.8 / 60)  # Song 01: 10.1 + 1.7, 11위는 한 번
    assert result.table.rows[0].cell("기여").text == "0.42666"


async def test_play_count_is_this_years(skill, conn, tmp_path) -> None:
    ctx = await _synced(conn, tmp_path)
    song = ctx.songdb.song_by_title("Song 11")
    assert song is not None

    result = await skill.play_count(ctx, chart=ResolvedChart(song=song, chart=song.charts["FTR"]))

    assert result.data["play_count"] == 7 and result.data["scope"] == "올해"
    assert result.display is not None and result.display.content == "Song 11 FTR  올해 7회"


async def test_sync_section_has_potential_lines_without_a_new_frame(skill, conn, tmp_path) -> None:
    from datetime import UTC, datetime

    ctx = await _synced(conn, tmp_path)
    improved = ChartPlay(song_title="Song 00", difficulty="FTR", score=10_000_500, rank="EX+",
                         clear_lamp="HARD CLEAR", combo_lamp="PURE MEMORY")
    others = [ChartPlay(song_title=f"Song {i:02d}", difficulty="FTR", score=9_900_000, rank="EX+",
                        clear_lamp="HARD CLEAR") for i in range(1, 12)]
    played = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    snapshot = SyncSnapshot(
        profile=ProfileSnapshot(username="Hikari", displayed_rating=None),
        best=[improved, *others], new=[], all_plays=[improved, *others],
        recent=[ChartPlay(song_title="Song 00", difficulty="FTR", score=10_000_500, rank="EX+", achieved_at=played)],
    )
    result = await ctx.records.save_sync(ctx.player_id, snapshot, ctx)

    section = await skill.on_arcaea_sync_completed(
        ctx, {"player_id": ctx.player_id, "sync_id": result.sync_id, "has_new_plays": result.has_new_plays,
              "is_first_sync": False},
    )

    content = section.display.content
    assert "포텐셜" in content and "베스트 50" in content and "신곡" not in content
    assert "Song 00 FTR 10.0" in content and "9,900,000 → 10,000,500" in content
