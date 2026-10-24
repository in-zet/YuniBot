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
from core.tables import TableStore, WorkTable, render_text
from core.usage import UsageService

# 코어는 게임 이름만 들고 다닌다. 게임 규칙과 등록된 게임 목록은 services/game/registry.py가 안다.
DEFAULT_GAME = "chunithm"


class UserFacingError(Exception):
    """도구가 일부러 올리는 실패. 메시지는 모델과 사용자에게 그대로 전해도 되는 한국어 문장이다.
    실행기는 이 예외를 오류 로그(스택)로 남기지 않고 `ToolResult(error=메시지)`로 바꾼다."""


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
    # 이 호출이 다루는 게임. 실행기가 도구를 부르기 전에 정한다(services/game/registry.py의 choose_game).
    game: str = DEFAULT_GAME

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


@dataclass(frozen=True, slots=True)
class DisplayFile:
    """표시물에 붙일 첨부 파일(이름과 바이트)."""

    name: str
    data: bytes


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
    # 메시지에 첨부할 파일. 본문이 2000자를 넘어 나뉘어도 첫 조각에 붙는다.
    files: list[DisplayFile] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ToolResult:
    data: dict[str, Any] = field(default_factory=dict)
    display: Display | None = None
    # 여러 항목을 돌려주는 도구의 결과. 표를 돌려주는 도구는 display를 비운다(코어가 표를 그려 붙인다).
    table: WorkTable | None = None
    # 코어가 채운다(도구는 쓰지 않는다): 입력 표가 옛 값이어서 이 결과 표도 옛 값으로 보관해야 한다.
    table_stale: bool = False
    # 그림 표 도구가 "이 표를 그렸다"고 알리는 표시(표 id). 코어는 그 턴의 마지막 표가 그려졌으면 글자로 다시 그리지 않는다.
    drew_table: str | None = None
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


class TableRef:
    """모델이 넘긴 작업 표 id(`"t3"` 또는 번호를 붙인 `"t3:2,3"`)를 도구가 받을 `WorkTable`로 바꾸는 자리.
    해석은 서비스가 아니라 실행기가 보관소에서 한다. 필요한 것은 `Annotated[TableRef, TableNeeds(...)]`로 선언한다."""


class GameRef:
    """사용자가 말한 게임 이름("마이마이", "츄니즘")을 등록된 게임으로 바꾸는 자리. 해석은 services/game/registry.py.
    게임을 다루는 도구가 선택 인자로 받는다. 안 주면 실행기가 주 게임 → 기본 게임 순으로 고른다."""


STANDARD_REF_TYPES: frozenset[type] = frozenset(
    {PlayerRef, SongQuery, ChartRef, ArcadeRef, RankRef, DifficultyRef, LampRef, ScoreRef, TableRef, GameRef}
)


def table_display(table: WorkTable, *, stale: bool = False, slash: bool = False) -> Display:
    """표를 글자로 그려 표시물로 만든다. 멘션 응답과 슬래시가 같은 함수를 쓴다."""
    rendered = render_text(table, stale=stale, slash=slash)
    files = [DisplayFile(rendered.file[0], rendered.file[1])] if rendered.file is not None else []
    return Display(content=rendered.content, files=files)
