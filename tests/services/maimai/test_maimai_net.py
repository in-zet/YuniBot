# tests/services/maimai/test_maimai_net.py
from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import aiolimiter
import aiosqlite
import httpx
import pytest
from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError
from cryptography.fernet import Fernet

from core.events import EventBus
from core.tool_types import ToolContext
from services.common.aime_auth import GATEWAY_HOST
from services.common.players import PlayersService
from services.common.secrets import SecretsService
from services.game.net_settings import ChangeNotAppliedError
from services.maimai import parser
from services.maimai.maimai_net import (
    LIST_DIFFICULTIES,
    MaimaiNetAdapter,
    MaimaiNetAdapterLike,
    MaimaiNetError,
    MaimaiNetService,
)
from services.maimai.parser import MaimaiProfile, PlayEntry, RatingTargets, ScoreEntry
from services.maimai.sync_snapshot import snapshot_from_pages

_DIR = Path("docs/reference/maimai-net")
_MIGRATIONS = "".join(
    Path(f"migrations/core/{name}").read_text()
    for name in ("001_players.sql", "002_player_tags.sql", "007_chunithm_net_cookies.sql",
                 "010_chunithm_net_cookies_nickname.sql", "026_sega_cookies.sql")
)
_MOBILE = "/maimai-mobile"


