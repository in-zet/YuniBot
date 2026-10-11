# tests/services/chunithm/test_records.py
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
from services.chunithm import records as records_module
from services.chunithm.chunithm_net import SyncFetch
from services.chunithm.records import RecordsService
from services.chunithm.records_calc import allowed_attacks, remaining_to_target
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
    assert result.best30_average is not None and result.new20_average is not None
    recombined = ((result.best30_average * 30 + result.new20_average * 20) / 50).quantize(
        Decimal("0.01")
    )
    assert recombined == result.computed_rating


async def test_second_sync_with_unimproved_score_does_not_overwrite_session_or_timestamp(
    conn, monkeypatch
) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    first_rows = await _pb_rows(conn, player.id)

    # 두 번째 동기화 — 다음 날, 같은 저장본이라 점수가 그대로다(개선 없음).
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 11, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 11, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    second_rows = await _pb_rows(conn, player.id)

    assert len(first_rows) == 50
    assert first_rows == second_rows


async def test_improved_score_updates_row_as_a_unit(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    target = _fixture_fetch().best30[0]
    # 기존 기록을 일부러 낮춰 두고 다시 동기화하면 저장본 점수로 올라가야 한다.
    await conn.execute(
        "UPDATE records_pb SET score = 1, rating = 'old', session_id = 'old', updated_at = 'old' "
        "WHERE player_id = ? AND song_title = ?",
        (player.id, target.song.title),
    )
    await conn.commit()

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT score, rating, session_id, updated_at FROM records_pb WHERE player_id = ? AND song_title = ?",
        (player.id, target.song.title),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["score"] == target.score
    assert "old" not in (row["rating"], row["session_id"], row["updated_at"])


async def test_session_date_uses_local_wall_clock_not_utc(conn, monkeypatch) -> None:
    """UTC로는 아직 10일이지만 로컬(예: KST)로는 11일 오전 5시(경계 4시 이후)인
    순간의 동기화는 11일 세션이어야 한다 — UTC를 넘기면 10일로 잘못 배정된다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 20, 0, tzinfo=UTC), local=datetime(2026, 10, 11, 5, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute("SELECT play_date FROM records_sessions WHERE player_id = ?", (player.id,))
    rows = await cursor.fetchall()
    assert [r[0] for r in rows] == ["2026-10-11"]
    cursor = await conn.execute("SELECT synced_at FROM records_sync_log WHERE player_id = ?", (player.id,))
    row = await cursor.fetchone()
    assert row is not None and row[0] == "2026-10-10T20:00:00+00:00"  # 저장 시각은 UTC 그대로


async def test_unknown_const_surfaces_as_not_computable(conn, monkeypatch) -> None:
    """상수를 모르는 채보가 하나라도 있으면 레이팅을 0으로 셈하지 않고 "계산 불가"로 남긴다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    songdb = SongDbService()
    fetch = _fixture_fetch()
    unknown_title = fetch.new20[0].song.title
    real_const_for = songdb.const_for
    monkeypatch.setattr(
        songdb, "const_for",
        lambda title, difficulty: None if title == unknown_title else real_const_for(title, difficulty),
    )
    service = RecordsService(conn=conn, songdb=songdb, players=players)

    result = await service.save_sync(player.id, fetch, ctx)

    assert result.computed_rating is None
    assert result.new20_average is None
    assert result.best30_average is not None
    assert result.rating_mismatch is True  # 대조할 수 없다
    cursor = await conn.execute(
        "SELECT rating FROM records_pb WHERE player_id = ? AND song_title = ?", (player.id, unknown_title)
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == "계산 불가"
    cursor = await conn.execute("SELECT computed_rating FROM records_sync_log WHERE player_id = ?", (player.id,))
    row = await cursor.fetchone()
    assert row is not None and row[0] == "계산 불가"


async def test_best_score_returns_saved_pb(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    fetch = _fixture_fetch()
    await service.save_sync(player.id, fetch, ctx)
    target = fetch.best30[0]

    score = await service.best_score(player.id, song_title=target.song.title, difficulty="MAS")

    assert score == target.score


async def test_best_score_returns_none_when_never_played(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    score = await service.best_score(player.id, song_title="존재하지않는곡", difficulty="MAS")

    assert score is None


async def test_last_sync_at_returns_most_recent(conn, monkeypatch) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    assert await service.last_sync_at(player.id) is None

    # 두 번 동기화해 ORDER BY synced_at DESC가 실제로 골라야 하는 상황을 만든다.
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 11, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 11, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)

    # 첫 번째(10일)가 아니라 나중(11일) 동기화 시각이어야 한다.
    assert await service.last_sync_at(player.id) == "2026-10-11T12:00:00+00:00"


async def test_allowed_attacks_and_remaining_to_target_are_exposed_passthroughs(conn) -> None:
    players = PlayersService(conn)
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    # 같은 순수 함수에 같은 인자를 넘긴 결과와 정확히 같아야 한다(인자 순서 포함).
    assert service.allowed_attacks(3000, 1_007_500) == allowed_attacks(3000, 1_007_500)
    assert service.remaining_to_target(3000, 1_000_000, 1_007_500) == remaining_to_target(
        3000, 1_000_000, 1_007_500
    )
    assert service.allowed_attacks(None, 1_007_500) is None
    assert service.remaining_to_target(None, 1_000_000, 1_007_500) is None

    # 기대값도 손으로 계산해 고정한다: (1,010,000 - 1,007,500) * 3000 / 510,000 = 14.70...
    assert service.allowed_attacks(3000, 1_007_500) == Decimal("14.70")
    # 남은 점수 7,500, 어택 7,500 * 3000 / 510,000 = 44.11...
    assert service.remaining_to_target(3000, 1_000_000, 1_007_500) == (7500, Decimal("44.11"))


def _freeze_now(monkeypatch: pytest.MonkeyPatch, *, utc: datetime, local: datetime) -> None:
    """records 모듈의 datetime.now를 고정한다. now(UTC)는 utc를, 인자 없는 now()는
    local의 벽시계 값(naive)을 돌려준다 — local은 tzinfo만 떼어 쓴다(벽시계 숫자를
    그대로 적으려는 것이지 UTC라는 뜻이 아니다)."""
    local_naive = local.replace(tzinfo=None)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return utc if tz is not None else local_naive

    monkeypatch.setattr(records_module, "datetime", _FrozenDatetime)


async def _pb_rows(conn: aiosqlite.Connection, player_id: str) -> list[tuple[object, ...]]:
    cursor = await conn.execute(
        "SELECT song_title, difficulty, score, rating, session_id, updated_at FROM records_pb "
        "WHERE player_id = ? ORDER BY song_title, difficulty",
        (player_id,),
    )
    return [tuple(r) for r in await cursor.fetchall()]
