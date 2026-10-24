from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest

from core.events import EventBus
from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.game import records as records_module
from services.game.records import RecordsService
from services.game.sync_types import ChartPlay, ProfileSnapshot, SyncSnapshot
from services.maimai.profile import MAIMAI
from services.maimai.songdb import MaimaiSongDbService

_MIGRATIONS = "".join(
    Path(f"migrations/core/{name}").read_text()
    for name in (
        "001_players.sql", "002_player_tags.sql", "008_records.sql", "013_records_overpower.sql",
        "014_records_sync_log_snapshot_averages.sql", "018_records_profile.sql",
        "019_records_session_state.sql", "024_records_chart_type.sql",
    )
)

# otoge-db 항목 모양을 본뜬 만든 표본. 구곡(CiRCLE 이전)과 신곡(CiRCLE PLUS)이 섞여 있다.
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
_T2 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATIONS)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def songdb(tmp_path: Path) -> MaimaiSongDbService:
    path = tmp_path / "music-ex-intl.json"
    path.write_text(json.dumps(_SAMPLE, ensure_ascii=False), encoding="utf-8")
    return MaimaiSongDbService(path)


async def _setup(
    conn: aiosqlite.Connection, songdb: MaimaiSongDbService
) -> tuple[PlayersService, str, ToolContext, RecordsService]:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=songdb, players=players, game="maimai", profile=MAIMAI)
    return players, player.id, ctx, service


def _play(title: str, chart_type: str, score: int, **extra: object) -> ChartPlay:
    return ChartPlay(song_title=title, difficulty="MAS", chart_type=chart_type, score=score, rank="SSS+", **extra)  # pyright: ignore[reportArgumentType]


def _snapshot(*plays: ChartPlay, displayed: int | None = None, **kwargs: object) -> SyncSnapshot:
    return SyncSnapshot(
        profile=ProfileSnapshot(username="유니", displayed_rating=Decimal(displayed) if displayed is not None else None),
        all_plays=list(plays),
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


def _freeze_now(monkeypatch: pytest.MonkeyPatch, utc: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return utc if tz is not None else utc.replace(tzinfo=None)

    monkeypatch.setattr(records_module, "datetime", _Frozen)


_BASE_PLAYS = (
    _play("Old Std", "STD", 1_005_000),  # 22.4 × 12.4 × 1.005 → 279
    _play("Old Both", "DX", 1_000_000),  # 21.6 × 13.7 → 295
    _play("Fresh", "DX", 1_005_000),  # 22.4 × 14.9 × 1.005 → 335
)


async def test_first_sync_derives_frames_and_sums_rating(conn, songdb) -> None:
    players, player_id, ctx, service = await _setup(conn, songdb)

    result = await service.save_sync(player_id, _snapshot(*_BASE_PLAYS, displayed=909), ctx)

    assert result.is_first_sync is True
    assert result.computed_rating == Decimal(909)  # 구곡 279 + 295, 신곡 335. 나누지 않는다
    assert result.best30_average == Decimal("287.0000")
    assert result.new20_average == Decimal("335.0000")
    assert result.rating_mismatch is False
    assert await players.tags_for(player_id) == {"maimai-synced"}


async def test_rating_mismatch_is_detected_against_displayed_rating(conn, songdb) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb)

    result = await service.save_sync(player_id, _snapshot(*_BASE_PLAYS, displayed=908), ctx)

    assert result.rating_mismatch is True


async def test_std_and_dx_of_same_song_are_separate_rows(conn, songdb) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb)
    plays = (*_BASE_PLAYS, _play("Old Both", "STD", 990_000, dx_score=1000))

    await service.save_sync(player_id, _snapshot(*plays), ctx)

    dx = await service.pb_row(player_id, song_title="Old Both", difficulty="MAS", chart_type="DX")
    std = await service.pb_row(player_id, song_title="Old Both", difficulty="MAS", chart_type="STD")
    assert dx is not None and dx.const == 13.7
    assert std is not None and std.const == 12.6 and std.dx_score == 1000
    assert await service.pb_row(player_id, song_title="Old Both", difficulty="MAS") is None  # 종류 없는 키는 다른 채보


