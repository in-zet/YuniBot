# core/search.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.llm import OnRetry


@dataclass(slots=True, kw_only=True, frozen=True)
class SearchSource:
    title: str
    url: str


@dataclass(slots=True, kw_only=True)
class SearchUsage:
    input_tokens: int
    output_tokens: int


@dataclass(slots=True, kw_only=True)
class SearchResult:
    text: str | None
    sources: list[SearchSource] = field(default_factory=list)
    usage: SearchUsage = field(default_factory=lambda: SearchUsage(input_tokens=0, output_tokens=0))


class SearchAdapter(Protocol):
    async def search(self, *, model: str, query: str, on_retry: OnRetry | None = None) -> SearchResult: ...

    async def aclose(self) -> None: ...
