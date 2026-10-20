# core/llm.py
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# 어댑터가 일시 오류(503 과부하 등)로 재시도하기 직전에 부른다. 인자는 (몇 번째 시도인지,
# 대기할 초). core/mention.py가 이걸로 "잠깐만 기다려 달라" 안내를 한 번만 보낸다.
OnRetry = Callable[[int, float], Awaitable[None]]


@dataclass(slots=True, kw_only=True)
class FunctionCallRequest:
    call_id: str | None
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class FunctionResultPart:
    name: str
    response: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None  # Claude는 결과를 호출 ID로 짝짓는다(이름만으론 같은 도구를
    # 병렬로 두 번 부른 경우를 구분 못 한다). Gemini 어댑터는 이름 매칭만으로 충분해 이 필드를
    # 쓰지 않는다.


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
    # 실제로 답한 모델. 보조 모델로 넘어갔을 때만 채운다(비어 있으면 요청한 모델 그대로).
    # core/mention.py가 비용 기록에 쓴다.
    model: str | None = None


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
        on_retry: OnRetry | None = None,
    ) -> GenerationTurn: ...
