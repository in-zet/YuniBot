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
from services.common.memory import MemoryService
from services.common.players import PlayersService

_CORE_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
    Path("migrations/core/004_memory.sql").read_text(),
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
    memory = MemoryService(conn)
    player_data = PlayerDataScanner(conn=conn, table_names=await discover_skill_tables(conn))
    skill = _load_skill()

    def make_ctx(player_id: str, discord_id: str) -> ToolContext:
        # discord_id는 "지금 명령을 실행한" 계정이다 — core/slash.py의 _dispatch가 실제로
        # interaction.user.id를 ToolContext.discord_id에 싣는 것과 같은 자리를 흉내 낸다.
        # 플레이어가 이미 여러 계정을 연결했어도 accounts_for()의 첫 번째가 아니라
        # 바로 이 값을 써야 한다(리뷰 Finding 1).
        # db/player_data는 _dispatch가 manifest.services에 선언한 스킬에게만 채워 준다 —
        # account-link은 둘 다 선언했으므로(SKILL.md) 여기서도 그대로 준다.
        return ToolContext(
            player_id=player_id, tags=frozenset(),
            services={"players": players, "notify": notify, "memory": memory},
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


async def test_single_side_confirm_does_not_write_status(link_env) -> None:
    """리뷰(3차) Important: '혼자만 확인했다 — 상대를 기다리는 중' 분기(else)는 예전에
    status를 "{side}_confirmed" 문자열로 덮어썼다. 이 분기와 마무리(완료) 분기 사이에 다른
    클릭이 끼어들어 먼저 마무리를 끝내 버리면(status='completed'), 그 뒤 늦게 도착하는 이
    쓰기가 'completed'를 되돌려 마무리를 한 번 더 돌게 만들 수 있었다(재현됨: 두 번째
    link_account 호출의 UNIQUE 충돌, 또는 데이터 있음 경로에서는 'finalizing'에 영구히
    갇힘). 고친 뒤에는 이 분기가 status를 전혀 쓰지 않으므로, 혼자 확인해도 status는 그대로
    'joined'로 남아야 한다 — 되돌릴 쓰기 자체가 없다는 것을 직접 확인한다."""
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id, "111")
    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=code)
    issuer_view = issuer_ctx.services["notify"].dms[-1][2]

    assert joined.display is not None  # 데이터가 없어 바로 상호 확인으로 들어갔다
    before = await skill.models.get_code(issuer_ctx.db, code)
    assert before is not None
    assert before.status == "joined"

    click = _FakeInteraction("111")
    await issuer_view.confirm.callback(click)  # pyright: ignore[reportCallIssue]

    assert "상대의 확인을 기다리는" in click.response.calls[0]["content"]
    after = await skill.models.get_code(issuer_ctx.db, code)
    assert after is not None
    assert after.status == "joined"  # 혼자 확인해도 status는 전혀 안 바뀐다
    assert after.issuer_confirmed is True
    assert after.joiner_confirmed is False


async def test_concurrent_both_sides_first_confirm_ends_with_completed_status(link_env) -> None:
    """리뷰(3차) Important: 양쪽이 "처음" 확인하는 클릭이 동시에 겹쳐도(이전 라운드 테스트는
    같은 쪽의 두 번째 클릭이었다 — 이번엔 서로 다른 쪽의 첫 클릭을 asyncio.gather로 동시에
    누른다), 최종 status는 'completed'로 남아야 하고 완료 메시지는 정확히 한 번만 나와야
    한다. '상대를 기다리는 중' 분기가 더는 status를 쓰지 않으므로, 어느 쪽이 마무리를
    가져가든 다른 쪽의 (지금은 없는) 쓰기가 되돌릴 일이 없다."""
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

    issuer_click = _FakeInteraction("111")
    joiner_click = _FakeInteraction("222")
    await asyncio.gather(
        issuer_view.confirm.callback(issuer_click),  # pyright: ignore[reportCallIssue]
        joiner_view.confirm.callback(joiner_click),  # pyright: ignore[reportCallIssue]
    )

    responses = [c["content"] for c in issuer_click.response.calls] + [c["content"] for c in joiner_click.response.calls]
    assert responses.count("연결이 완료됐다!") == 1

    link_code = await skill.models.get_code(issuer_ctx.db, code)
    assert link_code is not None
    assert link_code.status == "completed"

    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == issuer.id


