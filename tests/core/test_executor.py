# tests/core/test_executor.py
import pytest

from core.decorators import tool
from core.executor import ToolExecutor
from core.manifest import DefinedTag
from core.permissions import InMemoryTagProvider
from core.resolvers import ResolutionError, ResolverRegistry
from core.tool_types import ChartRef, ToolContext, ToolResult


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

    result = await executor.confirm(outcome.confirm_token, confirmed=True)
    assert result.data == {"done": True}


@pytest.mark.asyncio
async def test_invoke_confirm_declined_does_not_run() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})  # pyright: ignore[reportFunctionMemberAccess]
    assert outcome.confirm_token is not None
    result = await executor.confirm(outcome.confirm_token, confirmed=False)
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

    first = await executor.confirm(token, confirmed=True)
    assert first.data == {"done": True}

    second = await executor.confirm(token, confirmed=True)
    assert second.error is not None
    assert second.data == {}


@pytest.mark.asyncio
async def test_confirm_with_unknown_token_fails() -> None:
    executor, _ = _make_executor()

    result = await executor.confirm("never-issued-token", confirmed=True)

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
