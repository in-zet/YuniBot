# core/fallback_messages.py
"""LLM 호출 실패/상한/쿨다운 때 쓰는 고정 문구.

00-core.md "멘션 에이전트 규칙": "LLM 호출이 실패하거나 월 상한에 닿으면 유니 말투의 고정
문구로 슬래시 명령을 안내한다." 말투는 prompts/persona.md를 따른다(밝은 반말).
"""
from __future__ import annotations

LLM_FAILURE_MESSAGE = "미안, 지금은 대답을 만들 수가 없어. 슬래시 명령(/도움말 등)은 그대로 쓸 수 있으니까 그걸로 해 줘!"
MONTHLY_CAP_REACHED_MESSAGE = "이번 달에 쓸 수 있는 양을 다 써 버렸어. 슬래시 명령(/도움말 등)은 계속 쓸 수 있어!"
COOLDOWN_ACTIVE_MESSAGE = "잠깐, 너무 빨라! 조금만 있다가 다시 불러 줘."
TOOL_CALL_CAP_MESSAGE = "이번 요청에서 이것저것 너무 많이 찾아봤어. 질문을 나눠서 다시 말해 줄래?"
RETRY_WAIT_MESSAGE = "지금 답이 좀 늦네. 잠깐만 기다려 줘!"
WORKING_MESSAGE = "찾아보는 중이야, 잠깐만 기다려 줘!"