async def test_pb_rows_for_accepts_triples_and_pairs(conn, songdb) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb)
    await service.save_sync(player_id, _snapshot(*_BASE_PLAYS), ctx)

    found = await service.pb_rows_for(player_id, [("Old Std", "STD", "MAS"), ("Old Std", "MAS"), ("Fresh", "STD", "MAS")])

    assert set(found) == {("Old Std", "STD", "MAS")}
    assert found[("Old Std", "STD", "MAS")].score == 1_005_000


async def test_frames_use_the_net_lists_when_given(conn, songdb) -> None:
    """NET이 베스트/신곡 목록을 주면 서비스가 짜지 않고 그 목록을 믿는다."""
    _, player_id, ctx, service = await _setup(conn, songdb)
    snapshot = _snapshot(*_BASE_PLAYS, best=[_BASE_PLAYS[0]], new=[_BASE_PLAYS[2]])

    result = await service.save_sync(player_id, snapshot, ctx)

    assert result.computed_rating == Decimal(279 + 335)


async def test_second_sync_reports_chart_type_in_diff_and_raises_lamps(conn, songdb, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb)
    base = (*_BASE_PLAYS[:2], _play("Fresh", "DX", 1_001_000, combo_lamp="FC", chain_lamp="FS", dx_score=2000))
    _freeze_now(monkeypatch, _T1)
    await service.save_sync(player_id, _snapshot(*base), ctx)

    improved = _play("Fresh", "DX", 1_005_000, combo_lamp="AP", chain_lamp="FS", dx_score=2100)
    recent = replace(improved, achieved_at=datetime(2026, 10, 12, 20, 0, tzinfo=UTC))
    lamp_only = _play("Old Both", "DX", 1_000_000, combo_lamp="FC+")
    _freeze_now(monkeypatch, _T2)
    result = await service.save_sync(
        player_id, _snapshot(base[0], lamp_only, improved, recent=[recent]), ctx
    )

    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    [update] = diff.chart_updates
    assert (update.song_title, update.chart_type, update.difficulty) == ("Fresh", "DX", "MAS")
    assert update.old_score == 1_001_000 and update.new_score == 1_005_000
    assert update.new_rating == Decimal(335) and update.old_rating == Decimal(322)  # 21.6 × 14.9 × 1.001
    assert [(c.song_title, c.chart_type, c.old_combo_lamp, c.new_combo_lamp) for c in diff.lamp_changes] == [
        ("Old Both", "DX", "", "FC+")
    ]
    row = await service.pb_row(player_id, song_title="Fresh", difficulty="MAS", chart_type="DX")
    assert row is not None and (row.combo_lamp, row.chain_lamp, row.dx_score) == ("AP", "FS", 2100)


async def test_candidates_use_chart_type_and_target_rating(conn, tmp_path) -> None:
    fills = [
        {
            "title": f"Fill{i}", "artist": "A", "version": "10000", "image_url": "f.png", "intl": "1",
            "lev_mas": "12", "lev_mas_i": f"{12.0 + i / 10:.1f}", "lev_mas_notes": "300",
        }
        for i in range(8)  # 상수 12.0 ~ 12.7
    ]
    cand = {
        "title": "Cand", "artist": "A", "version": "10000", "image_url": "c.png", "intl": "1",
        "lev_mas": "12", "lev_mas_i": "12.4", "lev_mas_notes": "400",
        "dx_lev_mas": "12", "dx_lev_mas_i": "12.2", "dx_lev_mas_notes": "410",
    }
    path = tmp_path / "music-ex-intl.json"
    path.write_text(json.dumps([*fills, cand]), encoding="utf-8")
    _, player_id, ctx, service = await _setup(conn, MaimaiSongDbService(path))
    played = [_play(f"Fill{i}", "STD", 1_005_000) for i in range(8)]
    await service.save_sync(player_id, _snapshot(*played), ctx)

    hits = await service.candidates(player_id, min_const=12.0, max_const=13.0, count=10)

    # 프레임 3~8위 곡 레이팅은 270(12.0)~281(12.5). 기준 점수(SSS+)를 냈을 때 그 안에 드는 채보만 후보다.
    assert [(h.song_title, h.chart_type, h.upside) for h in hits] == [
        ("Cand", "STD", Decimal(279)), ("Cand", "DX", Decimal(274)),
    ]
    assert hits[0].remaining_score == 1_005_000  # 안 쳤으면 점수 0
