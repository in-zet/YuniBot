# tests/core/test_executor.py
import logging

import pytest

from core.decorators import tool
from core.executor import ToolExecutor
from core.manifest import DefinedTag
from core.permissions import InMemoryTagProvider
from core.resolvers import ResolutionError, ResolverRegistry
from core.tool_types import ArcadeRef, ChartRef, ToolContext, ToolResult


@tool(slash="보더")
async def lookup(ctx: ToolContext, chart: ChartRef, count: int = 1) -> ToolResult:
    """채보 기준 계산."""
    return ToolResult(data={"chart": chart, "count": count})


@tool(slash="보더잠김", tags=["chunithm-linked"])
async def locked(ctx: ToolContext) -> ToolResult:
    """상태 태그가 필요한 도구."""
    return ToolResult(data={})


@tool(slash="비밀도구", tags=["practice-secret"])
async def hidden_tool(ctx: ToolContext) -> ToolResult:
    """숨김 태그가 필요한 도구."""
    return ToolResult(data={})


@tool(slash="위험", mutates=True, confirm="always")
async def dangerous(ctx: ToolContext) -> ToolResult:
    """확인이 필요한 도구."""
    return ToolResult(data={"done": True})


_risky_call_count: list[int] = []


@tool(slash="위험2", mutates=True, confirm="always")
async def risky(ctx: ToolContext) -> ToolResult:
    """실제 호출 여부를 횟수로 증명하기 위한, 확인이 필요한 도구."""
    _risky_call_count.append(1)
    return ToolResult(data={"done": True})


@tool(slash="해석기없음")
async def no_resolver_tool(ctx: ToolContext, arcade: ArcadeRef) -> ToolResult:
    """ResolverRegistry에 해석기가 등록되지 않은 표준 타입을 쓰는 도구."""
    return ToolResult(data={"arcade": arcade})


@tool(slash="터짐")
async def boom(ctx: ToolContext) -> ToolResult:
    """예외를 던지는 도구."""
    raise RuntimeError("oops")


@tool(slash="태그호환", tags_any=["tool-c", "tool-d"])
async def tags_any_tool(ctx: ToolContext) -> ToolResult:
    """도구 레벨 tags_any와 스킬 레벨 tags_any 합성을 시험하는 도구."""
    return ToolResult(data={})


class _FakeChartResolver:
    async def resolve(self, raw: object, ctx: ToolContext) -> dict[str, str]:
        if raw == "크로노":
            return {"song": "크로노"}
        raise ResolutionError(candidates=["크로노"])


def _make_executor() -> tuple[ToolExecutor, InMemoryTagProvider]:
    resolvers = ResolverRegistry()
    resolvers.register(ChartRef, _FakeChartResolver())
    tag_provider = InMemoryTagProvider()
    tag_defs = {
        "chunithm-linked": DefinedTag(
            name="chunithm-linked", description="연동됨", how_to_get="로그인하면 얻는다", hidden=False
        ),
        "practice-secret": DefinedTag(
            name="practice-secret", description="비밀", how_to_get="관리자 부여", hidden=True
        ),
    }
    return ToolExecutor(resolvers=resolvers, tag_provider=tag_provider, tag_definitions=tag_defs), tag_provider


@pytest.mark.asyncio
async def test_invoke_happy_path() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(lookup.__yuni_tool__, ctx, {"chart": "크로노", "count": 2})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.needs_confirm is False
    assert outcome.result is not None
    assert outcome.result.data["count"] == 2
    assert outcome.result.data["chart"]["song"] == "크로노"


@pytest.mark.asyncio
async def test_invoke_resolution_failure_returns_error() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(lookup.__yuni_tool__, ctx, {"chart": "없는곡"})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.result is not None
    assert outcome.result.error is not None
    assert "크로노" in outcome.result.error


