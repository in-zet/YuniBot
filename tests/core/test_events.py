import asyncio

import pytest

from core.events import EventBus, Section, handler_kind
from core.tool_types import ToolContext, UndeclaredServiceError


class _FakeTagProvider:
    def __init__(self, tags: dict[str, set[str]] | None = None) -> None:
        self._tags = tags or {}

    async def tags_for(self, player_id: str) -> set[str]:
        return set(self._tags.get(player_id, set()))


class _FakeSettings:
    def __init__(self, enabled: dict[tuple[str, str], bool] | None = None) -> None:
        self._enabled = enabled or {}

    async def is_skill_enabled(self, *, guild_id: str, skill: str) -> bool:
        return self._enabled.get((guild_id, skill), True)


@pytest.mark.asyncio
async def test_emit_notify_does_not_block_emitter() -> None:
    bus = EventBus()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_handler(ctx: ToolContext, payload: object) -> None:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    bus.register_notify("players.linked", owner_skill="account-link", fn=slow_handler, skill_services=[])

    await bus.emit_notify("players.linked", {"player_id": "1"}, player_id="1", guild_id=None)
    assert not finished.is_set()  # emit_notify 자체는 핸들러를 기다리지 않는다

    await bus.drain()
    assert finished.is_set()


@pytest.mark.asyncio
async def test_emit_gather_orders_sections() -> None:
    bus = EventBus()

    async def from_b(ctx: ToolContext, payload: object) -> Section | None:
        return Section(order=1, data={"from": "b"})

    async def from_a(ctx: ToolContext, payload: object) -> Section | None:
        return Section(order=1, data={"from": "a"})

    async def from_c(ctx: ToolContext, payload: object) -> Section | None:
        return Section(order=0, data={"from": "c"})

    bus.register_gather("chunithm.sync.completed", owner_skill="skill-b", fn=from_b, skill_services=[])
    bus.register_gather("chunithm.sync.completed", owner_skill="skill-a", fn=from_a, skill_services=[])
    bus.register_gather("chunithm.sync.completed", owner_skill="skill-c", fn=from_c, skill_services=[])

    sections = await bus.emit_gather("chunithm.sync.completed", {}, player_id="1", guild_id=None)

    assert [s.data["from"] for s in sections] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_emit_gather_drops_slow_handler() -> None:
    bus = EventBus(gather_timeout=0.01)

    async def slow(ctx: ToolContext, payload: object) -> Section | None:
        await asyncio.sleep(1)
        return Section(data={"slow": True})

    async def fast(ctx: ToolContext, payload: object) -> Section | None:
        return Section(data={"fast": True})

    bus.register_gather("evt", owner_skill="slow-skill", fn=slow, skill_services=[])
    bus.register_gather("evt", owner_skill="fast-skill", fn=fast, skill_services=[])

    sections = await bus.emit_gather("evt", {}, player_id="1", guild_id=None)

    assert len(sections) == 1
    assert sections[0].data == {"fast": True}


@pytest.mark.asyncio
async def test_emit_gather_handler_returning_none_is_skipped() -> None:
    bus = EventBus()

    async def nothing_to_say(ctx: ToolContext, payload: object) -> Section | None:
        return None

    bus.register_gather("evt", owner_skill="s", fn=nothing_to_say, skill_services=[])
    sections = await bus.emit_gather("evt", {}, player_id="1", guild_id=None)
    assert sections == []


@pytest.mark.asyncio
async def test_chain_depth_limit_enforced() -> None:
    bus = EventBus(max_chain_depth=3)
    depths_seen: list[int] = []

    async def chain(ctx: ToolContext, payload: dict[str, int]) -> None:
        depths_seen.append(payload["depth"])
        await bus.emit_notify("chain", {"depth": payload["depth"] + 1}, player_id="1", guild_id=None)

    bus.register_notify("chain", owner_skill="s", fn=chain, skill_services=[])
    await bus.emit_notify("chain", {"depth": 0}, player_id="1", guild_id=None)
    await bus.drain()

    assert depths_seen == [0, 1, 2, 3]  # 0에서 시작해 depth 3까지만 돌고 4단계(다음 체인)는 끊긴다


