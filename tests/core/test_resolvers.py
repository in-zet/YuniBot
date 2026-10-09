# tests/core/test_resolvers.py
import pytest

from core.resolvers import ResolutionError, ResolverRegistry
from core.tool_types import ChartRef, RankRef, ToolContext


class _FakeChartResolver:
    async def resolve(self, raw: object, ctx: ToolContext) -> dict[str, object]:
        if raw == "크로노":
            return {"song": "크로노", "difficulty": "MAS", "const": 14.9}
        raise ResolutionError(candidates=["크로노", "크로노스"])


def test_register_and_resolve() -> None:
    registry = ResolverRegistry()
    registry.register(ChartRef, _FakeChartResolver())

    resolver = registry.get(ChartRef)
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})

    import asyncio

    result = asyncio.run(resolver.resolve("크로노", ctx))
    assert result["const"] == 14.9


def test_resolver_not_registered_raises() -> None:
    registry = ResolverRegistry()
    with pytest.raises(KeyError, match="RankRef"):
        registry.get(RankRef)


def test_resolution_error_carries_candidates() -> None:
    err = ResolutionError(candidates=["a", "b"])
    assert err.candidates == ["a", "b"]
    assert "a" in str(err)
