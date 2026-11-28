# tests/services/arcaea/test_arcaea_net.py
"""Arcaea Online 접속 서비스. 응답은 실제 표본(`docs/reference/arcaea-online/`)과 그 모양을 줄인 가짜다."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
from chuni_penguin.adapters.errors import AuthenticationError
from cryptography.fernet import Fernet

from core.events import EventBus
from core.migrations import run_migrations
from core.tool_types import ToolContext
from services.arcaea.arcaea_net import (
    ArcaeaNetService,
    ArcaeaOnlineAdapter,
    NotSubscribedError,
    observed_const,
    parse_sid,
    rank_of,
)
from services.arcaea.songdb import ArcaeaSongDbService
from services.common.players import PlayersService
from services.common.secrets import SecretsService

_REF = Path("docs/reference/arcaea-online")
_BEST = json.loads((_REF / "score_rating_me.json").read_text())["value"]["best_rated_scores"]
_PAGE = json.loads((_REF / "score_song_me_all_ftr_p1.json").read_text())["value"]["scores"]

_SONGS = {"songs": [
    {"title": "Sheriruth", "artist": "Team Grimoire", "version": "1.5.0", "charts": [
        {"difficulty": "FTR", "level": "10", "constant": 10.5, "notes": 1151}]},
    {"title": "Genesis", "artist": "Iris", "version": "1.0.0", "charts": [{"difficulty": "FTR", "constant": 8.2}]},
    {"title": "Genesis", "artist": "Morrigan feat.Lily", "version": "3.0.0", "charts": [{"difficulty": "FTR", "constant": 9.8}]},
]}


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await run_migrations(connection, roots=[("core", Path("migrations/core"))])
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def songdb(tmp_path: Path, conn: aiosqlite.Connection) -> ArcaeaSongDbService:
    path = tmp_path / "songs.json"
    path.write_text(json.dumps(_SONGS), encoding="utf-8")
    return ArcaeaSongDbService(path, conn=conn)


def _entry(song_id: str, title: str, difficulty: int, score: int, *, artist: str = "", clear: int = 1,
           best_clear: int | None = None, played: int = 1_795_835_208_748, yearly: int = 3) -> dict[str, Any]:
    return {
        "song_id": song_id, "title": {"en": title, "ja": title}, "artist": artist, "difficulty": difficulty,
        "score": score, "clear_type": clear, "best_clear_type": clear if best_clear is None else best_clear,
        "time_played": played, "yearly_play_count": yearly,
    }


class _FakeAdapter:
    def __init__(self, *, user: dict[str, Any] | None = None, best: list[dict[str, Any]] | None = None,
                 pages: Mapping[int, list[dict[str, Any]]] | None = None, fail: Exception | None = None,
                 sid_after_login: str = "new-sid") -> None:
        self.user = user or {"display_name": "Hikari", "rating": 1250, "recent_score": []}
        self.best = best or []
        self.pages = pages or {}
        self.fail = fail
        self.sid: str | None = None
        self.sid_after_login = sid_after_login
        self.requests: list[tuple[int, int]] = []
        self.logins: list[tuple[str, str]] = []
        self.closed = False

    def session_cookie(self) -> str | None:
        return self.sid

    async def login(self, username: str, password: str) -> None:
        self.logins.append((username, password))
        if self.fail is not None:
            raise self.fail
        self.sid = self.sid_after_login

    async def get_user(self) -> dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        return self.user

    async def get_best(self) -> list[dict[str, Any]]:
        return self.best

    async def get_scores(self, difficulty: int, page: int) -> tuple[list[dict[str, Any]], int]:
        self.requests.append((difficulty, page))
        entries = self.pages.get(difficulty, [])
        return entries[(page - 1) * 10: page * 10], len(entries)

    async def aclose(self) -> None:
        self.closed = True


async def _service(conn: aiosqlite.Connection, songdb: ArcaeaSongDbService, adapter: _FakeAdapter,
                   saved: dict[str, Any] | None = None) -> tuple[ArcaeaNetService, PlayersService, list[str | None]]:
    players = PlayersService(conn)
    opened: list[str | None] = []

    def factory(sid: str | None) -> _FakeAdapter:
        opened.append(sid)
        adapter.sid = adapter.sid or sid
        return adapter

    async def save(player_id: str, counts: Mapping[tuple[str, str], dict[str, int]]) -> None:
        if saved is not None:
            saved[player_id] = dict(counts)

    service = ArcaeaNetService(conn=conn, secrets=SecretsService(Fernet.generate_key()), players=players,
                               songdb=songdb, save_play_counts=save, adapter_factory=factory)
    return service, players, opened


# ---------------------------------------------------------------- 순수 함수


def test_observed_const_reverses_the_7_0_formula_on_the_real_sample() -> None:
    consts = {(e["song_id"], e["difficulty"]): observed_const(e) for e in _BEST}
    assert consts[("gothiveofra", 2)] == 9.8  # 9,937,688점 11.68844 − 1.68844 − 클리어 0.2
    assert all(value is not None for value in consts.values())


def test_observed_const_refuses_values_that_do_not_land_on_one_decimal() -> None:
    assert observed_const({"rating": 11.6543, "score": 9_937_688, "clear_type": 1}) is None


@pytest.mark.parametrize(("score", "rank"), [(10_001_000, "EX+"), (9_900_000, "EX+"), (9_899_999, "EX"),
                                             (9_500_000, "AA"), (8_599_999, "D")])
def test_rank_of(score: int, rank: str) -> None:
    assert rank_of(score) == rank


@pytest.mark.parametrize(("pasted", "sid"), [
    ("ctrcode=; _ga=GA1.1; sid=abc.def; _ga_X=1", "abc.def"),
    ("Cookie: sid=xyz", "xyz"),
    ("bare-value", "bare-value"),
    ("ctrcode=; _ga=1", None),
])
def test_parse_sid(pasted: str, sid: str | None) -> None:
    assert parse_sid(pasted) == sid


# ---------------------------------------------------------------- 동기화


async def test_fetch_snapshot_pages_every_difficulty_and_converts_scores(conn, songdb) -> None:
    ftr = [_entry(f"s{i}", "Sheriruth" if i == 0 else f"Song {i}", 2, 9_850_000 + i) for i in range(12)]
    adapter = _FakeAdapter(
        user={"display_name": "Hikari", "rating": 11366, "recent_score": [_entry("s0", "Sheriruth", 2, 9_000_000)]},
        pages={2: ftr, 4: [_entry("gen", "Genesis", 4, 10_000_500, artist="Morrigan feat.Lily", clear=3)]},
    )
    saved: dict[str, Any] = {}
    service, players, _ = await _service(conn, songdb, adapter, saved)
    player = await players.create_player()
    await service.login_with_cookie(player.id, "sid=abc")

    snapshot = await service.fetch_snapshot(player.id)

    assert snapshot.profile.username == "Hikari" and snapshot.profile.displayed_rating == Decimal("11.366")
    assert (2, 1) in adapter.requests and (2, 2) in adapter.requests and (2, 3) not in adapter.requests
    assert len(snapshot.all_plays) == 13 and snapshot.new == []
    genesis = next(p for p in snapshot.all_plays if p.difficulty == "ETR")
    assert genesis.song_title == "Genesis (Morrigan feat.Lily)"  # 같은 제목은 작곡가로 가른다
    assert (genesis.combo_lamp, genesis.clear_lamp, genesis.rank) == ("PURE MEMORY", "", "EX+")
    assert all(p.achieved_at is not None and p.achieved_at.utcoffset() is not None for p in snapshot.recent)
    assert len(snapshot.recent) == 14  # 기록 13 + 마지막 한 판
    assert saved[player.id][("Sheriruth", "")] == {"FTR": 3}


async def test_best_scores_teach_the_songdb_constants_it_lacks(conn, songdb) -> None:
    best = [_entry("new", "Brand New", 2, 9_937_688) | {"rating": 11.68844}]
    adapter = _FakeAdapter(best=best)
    service, players, _ = await _service(conn, songdb, adapter)
    player = await players.create_player()
    await service.login_with_cookie(player.id, "sid=abc")

    snapshot = await service.fetch_snapshot(player.id)

    assert snapshot.best is not None and snapshot.best[0].song_title == "Brand New"
    assert songdb.const_for("Brand New", "FTR") == 9.8
    reloaded = ArcaeaSongDbService(Path("missing.json"), conn=conn)
    await reloaded.load_observed()
    assert reloaded.const_for("Brand New", "FTR") == 9.8  # 저장해 둬서 다시 켜도 남는다


async def test_real_sample_page_converts(conn, songdb) -> None:
    adapter = _FakeAdapter(pages={2: _PAGE}, best=_BEST)
    service, players, _ = await _service(conn, songdb, adapter)
    player = await players.create_player()
    await service.login_with_cookie(player.id, "sid=abc")

    snapshot = await service.fetch_snapshot(player.id)

    assert len(snapshot.all_plays) == 10 and snapshot.best is not None and len(snapshot.best) == 50
    assert next(p for p in snapshot.all_plays if p.song_title == "Sheriruth").difficulty == "FTR"


# ---------------------------------------------------------------- 로그인


async def test_cookie_login_stores_only_the_sid_and_tags_the_player(conn, songdb) -> None:
    adapter = _FakeAdapter()
    service, players, opened = await _service(conn, songdb, adapter)
    player = await players.create_player()

    profile = await service.login_with_cookie(player.id, "ctrcode=; sid=abc; _ga=1")

    assert profile.username == "Hikari" and opened == ["abc"]
    assert "arcaea-linked" in await players.tags_for(player.id)
    status = await service.status(player.id)
    assert status.linked and status.nickname == "Hikari"
    row = await (await conn.execute("SELECT encrypted_cookie FROM arcaea_sessions")).fetchone()
    assert row is not None and b"abc" not in row[0]


async def test_credential_login_keeps_the_session_cookie(conn, songdb) -> None:
    adapter = _FakeAdapter()
    service, players, opened = await _service(conn, songdb, adapter)
    player = await players.create_player()

    await service.login_with_credentials(player.id, "me@example.com", "pw")

    assert opened == [None] and adapter.logins == [("me@example.com", "pw")]
    assert (await service.status(player.id)).linked


async def test_bad_cookie_is_an_authentication_error_and_stores_nothing(conn, songdb) -> None:
    service, players, _ = await _service(conn, songdb, _FakeAdapter(fail=AuthenticationError()))
    player = await players.create_player()

    with pytest.raises(AuthenticationError):
        await service.login_with_cookie(player.id, "sid=expired")
    with pytest.raises(AuthenticationError):
        await service.login_with_cookie(player.id, "ctrcode=")
    assert not (await service.status(player.id)).linked


async def test_expired_cookie_during_sync_unlinks_and_announces(conn, songdb) -> None:
    adapter = _FakeAdapter()
    service, players, _ = await _service(conn, songdb, adapter)
    player = await players.create_player()
    await service.login_with_cookie(player.id, "sid=abc")
    adapter.fail = AuthenticationError()
    bus = EventBus()
    seen: list[str] = []

    async def emit_notify(event_name: str, payload: Any, *, player_id: str, guild_id: str | None,
                          game: str | None = None) -> None:
        seen.append(event_name)

    bus.emit_notify = emit_notify
    ctx = ToolContext(player_id=player.id, tags=frozenset(), event_bus=bus, services={})

    with pytest.raises(AuthenticationError):
        await service.fetch_snapshot(player.id, ctx=ctx)

    assert "arcaea-linked" not in await players.tags_for(player.id)
    assert seen == ["arcaea.login.expired"] and adapter.closed


async def test_logout_deletes_the_cookie(conn, songdb) -> None:
    service, players, _ = await _service(conn, songdb, _FakeAdapter())
    player = await players.create_player()
    await service.login_with_cookie(player.id, "sid=abc")

    await service.logout(player.id)

    assert not (await service.status(player.id)).linked
    with pytest.raises(ConnectionError):
        await service.fetch_snapshot(player.id)


# ---------------------------------------------------------------- 실제 연결(httpx 가짜 전송)


async def test_adapter_maps_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/webapi/user/me":
            return httpx.Response(401, json={"success": False})
        if request.url.path == "/webapi/score/rating/me":
            return httpx.Response(403, json={"success": False})
        assert request.url.params["sort"] == "date"
        assert request.headers["cookie"] == "sid=abc"
        return httpx.Response(200, json={"success": True, "value": {"scores": _PAGE, "count": 199}})

    adapter = ArcaeaOnlineAdapter("abc", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AuthenticationError):
            await adapter.get_user()
        with pytest.raises(NotSubscribedError):
            await adapter.get_best()
        scores, count = await adapter.get_scores(2, 1)
        assert len(scores) == 10 and count == 199
    finally:
        await adapter.aclose()


async def test_adapter_login_sends_the_site_form_and_reads_the_new_sid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/me":
            return httpx.Response(401, headers={"set-cookie": "csrf=tok; Domain=.lowiro.com; Path=/"})
        if request.url.path == "/auth/login":
            assert json.loads(request.content) == {"email": "me", "password": "pw"}
            assert request.headers["x-csrf-token"] == "tok"
            # 실제 응답은 다른 API의 {"success", "value"} 모양이 아니다(2026-11-28 실기) — 본문은 보지 않는다.
            return httpx.Response(200, json={"isLoggedIn": True},
                                  headers={"set-cookie": "sid=fresh; Domain=.lowiro.com; Path=/"})
        raise AssertionError(request.url)

    adapter = ArcaeaOnlineAdapter(None, transport=httpx.MockTransport(handler))
    try:
        await adapter.login("me", "pw")
        assert adapter.session_cookie() == "fresh"
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------- 기록 저장까지


async def test_synced_potential_matches_arcaea_online_on_the_real_sample(conn, tmp_path: Path) -> None:
    """곡 DB가 비어 있어도 베스트 50의 곡별 포텐셜에서 상수를 배워, 계산한 포텐셜이 프로필 값(11.366)과 같다."""
    from services.game.records import RecordsService

    songdb = ArcaeaSongDbService(tmp_path / "none.json", conn=conn)
    adapter = _FakeAdapter(user={"display_name": "Hikari", "rating": 11366, "recent_score": []},
                           best=_BEST, pages={2: _PAGE})
    service, players, _ = await _service(conn, songdb, adapter)
    player = await players.create_player()
    await service.login_with_cookie(player.id, "sid=abc")
    records = RecordsService(conn=conn, songdb=songdb, players=players, game="arcaea")
    ctx = ToolContext(player_id=player.id, tags=frozenset(), game="arcaea", event_bus=EventBus(), services={})

    result = await records.save_sync(player.id, await service.fetch_snapshot(player.id), ctx)

    assert result.computed_rating is not None
    assert str(result.computed_rating) == "11.366" and result.displayed_rating == Decimal("11.366")
    assert not result.rating_mismatch
    assert "arcaea-synced" in await players.tags_for(player.id)


async def test_adapter_login_with_a_wrong_password_is_an_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/me":
            return httpx.Response(401)
        return httpx.Response(401, json={"success": False})

    adapter = ArcaeaOnlineAdapter(None, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AuthenticationError):
            await adapter.login("me", "wrong")
    finally:
        await adapter.aclose()


async def test_sync_keeps_the_official_jacket_and_the_current_character(conn, songdb) -> None:
    """곡 DB에 재킷이 없는 신곡은 공식 재킷을 쓰고, 프로필의 지금 캐릭터 그림을 머리말에 넘긴다(2026-11-28)."""
    adapter = _FakeAdapter(
        user={"display_name": "Hikari", "rating": 1000, "recent_score": [], "character": 5,
              "character_stats": [{"character_id": 1, "icon": "1"}, {"character_id": 5, "icon": "5u"}]},
        pages={2: [_entry("new", "Brand New", 2, 9_900_000) | {"bg": "abc123"},
                   _entry("sheri", "Sheriruth", 2, 9_900_000) | {"bg": "def456"}]},
    )
    service, players, _ = await _service(conn, songdb, adapter)
    player = await players.create_player()
    await service.login_with_cookie(player.id, "sid=abc")

    snapshot = await service.fetch_snapshot(player.id)

    assert snapshot.profile.character_image_url == "https://webassets.lowiro.com/chr/5u.png"
    sheri = songdb.song_by_title("Sheriruth")
    assert sheri is not None and sheri.jacket == "https://webassets.lowiro.com/def456.jpg"  # 곡 자료에 재킷이 없던 곡
    reloaded = ArcaeaSongDbService(Path("missing.json"), conn=conn)
    await reloaded.load_observed()
    assert reloaded.song_by_title("Brand New") is None  # 곡 DB에도 상수에도 없는 곡은 만들지 않는다
