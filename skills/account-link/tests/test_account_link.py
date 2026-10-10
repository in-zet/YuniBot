# skills/account-link/tests/test_account_link.py
from __future__ import annotations

import asyncio
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

    def make_ctx(player_id: str, discord_id: str) -> ToolContext:
        # discord_id는 "지금 명령을 실행한" 계정이다 — core/slash.py의 _dispatch가 실제로
        # interaction.user.id를 ToolContext.discord_id에 싣는 것과 같은 자리를 흉내 낸다.
        # 플레이어가 이미 여러 계정을 연결했어도 accounts_for()의 첫 번째가 아니라
        # 바로 이 값을 써야 한다(리뷰 Finding 1).
        return ToolContext(
            player_id=player_id, tags=frozenset(), services={"players": players, "notify": notify},
            db=conn, event_bus=EventBus(), player_data=player_data, discord_id=discord_id,
        )

    try:
        yield skill, players, notify, make_ctx
    finally:
        await conn.close()


async def test_full_link_flow_resolves_to_same_player(link_env) -> None:
    skill, players, _, make_ctx = link_env

    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id, "111")

    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner_ctx = make_ctx(joiner.id, "222")

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
    issuer_ctx = make_ctx(issuer.id, "111")

    assert issuer_ctx.db is not None
    await skill.models.create_code(
        issuer_ctx.db, code="000000", issuer_player_id=issuer.id, issuer_discord_id="111",
        now=datetime.now(UTC) - timedelta(minutes=20), ttl=timedelta(minutes=10),
    )

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    result = await skill.join_link(make_ctx(joiner.id, "222"), code="000000")

    assert result.error is not None
    assert "만료" in result.error or "틀렸" in result.error
    assert await players.player_for_discord("111") != await players.player_for_discord("222")


async def test_five_wrong_attempts_locks_out_further_tries(link_env) -> None:
    skill, players, _, make_ctx = link_env
    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    ctx = make_ctx(joiner.id, "222")

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
    started = await skill.start_link(make_ctx(issuer.id, "111"))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=code)

    # 참여자만 확인하고, 발급자는 확인하지 않는다.
    await joined.display.view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.player_for_discord("111") != await players.player_for_discord("222")


async def test_cancel_discard_changes_nothing(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id, "111"))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner = await players.get_player(joiner.id)  # link_account가 바꾼 primary_discord_id까지 반영한다
    joiner_ctx = make_ctx(joiner.id, "222")
    await joiner_ctx.players.grant_tag(joiner.id, "tester", source="test")
    assert joiner_ctx.db is not None
    await joiner_ctx.db.execute(
        "INSERT INTO goals_items (id, player_id, text) VALUES ('g1', ?, '목표')", (joiner.id,)
    )
    await joiner_ctx.db.commit()

    result = await skill.join_link(joiner_ctx, code=code)
    assert result.display is not None and result.display.view is not None  # 데이터가 있어 확인 버튼이 떴다
    assert result.display.ephemeral is True  # 다른 사람에게는 안 보인다

    await result.display.view.cancel.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.tags_for(joiner.id) == {"tester"}  # 그대로다
    assert await players.player_for_discord("222") == joiner  # 아무것도 안 바뀌었다
    assert joiner_ctx.player_data is not None
    assert await joiner_ctx.player_data.count(joiner.id) == {"goals_items": 1}  # 가짜 스킬 데이터도 그대로다


async def test_stranger_clicking_discard_confirm_is_rejected(link_env) -> None:
    """리뷰 Finding 4: 참여자가 아닌 사람이 버리고 연결/취소 버튼을 눌러도 아무 효과가 없어야 한다."""
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id, "111"))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner = await players.get_player(joiner.id)
    joiner_ctx = make_ctx(joiner.id, "222")
    assert joiner_ctx.db is not None
    await joiner_ctx.db.execute(
        "INSERT INTO goals_items (id, player_id, text) VALUES ('g2', ?, '목표')", (joiner.id,)
    )
    await joiner_ctx.db.commit()

    result = await skill.join_link(joiner_ctx, code=code)
    view = result.display.view
    assert view is not None

    stranger_discard = _FakeInteraction("999")
    await view.discard_and_continue.callback(stranger_discard)  # pyright: ignore[reportCallIssue]
    assert "당신" in stranger_discard.response.calls[0]["content"]

    stranger_cancel = _FakeInteraction("999")
    await view.cancel.callback(stranger_cancel)  # pyright: ignore[reportCallIssue]
    assert "당신" in stranger_cancel.response.calls[0]["content"]

    # 아무것도 바뀌지 않았다 — 데이터도, 연결 상태도 그대로다.
    assert await players.player_for_discord("222") == joiner
    assert joiner_ctx.player_data is not None
    assert await joiner_ctx.player_data.count(joiner.id) == {"goals_items": 1}


