# services/game/builder.py
"""게임 서비스 묶음을 만들어 코어에 건넨다. `core/bot.py`가 게임 서비스에 대해 아는 것은 이 파일뿐이다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from core.resolvers import ResolverRegistry
from core.tool_types import (
    ChartRef,
    DifficultyRef,
    LampRef,
    RankRef,
    ScoreRef,
    SongQuery,
    ToolContext,
    UserFacingError,
)
from services.chunithm.chunithm_net import ChunithmNetService
from services.chunithm.songdb import SongDbService
from services.common.players import PlayersService
from services.common.secrets import SecretsService
from services.game.chart_ref import ChartRefResolver
from services.game.profile import GameProfile
from services.game.records import RecordsService
from services.game.registry import GAME_PROFILES, profile_for
from services.game.songdb import SongDb
from services.game.terms import TermsService
from services.maimai.songdb import MaimaiSongDbService


@dataclass(slots=True, kw_only=True)
class _RankRefAdapter:
    """ResolverRegistry는 타입 하나에 resolve(raw, ctx) 하나만 등록받는다 — TermsService는
    랭크/난이도/램프/점수를 이름이 다른 네 메서드로 노출하므로, 값 타입마다 이 얇은
    어댑터로 감싸 등록한다(TermsService 자신을 네 번 등록할 수 없다)."""

    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_rank(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _DifficultyRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_difficulty(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _LampRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_lamp(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _ScoreRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_score(raw, ctx)


class _NoNet:
    """NET 연결이 아직 없는 게임이 `chunithm_net` 자리에 끼우는 자리표시자. 무엇을 부르든 사용자에게 이유를 말한다."""

    def __init__(self, label: str) -> None:
        self._label = label

    def __getattr__(self, name: str) -> Any:
        raise UserFacingError(f"{self._label}는 아직 SEGA 사이트 연결이 없어서 기록을 가져올 수 없어")


class _ByGame:
    """표준 인자 해석기를 게임마다 하나씩 두고, `ctx.game`에 맞는 것에게 넘긴다."""

    def __init__(self, by_game: Mapping[str, Any]) -> None:
        self._by_game = by_game

    async def resolve(self, raw: Any, ctx: ToolContext):
        return await self._by_game[ctx.game].resolve(raw, ctx)


@dataclass(slots=True, kw_only=True)
class GameServices:
    """한 게임의 서비스 한 벌."""

    profile: GameProfile
    songdb: SongDb
    terms: TermsService
    records: RecordsService
    chart_ref: ChartRefResolver
    net: ChunithmNetService | None
    # NET 어댑터가 아직 없는 게임이면 `chunithm_net` 자리에 사정을 말해 주는 자리표시자를 준다.
    net_unavailable: bool = False

    def skill_services(self) -> dict[str, object]:
        """manifest `services`로 스킬에게 주는 이름 → 서비스."""
        services: dict[str, object] = {"songdb": self.songdb, "terms": self.terms, "records": self.records}
        if self.net is not None:
            services["chunithm_net"] = self.net
        elif self.net_unavailable:
            services["chunithm_net"] = _NoNet(self.profile.label)
        return services

    def resolvers(self) -> dict[type, Any]:
        return {
            SongQuery: self.songdb,
            ChartRef: self.chart_ref,
            RankRef: _RankRefAdapter(terms=self.terms),
            DifficultyRef: _DifficultyRefAdapter(terms=self.terms),
            LampRef: _LampRefAdapter(terms=self.terms),
            ScoreRef: _ScoreRefAdapter(terms=self.terms),
        }


def register_resolvers(resolvers: ResolverRegistry, games: Mapping[str, GameServices]) -> None:
    """게임마다 다른 표준 인자 해석기를 등록한다. 실제 해석은 `ctx.game`의 게임 것이 한다."""
    by_type: dict[type, dict[str, Any]] = {}
    for game, services in games.items():
        for ref_type, resolver in services.resolvers().items():
            by_type.setdefault(ref_type, {})[game] = resolver
    for ref_type, by_game in by_type.items():
        resolvers.register(ref_type, _ByGame(by_game))


async def build_game_services(
    game: str,
    *,
    conn: aiosqlite.Connection,
    players: PlayersService,
    secrets: SecretsService | None,
    data_dir: Path,
) -> GameServices:
    profile = profile_for(game)
    songdb: SongDb
    net_unavailable = False
    if game == "maimai":
        songdb = MaimaiSongDbService(data_dir / "maimai" / "music-ex-intl.json", conn=conn)
        net = None  # maimai NET 어댑터는 17단계 Part 3
        net_unavailable = True
    else:
        songdb = SongDbService(conn=conn, profile=profile)
        net = ChunithmNetService(conn=conn, secrets=secrets, players=players) if secrets is not None else None
    terms = TermsService(profile.terms_path, conn=conn, game=game, percent_scores=profile.score_is_percent)
    await terms.load_aliases()
    return GameServices(
        profile=profile,
        songdb=songdb,
        terms=terms,
        records=RecordsService(conn=conn, songdb=songdb, players=players, game=game),
        chart_ref=ChartRefResolver(songdb=songdb, terms=terms),
        net=net,
        net_unavailable=net_unavailable,
    )


async def build_all_game_services(
    *, conn: aiosqlite.Connection, players: PlayersService, secrets: SecretsService | None, data_dir: Path
) -> dict[str, GameServices]:
    return {
        game: await build_game_services(game, conn=conn, players=players, secrets=secrets, data_dir=data_dir)
        for game in GAME_PROFILES
    }
