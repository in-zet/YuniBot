import json
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
from cryptography.fernet import Fernet

from core.resolvers import ResolverRegistry
from core.tool_types import RankRef, ToolContext
from services.chunithm.chunithm_net import ChunithmNetService
from services.common.players import PlayersService
from services.common.secrets import SecretsService
from services.game.builder import (
    build_all_game_services,
    main_game_nickname_source,
    register_resolvers,
)
from services.game.registry import GAME_PROFILES
from services.maimai.maimai_net import MaimaiNetService
from services.maimai.songdb import MaimaiSongDbService

_MIGRATIONS = "".join(
    Path(f"migrations/core/{name}").read_text()
    for name in (
        "001_players.sql", "002_player_tags.sql", "008_records.sql", "013_records_overpower.sql",
        "014_records_sync_log_snapshot_averages.sql", "018_records_profile.sql", "030_records_profile_username.sql", "019_records_session_state.sql",
        "024_records_chart_type.sql", "009_terms_server.sql", "021_terms_aliases.sql",
        "028_terms_aliases_game.sql",
    )
) + Path("migrations/core/011_songdb_aliases.sql").read_text() + Path("migrations/core/025_songdb_aliases_game.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATIONS)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_builds_one_service_set_per_registered_game(conn, tmp_path: Path) -> None:
    games = await build_all_game_services(
        conn=conn, players=PlayersService(conn), secrets=None, data_dir=tmp_path
    )

    assert set(games) == set(GAME_PROFILES)
    assert isinstance(games["maimai"].songdb, MaimaiSongDbService)
    assert games["maimai"].songdb.profile.game == "maimai"
    assert games["chunithm"].songdb.profile.game == "chunithm"
    assert games["maimai"].records.__class__ is games["chunithm"].records.__class__


async def test_each_game_gets_its_own_net_service_in_the_chunithm_net_slot(conn, tmp_path: Path) -> None:
    games = await build_all_game_services(
        conn=conn, players=PlayersService(conn), secrets=SecretsService(Fernet.generate_key()), data_dir=tmp_path
    )

    assert isinstance(games["chunithm"].skill_services()["chunithm_net"], ChunithmNetService)
    assert isinstance(games["maimai"].skill_services()["chunithm_net"], MaimaiNetService)


async def _save_profile_name(conn: aiosqlite.Connection, player_id: str, game: str, name: str) -> None:
    await conn.execute(
        "INSERT INTO records_profile (player_id, game, updated_at, username) VALUES (?, ?, '2026-11-27T00:00:00+00:00', ?)",
        (player_id, game, name),
    )
    await conn.commit()


async def test_nickname_source_reads_the_main_game_name(conn, tmp_path: Path) -> None:
    """유니는 주 게임 NET의 이름으로 부른다(2026-11-27 사용자 결정). 주 게임이 없으면 기본 게임(CHUNITHM)."""
    players = PlayersService(conn)
    player = await players.create_player()
    games = await build_all_game_services(conn=conn, players=players, secrets=None, data_dir=tmp_path)
    await _save_profile_name(conn, player.id, "chunithm", "츄니이름")
    await _save_profile_name(conn, player.id, "maimai", "마이이름")
    main: dict[str, str | None] = {player.id: None}

    async def main_game(player_id: str) -> str | None:
        return main[player_id]

    source = main_game_nickname_source(games, main_game)

    assert await source(player.id) == "츄니이름"
    main[player.id] = "maimai"
    assert await source(player.id) == "마이이름"


async def test_nickname_source_falls_back_to_the_login_name_before_the_main_game_is_synced(conn, tmp_path: Path) -> None:
    secrets = SecretsService(Fernet.generate_key())
    players = PlayersService(conn)
    player = await players.create_player()
    games = await build_all_game_services(conn=conn, players=players, secrets=secrets, data_dir=tmp_path)
    await _save_profile_name(conn, player.id, "chunithm", "츄니이름")

    async def main_game(player_id: str) -> str | None:
        return "maimai"

    async def login_status(player_id: str):
        from services.common.sega_cookies import LoginStatus

        return LoginStatus(linked=True, nickname="로그인이름")

    for bundle in games.values():
        assert bundle.net is not None
        bundle.net.status = login_status  # type: ignore[method-assign]

    assert await main_game_nickname_source(games, main_game)(player.id) == "로그인이름"


async def test_without_a_secrets_key_no_game_has_a_net_service(conn, tmp_path: Path) -> None:
    games = await build_all_game_services(
        conn=conn, players=PlayersService(conn), secrets=None, data_dir=tmp_path
    )

    assert all("chunithm_net" not in game.skill_services() for game in games.values())


async def test_maimai_reads_the_cache_under_data_dir(conn, tmp_path: Path) -> None:
    (tmp_path / "maimai").mkdir()
    entry = {
        "title": "곡", "artist": "A", "version": "26511", "image_url": "a.png", "intl": "1",
        "dx_lev_mas": "14", "dx_lev_mas_i": "14.2", "dx_lev_mas_notes": "700",
    }
    (tmp_path / "maimai" / "music-ex-intl.json").write_text(json.dumps([entry]), encoding="utf-8")

    games = await build_all_game_services(
        conn=conn, players=PlayersService(conn), secrets=None, data_dir=tmp_path
    )

    assert games["maimai"].songdb.const_for("곡", "MAS", "DX") == 14.2


async def test_resolvers_are_dispatched_by_ctx_game(conn, tmp_path: Path) -> None:
    games = await build_all_game_services(
        conn=conn, players=PlayersService(conn), secrets=None, data_dir=tmp_path
    )
    resolvers = ResolverRegistry()
    register_resolvers(resolvers, games)
    resolver = resolvers.get(RankRef)

    chuni = await resolver.resolve("SSS", ToolContext(player_id="p", tags=frozenset(), game="chunithm"))
    mai = await resolver.resolve("SSS", ToolContext(player_id="p", tags=frozenset(), game="maimai"))

    assert (chuni.min_score, mai.min_score) == (1_007_500, 1_000_000)