def _html(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


class _Site:
    """maimaidx-eng.com을 흉내 낸다: 세션 쿠키(`sess`)가 있어야 표본 페이지를 주고, 없으면 오류 페이지(100001)로 보낸다.
    `error_code`를 주면 로그인된 요청도 그 코드의 오류 페이지로 보낸다."""

    def __init__(self, *, error_code: int | None = None, status: int = 200) -> None:
        self.error_code = error_code
        self.status = status
        self.requests: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(f"{url.host}{url.path}{('?' + url.query.decode()) if url.query else ''}")
        cookies = request.headers.get("cookie", "")
        if url.host == GATEWAY_HOST and request.method == "POST":
            # ID/비밀번호 로그인: 맞으면 clal을 주고 사이트로 돌려보낸다.
            if request.content.decode() != "retention=1&sid=me&password=pw":
                return httpx.Response(200, text="login form")
            return httpx.Response(
                302, headers={
                    "location": f"https://maimaidx-eng.com{_MOBILE}/",
                    "set-cookie": f"clal=good; Domain={GATEWAY_HOST}; Path=/common_auth",
                },
            )
        if url.host == GATEWAY_HOST:
            if "clal=good" not in cookies:
                return httpx.Response(200, text="login form")
            return httpx.Response(302, headers={"location": f"https://maimaidx-eng.com{_MOBILE}/"})
        if self.status != 200:
            return httpx.Response(self.status)
        if url.path == f"{_MOBILE}/":
            return httpx.Response(200, text="home", headers={"set-cookie": "sess=1; Path=/"})
        if url.path == f"{_MOBILE}/error/":
            code = self.error_code if "sess=1" in cookies and self.error_code is not None else 100001
            return httpx.Response(200, text=_html("error_session.html").replace("100001", str(code)))
        if "sess=1" not in cookies or self.error_code is not None:
            return httpx.Response(302, headers={"location": f"https://maimaidx-eng.com{_MOBILE}/error/"})
        if url.path == f"{_MOBILE}/home/ratingTargetMusic/":
            return httpx.Response(200, text=_html("rating_target.html"))
        if url.path == f"{_MOBILE}/record/":
            return httpx.Response(200, text=_html("recent.html"))
        if url.path == f"{_MOBILE}/record/musicSort/search/":
            page = {"3": "music_master.html", "4": "music_remaster.html"}.get(url.params["diff"], "music_empty.html")
            return httpx.Response(200, text=_html(page))
        raise AssertionError(f"예상하지 못한 요청 {url}")


def _adapter(site: _Site, clal: str = "good") -> MaimaiNetAdapter:
    return MaimaiNetAdapter(
        clal, transport=httpx.MockTransport(site.handle), limiter=aiolimiter.AsyncLimiter(1000, 1)
    )


async def test_adapter_logs_in_with_clal_when_the_session_is_missing_and_reads_pages() -> None:
    site = _Site()
    adapter = _adapter(site)
    try:
        profile, targets = await adapter.get_rating_targets()
        recent = await adapter.get_recent()
        master = await adapter.get_music_list("MAS")
    finally:
        await adapter.aclose()

    assert profile.rating == 8564 and len(targets.new) == 4 and len(targets.old) == 3
    assert len(recent) == 6 and len(master) == 7
    assert sum("common_auth/login" in request for request in site.requests) == 1  # 로그인은 한 번만


async def test_adapter_logs_in_with_credentials_and_hands_out_the_new_clal() -> None:
    site = _Site()
    adapter = MaimaiNetAdapter(
        None, username="me", password="pw", transport=httpx.MockTransport(site.handle),
        limiter=aiolimiter.AsyncLimiter(1000, 1),
    )
    try:
        profile, _targets = await adapter.get_rating_targets()
        clal = adapter.session_clal()
    finally:
        await adapter.aclose()

    assert profile.rating == 8564 and clal == "good"


async def test_adapter_with_wrong_credentials_is_an_authentication_error() -> None:
    adapter = MaimaiNetAdapter(
        None, username="me", password="wrong", transport=httpx.MockTransport(_Site().handle),
        limiter=aiolimiter.AsyncLimiter(1000, 1),
    )
    try:
        with pytest.raises(AuthenticationError):
            await adapter.get_rating_targets()
    finally:
        await adapter.aclose()


async def test_adapter_asks_each_difficulty_page_with_its_diff_value() -> None:
    site = _Site()
    adapter = _adapter(site)
    try:
        for code in LIST_DIFFICULTIES:
            await adapter.get_music_list(code)
    finally:
        await adapter.aclose()

    diffs = [r.rsplit("diff=", 1)[1] for r in site.requests if "musicSort" in r]
    assert list(dict.fromkeys(diffs)) == ["0", "1", "2", "3", "4", "10"]  # 첫 요청은 로그인 뒤 한 번 더 간다
    assert all("playCheck=on" in r for r in site.requests if "musicSort" in r)


async def test_adapter_with_a_stale_clal_is_an_authentication_error() -> None:
    adapter = _adapter(_Site(), clal="stale")
    try:
        with pytest.raises(AuthenticationError):
            await adapter.get_recent()
    finally:
        await adapter.aclose()


async def test_adapter_turns_other_error_pages_into_a_user_facing_error() -> None:
    adapter = _adapter(_Site(error_code=200010))
    try:
        with pytest.raises(MaimaiNetError, match="200010") as exc_info:
            await adapter.get_recent()
    finally:
        await adapter.aclose()

    assert exc_info.value.code == 200010


async def test_adapter_reports_maintenance_on_http_503() -> None:
    adapter = _adapter(_Site(status=503))
    try:
        with pytest.raises(MaintenanceError):
            await adapter.get_recent()
    finally:
        await adapter.aclose()


def _pages() -> tuple[MaimaiProfile, RatingTargets, list[PlayEntry], dict[str, list[ScoreEntry]]]:
    targets = parser.parse_rating_targets(_html("rating_target.html"))
    lists = {
        "MAS": parser.parse_music_list(_html("music_master.html")),
        "REM": parser.parse_music_list(_html("music_remaster.html")),
        "BAS": [],
    }
    return parser.parse_profile(_html("rating_target.html")), targets, parser.parse_recent(_html("recent.html")), lists


def test_snapshot_gives_rating_targets_as_best_and_new_and_everything_else_as_all_plays() -> None:
    snapshot = snapshot_from_pages(*_pages())

    assert snapshot.profile.username == "テストプレイヤー"
    assert snapshot.profile.displayed_rating == Decimal(8564)
    assert snapshot.best is not None and snapshot.new is not None
    assert [p.song_title for p in snapshot.best] == ["神っぽいな", "きゅびびびびずむ", "ラビットホール"]
    assert len(snapshot.new) == 4
    assert len(snapshot.recent) == 6 and len(snapshot.all_plays) == 8


def test_snapshot_plays_carry_chart_type_score_and_dx_score() -> None:
    snapshot = snapshot_from_pages(*_pages())
    first = snapshot.all_plays[0]

    assert (first.song_title, first.chart_type, first.difficulty) == ("Tell Your World", "STD", "MAS")
    assert (first.score, first.rank, first.dx_score) == (940693, "AAA", 827)
    assert first.clear_lamp == ""
    assert snapshot.recent[0].achieved_at is not None and snapshot.recent[0].chart_type == "DX"


def test_snapshot_puts_combo_lamp_and_sync_lamp_in_their_own_columns() -> None:
    profile, targets, recent, lists = _pages()
    played = lists["MAS"][0]
    lists["MAS"][0] = replace(played, combo_lamp="AP", sync_lamp="FDX+")

    first = snapshot_from_pages(profile, targets, recent, lists).all_plays[0]

    assert (first.combo_lamp, first.chain_lamp) == ("AP", "FDX+")


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATIONS)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