async def test_finalize_rechecks_joiner_account_count_before_discarding_data(link_env) -> None:
    """리뷰(3차) Important(좁은 범위): join_link은 '버릴 데이터' 확인을 보여줄 때 참여자
    계정이 하나뿐인지 확인하지만, 확인 버튼은 최대 10분(CODE_TTL) 떠 있다 — 그 사이 참여자가
    완전히 다른(동시에 진행 중인) 연결 흐름으로 계정을 하나 더 얻으면, join_link이 막으려던
    "이 데이터가 어느 계정 것인지 모호한" 상황이 마무리 시점에 되살아난다. 마무리가 실제로
    지우기 직전에 다시 확인해서 거절하는지 본다."""
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id, "111")
    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner_ctx = make_ctx(joiner.id, "222")
    assert joiner_ctx.db is not None
    await joiner_ctx.db.execute(
        "INSERT INTO goals_items (id, player_id, text) VALUES ('g3', ?, '목표')", (joiner.id,)
    )
    await joiner_ctx.db.commit()

    joined = await skill.join_link(joiner_ctx, code=code)
    discard_view = joined.display.view
    assert discard_view is not None

    discard_click = _FakeInteraction("222")
    await discard_view.discard_and_continue.callback(discard_click)  # pyright: ignore[reportCallIssue]
    joiner_view = discard_click.response.calls[0]["view"]

    # 확인 버튼이 떠 있는 동안, 참여자가 (이 코드와 무관한) 다른 계정을 하나 더 연결했다고
    # 가정한다.
    await players.link_account(joiner.id, "999")

    issuer_view = issuer_ctx.services["notify"].dms[-1][2]
    await issuer_view.confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]

    joiner_click = _FakeInteraction("222")
    await joiner_view.confirm.callback(joiner_click)  # pyright: ignore[reportCallIssue]

    assert "안전하게" in joiner_click.response.calls[0]["content"]
    # 아무것도 지워지거나 옮겨지지 않았다 — 두 계정 다 여전히 원래 joiner 플레이어다.
    moved_check = await players.player_for_discord("222")
    assert moved_check is not None
    assert moved_check.id == joiner.id
    other_account = await players.player_for_discord("999")
    assert other_account is not None
    assert other_account.id == joiner.id
    assert await joiner_ctx.player_data.count(joiner.id) == {"goals_items": 1}  # pyright: ignore[reportOptionalMemberAccess]


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


async def test_unlink_rejects_another_players_account(link_env) -> None:
    """리뷰(최종) Fix 1 [Critical]: /연결 해제에 남의 디스코드 ID를 넣어도 남의 연결이 끊기면 안 된다."""
    skill, players, _, make_ctx = link_env
    attacker = await players.create_player()
    await players.link_account(attacker.id, "111")
    victim = await players.create_player()
    await players.link_account(victim.id, "222")
    await players.link_account(victim.id, "333")
    await players.grant_tag(victim.id, "tester", source="test")
    ctx = make_ctx(attacker.id, "111")

    for target in ("222", "333"):  # 대표 계정이든 아니든
        result = await skill.unlink(ctx, discord_id=target)
        assert result.error is not None
        assert "내 플레이어" in result.error

    # 피해자는 그대로다 — 계정, 대표, 태그 모두.
    assert [a.discord_id for a in await players.accounts_for(victim.id)] == ["222", "333"]
    refreshed_victim = await players.get_player(victim.id)
    assert refreshed_victim is not None
    assert refreshed_victim.primary_discord_id == "222"
    assert await players.tags_for(victim.id) == {"tester"}
    # 공격자 자신도 그대로다.
    assert [a.discord_id for a in await players.accounts_for(attacker.id)] == ["111"]


async def test_unlink_own_non_primary_account_still_works(link_env) -> None:
    skill, players, _, make_ctx = link_env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")

    result = await skill.unlink(make_ctx(player.id, "111"), discord_id="222")

    assert result.error is None
    assert await players.player_for_discord("222") is None
    assert [a.discord_id for a in await players.accounts_for(player.id)] == ["111"]


async def test_issuer_started_from_non_primary_account_can_confirm_from_any_linked_account(link_env) -> None:
    """리뷰(최종) Fix 2: 발급자 확인 DM은 notify.dm이 대표 계정으로 보낸다. /연결 시작을 대표가
    아닌 계정에서 쳐도 교착되면 안 된다 — 발급자 플레이어의 어느 계정이든 확인을 누를 수 있다."""
    skill, players, notify, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    await players.link_account(issuer.id, "333")
    await players.set_primary(issuer.id, "333")  # DM은 333으로 간다

    started = await skill.start_link(make_ctx(issuer.id, "111"))  # 대표가 아닌 111에서 시작
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=code)
    joiner_view = joined.display.view
    dm_player_id, _, issuer_view = notify.dms[-1]
    assert dm_player_id == issuer.id

    # 남이나 상대(참여자)는 발급자 쪽 버튼을 못 누른다.
    stranger = _FakeInteraction("999")
    await issuer_view.confirm.callback(stranger)  # pyright: ignore[reportCallIssue]
    assert "당신" in stranger.response.calls[0]["content"]
    joiner_on_issuer_side = _FakeInteraction("222")
    await issuer_view.confirm.callback(joiner_on_issuer_side)  # pyright: ignore[reportCallIssue]
    assert "당신" in joiner_on_issuer_side.response.calls[0]["content"]
    # 발급자도 참여자 쪽 버튼을 못 누른다.
    issuer_on_joiner_side = _FakeInteraction("333")
    await joiner_view.confirm.callback(issuer_on_joiner_side)  # pyright: ignore[reportCallIssue]
    assert "당신" in issuer_on_joiner_side.response.calls[0]["content"]

    # DM을 받은 대표 계정 333이 확인한다(명령을 친 111이 아니다).
    primary_click = _FakeInteraction("333")
    await issuer_view.confirm.callback(primary_click)  # pyright: ignore[reportCallIssue]
    assert "기다리는" in primary_click.response.calls[0]["content"]

    joiner_click = _FakeInteraction("222")
    await joiner_view.confirm.callback(joiner_click)  # pyright: ignore[reportCallIssue]
    assert joiner_click.response.calls[0]["content"] == "연결이 완료됐다!"

    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == issuer.id


