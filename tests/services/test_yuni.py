import json

import pytest

from core.events import Section
from core.llm import GenerationTurn, LlmContent, LlmPart, UsageInfo
from core.threads import ThreadTranscript
from services.common.yuni import YuniService
from tests.support.fake_llm import FakeLlmAdapter


async def test_line_picks_highest_stage_regardless_of_order() -> None:
    service = YuniService()
    sections = [Section(stage="good"), Section(stage="great"), Section(stage="rest")]
    assert await service.line(sections) == await service.line([Section(stage="great")])


async def test_line_close_and_rest_differ() -> None:
    service = YuniService()
    assert await service.line([Section(stage="close")]) != await service.line([Section(stage="rest")])


async def test_line_default_when_no_section_has_a_stage() -> None:
    service = YuniService()
    assert await service.line([Section(data={"x": 1})]) == await service.line([])


def test_line_is_async_so_the_llm_swap_keeps_the_signature() -> None:
    """11단계 LLM 구현은 비동기여야 한다 — 지금부터 async로 둬 부르는 쪽이 안 바뀐다."""
    import inspect

    assert inspect.iscoroutinefunction(YuniService.line)


async def test_summarize_conversation_is_a_placeholder() -> None:
    service = YuniService()
    transcript = ThreadTranscript(thread_id="t1", player_id="p1", messages=[], closed_reason="explicit")

    summary = await service.summarize_conversation(transcript)

    assert summary == ""


# ---- 11단계: LLM으로 만드는 한마디


def _turn(text: str | None, *, input_tokens: int = 100, output_tokens: int = 20) -> GenerationTurn:
    return GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text=text)] if text else []), text=text,
        usage=UsageInfo(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class _FailingLlm:
    async def generate(self, **_kwargs: object) -> GenerationTurn:
        raise RuntimeError("503")


class _RecordingUsage:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[dict[str, object]] = []
        self._fail = fail

    async def record(self, **kwargs: object) -> float:
        if self._fail:
            raise RuntimeError("db")
        self.records.append(kwargs)
        return 0.0


def _service(llm: object, *, usage: _RecordingUsage | None = None) -> YuniService:
    return YuniService(
        llm=llm,  # pyright: ignore[reportArgumentType]
        model="cheap-model", system_prompt="규칙과 말투", usage=usage,  # pyright: ignore[reportArgumentType]
    )


async def test_line_uses_llm_text_and_sends_persona_and_section_json() -> None:
    llm = FakeLlmAdapter([_turn("대단해! 레이팅이 올랐어!")])
    service = _service(llm)

    line = await service.line([Section(stage="great", data={"rating": 17.25}, hint="신기록")])

    assert line == "대단해! 레이팅이 올랐어!"
    model, system, contents, tools = llm.calls[0]
    assert model == "cheap-model"
    assert system.startswith("규칙과 말투")
    assert tools == []
    sent = json.loads(contents[0].parts[0].text or "")
    assert sent == {"stage": "great", "sections": [{"data": {"rating": 17.25}, "hint": "신기록"}]}


async def test_line_without_content_does_not_call_llm() -> None:
    llm = FakeLlmAdapter([])
    service = _service(llm)

    assert await service.line([]) == await YuniService().line([])
    assert llm.calls == []


async def test_line_falls_back_to_fixed_text_when_llm_fails() -> None:
    service = _service(_FailingLlm())

    assert await service.line([Section(stage="close")]) == await YuniService().line([Section(stage="close")])


async def test_line_rejects_number_that_is_not_in_data() -> None:
    """유니의 말 속 숫자는 도구 결과 data와 대조한다(CLAUDE.md 하드 제약)."""
    service = _service(FakeLlmAdapter([_turn("레이팅이 17.30이나 올랐어!")]))

    line = await service.line([Section(stage="great", data={"rating": 17.25})])

    assert line == await YuniService().line([Section(stage="great")])


async def test_line_accepts_number_found_in_data() -> None:
    service = _service(FakeLlmAdapter([_turn("레이팅 17.25 달성!")]))

    assert await service.line([Section(stage="great", data={"rating": 17.25})]) == "레이팅 17.25 달성!"


@pytest.mark.parametrize("text", [None, "", "   ", "가" * 201])
async def test_line_falls_back_when_text_is_empty_or_too_long(text: str | None) -> None:
    service = _service(FakeLlmAdapter([_turn(text)]))

    assert await service.line([Section(stage="rest")]) == await YuniService().line([Section(stage="rest")])


async def test_line_records_usage_when_player_is_given() -> None:
    usage = _RecordingUsage()
    service = _service(
        FakeLlmAdapter([_turn("좋아!", input_tokens=50, output_tokens=7), _turn("좋아!")]), usage=usage
    )

    _ = await service.line([Section(stage="good")], player_id="p1", guild_id="g1")
    _ = await service.line([Section(stage="good")], player_id="p1")  # DM

    assert usage.records[0] == {
        "guild_id": "g1", "player_id": "p1", "model": "cheap-model", "kind": "yuni_line",
        "input_tokens": 50, "output_tokens": 7,
    }
    assert usage.records[1]["guild_id"] == "dm"


async def test_line_does_not_record_usage_without_player_and_survives_usage_failure() -> None:
    usage = _RecordingUsage()
    service = _service(FakeLlmAdapter([_turn("좋아!")]), usage=usage)
    assert await service.line([Section(stage="good")]) == "좋아!"
    assert usage.records == []

    failing = _service(FakeLlmAdapter([_turn("좋아!")]), usage=_RecordingUsage(fail=True))
    assert await failing.line([Section(stage="good")], player_id="p1") == "좋아!"
