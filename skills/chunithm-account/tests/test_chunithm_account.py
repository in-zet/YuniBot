# skills/chunithm-account/tests/test_chunithm_account.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from core.tool_types import ToolContext

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_account_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeChunithmNet:
    def __init__(
        self,
        *,
        linked: bool,
        nickname: str | None = None,
        login_username: str = "유니상",
        login_error: Exception | None = None,
    ) -> None:
        from services.chunithm.chunithm_net import LoginStatus

        self._status = LoginStatus(linked=linked, nickname=nickname, last_checked_at="2026-10-11T00:00:00+00:00")
        self.logged_out: list[str] = []
        self.login_calls: list[tuple[str, str]] = []
        self._login_username = login_username
        self._login_error = login_error

    async def status(self, player_id: str):
        return self._status

    async def logout(self, player_id: str, ctx) -> None:
        self.logged_out.append(player_id)

    async def login(self, player_id: str, clal: str, ctx):
        from types import SimpleNamespace

        self.login_calls.append((player_id, clal))
        if self._login_error is not None:
            raise self._login_error
        return SimpleNamespace(username=self._login_username)


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


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred: list[bool] = []

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred.append(ephemeral)


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool, Any]] = []

    async def send(self, content: str, *, ephemeral: bool = False, view: Any = None) -> None:
        self.sent.append((content, ephemeral, view))


class _FakeInteraction:
    def __init__(self) -> None:
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


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


async def test_status_is_ephemeral() -> None:
    # 로그인 상태, 닉네임, 기록 공개 여부는 본인만 봐야 한다 — 채널에 공개로 뜨면 안 된다.
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True, nickname="유니상")
    players = _FakePlayers(records_public=False)
    records = _FakeRecords(None)

    result = await skill.status(_ctx(chunithm_net=chunithm_net, players=players, records=records))

    assert result.display is not None
    assert result.display.ephemeral is True


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


async def test_login_modal_not_linked_logs_in_directly() -> None:
    """처음 로그인하는 경우(아직 연결 안 됨)는 확인 없이 바로 login()을 부른다."""
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=False, login_username="유니상")

    modal = skill._LoginModal(ctx=_ctx(chunithm_net=chunithm_net))
    modal.clal_input._value = "clal=abc123"  # TextInput의 .value는 제출 후 채워지는 속성
    interaction = _FakeInteraction()

    await modal.on_submit(interaction)

    assert chunithm_net.login_calls == [("p1", "clal=abc123")]
    assert interaction.followup.sent
    assert "유니상" in interaction.followup.sent[0][0]


async def test_login_modal_already_linked_shows_overwrite_confirm() -> None:
    """이미 연결돼 있으면 바로 덮어쓰지 않고, 확인 버튼이 달린 메시지를 먼저 보여준다."""
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True, nickname="기존닉")

    modal = skill._LoginModal(ctx=_ctx(chunithm_net=chunithm_net))
    modal.clal_input._value = "clal=new999"
    interaction = _FakeInteraction()

    await modal.on_submit(interaction)

    assert chunithm_net.login_calls == []
    assert interaction.followup.sent
    content, _ephemeral, view = interaction.followup.sent[0]
    assert "기존닉" in content
    assert "덮어쓸까" in content
    assert view is not None


async def test_login_modal_overwrite_button_logs_in_with_submitted_clal() -> None:
    """"덮어쓰기"를 누르면 모달에 처음 제출됐던 clal 값으로 실제 login()을 부른다."""
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True, nickname="기존닉", login_username="새유저")

    modal = skill._LoginModal(ctx=_ctx(chunithm_net=chunithm_net))
    modal.clal_input._value = "clal=new999"
    submit_interaction = _FakeInteraction()
    await modal.on_submit(submit_interaction)
    _content, _ephemeral, view = submit_interaction.followup.sent[0]

    click_interaction = _FakeInteraction()
    await view.overwrite.callback(click_interaction)

    assert chunithm_net.login_calls == [("p1", "clal=new999")]
    assert click_interaction.followup.sent
    assert "새유저" in click_interaction.followup.sent[0][0]


async def test_login_modal_cancel_button_does_not_log_in() -> None:
    """"취소"를 누르면 login()을 부르지 않고 취소 메시지만 보여준다."""
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True, nickname="기존닉")

    modal = skill._LoginModal(ctx=_ctx(chunithm_net=chunithm_net))
    modal.clal_input._value = "clal=new999"
    submit_interaction = _FakeInteraction()
    await modal.on_submit(submit_interaction)
    _content, _ephemeral, view = submit_interaction.followup.sent[0]

    click_interaction = _FakeInteraction()
    await view.cancel.callback(click_interaction)

    assert chunithm_net.login_calls == []
    assert click_interaction.followup.sent
    assert "취소" in click_interaction.followup.sent[0][0]


async def test_login_expired_handler_sends_dm_once() -> None:
    skill = _load_skill()
    notify = _FakeNotify()
    ctx = _ctx(notify=notify)

    await skill.on_login_expired(ctx, {"player_id": "p1"})
    await skill.on_login_expired(ctx, {"player_id": "p1"})

    assert len(notify.dms) == 1
    assert notify.dms[0][0] == "p1"


async def test_logout_locked_without_chunithm_linked_shows_how_to_get_it() -> None:
    """chunithm-linked는 chunithm-account의 defines_tags가 설명을 소유한다(finding 3).
    실제 SkillRegistry로 manifest를 읽어야 describe_lock이 쓸 설명이 채워진다 —
    그래서 skill.logout()을 직접 부르지 않고 core.executor.ToolExecutor를 거친다
    (skills/chunithm-terms의 test_review_requires_admin_tag_via_executor와 같은 모양)."""
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.registry import SkillRegistry
    from core.resolvers import ResolverRegistry

    registry = SkillRegistry([Path("skills/chunithm-account")])
    registry.load()
    assert registry.failures() == {}

    [(skill, spec)] = [(s, sp) for s, sp in registry.tool_specs() if sp.name == "logout"]

    executor = ToolExecutor(
        resolvers=ResolverRegistry(),
        tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(),
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(spec, ctx, {}, skill_tags=skill.manifest.tags, skill_name="chunithm-account")

    assert outcome.result is not None
    assert outcome.result.error is not None
    assert outcome.result.error != "모르는 명령이에요"
    assert "로그인" in outcome.result.error
