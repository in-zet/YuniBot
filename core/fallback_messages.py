# core/fallback_messages.py
"""LLM 호출 실패/상한/쿨다운, 대기 안내 등에 쓰는 고정 문구.

00-core.md "멘션 에이전트 규칙": "LLM 호출이 실패하거나 월 상한에 닿으면 유니 말투의 고정
문구로 슬래시 명령을 안내한다." 말투는 prompts/persona.md를 따른다(밝은 반말, 늘임·감탄사, 사과는 짧게).
"""
from __future__ import annotations

LLM_FAILURE_MESSAGE = "으으, 미안! 지금은 대답을 못 만들겠어. 슬래시 명령(/도움말 등)은 그대로 쓸 수 있으니까 그걸로 해 줘!"
MONTHLY_CAP_REACHED_MESSAGE = "으으, 이번 달에 쓸 수 있는 양을 다 써 버렸어. 슬래시 명령(/도움말 등)은 계속 쓸 수 있으니까 그걸로 불러 줘!"
COOLDOWN_ACTIVE_MESSAGE = "와와, 너무 빨라! 나 숨 좀 돌릴게~ 조금만 있다가 다시 불러 줘!"
TOOL_CALL_CAP_MESSAGE = "으으, 이번엔 이것저것 너무 많이 찾아봤어. 질문을 나눠서 다시 말해 줄래?"
RETRY_WAIT_MESSAGE = "으음, 답이 좀 늦어지네~ 조오금만 기다려 줘!"
WORKING_MESSAGE = "얏호, 찾아보는 중이야! 나비짱이랑 금방 찾아올게~"
PRIVATE_DM_FAILED_MESSAGE = "으으, DM을 못 보냈어. 디스코드 설정에서 DM을 받을 수 있게 해 주거나, 슬래시 명령으로 다시 불러 줘!"
IDLE_CHECK_MESSAGE = "저기~ 이야기 다 끝난 거야?"