async def test_issuer_command_account_can_also_confirm(link_env) -> None:
    skill, players, notify, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    await players.link_account(issuer.id, "333")
    await players.set_primary(issuer.id, "333")
    started = await skill.start_link(make_ctx(issuer.id, "111"))

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=started.data["code"])
    issuer_view = notify.dms[-1][2]

    await issuer_view.confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]
    await joined.display.view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == issuer.id


async def test_finalize_dissolves_stale_joiner_so_its_admin_tag_is_not_counted(link_env) -> None:
    """리뷰(최종) Fix 4: 참여자의 옛 플레이어(계정 하나짜리)는 연결 뒤 닿을 수 없는 고아가 된다.
    그 고아가 손으로 받은 admin 태그를 계속 쥐고 있으면 players_with_tag("admin")이 유령 관리자를
    세어 admin의 '마지막 관리자' 검사가 속는다. 마무리가 dissolve_player로 태그까지 지워야 한다."""
    skill, players, notify, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id, "111"))

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    await players.grant_tag(joiner.id, "admin", source="admin:someone")
    await players.grant_tag(joiner.id, "tester", source="test")
    assert joiner.id in await players.players_with_tag("admin")

    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=started.data["code"])
    await notify.dms[-1][2].confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]
    await joined.display.view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == issuer.id
    assert joiner.id not in await players.players_with_tag("admin")
    assert await players.tags_for(joiner.id) == set()
    assert await players.accounts_for(joiner.id) == []
    # 태그는 합치지 않는다 — 발급자는 참여자의 옛 태그를 얻지 않는다.
    assert await players.tags_for(issuer.id) == set()


async def test_finalize_without_discard_keeps_stale_joiner_with_other_accounts_intact(link_env) -> None:
    """Fix 4의 경계: 버릴 데이터가 없는 경로에서 확인을 기다리는 사이 옛 플레이어가 계정을 하나 더
    얻었다면, 그 플레이어는 고아가 아니다 — 해체하지 않고 옮기는 계정 하나만 뗀다."""
    skill, players, notify, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id, "111"))

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    await players.grant_tag(joiner.id, "tester", source="test")
    joined = await skill.join_link(make_ctx(joiner.id, "222"), code=started.data["code"])
    await players.link_account(joiner.id, "999")  # 확인 버튼이 떠 있는 사이

    await notify.dms[-1][2].confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]
    await joined.display.view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == issuer.id
    assert [a.discord_id for a in await players.accounts_for(joiner.id)] == ["999"]
    assert await players.tags_for(joiner.id) == {"tester"}


async def test_memory_only_joiner_gets_discard_prompt_and_memory_is_cleared(link_env) -> None:
    """리뷰(최종) Fix 7: player_data 스캐너는 코어 테이블(player_memory)을 세지 않는다. 메모리만
    있는 계정도 버리기 확인을 받아야 하고, 버리고 연결하면 그 메모리가 실제로 지워져야 한다."""
    skill, players, notify, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id, "111"))

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner_ctx = make_ctx(joiner.id, "222")
    await joiner_ctx.memory.set(joiner.id, "호칭: 펭귄")
    assert joiner_ctx.player_data is not None
    assert await joiner_ctx.player_data.count(joiner.id) == {}  # 스킬 테이블 행은 없다

    result = await skill.join_link(joiner_ctx, code=started.data["code"])
    assert result.data["has_memory"] is True
    assert result.display is not None
    assert "메모리" in (result.display.content or "")
    discard_view = result.display.view
    assert discard_view is not None
    # 아직 상호 확인으로 넘어가지 않았다(발급자에게 DM이 가지 않았다).
    assert notify.dms == []

    discard_click = _FakeInteraction("222")
    await discard_view.discard_and_continue.callback(discard_click)  # pyright: ignore[reportCallIssue]
    joiner_view = discard_click.response.calls[0]["view"]
    # 양쪽이 확인하기 전에는 아직 지우지 않는다.
    assert await joiner_ctx.memory.get(joiner.id) == "호칭: 펭귄"

    await notify.dms[-1][2].confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]
    await joiner_view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    moved = await players.player_for_discord("222")
    assert moved is not None
    assert moved.id == issuer.id
    assert await joiner_ctx.memory.get(joiner.id) == ""


def test_account_link_manifest_declares_every_ctx_field_it_uses() -> None:
    """리뷰(최종) Fix 3: ctx.db/ctx.player_data도 manifest.services에 선언해야 로드된다."""
    from core.registry import SkillRegistry

    registry = SkillRegistry([Path("skills/account-link")])
    registry.load()
    assert registry.failures() == {}
