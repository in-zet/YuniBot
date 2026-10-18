"""실제 Gemini API를 부르지 않는 가짜 LlmAdapter. 에이전트 루프 테스트 전용."""
from __future__ import annotations

from collections.abc import Callable

from core.llm import GenerationTurn, LlmContent, OnRetry, ToolDeclaration


class FakeLlmAdapter:
    """스크립트(턴마다 돌려줄 GenerationTurn 목록)를 그대로 재생한다.

    script는 호출 순서대로 쓸 GenerationTurn 리스트, 또는 (model, system_instruction,
    contents, tools)를 받아 GenerationTurn을 만드는 콜백이다. 콜백을 쓰면 테스트가
    "이전 턴에 뭘 봤는지"에 따라 다른 응답을 만들 수 있다.
    """

    def __init__(
        self,
        script: list[GenerationTurn]
        | Callable[[str, str, list[LlmContent], list[ToolDeclaration]], GenerationTurn],
    ) -> None:
        self._script = script
        self._index = 0
        self.calls: list[tuple[str, str, list[LlmContent], list[ToolDeclaration]]] = []

    async def generate(
        self,
        *,
        model: str,
        system_instruction: str,
        contents: list[LlmContent],
        tools: list[ToolDeclaration],
        thinking_level: str,
        on_retry: OnRetry | None = None,
    ) -> GenerationTurn:
        del on_retry  # 가짜 어댑터는 재시도를 흉내 내지 않는다.
        self.calls.append((model, system_instruction, list(contents), list(tools)))
        if callable(self._script):
            return self._script(model, system_instruction, contents, tools)
        turn = self._script[self._index]
        self._index += 1
        return turn
