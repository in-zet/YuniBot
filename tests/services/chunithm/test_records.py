# tests/services/chunithm/test_records.py
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest
from chuni_penguin.adapters.chunithm_net.parser import (
    parse_music_for_rating,
    parse_player_data,
)
from selectolax.lexbor import LexborHTMLParser

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.chunithm_net import SyncFetch
from services.chunithm.records import RecordsService
from services.chunithm.songdb import SongDbService
from services.common.players import PlayersService

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_RECORDS_MIGRATION = Path("migrations/core/008_records.sql").read_text()

_PROFILE_HTML = Path("docs/reference/chunithm-net/player_data.html").read_text(encoding="utf-8")
_BEST_HTML = Path("docs/reference/chunithm-net/rating_detail_best.html").read_text(encoding="utf-8")
_RECENT_HTML = Path("docs/reference/chunithm-net/rating_detail_recent.html").read_text(encoding="utf-8")


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_PLAYERS_MIGRATION + _RECORDS_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


def _fixture_fetch() -> SyncFetch:
    profile = parse_player_data(LexborHTMLParser(_PROFILE_HTML))
    best30 = parse_music_for_rating(LexborHTMLParser(_BEST_HTML))
    new20 = parse_music_for_rating(LexborHTMLParser(_RECENT_HTML))
    return SyncFetch(profile=profile, best30=best30, new20=new20, recent_scores=[])


async def test_first_sync_sets_baseline_and_grants_synced_tag(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.is_first_sync is True
    assert await players.tags_for(player.id) == {"chunithm-synced"}


async def test_second_sync_is_not_first(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    await service.save_sync(player.id, _fixture_fetch(), ctx)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.is_first_sync is False


async def test_save_sync_upserts_personal_bests(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM records_pb WHERE player_id = ?", (player.id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 50  # 베스트30 + 신곡20, 중복 없음(제목이 전부 다른 저장본 기준)


async def test_save_sync_logs_sync_entry(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM records_sync_log WHERE player_id = ?", (player.id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_computed_rating_matches_fixture_displayed_rating(conn) -> None:
    """이번 단계 전체의 핵심 통과 기준: 저장본 베스트30+신곡20으로 계산한 전체
    레이팅과 베스트 평균이 저장본의 표시값과 맞아야 한다. 두 저장본이 같은
    시점(같은 로그인 세션)에서 받은 것이어야 이 테스트가 의미 있다 — 이 계획을
    쓰던 중 실제로 세 파일 중 하나가 다른 시점이라 어긋난 적이 있다(player_data.html을
    베스트30/신곡20과 같은 시점으로 다시 저장해 고쳤다).

    전체 레이팅(16.94)은 CHUNITHM-NET이 숫자 이미지로 보여주는 실제 표시값과
    대조한다. 베스트30 평균(17.02)·신곡20 평균(16.83)은 CHUNITHM-NET이 텍스트로
    따로 보여주는 페이지가 없어 저장본과 직접 대조할 수는 없지만, 공식대로
    (30곡·20곡 가중) 다시 합쳤을 때 이미 검증된 전체 레이팅과 정확히 같아야 한다
    — 이 일관성이 "베스트 평균"이 올바로 계산됐다는 증거다. 아래 숫자는 지금
    저장된 두 파일(2026-10-11)로 실제로 계산해 확인한 값이다 — 저장본을 다시
    받으면 이 상수들도 다시 계산해서 맞춰야 한다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.displayed_rating is not None
    assert result.rating_mismatch is False, (
        f"계산값 {result.computed_rating} != 표시값 {result.displayed_rating}"
    )
    assert result.computed_rating == Decimal("16.94")
    assert result.best30_average == Decimal("17.02")
    assert result.new20_average == Decimal("16.83")

    # 베스트 평균 일관성: 30곡·20곡 가중 재합산이 이미 검증된 전체 레이팅과 같아야 한다.
    recombined = ((result.best30_average * 30 + result.new20_average * 20) / 50).quantize(
        Decimal("0.01")
    )
    assert recombined == result.computed_rating
