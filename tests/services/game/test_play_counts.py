# tests/services/game/test_play_counts.py
"""플레이 횟수 저장·수집(2026-11-28 사용자 결정). NET·기록·곡 DB는 가짜다."""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import aiosqlite
import pytest

from core.migrations import run_migrations
from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.game.play_counts import PlayActivity, PlayCountService, RecentPlay

_JST = timezone(timedelta(hours=9))


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await run_migrations(connection, roots=[("core", Path("migrations/core"))])
    try:
        yield connection
    finally:
        await connection.close()


@dataclass(slots=True)
class _Song:
    id: int


@dataclass(slots=True)
class _Songdb:
    ids: dict[str, int]

    def song_by_title(self, title: str) -> _Song | None:
        return _Song(self.ids[title]) if title in self.ids else None


@dataclass(slots=True)
class _Pb:
    song_title: str
    chart_type: str = ""
    difficulty: str = "MAS"


@dataclass(slots=True)
class _Records:
    played: list[_Pb]

    async def all_pb_rows(self, player_id: str) -> list[_Pb]:
        return self.played


@dataclass(slots=True, kw_only=True)
class _Net:
    activity: PlayActivity
    counts: dict[Any, dict[str, int] | None]
    asked: list[list[Any]] = field(default_factory=list)

    async def play_activity(self, player_id: str, *, ctx: ToolContext | None = None) -> PlayActivity:
        return self.activity

    async def play_counts(
        self, player_id: str, keys: Sequence[Any], *, ctx: ToolContext | None = None
    ) -> Mapping[Any, dict[str, int] | None]:
        self.asked.append(list(keys))
        return {key: self.counts[key] for key in keys if key in self.counts}


def _play(title: str, minute: int, track: int, chart_type: str = "") -> RecentPlay:
    return RecentPlay(title=title, chart_type=chart_type, played_at=datetime(2026, 11, 28, 12, minute, tzinfo=_JST), track=track)


async def _service(conn: aiosqlite.Connection, net: _Net, *, game: str = "chunithm") -> tuple[PlayCountService, str]:
    player = await PlayersService(conn).create_player()
    service = PlayCountService(
        conn=conn, game=game, records=cast(Any, _Records([_Pb("A"), _Pb("B"), _Pb("C"), _Pb("A", "", "WE")])),
        songdb=cast(Any, _Songdb({"A": 1, "B": 2, "C": 3})), net=net,
    )
    return service, player.id


async def test_first_collection_fetches_every_played_song_and_reads_back(conn: aiosqlite.Connection) -> None:
    net = _Net(activity=PlayActivity(total_plays=10, recent=[_play("A", 0, 1)]),
               counts={1: {"MAS": 5}, 2: {"EXP": 1}, 3: {"MAS": 2}})
    service, pid = await _service(conn, net)

    result = await service.collect(pid)

    assert result is not None and (result.mode, result.asked, result.fetched) == ("full", 3, 3)
    assert net.asked == [[1, 2, 3]]
    assert await service.counts(pid, [("1", ""), ("9", "")]) == {("1", ""): {"MAS": 5}}
    state = await service.state(pid)
    assert state is not None and state.complete and state.total_plays == 10


async def test_songs_the_net_blocks_count_as_done_and_read_back_as_unknown(conn: aiosqlite.Connection) -> None:
    net = _Net(activity=PlayActivity(total_plays=10, recent=[_play("A", 0, 1)]), counts={1: {"MAS": 5}, 2: None, 3: {}})
    service, pid = await _service(conn, net)

    await service.collect(pid)

    state = await service.state(pid)
    assert state is not None and state.complete  # 막힌 곡 때문에 다음 동기화가 다시 전체 수집이 되지 않는다
    assert await service.counts(pid, [("2", "")]) == {("2", ""): None}


async def test_new_credits_all_in_the_recent_list_refetch_only_those_songs(conn: aiosqlite.Connection) -> None:
    net = _Net(activity=PlayActivity(total_plays=10, recent=[_play("A", 0, 1)]), counts={1: {"MAS": 5}, 2: {}, 3: {}})
    service, pid = await _service(conn, net)
    await service.collect(pid)

    # 한 크레딧(곡 번호 1~3) 더 쳤고 총 플레이 횟수도 1 늘었다 → 그 판들의 곡만 다시 받는다.
    net.activity = PlayActivity(total_plays=11, recent=[_play("C", 13, 3), _play("B", 10, 2), _play("B", 7, 1), _play("A", 0, 1)])
    net.counts[2] = {"MAS": 2}
    result = await service.collect(pid)

    assert result is not None and result.mode == "recent"
    assert net.asked[-1] == [3, 2]
    assert await service.counts(pid, [("2", "")]) == {("2", ""): {"MAS": 2}}


async def test_plays_missing_from_the_recent_list_refetch_everything(conn: aiosqlite.Connection) -> None:
    net = _Net(activity=PlayActivity(total_plays=10, recent=[_play("A", 0, 1)]), counts={1: {}, 2: {}, 3: {}})
    service, pid = await _service(conn, net)
    await service.collect(pid)

    # 총 플레이 횟수는 3 늘었는데 최근 목록에는 새 크레딧이 하나뿐 → 목록 밖에서 친 판이 있다.
    net.activity = PlayActivity(total_plays=13, recent=[_play("B", 7, 1), _play("A", 0, 1)])
    result = await service.collect(pid)

    assert result is not None and result.mode == "full" and net.asked[-1] == [1, 2, 3]


async def test_no_new_plays_fetches_nothing(conn: aiosqlite.Connection) -> None:
    net = _Net(activity=PlayActivity(total_plays=10, recent=[_play("A", 0, 1)]), counts={1: {}, 2: {}, 3: {}})
    service, pid = await _service(conn, net)
    await service.collect(pid)

    result = await service.collect(pid)

    assert result is not None and result.mode == "none" and len(net.asked) == 1


async def test_unreadable_songs_leave_the_state_incomplete_so_the_next_run_is_full(conn: aiosqlite.Connection) -> None:
    net = _Net(activity=PlayActivity(total_plays=10, recent=[_play("A", 0, 1)]), counts={1: {}, 2: {}})
    service, pid = await _service(conn, net)
    await service.collect(pid)
    state = await service.state(pid)
    assert state is not None and not state.complete

    net.activity = PlayActivity(total_plays=11, recent=[_play("B", 7, 1), _play("A", 0, 1)])
    result = await service.collect(pid)

    assert result is not None and result.mode == "full"


async def test_maimai_keys_are_title_and_chart_type(conn: aiosqlite.Connection) -> None:
    net = _Net(activity=PlayActivity(total_plays=3, recent=[]), counts={("A", "DX"): {"MAS": 4}})
    player = await PlayersService(conn).create_player()
    service = PlayCountService(
        conn=conn, game="maimai", records=cast(Any, _Records([_Pb("A", "DX"), _Pb("宴곡", "DX", "UTA")])),
        songdb=cast(Any, _Songdb({})), net=net,
    )

    await service.collect(player.id)

    assert net.asked == [[("A", "DX")]]  # 宴은 받을 수 없어 빼고, 그래도 "다 받음"이다
    state = await service.state(player.id)
    assert state is not None and state.complete
    assert await service.counts(player.id, [("A", "DX")]) == {("A", "DX"): {"MAS": 4}}
    # 다른 게임 저장본과 섞이지 않는다.
    other = PlayCountService(conn=conn, game="chunithm", records=cast(Any, _Records([])), songdb=cast(Any, _Songdb({})), net=None)
    assert await other.state(player.id) is None