def test_handler_kind_infers_gather_from_return_annotation() -> None:
    async def gather_handler(ctx: ToolContext, payload: object) -> Section | None:
        return None

    async def notify_handler(ctx: ToolContext, payload: object) -> None:
        return None

    assert handler_kind(gather_handler) == "gather"
    assert handler_kind(notify_handler) == "notify"


@pytest.mark.asyncio
async def test_notify_handler_receives_ctx_scoped_to_skill_services() -> None:
    bus = EventBus()
    bus.bind_dispatch(
        services={"songdb": object(), "secrets": object()},
        tag_provider=_FakeTagProvider(),
        settings=None,
    )
    seen_services: list[set[str]] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        seen_services.append(set(ctx.services))
        with pytest.raises(UndeclaredServiceError):
            _ = ctx.secrets

    bus.register_notify("evt", owner_skill="songdb-skill", fn=handler, skill_services=["songdb"])
    await bus.emit_notify("evt", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert seen_services == [{"songdb"}]


@pytest.mark.asyncio
async def test_gather_handler_receives_ctx_scoped_to_skill_services() -> None:
    bus = EventBus()
    bus.bind_dispatch(
        services={"songdb": object(), "secrets": object()},
        tag_provider=_FakeTagProvider(),
        settings=None,
    )

    async def handler(ctx: ToolContext, payload: object) -> Section | None:
        assert set(ctx.services) == {"songdb"}
        with pytest.raises(UndeclaredServiceError):
            _ = ctx.secrets
        return Section(data={"ok": True})

    bus.register_gather("evt", owner_skill="songdb-skill", fn=handler, skill_services=["songdb"])
    sections = await bus.emit_gather("evt", {}, player_id="p1", guild_id=None)

    assert sections == [Section(data={"ok": True})]


@pytest.mark.asyncio
async def test_handler_called_with_empty_services_before_bind_dispatch() -> None:
    bus = EventBus()  # bind_dispatch를 아직 안 불렀다
    seen_services: list[dict[str, object]] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        seen_services.append(dict(ctx.services))

    bus.register_notify("evt", owner_skill="s", fn=handler, skill_services=["songdb"])
    await bus.emit_notify("evt", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert seen_services == [{}]


@pytest.mark.asyncio
async def test_notify_handler_skipped_when_skill_disabled_in_guild() -> None:
    bus = EventBus()
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider(), settings=_FakeSettings({("g1", "s"): False})
    )
    called: list[str] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        called.append("called")

    bus.register_notify("evt", owner_skill="s", fn=handler, skill_services=[])
    await bus.emit_notify("evt", {}, player_id="p1", guild_id="g1")
    await bus.drain()

    assert called == []


@pytest.mark.asyncio
async def test_gather_handler_section_dropped_when_skill_disabled_in_guild() -> None:
    bus = EventBus()
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider(), settings=_FakeSettings({("g1", "s"): False})
    )

    async def handler(ctx: ToolContext, payload: object) -> Section | None:
        return Section(data={"should": "not appear"})

    bus.register_gather("evt", owner_skill="s", fn=handler, skill_services=[])
    sections = await bus.emit_gather("evt", {}, player_id="p1", guild_id="g1")

    assert sections == []


