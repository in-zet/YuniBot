# skills/chunithm-account/tests/test_chunithm_account.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from core.tool_types import ToolContext

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_account_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeChunithmNet:
    def __init__(self, *, linked: bool, nickname: str | None = None) -> None:
        from services.chunithm.chunithm_net import LoginStatus

        self._status = LoginStatus(linked=linked, nickname=nickname, last_checked_at="2026-10-11T00:00:00+00:00")
        self.logged_out: list[str] = []

    async def status(self, player_id: str):
        return self._status

    async def logout(self, player_id: str, ctx) -> None:
        self.logged_out.append(player_id)


class _FakePlayers:
    def __init__(self, *, records_public: bool) -> None:
        from dataclasses import dataclass

        @dataclass(slots=True, kw_only=True)
        class _P:
            records_public: bool

        self._player = _P(records_public=records_public)
        self.visibility_calls: list[tuple[str, str, bool]] = []

    async def get_player(self, player_id: str):
        return self._player

    async def set_visibility(self, player_id: str, field: str, value: bool) -> None:
        self.visibility_calls.append((player_id, field, value))
        self._player.records_public = value


class _FakeRecords:
    def __init__(self, last_sync_at: str | None) -> None:
        self._last = last_sync_at

    async def last_sync_at(self, player_id: str):
        return self._last


class _FakeNotify:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str]] = []

    async def dm(self, player_id: str, content: str, *, view=None) -> None:
        self.dms.append((player_id, content))


def _ctx(*, chunithm_net=None, players=None, records=None, notify=None) -> ToolContext:
    services: dict[str, object] = {}
    if chunithm_net is not None:
        services["chunithm_net"] = chunithm_net
    if players is not None:
        services["players"] = players
    if records is not None:
        services["records"] = records
    if notify is not None:
        services["notify"] = notify
    return ToolContext(player_id="p1", tags=frozenset(), services=services)


async def test_status_reports_linked_and_last_sync() -> None:
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True, nickname="유니상")
    players = _FakePlayers(records_public=False)
    records = _FakeRecords("2026-10-11T03:00:00+00:00")

    result = await skill.status(_ctx(chunithm_net=chunithm_net, players=players, records=records))

    assert result.error is None
    assert result.data["linked"] is True
    assert result.data["nickname"] == "유니상"
    assert result.data["last_synced_at"] == "2026-10-11T03:00:00+00:00"
    assert result.data["records_public"] is False


async def test_status_reports_never_synced() -> None:
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=False)
    players = _FakePlayers(records_public=False)
    records = _FakeRecords(None)

    result = await skill.status(_ctx(chunithm_net=chunithm_net, players=players, records=records))

    assert result.data["last_synced_at"] is None
    assert result.display is not None and "아직" in (result.display.content or "")


async def test_set_privacy_updates_visibility() -> None:
    skill = _load_skill()
    players = _FakePlayers(records_public=False)

    result = await skill.set_privacy(_ctx(players=players), public=True)

    assert result.error is None
    assert players.visibility_calls == [("p1", "records_public", True)]


async def test_logout_clears_cookie() -> None:
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True)

    result = await skill.logout(_ctx(chunithm_net=chunithm_net))

    assert result.error is None
    assert chunithm_net.logged_out == ["p1"]


async def test_login_modal_success_sends_followup() -> None:
    skill = _load_skill()

    class _LoginSuccessChunithmNet:
        async def login(self, player_id: str, clal: str, ctx) -> object:
            from types import SimpleNamespace

            assert clal == "clal=abc123"
            return SimpleNamespace(username="유니상")

    class _FakeResponse:
        async def defer(self, *, ephemeral: bool = False) -> None:
            pass

    class _FakeFollowup:
        def __init__(self) -> None:
            self.sent: list[tuple[str, bool]] = []

        async def send(self, content: str, *, ephemeral: bool = False) -> None:
            self.sent.append((content, ephemeral))

    class _FakeInteraction:
        def __init__(self) -> None:
            self.response = _FakeResponse()
            self.followup = _FakeFollowup()

    modal = skill._LoginModal(ctx=_ctx(chunithm_net=_LoginSuccessChunithmNet()))
    modal.clal_input._value = "clal=abc123"  # TextInput의 .value는 제출 후 채워지는 속성
    interaction = _FakeInteraction()

    await modal.on_submit(interaction)

    assert interaction.followup.sent
    assert "유니상" in interaction.followup.sent[0][0]


async def test_login_expired_handler_sends_dm_once() -> None:
    skill = _load_skill()
    notify = _FakeNotify()
    ctx = _ctx(notify=notify)

    await skill.on_login_expired(ctx, {"player_id": "p1"})
    await skill.on_login_expired(ctx, {"player_id": "p1"})

    assert len(notify.dms) == 1
    assert notify.dms[0][0] == "p1"
