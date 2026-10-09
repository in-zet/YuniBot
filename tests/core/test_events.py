import asyncio

import pytest

from core.events import EventBus, Section


@pytest.mark.asyncio
async def test_emit_notify_does_not_block_emitter() -> None:
    bus = EventBus()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_handler(payload: object) -> None:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    bus.register_notify("players.linked", owner_skill="account-link", fn=slow_handler)

    await bus.emit_notify("players.linked", {"player_id": "1"})
    assert not finished.is_set()  # emit_notify 자체는 핸들러를 기다리지 않는다

    await bus.drain()
    assert finished.is_set()


@pytest.mark.asyncio
async def test_emit_gather_orders_sections() -> None:
    bus = EventBus()

    async def from_b(payload: object) -> Section | None:
        return Section(order=1, data={"from": "b"})

    async def from_a(payload: object) -> Section | None:
        return Section(order=1, data={"from": "a"})

    async def from_c(payload: object) -> Section | None:
        return Section(order=0, data={"from": "c"})

    bus.register_gather("chunithm.sync.completed", owner_skill="skill-b", fn=from_b)
    bus.register_gather("chunithm.sync.completed", owner_skill="skill-a", fn=from_a)
    bus.register_gather("chunithm.sync.completed", owner_skill="skill-c", fn=from_c)

    sections = await bus.emit_gather("chunithm.sync.completed", {})

    assert [s.data["from"] for s in sections] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_emit_gather_drops_slow_handler() -> None:
    bus = EventBus(gather_timeout=0.01)

    async def slow(payload: object) -> Section | None:
        await asyncio.sleep(1)
        return Section(data={"slow": True})

    async def fast(payload: object) -> Section | None:
        return Section(data={"fast": True})

    bus.register_gather("evt", owner_skill="slow-skill", fn=slow)
    bus.register_gather("evt", owner_skill="fast-skill", fn=fast)

    sections = await bus.emit_gather("evt", {})

    assert len(sections) == 1
    assert sections[0].data == {"fast": True}


@pytest.mark.asyncio
async def test_emit_gather_handler_returning_none_is_skipped() -> None:
    bus = EventBus()

    async def nothing_to_say(payload: object) -> Section | None:
        return None

    bus.register_gather("evt", owner_skill="s", fn=nothing_to_say)
    sections = await bus.emit_gather("evt", {})
    assert sections == []


@pytest.mark.asyncio
async def test_chain_depth_limit_enforced() -> None:
    bus = EventBus(max_chain_depth=3)
    depths_seen: list[int] = []

    async def chain(payload: dict[str, int]) -> None:
        depths_seen.append(payload["depth"])
        await bus.emit_notify("chain", {"depth": payload["depth"] + 1})

    bus.register_notify("chain", owner_skill="s", fn=chain)
    await bus.emit_notify("chain", {"depth": 0})
    await bus.drain()

    assert depths_seen == [0, 1, 2, 3]  # 0에서 시작해 depth 3까지만 돌고 4단계(다음 체인)는 끊긴다