@pytest.mark.asyncio
async def test_invoke_missing_visible_tag_gives_guidance() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(locked.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.result is not None
    assert "로그인" in (outcome.result.error or "")


@pytest.mark.asyncio
async def test_invoke_missing_hidden_tag_gives_generic_error() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(hidden_tool.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.result is not None
    assert "로그인" not in (outcome.result.error or "")
    assert "비밀" not in (outcome.result.error or "")


@pytest.mark.asyncio
async def test_invoke_with_satisfied_tag_succeeds() -> None:
    executor, tag_provider = _make_executor()
    tag_provider.grant("p1", "chunithm-linked")
    tags = await tag_provider.tags_for("p1")
    ctx = ToolContext(player_id="p1", tags=frozenset(tags), services={})

    outcome = await executor.invoke(locked.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.result is not None
    assert outcome.result.error is None


@pytest.mark.asyncio
async def test_invoke_confirm_flow() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.needs_confirm is True
    assert outcome.result is None
    assert outcome.confirm_token is not None

    result = await executor.confirm(outcome.confirm_token, confirmed=True, player_id="p1")
    assert result.data == {"done": True}


@pytest.mark.asyncio
async def test_invoke_confirm_declined_does_not_run() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.confirm_token is not None
    result = await executor.confirm(outcome.confirm_token, confirmed=False, player_id="p1")
    assert result.error is not None


@pytest.mark.asyncio
async def test_invoke_exception_becomes_error_result() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(boom.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.result is not None
    assert "oops" in (outcome.result.error or "")


@pytest.mark.asyncio
async def test_invoke_confirm_twice_second_call_fails() -> None:
    """같은 확인 토큰으로 두 번 confirm하면(디스코드 버튼 중복 클릭) 두 번째는 실패해야 한다."""
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    token = outcome.confirm_token
    assert token is not None

    first = await executor.confirm(token, confirmed=True, player_id="p1")
    assert first.data == {"done": True}

    second = await executor.confirm(token, confirmed=True, player_id="p1")
    assert second.error is not None
    assert second.data == {}


@pytest.mark.asyncio
async def test_confirm_with_unknown_token_fails() -> None:
    executor, _ = _make_executor()

    result = await executor.confirm("never-issued-token", confirmed=True, player_id="p1")

    assert result.error is not None


@pytest.mark.asyncio
async def test_skill_and_tool_tags_any_are_independent_conditions() -> None:
    """스킬의 tags_any와 도구의 tags_any는 각각 독립된 "둘 중 하나" 조건이다.
    둘을 하나로 합쳐서 "넷 중 하나"로 느슨하게 검사하면 안 된다
    ((skill-a 또는 skill-b)와 (tool-c 또는 tool-d)를 모두 만족해야 한다)."""
    executor, _ = _make_executor()

    # 스킬 조건만 만족, 도구 조건은 불만족 -> 실패해야 한다.
    ctx_skill_only = ToolContext(player_id="p1", tags=frozenset({"skill-a"}), services={})
    outcome_skill_only = await executor.invoke(
        tags_any_tool.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
        ctx_skill_only,
        {},
        skill_tags_any=["skill-a", "skill-b"],
    )
    assert outcome_skill_only.result is not None
    assert outcome_skill_only.result.error is not None

    # 도구 조건만 만족, 스킬 조건은 불만족 -> 실패해야 한다.
    ctx_tool_only = ToolContext(player_id="p1", tags=frozenset({"tool-c"}), services={})
    outcome_tool_only = await executor.invoke(
        tags_any_tool.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
        ctx_tool_only,
        {},
        skill_tags_any=["skill-a", "skill-b"],
    )
    assert outcome_tool_only.result is not None
    assert outcome_tool_only.result.error is not None

    # 둘 다 만족 -> 성공해야 한다.
    ctx_both = ToolContext(player_id="p1", tags=frozenset({"skill-a", "tool-c"}), services={})
    outcome_both = await executor.invoke(
        tags_any_tool.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
        ctx_both,
        {},
        skill_tags_any=["skill-a", "skill-b"],
    )
    assert outcome_both.result is not None
    assert outcome_both.result.error is None


@pytest.mark.asyncio
async def test_invoke_missing_resolver_becomes_error_result() -> None:
    """ref-type 인자의 해석기가 아예 등록되지 않았으면(ResolverRegistry.get()이 던지는
    KeyError) 예외가 그대로 터지지 않고 에러 ToolResult로 와야 한다. invoke()는 호출자에게
    절대 날것 예외를 흘려보내지 않는다는 보장이 해석 단계에도 적용돼야 한다."""
    executor, _ = _make_executor()  # ArcadeRef용 해석기는 등록돼 있지 않다
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(
        no_resolver_tool.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
        ctx,
        {"arcade": "아무거나"},
    )

    assert outcome.result is not None
    assert outcome.result.error is not None


@pytest.mark.asyncio
async def test_invoke_confirm_declined_does_not_call_function() -> None:
    """미승인(confirmed=False, player_id="p1")이면 실제 도구 함수가 호출되지 않는다는 것을
    호출 횟수로 직접 증명한다(result is None이라는 구조만으로는 부족하다)."""
    before = len(_risky_call_count)
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(risky.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.needs_confirm is True
    assert len(_risky_call_count) == before  # invoke()만으로는 아직 호출되지 않는다

    assert outcome.confirm_token is not None
    result = await executor.confirm(outcome.confirm_token, confirmed=False, player_id="p1")

    assert len(_risky_call_count) == before  # 거절해도 호출되지 않는다
    assert result.error is not None


@pytest.mark.asyncio
async def test_invoke_confirm_after_decline_cannot_be_retried_as_confirmed() -> None:
    """한 번 거절한 토큰으로 다시 confirmed=True를 보내도(토큰 재사용) 도구가
    실행돼서는 안 된다 — 거절 시점에 토큰이 이미 소비된다."""
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.confirm_token is not None
    token = outcome.confirm_token

    declined = await executor.confirm(token, confirmed=False, player_id="p1")
    assert declined.error is not None

    retried = await executor.confirm(token, confirmed=True, player_id="p1")
    assert retried.error is not None
    assert retried.data == {}


@pytest.mark.asyncio
async def test_confirm_by_a_different_player_is_rejected_and_does_not_run() -> None:
    """확인 버튼은 요청한 본인만 누를 수 있다. 다른 플레이어가 같은 토큰으로 확인(또는 취소)해도
    도구가 실행되지 않고, 토큰도 소비되지 않아 본인은 그대로 확인할 수 있어야 한다."""
    before = len(_risky_call_count)
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(risky.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    token = outcome.confirm_token
    assert token is not None

    hijack = await executor.confirm(token, confirmed=True, player_id="p2")
    assert hijack.error is not None
    assert hijack.data == {}
    assert len(_risky_call_count) == before

    cancel_attempt = await executor.confirm(token, confirmed=False, player_id="p2")
    assert cancel_attempt.error is not None
    assert len(_risky_call_count) == before

    owner = await executor.confirm(token, confirmed=True, player_id="p1")
    assert owner.error is None
    assert owner.data == {"done": True}
    assert len(_risky_call_count) == before + 1


class _BuggyResolver:
    async def resolve(self, raw: object, ctx: ToolContext) -> object:
        raise KeyError("resolver internal bug")


@pytest.mark.asyncio
async def test_buggy_registered_resolver_is_logged_not_masked_as_unregistered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """등록된 해석기 자체의 버그(KeyError 등)는 "해석기 미등록"과 구분되어 로그에 남아야 한다.
    그래도 호출자에게 날것 예외를 흘려보내지는 않는다."""
    executor, _ = _make_executor()
    executor._resolvers.register(ArcadeRef, _BuggyResolver())  # pyright: ignore[reportPrivateUsage]
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    with caplog.at_level(logging.ERROR):
        outcome = await executor.invoke(
            no_resolver_tool.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
            ctx,
            {"arcade": "아무거나"},
        )

    assert outcome.result is not None
    assert outcome.result.error is not None
    assert "등록된 해석기가 없다" not in outcome.result.error
    assert "resolver internal bug" in caplog.text


@pytest.mark.asyncio
async def test_unregistered_resolver_error_names_the_type() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(
        no_resolver_tool.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
        ctx,
        {"arcade": "아무거나"},
    )

    assert outcome.result is not None
    assert "ArcadeRef" in (outcome.result.error or "")


class _FakeSettings:
    def __init__(self) -> None:
        self._disabled: set[tuple[str, str]] = set()

    def disable(self, guild_id: str, skill: str) -> None:
        self._disabled.add((guild_id, skill))

    async def is_skill_enabled(self, *, guild_id: str, skill: str) -> bool:
        return (guild_id, skill) not in self._disabled


async def test_invoke_blocks_tool_when_skill_disabled_for_guild() -> None:
    @tool(slash="보더")
    async def lookup(ctx: ToolContext) -> ToolResult:
        """더미."""
        return ToolResult(data={})

    settings = _FakeSettings()
    settings.disable("g1", "chunithm-border")
    executor = ToolExecutor(
        resolvers=ResolverRegistry(),
        tag_provider=InMemoryTagProvider(),
        tag_definitions={},
        settings=settings,  # type: ignore[arg-type]
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    outcome = await executor.invoke(
        lookup.__yuni_tool__, ctx, {}, skill_name="chunithm-border"  # pyright: ignore[reportFunctionMemberAccess]
    )

    assert outcome.result is not None
    assert outcome.result.error == "모르는 명령이에요"


async def test_invoke_allows_tool_when_no_guild_context() -> None:
    @tool(slash="보더2")
    async def lookup(ctx: ToolContext) -> ToolResult:
        """더미."""
        return ToolResult(data={"ok": True})

    settings = _FakeSettings()
    settings.disable("g1", "chunithm-border")
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={}, settings=settings  # type: ignore[arg-type]
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id=None)

    outcome = await executor.invoke(
        lookup.__yuni_tool__, ctx, {}, skill_name="chunithm-border"  # pyright: ignore[reportFunctionMemberAccess]
    )

    assert outcome.result is not None
    assert outcome.result.data == {"ok": True}


# ---------------------------------------------------------------- confirm="bulk"

_bulk_writes: list[int] = []


@tool(slash="일괄", mutates=True, confirm="bulk")
async def bulk_tool(ctx: ToolContext, n: int, *, dry_run: bool = False) -> ToolResult:
    """n건을 바꾸는 도구. dry_run이면 바꿀 건수만 돌려준다."""
    if n < 0:
        return ToolResult(error="음수는 안 된다")
    if dry_run:
        return ToolResult(data={"count": n})
    _bulk_writes.append(n)
    return ToolResult(data={"count": n, "written": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [0, 1])
async def test_bulk_tool_with_zero_or_one_item_runs_immediately(n: int) -> None:
    _bulk_writes.clear()
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(bulk_tool.__yuni_tool__, ctx, {"n": n})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.needs_confirm is False
    assert outcome.confirm_token is None
    assert outcome.result is not None
    assert outcome.result.data == {"count": n, "written": True}
    assert _bulk_writes == [n]


@pytest.mark.asyncio
async def test_bulk_tool_with_many_items_pauses_until_confirmed() -> None:
    _bulk_writes.clear()
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(bulk_tool.__yuni_tool__, ctx, {"n": 3})  # pyright: ignore[reportFunctionMemberAccess]

    assert outcome.needs_confirm is True
    assert outcome.result is None
    assert outcome.confirm_token is not None
    assert outcome.preview is not None
    assert outcome.preview.data == {"count": 3}
    assert _bulk_writes == []  # 확인 전에는 아무것도 쓰지 않는다

    result = await executor.confirm(outcome.confirm_token, confirmed=True, player_id="p1")
    assert result.data == {"count": 3, "written": True}
    assert _bulk_writes == [3]


@pytest.mark.asyncio
async def test_bulk_tool_declined_does_not_write() -> None:
    _bulk_writes.clear()
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(bulk_tool.__yuni_tool__, ctx, {"n": 5})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.confirm_token is not None
    result = await executor.confirm(outcome.confirm_token, confirmed=False, player_id="p1")
    assert result.error is not None
    assert _bulk_writes == []


@pytest.mark.asyncio
async def test_bulk_tool_dry_run_error_is_returned_without_pending() -> None:
    _bulk_writes.clear()
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(bulk_tool.__yuni_tool__, ctx, {"n": -1})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.needs_confirm is False
    assert outcome.result is not None
    assert outcome.result.error == "음수는 안 된다"
    assert _bulk_writes == []


@pytest.mark.asyncio
async def test_bulk_tool_ignores_caller_supplied_dry_run() -> None:
    """dry_run은 실행기가 넣는 인자다 — 모델/슬래시가 넘겨도 스키마에 없어 무시된다."""
    _bulk_writes.clear()
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(
        bulk_tool.__yuni_tool__, ctx, {"n": 1, "dry_run": True}  # pyright: ignore[reportFunctionMemberAccess]
    )
    assert outcome.result is not None
    assert outcome.result.data.get("written") is True
    assert _bulk_writes == [1]


@pytest.mark.asyncio
async def test_always_tool_still_pauses_regardless_of_count() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.needs_confirm is True
    assert outcome.preview is None
