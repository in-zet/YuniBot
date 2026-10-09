# core/resolvers.py
from __future__ import annotations

from typing import Any, Protocol

from core.tool_types import ToolContext


class ResolutionError(Exception):
    """표준 인자 타입 해석 실패. candidates가 비어 있으면 '찾지 못함'이다."""

    def __init__(self, *, candidates: list[str] | None = None, message: str | None = None) -> None:
        self.candidates = candidates or []
        if message is None:
            message = (
                f"후보: {', '.join(self.candidates)}" if self.candidates else "찾지 못함"
            )
        super().__init__(message)


class Resolver(Protocol):
    async def resolve(self, raw: Any, ctx: ToolContext) -> Any: ...


class ResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[type, Resolver] = {}

    def register(self, ref_type: type, resolver: Resolver) -> None:
        self._resolvers[ref_type] = resolver

    def is_registered(self, ref_type: type) -> bool:
        return ref_type in self._resolvers

    def get(self, ref_type: type) -> Resolver:
        try:
            return self._resolvers[ref_type]
        except KeyError:
            raise KeyError(f"{ref_type.__name__}에 등록된 해석기가 없다") from None
