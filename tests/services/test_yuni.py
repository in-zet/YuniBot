from core.events import Section
from core.threads import ThreadTranscript
from services.common.yuni import YuniService


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
