# skills/account-link/tests/test_account_link.py
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.player_data import PlayerDataScanner, discover_skill_tables
from core.tool_types import ToolContext
from services.common.players import PlayersService

_CORE_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
]
_SKILL_MIGRATION = Path(__file__).parent.parent / "migrations" / "001_init.sql"
_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_account_link_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        self.calls.append({"content": content, **kwargs})


class _FakeInteraction:
    def __init__(self, discord_id: str) -> None:
        self.user = SimpleNamespace(id=int(discord_id))
        self.response = _FakeResponse()


class _FakeNotify:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str, object]] = []

    async def dm(self, player_id: str, content: str, *, view: object | None = None) -> None:
        self.dms.append((player_id, content, view))


@pytest.fixture
async def link_env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.executescript(_SKILL_MIGRATION.read_text())
    await conn.executescript(
        "CREATE TABLE goals_items (id TEXT PRIMARY KEY, player_id TEXT, text TEXT);"
    )
    await conn.commit()

    players = PlayersService(conn)
    notify = _FakeNotify()
    player_data = PlayerDataScanner(conn=conn, table_names=await discover_skill_tables(conn))
    skill = _load_skill()

    def make_ctx(player_id: str) -> ToolContext:
        return ToolContext(
            player_id=player_id, tags=frozenset(), services={"players": players, "notify": notify},
            db=conn, event_bus=EventBus(), player_data=player_data,
        )

    try:
        yield skill, players, notify, make_ctx
    finally:
        await conn.close()


async def test_full_link_flow_resolves_to_same_player(link_env) -> None:
    skill, players, _, make_ctx = link_env

    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id)

    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner_ctx = make_ctx(joiner.id)

    joined = await skill.join_link(joiner_ctx, code=code)
    joiner_view = joined.display.view
    issuer_view = issuer_ctx.services["notify"].dms[-1][2]

    await issuer_view.confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]
    await joiner_view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.player_for_discord("111") == await players.player_for_discord("222")
    assert (await players.player_for_discord("222")).id == issuer.id  # pyright: ignore[reportOptionalMemberAccess]


async def test_expired_code_is_rejected(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id)

    assert issuer_ctx.db is not None
    await skill.models.create_code(
        issuer_ctx.db, code="000000", issuer_player_id=issuer.id, issuer_discord_id="111",
        now=datetime.now(UTC) - timedelta(minutes=20), ttl=timedelta(minutes=10),
    )

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    result = await skill.join_link(make_ctx(joiner.id), code="000000")

    assert result.error is not None
    assert "만료" in result.error or "틀렸" in result.error
    assert await players.player_for_discord("111") != await players.player_for_discord("222")


async def test_five_wrong_attempts_locks_out_further_tries(link_env) -> None:
    skill, players, _, make_ctx = link_env
    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    ctx = make_ctx(joiner.id)

    for _ in range(skill.MAX_WRONG_ATTEMPTS):
        result = await skill.join_link(ctx, code="wrong-code")
        assert result.error is not None

    locked = await skill.join_link(ctx, code="wrong-code")
    assert locked.error is not None
    assert "너무 많이" in locked.error


async def test_one_sided_confirmation_does_not_link(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id), code=code)

    # 참여자만 확인하고, 발급자는 확인하지 않는다.
    await joined.display.view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.player_for_discord("111") != await players.player_for_discord("222")


async def test_cancel_discard_changes_nothing(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner = await players.get_player(joiner.id)  # link_account가 바꾼 primary_discord_id까지 반영한다
    joiner_ctx = make_ctx(joiner.id)
    await joiner_ctx.players.grant_tag(joiner.id, "tester", source="test")
    assert joiner_ctx.db is not None
    await joiner_ctx.db.execute(
        "INSERT INTO goals_items (id, player_id, text) VALUES ('g1', ?, '목표')", (joiner.id,)
    )
    await joiner_ctx.db.commit()

    result = await skill.join_link(joiner_ctx, code=code)
    assert result.display is not None and result.display.view is not None  # 데이터가 있어 확인 버튼이 떴다

    await result.display.view.cancel.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.tags_for(joiner.id) == {"tester"}  # 그대로다
    assert await players.player_for_discord("222") == joiner  # 아무것도 안 바뀌었다
    assert joiner_ctx.player_data is not None
    assert await joiner_ctx.player_data.count(joiner.id) == {"goals_items": 1}  # 가짜 스킬 데이터도 그대로다


async def test_second_join_with_same_code_is_rejected(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id))
    code = started.data["code"]

    first_joiner = await players.create_player()
    await players.link_account(first_joiner.id, "222")
    await skill.join_link(make_ctx(first_joiner.id), code=code)

    second_joiner = await players.create_player()
    await players.link_account(second_joiner.id, "333")
    result = await skill.join_link(make_ctx(second_joiner.id), code=code)

    assert result.error is not None
    assert "다른 사람" in result.error
    # 첫 참여자의 진행이 덮어써지지 않았는지, models로 직접 확인한다.
    link_code = await skill.models.get_code(make_ctx(issuer.id).db, code)
    assert link_code is not None
    assert link_code.joiner_discord_id == "222"


async def test_confirm_button_rejects_expired_code(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id)
    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id), code=code)
    joiner_view = joined.display.view

    # 코드를 과거로 되돌려 '만료된 뒤에 버튼을 누른' 상황을 흉내 낸다.
    assert issuer_ctx.db is not None
    await issuer_ctx.db.execute(
        "UPDATE account_link_codes SET expires_at = ? WHERE code = ?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), code),
    )
    await issuer_ctx.db.commit()

    expired_click = _FakeInteraction("222")
    await joiner_view.confirm.callback(expired_click)  # pyright: ignore[reportCallIssue]

    assert "만료" in expired_click.response.calls[0]["content"]
    assert await players.player_for_discord("111") != await players.player_for_discord("222")
