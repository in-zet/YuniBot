# services/game/builder.py
"""게임 서비스 묶음을 만들어 코어에 건넨다. `core/bot.py`가 게임 서비스에 대해 아는 것은 이 파일뿐이다."""

from __future__ import annotations

from dataclasses import dataclass

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
)
from services.chunithm.chunithm_net import ChunithmNetService
from services.chunithm.songdb import SongDbService
from services.chunithm.terms import TermsService
from services.common.players import PlayersService
from services.common.secrets import SecretsService
from services.game.chart_ref import ChartRefResolver
from services.game.profile import GameProfile
from services.game.records import RecordsService
from services.game.registry import profile_for


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


@dataclass(slots=True, kw_only=True)
class GameServices:
    """한 게임의 서비스 한 벌. 지금은 기본 게임 하나만 만들고, 스킬이 게임을 고르게 되면 게임마다 한 벌을 둔다."""

    profile: GameProfile
    songdb: SongDbService
    terms: TermsService
    records: RecordsService
    chart_ref: ChartRefResolver
    net: ChunithmNetService | None

    def skill_services(self) -> dict[str, object]:
        """manifest `services`로 스킬에게 주는 이름 → 서비스."""
        services: dict[str, object] = {"songdb": self.songdb, "terms": self.terms, "records": self.records}
        if self.net is not None:
            services["chunithm_net"] = self.net
        return services

    def register_resolvers(self, resolvers: ResolverRegistry) -> None:
        resolvers.register(SongQuery, self.songdb)
        resolvers.register(ChartRef, self.chart_ref)
        resolvers.register(RankRef, _RankRefAdapter(terms=self.terms))
        resolvers.register(DifficultyRef, _DifficultyRefAdapter(terms=self.terms))
        resolvers.register(LampRef, _LampRefAdapter(terms=self.terms))
        resolvers.register(ScoreRef, _ScoreRefAdapter(terms=self.terms))


async def build_game_services(
    game: str, *, conn: aiosqlite.Connection, players: PlayersService, secrets: SecretsService | None
) -> GameServices:
    profile = profile_for(game)
    songdb = SongDbService(conn=conn, profile=profile)
    terms = TermsService(conn=conn)
    await terms.load_aliases()
    net = ChunithmNetService(conn=conn, secrets=secrets, players=players) if secrets is not None else None
    return GameServices(
        profile=profile,
        songdb=songdb,
        terms=terms,
        records=RecordsService(conn=conn, songdb=songdb, players=players, game=game),
        chart_ref=ChartRefResolver(songdb=songdb, terms=terms),
        net=net,
    )