async def test_multi_account_joiner_is_rejected_and_nothing_changes(link_env) -> None:
    """리뷰(2차) Important(이미 결정된 설계): player_data는 계정이 아니라 플레이어에 딸린다 —
    참여자가 이미 계정을 2개 이상 연결했다면 "버릴 데이터가 어느 계정 것인지"가 애매해진다.
    그 모호함을 풀지 않고, 참여 자체를 거절해 애초에 그 상황이 생기지 않게 한다. 거절 뒤에는
    계정도 태그도 코드 상태도 전혀 바뀌지 않아야 한다."""
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id, "111"))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    await players.link_account(joiner.id, "333")
    await players.grant_tag(joiner.id, "tester", source="test")

    result = await skill.join_link(make_ctx(joiner.id, "222"), code=code)  # 222 계정에서 참여 시도

    assert result.error is not None
    assert "다른 계정" in result.error

    # 아무것도 바뀌지 않았다 — 계정도, 태그도, 코드 상태도 그대로다.
    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == joiner.id
    remaining = await players.player_for_discord("333")
    assert remaining is not None
    assert remaining.id == joiner.id
    assert await players.tags_for(joiner.id) == {"tester"}

    link_code = await skill.models.get_code(make_ctx(issuer.id, "111").db, code)
    assert link_code is not None
    assert link_code.status == "pending"
    assert link_code.joiner_discord_id is None


async def test_second_join_with_same_code_is_rejected(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id, "111"))
    code = started.data["code"]

    first_joiner = await players.create_player()
    await players.link_account(first_joiner.id, "222")
    await skill.join_link(make_ctx(first_joiner.id, "222"), code=code)

    second_joiner = await players.create_player()
    await players.link_account(second_joiner.id, "333")
    result = await skill.join_link(make_ctx(second_joiner.id, "333"), code=code)

    assert result.error is not None
    assert "다른 사람" in result.error
    # 첫 참여자의 진행이 덮어써지지 않았는지, models로 직접 확인한다.
    link_code = await skill.models.get_code(make_ctx(issuer.id, "111").db, code)
    assert link_code is not None
    assert link_code.joiner_discord_id == "222"


async def test_confirm_button_rejects_expired_code(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id, "111")
    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=code)
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


async def test_confirm_after_completion_does_not_crash(link_env) -> None:
    """리뷰 Minor: 완료된 뒤 다시 확인 버튼을 눌러도 link_account의 UNIQUE 제약으로 터지지
    않고, 이미 끝났다는 안내만 돌아와야 한다."""
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id, "111")
    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=code)
    joiner_view = joined.display.view
    issuer_view = issuer_ctx.services["notify"].dms[-1][2]

    await issuer_view.confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]
    await joiner_view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    second_click = _FakeInteraction("222")
    await joiner_view.confirm.callback(second_click)  # pyright: ignore[reportCallIssue]

    assert "끝났다" in second_click.response.calls[0]["content"]


async def test_concurrent_confirm_clicks_do_not_double_finalize_or_delete_issuer_data(link_env) -> None:
    """리뷰(2차) New Critical: 마무리 단계(양쪽 다 확인된 뒤 실제로 계정을 옮기고 필요하면
    데이터를 지우는 부분)는 '상태를 읽고 나중에 행동'하는 식이라 매 await마다 다른 클릭이
    끼어들 수 있다. 재현된 문제: 두 번째 클릭이 끝나기 전 클릭과 겹치면 link_account가 두 번
    불려 터지거나, 이미 발급자로 옮겨간 참여자 계정을 다시 조회해 발급자 자신의 데이터를
    지워 버릴 수 있었다. mark_finalizing의 원자적 UPDATE가 이 경쟁을 실제로 막는지, 같은
    버튼을 asyncio.gather로 동시에 눌러 확인한다."""
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id, "111")
    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=code)
    joiner_view = joined.display.view
    issuer_view = issuer_ctx.services["notify"].dms[-1][2]

    # 발급자는 먼저 확인해 둔다 — 그래야 참여자의 확인이 "양쪽 다 확인됨" 분기(마무리 단계)로
    # 바로 들어간다.
    await issuer_view.confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]

    click_a = _FakeInteraction("222")
    click_b = _FakeInteraction("222")
    await asyncio.gather(
        joiner_view.confirm.callback(click_a),  # pyright: ignore[reportCallIssue]
        joiner_view.confirm.callback(click_b),  # pyright: ignore[reportCallIssue]
    )

    # 터지지 않았고(gather가 예외 없이 끝났다), "연결이 완료됐다!"는 정확히 한 번만 나왔다.
    responses = [c["content"] for c in click_a.response.calls] + [c["content"] for c in click_b.response.calls]
    assert responses.count("연결이 완료됐다!") == 1

    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == issuer.id

    # 이 테스트는 버릴 데이터가 없는(discard_agreed=False) 경로다 — 그래도 발급자 자신의
    # player_data가 지워졌다면(재현된 버그) 여기서 드러난다.
    assert issuer_ctx.player_data is not None
    assert await issuer_ctx.player_data.count(issuer.id) == {}


async def test_start_link_rejects_when_already_at_max_linked_accounts(link_env) -> None:
    skill, players, _, make_ctx = link_env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")
    await players.link_account(player.id, "333")

    result = await skill.start_link(make_ctx(player.id, "111"))

    assert result.error is not None
    assert str(skill.MAX_LINKED_ACCOUNTS) in result.error
    # 발급도 안 됐으니 아무 코드도 안 생겼다.
    assert result.data == {}


async def test_unlink_rejects_removing_primary_while_other_accounts_remain(link_env) -> None:
    skill, players, _, make_ctx = link_env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")

    result = await skill.unlink(make_ctx(player.id, "111"), discord_id="111")

    assert result.error is not None
    assert "대표" in result.error
    assert await players.player_for_discord("111") is not None
    assert await players.player_for_discord("222") is not None
