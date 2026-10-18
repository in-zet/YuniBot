# core/fallback_messages.py
"""LLM 호출 실패/상한/쿨다운 때 쓰는 고정 문구.

00-core.md "멘션 에이전트 규칙": "LLM 호출이 실패하거나 월 상한에 닿으면 유니 말투의 고정
문구로 슬래시 명령을 안내한다." 지금은 persona.md가 건조한 임시 말투이므로
이 문자열들도 평서체다. 11단계(유니 인격)에서 유니 말투로 바꿀 때 이 파일만 고치면 된다.
"""
from __future__ import annotations

LLM_FAILURE_MESSAGE = "지금 응답을 만들 수 없다. 슬래시 명령(/도움말 등)은 그대로 쓸 수 있다."
MONTHLY_CAP_REACHED_MESSAGE = "이번 달 LLM 사용량 상한에 도달했다. 슬래시 명령(/도움말 등)은 계속 쓸 수 있다."
COOLDOWN_ACTIVE_MESSAGE = "요청이 너무 잦다. 잠시 후 다시 멘션해 달라."
TOOL_CALL_CAP_MESSAGE = "이번 요청에서 도구를 너무 많이 불렀다. 질문을 나눠서 다시 말해 달라."
RETRY_WAIT_MESSAGE = "지금 응답이 늦네. 잠깐만 기다려 달라."
WORKING_MESSAGE = "확인하고 있다. 잠깐만 기다려 달라."
