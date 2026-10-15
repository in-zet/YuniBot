# core/llm.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(slots=True, kw_only=True)
class FunctionCallRequest:
    call_id: str | None
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class FunctionResultPart:
    name: str
    response: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class LlmPart:
    text: str | None = None
    function_call: FunctionCallRequest | None = None
    function_response: FunctionResultPart | None = None
    raw: Any | None = None  # 어댑터 내부용(예: google.genai.types.Part) — 코어는 들여다보지 않는다.


@dataclass(slots=True, kw_only=True)
class LlmContent:
    role: Literal["user", "model"]
    parts: list[LlmPart] = field(default_factory=list)
    raw: Any | None = None


@dataclass(slots=True, kw_only=True)
class UsageInfo:
    input_tokens: int
    output_tokens: int


@dataclass(slots=True, kw_only=True)
class GenerationTurn:
    content: LlmContent
    function_calls: list[FunctionCallRequest] = field(default_factory=list)
    text: str | None = None
    usage: UsageInfo = field(default_factory=lambda: UsageInfo(input_tokens=0, output_tokens=0))


@dataclass(slots=True, kw_only=True)
class ToolDeclaration:
    name: str
    description: str
    parameters_json_schema: dict[str, Any]


class LlmAdapter(Protocol):
    async def generate(
        self,
        *,
        model: str,
        system_instruction: str,
        contents: list[LlmContent],
        tools: list[ToolDeclaration],
        thinking_level: str,
    ) -> GenerationTurn: ...
