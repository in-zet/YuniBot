from __future__ import annotations

from core.events import Section, Stage, highest_stage
from core.threads import ThreadTranscript

_STAGE_LINES: dict[str, str] = {
    "great": "완전 짱이에요! 최고예요!",
    "good": "좋아요, 더 가 볼 수 있어요!",
    "close": "조금만 더예요. 같이 세팅 봐 볼까요?",
    "rest": "오늘은 이 정도로도 충분해요. 쉬어도 돼요.",
}
_DEFAULT_LINE = "확인했어요."


class YuniService:
    """유니의 한마디(조각들의 data·hint·stage를 받아 정해진 문구를 돌려줌)와 대화 요약
    인터페이스. 11단계에서 실제 LLM 구현으로 바꿔 끼울 때 이 두 메서드를 부르는 쪽
    코드가 바뀌지 않도록, 인자와 반환 형식을 설계서 이벤트 절의 조각 칸에 맞춘다."""

    async def line(self, sections: list[Section]) -> str:
        """11단계에서 LLM 호출로 바뀐다. 그때 부르는 쪽이 바뀌지 않도록 지금부터 비동기다."""
        stages: list[Stage] = [s.stage for s in sections if s.stage is not None]
        highest = highest_stage(stages)
        return _STAGE_LINES[highest] if highest is not None else _DEFAULT_LINE

    async def summarize_conversation(self, transcript: ThreadTranscript) -> str:  # pyright: ignore[reportUnusedParameter]
        """대화 전문을 요약한다. 11~12단계에서 싼 모델 호출로 바뀐다 — 지금은 자리만 만든다."""
        return ""