def _select_values(html: str, payload: Mapping[str, str]) -> str:
    """POST를 받은 사이트처럼 각 `<select>`에서 보낸 value의 option만 selected로 둔다."""

    def one(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        if name not in payload:
            return match.group(0)
        body = re.sub(r"\s+selected(=\"selected\")?", "", body)
        body = body.replace(f'value="{payload[name]}"', f'value="{payload[name]}" selected', 1)
        return match.group(0).replace(match.group(2), body)

    return re.sub(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', one, html, flags=re.DOTALL)


class _FakeAdapter:
    def __init__(self, *, error: Exception | None = None, clal: str | None = None, applies_posts: bool = True) -> None:
        self.error = error
        self.closed = False
        self.listed: list[str] = []
        self.clal = clal
        # 설정·즐겨찾기 페이지는 실측 표본에서 시작하고, POST를 받으면(applies_posts) 사이트처럼 선택을 바꾼다.
        self.option_html = (_DIR / "user_option.html").read_text(encoding="utf-8")
        self.favorite_html = (_DIR / "favorite_update.html").read_text(encoding="utf-8")
        self.applies_posts = applies_posts
        self.posts: list[tuple[str, Mapping[str, str | list[str]]]] = []

    async def get_user_option(self) -> bytes:
        if self.error is not None:
            raise self.error
        return self.option_html.encode()

    async def post_user_option(self, payload: Mapping[str, str]) -> None:
        self.posts.append(("option", payload))
        if self.applies_posts:
            self.option_html = _select_values(self.option_html, payload)

    async def get_favorite_page(self) -> bytes:
        if self.error is not None:
            raise self.error
        return self.favorite_html.encode()

    async def post_favorites(self, payload: Mapping[str, str | list[str]]) -> None:
        self.posts.append(("favorite", payload))
        if self.applies_posts:
            chosen = set(payload["music[]"])
            self.favorite_html = re.sub(
                r'(<input type="checkbox" id="favorite" name="music\[\]" value="([^"]+)") ?(checked)? ?',
                lambda m: f"{m.group(1)} {'checked ' if m.group(2) in chosen else ''}",
                self.favorite_html,
            )

    def session_clal(self) -> str | None:
        return self.clal

    async def get_rating_targets(self) -> tuple[MaimaiProfile, RatingTargets]:
        if self.error is not None:
            raise self.error
        profile, targets, _, _ = _pages()
        return profile, targets

    async def get_recent(self) -> list[PlayEntry]:
        return _pages()[2]

    async def get_music_list(self, difficulty: str) -> list[ScoreEntry]:
        self.listed.append(difficulty)
        return _pages()[3].get(difficulty, [])

    async def aclose(self) -> None:
        self.closed = True


async def _linked_player(conn: aiosqlite.Connection, secrets: SecretsService, clal: str = "c" * 64):
    players = PlayersService(conn)
    player = await players.create_player()
    await conn.execute(
        "INSERT INTO sega_cookies (player_id, encrypted_cookie, linked_at, last_checked_at, nickname) "
        "VALUES (?, ?, 't', 't', 'n')",
        (player.id, secrets.encrypt(clal)),
    )
    await players.set_status_tag(player.id, "sega-linked", source="test")
    await conn.commit()
    return players, player


async def test_service_fetches_every_page_with_the_stored_clal_and_closes_the_adapter(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)
    adapter = _FakeAdapter()
    received: list[str] = []
    service = MaimaiNetService(
        conn=conn, secrets=secrets, players=players,
        adapter_factory=lambda clal: (received.append(clal), cast(MaimaiNetAdapterLike, adapter))[1],
    )

    snapshot = await service.fetch_snapshot(player.id)

    assert received == ["c" * 64]
    assert adapter.listed == list(LIST_DIFFICULTIES) and adapter.closed
    assert len(snapshot.all_plays) == 8


async def test_service_without_a_stored_cookie_says_not_logged_in(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players = PlayersService(conn)
    player = await players.create_player()
    service = MaimaiNetService(conn=conn, secrets=secrets, players=players)

    with pytest.raises(ConnectionError, match="로그인되어 있지 않다"):
        await service.fetch_snapshot(player.id)


async def test_authentication_failure_expires_the_login_and_notifies(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)
    received: list[object] = []

    async def record(ctx: ToolContext, payload: object) -> None:
        received.append(payload)

    bus = EventBus()
    bus.register_notify("sega.login.expired", owner_skill="test", fn=record, skill_services=[])
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    adapter = _FakeAdapter(error=AuthenticationError("만료"))
    service = MaimaiNetService(
        conn=conn, secrets=secrets, players=players, adapter_factory=lambda clal: cast(MaimaiNetAdapterLike, adapter)
    )

    with pytest.raises(AuthenticationError):
        await service.fetch_snapshot(player.id, ctx=ctx)
    await bus.drain()

    assert len(received) == 1 and adapter.closed
    assert await players.tags_for(player.id) == set()


async def test_maintenance_keeps_the_login(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)
    service = MaimaiNetService(
        conn=conn, secrets=secrets, players=players,
        adapter_factory=lambda clal: cast(MaimaiNetAdapterLike, _FakeAdapter(error=MaintenanceError())),
    )

    with pytest.raises(MaintenanceError):
        await service.fetch_snapshot(player.id)

    assert await players.tags_for(player.id) == {"sega-linked"}


async def test_login_with_credentials_stores_the_session_clal_and_links(conn) -> None:
    """2026-11-22: CHUNITHM-NET 점검 중에도 maimai DX NET으로 로그인해 게임 공통 clal을 저장한다."""
    secrets = SecretsService(Fernet.generate_key())
    players = PlayersService(conn)
    player = await players.create_player()
    adapter = _FakeAdapter(clal="s" * 64)
    seen: list[tuple[str, str]] = []
    service = MaimaiNetService(
        conn=conn, secrets=secrets, players=players,
        credential_adapter_factory=lambda u, p: (seen.append((u, p)), cast(MaimaiNetAdapterLike, adapter))[1],
    )
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})

    profile = await service.login_with_credentials(player.id, "sid", "pw", ctx)

    assert seen == [("sid", "pw")] and adapter.closed
    status = await service.status(player.id)
    assert status.linked and status.nickname == profile.username
    row = await (await conn.execute("SELECT encrypted_cookie FROM sega_cookies WHERE player_id = ?", (player.id,))).fetchone()
    assert row is not None and secrets.decrypt(row[0]) == "s" * 64


async def test_login_with_credentials_saves_nothing_on_maintenance(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players = PlayersService(conn)
    player = await players.create_player()
    service = MaimaiNetService(
        conn=conn, secrets=secrets, players=players,
        credential_adapter_factory=lambda u, p: cast(MaimaiNetAdapterLike, _FakeAdapter(error=MaintenanceError())),
    )

    with pytest.raises(MaintenanceError):
        await service.login_with_credentials(player.id, "sid", "pw", ToolContext(player_id=player.id, tags=frozenset(), services={}))

    assert not (await service.status(player.id)).linked


# ---------------------------------------------------------------- 인게임 설정·즐겨찾기 (2026-11-27, plan 6-8)


def _service_with(conn: aiosqlite.Connection, secrets: SecretsService, players: PlayersService, adapter: _FakeAdapter):
    return MaimaiNetService(
        conn=conn, secrets=secrets, players=players, adapter_factory=lambda clal: cast(MaimaiNetAdapterLike, adapter)
    )


async def test_get_arcade_settings_reads_the_named_fields(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)
    adapter = _FakeAdapter()

    settings = await _service_with(conn, secrets, players, adapter).get_arcade_settings(player.id)

    assert settings.named == {
        "speed": "5.75", "touch_speed": "5.50", "slide_timing": "0.0",
        "play_timing_offset": "0.0", "judge_timing_offset": "0.6", "guide_sound": "OFF",
    }
    assert len(settings.raw) == 39 and settings.raw["optionKind"] == "Details"  # 나머지 설정도 통째로
    assert adapter.closed


async def test_update_arcade_settings_posts_every_select_with_the_token_and_rereads(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)
    adapter = _FakeAdapter()

    settings = await _service_with(conn, secrets, players, adapter).update_arcade_settings(
        player.id, changes={"speed": "6.00", "judge_timing_offset": "0.5"}
    )

    assert settings.named["speed"] == "6.00" and settings.named["judge_timing_offset"] == "0.5"
    assert settings.named["touch_speed"] == "5.50"  # 말하지 않은 항목은 그대로
    [(kind, payload)] = adapter.posts
    assert kind == "option" and payload["token"] == "TOKEN"
    assert payload["optionKind"] == "3" and payload["touchSpeed"] == "18"  # 폼 전체를 지금 값으로 되돌려 보낸다
    assert len(payload) == 40


async def test_update_arcade_settings_raises_when_the_site_ignored_it(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)

    with pytest.raises(ChangeNotAppliedError, match="maimai DX NET에 반영되지 않았어 — 노트 속도"):
        await _service_with(conn, secrets, players, _FakeAdapter(applies_posts=False)).update_arcade_settings(
            player.id, changes={"speed": "6.00"}
        )


async def test_update_arcade_settings_unknown_value_is_refused_without_posting(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)
    adapter = _FakeAdapter()

    with pytest.raises(ValueError, match="가이드음에 '11'라는 값은 없어"):
        await _service_with(conn, secrets, players, adapter).update_arcade_settings(player.id, changes={"guide_sound": "11"})
    assert adapter.posts == []


async def test_favorites_are_read_and_written_by_title(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)
    adapter = _FakeAdapter()
    service = _service_with(conn, secrets, players, adapter)

    assert await service.get_favorite_music(player.id) == ["テトリス", "運命線上のアリア"]

    await service.set_favorite_music(player.id, ["テトリス", "AiAe"])

    [(kind, payload)] = adapter.posts
    assert kind == "favorite" and payload["idx"] == "99" and payload["token"] == "TOKEN"
    assert len(payload["music[]"]) == 2
    assert await service.get_favorite_music(player.id) == ["テトリス", "AiAe"]


async def test_set_favorites_raises_when_the_site_ignored_it(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)

    with pytest.raises(ChangeNotAppliedError, match="maimai DX NET에 반영되지 않았어"):
        await _service_with(conn, secrets, players, _FakeAdapter(applies_posts=False)).set_favorite_music(
            player.id, ["テトリス"]
        )


async def test_settings_auth_failure_expires_the_login(conn) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players, player = await _linked_player(conn, secrets)

    with pytest.raises(AuthenticationError):
        await _service_with(conn, secrets, players, _FakeAdapter(error=AuthenticationError())).get_favorite_music(player.id)

    assert "sega-linked" not in await players.tags_for(player.id)