@pytest.mark.asyncio
async def test_dm_guild_id_none_skips_skill_enabled_check_even_with_settings_bound() -> None:
    bus = EventBus()
    # settings가 있어도 guild_id가 None(DM)이면 켜짐 검사를 하지 않는다(core/slash.py와 같은 패턴).
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider(), settings=_FakeSettings({("g1", "s"): False})
    )
    called: list[str] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        called.append("called")

    bus.register_notify("evt", owner_skill="s", fn=handler, skill_services=[])
    await bus.emit_notify("evt", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert called == ["called"]


@pytest.mark.asyncio
async def test_notify_handler_skipped_when_player_missing_required_tags() -> None:
    bus = EventBus()
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider({"p1": set()}), settings=None
    )
    called: list[str] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        called.append("called")

    bus.register_notify(
        "evt", owner_skill="s", fn=handler, skill_services=[], tags=["chunithm-linked"]
    )
    await bus.emit_notify("evt", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert called == []


@pytest.mark.asyncio
async def test_notify_handler_runs_when_player_has_required_tags() -> None:
    bus = EventBus()
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider({"p1": {"chunithm-linked"}}), settings=None
    )
    called: list[str] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        called.append("called")
        assert ctx.tags == frozenset({"chunithm-linked"})

    bus.register_notify(
        "evt", owner_skill="s", fn=handler, skill_services=[], tags=["chunithm-linked"]
    )
    await bus.emit_notify("evt", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert called == ["called"]


@pytest.mark.asyncio
async def test_notify_handler_tags_any_requires_at_least_one() -> None:
    bus = EventBus()
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider({"p1": {"vip"}}), settings=None
    )
    called: list[str] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        called.append("called")

    bus.register_notify(
        "evt", owner_skill="s", fn=handler, skill_services=[], tags_any=["vip", "admin"]
    )
    await bus.emit_notify("evt", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert called == ["called"]


@pytest.mark.asyncio
async def test_handler_skipped_when_player_lacks_skill_level_tags() -> None:
    # 핸들러 자신의 조건(chunithm-linked)은 갖췄지만 그 스킬의 manifest 조건(chunithm-synced)은
    # 없는 플레이어: 알림은 안 불리고 모으기 조각은 빠진다. 두 조건은 따로 검사한다.
    bus = EventBus()
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider({"p1": {"chunithm-linked"}}), settings=None
    )
    notified: list[str] = []

    async def notify_handler(ctx: ToolContext, payload: object) -> None:
        notified.append("called")

    async def gather_handler(ctx: ToolContext, payload: object) -> Section | None:
        return Section(data={"from": "gated"})

    async def other_gather(ctx: ToolContext, payload: object) -> Section | None:
        return Section(data={"from": "open"})

    bus.register_notify(
        "evt", owner_skill="s", fn=notify_handler, skill_services=[],
        tags=["chunithm-linked"], skill_tags=["chunithm-synced"],
    )
    bus.register_gather(
        "evt", owner_skill="s", fn=gather_handler, skill_services=[],
        tags=["chunithm-linked"], skill_tags=["chunithm-synced"],
    )
    bus.register_gather("evt", owner_skill="other", fn=other_gather, skill_services=[])

    await bus.emit_notify("evt", {}, player_id="p1", guild_id=None)
    await bus.drain()
    sections = await bus.emit_gather("evt", {}, player_id="p1", guild_id=None)

    assert notified == []
    assert [s.data["from"] for s in sections] == ["open"]


@pytest.mark.asyncio
async def test_skill_level_tags_any_checked_separately_from_handler_tags_any() -> None:
    # 핸들러의 tags_any(vip)만 맞고 스킬의 tags_any(admin, mod)는 하나도 안 맞는다 — 둘을 합쳐
    # 한 번에 검사하면 통과해 버리므로, 따로 검사해서 걸러져야 한다.
    bus = EventBus()
    bus.bind_dispatch(services={}, tag_provider=_FakeTagProvider({"p1": {"vip"}}), settings=None)

    async def gather_handler(ctx: ToolContext, payload: object) -> Section | None:
        return Section(data={"from": "gated"})

    bus.register_gather(
        "evt", owner_skill="s", fn=gather_handler, skill_services=[],
        tags_any=["vip"], skill_tags_any=["admin", "mod"],
    )

    assert await bus.emit_gather("evt", {}, player_id="p1", guild_id=None) == []

    bus.bind_dispatch(services={}, tag_provider=_FakeTagProvider({"p1": {"vip", "mod"}}), settings=None)
    sections = await bus.emit_gather("evt", {}, player_id="p1", guild_id=None)
    assert [s.data["from"] for s in sections] == ["gated"]
