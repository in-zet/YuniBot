# tests/services/test_memory.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from services.common.memory import (
    MAX_LENGTH,
    MAX_PENDING_MESSAGES,
    MAX_SUMMARY_ATTEMPTS,
    SUMMARY_DELAY_SECONDS,
    MemoryMerge,
    MemoryService,
    MemoryState,
    PendingMessage,
    PendingSummary,
    clean_nickname,
)

_MIGRATIONS = [
    Path("migrations/core/004_memory.sql").read_text(),
    Path("migrations/core/015_memory_nickname_pending.sql").read_text(),
]


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    for migration in _MIGRATIONS:
        await connection.executescript(migration)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


def _msgs(*pairs: tuple[str, str]) -> list[PendingMessage]:
    return [PendingMessage(id=i, text=t) for i, t in pairs]


async def _enqueue(service: MemoryService, player_id: str = "p1", *, now: float = 1000.0, **kw) -> bool:
    messages = kw.pop("messages", _msgs(("m1", "왼손이 약해")))
    return await service.enqueue(
        player_id, messages=messages, guild_id="g1", channel_id="c1", message_id="m1", now=now,
    )


# ---------------------------------------------------------------- 본문·호칭·자동저장


async def test_get_defaults_to_empty_string(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await service.get("p1") == ""
    assert await service.get_nickname("p1") == ""


async def test_set_then_get_round_trips(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "왼손이 약하다.")
    assert await service.get("p1") == "왼손이 약하다."


async def test_set_rejects_content_over_1500_chars(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    with pytest.raises(ValueError, match="1500"):
        await service.set("p1", "가" * 1501)


async def test_nickname_is_separate_from_content_and_not_counted_in_its_limit(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "가" * MAX_LENGTH)
    assert await service.set_nickname("p1", "디짐") == "디짐"
    assert await service.get_nickname("p1") == "디짐"
    assert len(await service.get("p1")) == MAX_LENGTH


def test_clean_nickname_strips_mentions_newlines_and_caps_length() -> None:
    assert clean_nickname("  유키\n상 ") == "유키 상"
    assert clean_nickname("@everyone <@123> `x`") == "everyone 123 x"
    assert len(clean_nickname("가" * 50)) == 20
    assert clean_nickname("@<>") == ""


async def test_clear_empties_content_and_nickname_but_keeps_forgotten_and_auto_save(
    conn: aiosqlite.Connection,
) -> None:
    service = MemoryService(conn)
    await service.set("p1", "내용")
    await service.set_nickname("p1", "디짐")
    await service.set_auto_save("p1", False)
    await service.forget("p1", "내용")
    await service.clear("p1")
    state = await service.get_state("p1")
    assert state.content == "" and state.nickname == ""
    assert state.forgotten == "내용"
    assert state.auto_save is False


async def test_auto_save_defaults_to_true_and_can_be_toggled(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await service.get_auto_save("p1") is True

    await service.set_auto_save("p1", False)
    assert await service.get_auto_save("p1") is False


async def test_set_auto_save_off_drops_pending_summary(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await _enqueue(service)
    await service.set_auto_save("p1", False)
    assert await service.due_pending(now=10**9) == []


async def test_memory_is_scoped_per_player(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "p1의 메모리")
    await service.set_nickname("p1", "디짐")
    assert await service.get("p2") == ""
    assert await service.get_nickname("p2") == ""


# ---------------------------------------------------------------- remember


async def test_remember_appends_a_line_and_skips_duplicates(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.remember("p1", content="왼손이 약하다")
    await service.remember("p1", content="  왼손이   약하다 ")
    await service.remember("p1", content="짧은 답을 좋아한다")
    assert await service.get("p1") == "왼손이 약하다\n짧은 답을 좋아한다"


async def test_remember_trims_oldest_lines_over_the_limit(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "\n".join(["가" * 700, "나" * 700]))
    await service.remember("p1", content="다" * 700)
    lines = (await service.get("p1")).splitlines()
    assert lines == ["나" * 700, "다" * 700]


async def test_remember_nickname_sets_the_nickname_slot(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    result = await service.remember("p1", nickname="  디짐 ")
    assert result.nickname_saved == "디짐" and result.content_saved is None
    assert await service.get_nickname("p1") == "디짐"
    assert await service.get("p1") == ""


async def test_remember_removes_the_item_from_forgotten(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "왼손이 약하다")
    await service.forget("p1", "왼손")
    assert "왼손이 약하다" in (await service.get_state("p1")).forgotten
    await service.remember("p1", content="왼손이 약하다")
    state = await service.get_state("p1")
    assert state.forgotten == "" and state.content == "왼손이 약하다"


# ---------------------------------------------------------------- forget


async def test_find_forget_does_not_write(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "왼손이 약하다\n짧은 답을 좋아한다")
    assert await service.find_forget("p1", "왼손") == ["왼손이 약하다"]
    assert await service.get("p1") == "왼손이 약하다\n짧은 답을 좋아한다"


async def test_forget_removes_matching_lines_and_remembers_them_as_forgotten(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "왼손이 약하다\n짧은 답을 좋아한다")
    removed = await service.forget("p1", "왼손이 약하다")
    assert removed == ["왼손이 약하다"]
    state = await service.get_state("p1")
    assert state.content == "짧은 답을 좋아한다"
    assert state.forgotten == "왼손이 약하다"


async def test_forget_nickname_by_keyword_or_value(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set_nickname("p1", "디짐")
    assert await service.forget("p1", "호칭") == ["호칭: 디짐"]
    assert await service.get_nickname("p1") == ""
    await service.set_nickname("p1", "디짐")
    assert await service.forget("p1", "디짐") == ["호칭: 디짐"]
    assert (await service.get_state("p1")).forgotten == ""


async def test_forget_without_what_wipes_everything_and_pending(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "a\nb")
    await service.set_nickname("p1", "디짐")
    await _enqueue(service)
    assert await service.find_forget("p1", None) == ["a", "b", "호칭: 디짐"]
    removed = await service.forget("p1", None)
    assert len(removed) == 3
    state = await service.get_state("p1")
    assert state.content == "" and state.nickname == "" and state.forgotten == "a\nb"
    assert await service.due_pending(now=10**9) == []


async def test_forget_returns_empty_when_nothing_matches(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "왼손이 약하다")
    assert await service.forget("p1", "오른손") == []
    assert await service.get("p1") == "왼손이 약하다"


async def test_forgotten_list_drops_oldest_over_500_chars(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", f"{'가' * 300}\n{'나' * 300}")
    await service.forget("p1", None)
    forgotten = (await service.get_state("p1")).forgotten
    assert forgotten == "나" * 300


async def test_forget_only_touches_the_given_player(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "왼손이 약하다")
    await service.set("p2", "왼손이 약하다")
    await service.forget("p1", "왼손")
    assert await service.get("p2") == "왼손이 약하다"


# ---------------------------------------------------------------- 요약 대기 목록


async def test_enqueue_sets_due_time_five_minutes_after_now(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await _enqueue(service, now=1000.0) is True
    assert await service.due_pending(now=1000.0 + SUMMARY_DELAY_SECONDS - 1) == []
    [pending] = await service.due_pending(now=1000.0 + SUMMARY_DELAY_SECONDS)
    assert pending.player_id == "p1" and pending.guild_id == "g1" and pending.channel_id == "c1"
    assert [m.text for m in pending.messages] == ["왼손이 약해"]


async def test_enqueue_again_postpones_and_dedupes_by_message_id(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await _enqueue(service, now=1000.0)
    await _enqueue(service, now=1200.0, messages=_msgs(("m1", "왼손이 약해"), ("m2", "짧게 답해 줘")))
    assert await service.due_pending(now=1000.0 + SUMMARY_DELAY_SECONDS) == []
    [pending] = await service.due_pending(now=1200.0 + SUMMARY_DELAY_SECONDS)
    assert [m.id for m in pending.messages] == ["m1", "m2"]


async def test_enqueue_keeps_only_the_latest_messages(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    many = _msgs(*[(f"m{i}", f"글{i}") for i in range(MAX_PENDING_MESSAGES + 5)])
    await _enqueue(service, messages=many)
    [pending] = await service.due_pending(now=10**9)
    assert len(pending.messages) == MAX_PENDING_MESSAGES
    assert pending.messages[-1].id == f"m{MAX_PENDING_MESSAGES + 4}"


async def test_enqueue_skips_when_auto_save_is_off_or_nothing_to_add(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await _enqueue(service, messages=[]) is False
    assert await _enqueue(service, messages=_msgs(("m1", "   "))) is False
    await service.set_auto_save("p1", False)
    assert await _enqueue(service) is False
    assert await service.due_pending(now=10**9) == []


async def _merge_to(result: MemoryMerge | None, seen: list[tuple[MemoryState, PendingSummary]] | None = None):
    async def merge(state: MemoryState, pending: PendingSummary) -> MemoryMerge | None:
        if seen is not None:
            seen.append((state, pending))
        return result

    return merge


async def test_flush_due_applies_merge_and_reports_change(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "기존")
    await _enqueue(service, now=1000.0)
    changed: list[str] = []
    seen: list[tuple[MemoryState, PendingSummary]] = []

    async def on_changed(pending: PendingSummary) -> None:
        changed.append(pending.message_id or "")

    count = await service.flush_due(
        now=1000.0 + SUMMARY_DELAY_SECONDS,
        merge=await _merge_to(MemoryMerge(content="기존\n왼손이 약하다", nickname="디짐"), seen),
        on_changed=on_changed,
    )
    assert count == 1 and changed == ["m1"]
    assert seen[0][0].content == "기존"
    state = await service.get_state("p1")
    assert state.content == "기존\n왼손이 약하다" and state.nickname == "디짐"
    assert await service.due_pending(now=10**9) == []


async def test_flush_due_before_due_time_does_nothing(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await _enqueue(service, now=1000.0)
    seen: list[tuple[MemoryState, PendingSummary]] = []
    count = await service.flush_due(now=1000.0 + 10, merge=await _merge_to(MemoryMerge(content="x"), seen))
    assert count == 0 and seen == []


async def test_flush_due_unchanged_result_clears_pending_without_reporting(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "기존")
    await _enqueue(service, now=1000.0)
    changed: list[str] = []

    async def on_changed(pending: PendingSummary) -> None:
        changed.append(pending.player_id)

    count = await service.flush_due(
        now=10**6, merge=await _merge_to(MemoryMerge(content="기존", nickname=None)), on_changed=on_changed,
    )
    assert count == 0 and changed == []
    assert await service.due_pending(now=10**9) == []


async def test_flush_due_does_not_bring_back_forgotten_items(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "왼손이 약하다\n짧은 답을 좋아한다")
    await service.forget("p1", "왼손이 약하다")
    await _enqueue(service, now=1000.0)
    await service.flush_due(
        now=10**6,
        merge=await _merge_to(MemoryMerge(content="짧은 답을 좋아한다\n왼손이  약하다\n리듬게임을 오래 했다")),
    )
    assert await service.get("p1") == "짧은 답을 좋아한다\n리듬게임을 오래 했다"


async def test_flush_due_trims_oversized_result_from_the_front_and_cleans_nickname(
    conn: aiosqlite.Connection,
) -> None:
    service = MemoryService(conn)
    await _enqueue(service, now=1000.0)
    big = "\n".join(["가" * 800, "나" * 800, "나" * 800, "다" * 100])
    await service.flush_due(now=10**6, merge=await _merge_to(MemoryMerge(content=big, nickname="@디짐\n상")))
    state = await service.get_state("p1")
    assert state.content.splitlines() == ["나" * 800, "다" * 100]
    assert state.nickname == "디짐 상"


async def test_flush_due_ignores_blank_nickname_from_the_model(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set_nickname("p1", "디짐")
    await _enqueue(service, now=1000.0)
    await service.flush_due(now=10**6, merge=await _merge_to(MemoryMerge(content="새 사실", nickname="@")))
    assert await service.get_nickname("p1") == "디짐"


async def test_flush_due_skips_and_drops_when_auto_save_was_turned_off(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await _enqueue(service, now=1000.0)
    # 대기 목록을 남긴 채 자동저장만 꺼진 상태(꺼졌다가 요약 직전에 다시 쌓인 경우를 흉내)
    await conn.execute(
        "INSERT INTO player_memory (player_id, auto_save, updated_at) VALUES ('p1', 0, 'x') "
        "ON CONFLICT (player_id) DO UPDATE SET auto_save = 0"
    )
    await conn.commit()
    seen: list[tuple[MemoryState, PendingSummary]] = []
    count = await service.flush_due(now=10**6, merge=await _merge_to(MemoryMerge(content="x"), seen))
    assert count == 0 and seen == []
    assert await service.due_pending(now=10**9) == []


async def test_flush_due_retries_after_failure_then_gives_up(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await _enqueue(service, now=1000.0)
    now = 1000.0 + SUMMARY_DELAY_SECONDS
    for attempt in range(MAX_SUMMARY_ATTEMPTS - 1):
        assert await service.flush_due(now=now, merge=await _merge_to(None)) == 0
        [pending] = await service.due_pending(now=now + SUMMARY_DELAY_SECONDS)
        assert pending.attempts == attempt + 1
        assert await service.due_pending(now=now) == []  # 5분 뒤로 미뤄졌다
        now += SUMMARY_DELAY_SECONDS
    assert await service.flush_due(now=now, merge=await _merge_to(None)) == 0
    assert await service.due_pending(now=10**9) == []


async def test_flush_due_treats_merge_exception_as_failure_and_continues_with_others(
    conn: aiosqlite.Connection,
) -> None:
    service = MemoryService(conn)
    await _enqueue(service, "p1", now=1000.0)
    await _enqueue(service, "p2", now=1001.0)

    async def merge(state: MemoryState, pending: PendingSummary) -> MemoryMerge | None:
        if pending.player_id == "p1":
            raise RuntimeError("boom")
        return MemoryMerge(content="p2 내용")

    assert await service.flush_due(now=10**6, merge=merge) == 1
    assert await service.get("p2") == "p2 내용"
    [still] = await service.due_pending(now=10**9)
    assert still.player_id == "p1" and still.attempts == 1


async def test_flush_due_keeps_pending_that_was_postponed_during_the_merge(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await _enqueue(service, now=1000.0)

    async def merge(state: MemoryState, pending: PendingSummary) -> MemoryMerge | None:
        # 요약하는 동안 같은 플레이어가 또 말했다
        await _enqueue(service, now=2000.0, messages=_msgs(("m9", "새 글")))
        return MemoryMerge(content="요약")

    await service.flush_due(now=1000.0 + SUMMARY_DELAY_SECONDS, merge=merge)
    [pending] = await service.due_pending(now=2000.0 + SUMMARY_DELAY_SECONDS)
    assert "m9" in [m.id for m in pending.messages]


async def test_on_changed_failure_does_not_undo_the_save(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await _enqueue(service, now=1000.0)

    async def on_changed(pending: PendingSummary) -> None:
        raise RuntimeError("반응 실패")

    count = await service.flush_due(now=10**6, merge=await _merge_to(MemoryMerge(content="저장됨")), on_changed=on_changed)
    assert count == 1 and await service.get("p1") == "저장됨"
