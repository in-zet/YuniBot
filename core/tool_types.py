# core/tool_types.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import aiosqlite

from core.events import EventBus, Section
from core.player_data import PlayerDataScanner
from core.registry_snapshot import RegistrySnapshot
from core.scheduler import Scheduler
from core.tables import TableStore
from core.usage import UsageService


class UndeclaredServiceError(AttributeError):
    def __init__(self, service_name: str) -> None:
        super().__init__(
            f"이 스킬은 서비스 '{service_name}'를 manifest의 services에 선언하지 않았다"
        )
        self.service_name = service_name


@dataclass(slots=True, kw_only=True)
class ToolContext:
    """도구/이벤트 핸들러/예약 작업에 전달되는 실행 맥락. manifest.services에 없는 서비스는 접근 자체가 실패한다."""

    player_id: str
    tags: frozenset[str]
    services: Mapping[str, object] = field(default_factory=dict)
    guild_id: str | None = None
    event_bus: EventBus = field(default_factory=EventBus)
    player_data: PlayerDataScanner | None = None
    db: aiosqlite.Connection | None = None
    registry_snapshot: RegistrySnapshot | None = None
    usage: UsageService | None = None
    discord_id: str | None = None
    scheduler: Scheduler | None = None
    # 작업 표 보관소. 스킬은 동기화 저장 뒤 mark_stale을 부르는 데만 쓴다.
    tables: TableStore | None = None
    # 요청이 온 채널(대화 스레드면 스레드) id. 슬래시는 표를 보관하지 않으므로 None이다.
    channel_id: str | None = None

    def __getattr__(self, name: str) -> Any:
        # dataclass가 정의한 필드(player_id, tags, services)는 정상 __getattribute__로 먼저 해석되므로
        # 여기 도달하는 것은 선언되지 않은 속성/서비스 접근뿐이다.
        services = object.__getattribute__(self, "services")
        if name in services:
            return services[name]
        raise UndeclaredServiceError(name)

    async def emit(self, event_name: str, payload: Any) -> None:
        """스킬이 알림 이벤트를 낸다. 모으기 이벤트는 각 스킬이 직접 내지 않고 모으는 쪽이 bus.emit_gather를 부른다."""
        await self.event_bus.emit_notify(event_name, payload, player_id=self.player_id, guild_id=self.guild_id)

    async def emit_gather(self, event_name: str, payload: Any) -> list[Section]:
        """모으기 이벤트는 스킬이 직접 emit_gather를 호출해 조각들을 모은다(emit()은 알림 전용)."""
        return await self.event_bus.emit_gather(
            event_name, payload, player_id=self.player_id, guild_id=self.guild_id
        )


@dataclass(slots=True, kw_only=True)
class Display:
    """슬래시/멘션 응답에 붙일 표시물. 코어는 내용을 고치지 않고 그대로 보낸다."""

    content: str | None = None
    embeds: list[Any] = field(default_factory=list)
    view: Any | None = None
    ephemeral: bool = False
    # 본인에게만 보여야 하는 내용. 멘션 응답은 채널 답장이라 ephemeral이 없으므로, 이 표시물은 채널에
    # 붙이지 않고 요청한 플레이어에게 DM으로 보낸다(content만). 슬래시는 ephemeral을 따로 켠다.
    private: bool = False
    # 슬래시에서만 쓰는 본문. 있으면 슬래시 응답이 content 대신 이것을 보낸다(멘션은 content만 쓴다).
    # 다음 슬래시 명령에 타이핑할 id처럼 사람이 직접 봐야 하지만 멘션 답에는 군더더기인 것을 담는다.
    slash_content: str | None = None
    # required 표시물 중 질문이 표 전체가 아니라 조건에 맞는 일부일 때 유니가 `[[no_table]]`로 뺄 수 있는 것.
    # 출처처럼 반드시 붙어야 하는 표시물에는 켜지 않는다.
    omittable: bool = False


@dataclass(slots=True, kw_only=True)
class ToolResult:
    data: dict[str, Any] = field(default_factory=dict)
    display: Display | None = None
    display_mode: Literal["required", "optional"] = "required"
    error: str | None = None


class PlayerRef:
    """모델/슬래시가 넘긴 원문(이름, 호칭, 멘션, "나")을 실제 플레이어로 바꾸는 자리. 해석은 players 서비스."""


class SongQuery:
    """곡 이름/별칭/줄임말을 곡 하나로 좁히는 자리. 해석은 songdb 서비스."""


class ChartRef:
    """SongQuery + 난이도를 채보(상수, 노트 수 포함)로 바꾸는 자리."""


class ArcadeRef:
    """오락실 이름/별칭/줄임말을 오락실로 바꾸는 자리. 해석은 공통 arcades 서비스."""


class RankRef:
    """랭크 표현(정식/은어)을 정식 랭크와 기준 점수로 바꾸는 자리. 해석은 terms 서비스."""


class DifficultyRef:
    """난이도 표현을 정식 난이도로 바꾸는 자리."""


class LampRef:
    """램프 표현을 램프(와 있으면 기준 점수)로 바꾸는 자리."""


class ScoreRef:
    """점수 표현(숫자/랭크/램프 이름)을 0~1,010,000 점수로 바꾸는 자리."""


STANDARD_REF_TYPES: frozenset[type] = frozenset(
    {PlayerRef, SongQuery, ChartRef, ArcadeRef, RankRef, DifficultyRef, LampRef, ScoreRef}
)
