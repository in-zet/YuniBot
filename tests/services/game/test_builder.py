import json
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.resolvers import ResolverRegistry
from core.tool_types import RankRef, ToolContext, UserFacingError
from services.common.players import PlayersService
from services.game.builder import build_all_game_services, register_resolvers
from services.game.registry import GAME_PROFILES
from services.maimai.songdb import MaimaiSongDbService

_MIGRATIONS = "".join(
    Path(f"migrations/core/{name}").read_text()
    for name in (
        "001_players.sql", "002_player_tags.sql", "008_records.sql", "013_records_overpower.sql",
        "014_records_sync_log_snapshot_averages.sql", "018_records_profile.sql", "019_records_session_state.sql",
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


async def test_maimai_has_no_net_and_says_so(conn, tmp_path: Path) -> None:
    games = await build_all_game_services(
        conn=conn, players=PlayersService(conn), secrets=None, data_dir=tmp_path
    )

    assert "chunithm_net" not in games["chunithm"].skill_services()  # 시험은 비밀 키 없이 만든다
    net = games["maimai"].skill_services()["chunithm_net"]
    with pytest.raises(UserFacingError, match="maimai DX는 아직 SEGA 사이트 연결이 없어서"):
        _ = getattr(net, "fetch_snapshot", None)


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
