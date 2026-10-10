from core.events import Section
from core.threads import ThreadTranscript
from services.common.yuni import YuniService


def test_line_picks_highest_stage_regardless_of_order() -> None:
    service = YuniService()
    sections = [Section(stage="good"), Section(stage="great"), Section(stage="rest")]
    assert service.line(sections) == service.line([Section(stage="great")])


def test_line_close_and_rest_differ() -> None:
    service = YuniService()
    assert service.line([Section(stage="close")]) != service.line([Section(stage="rest")])


def test_line_default_when_no_section_has_a_stage() -> None:
    service = YuniService()
    assert service.line([Section(data={"x": 1})]) == service.line([])


async def test_summarize_conversation_is_a_placeholder() -> None:
    service = YuniService()
    transcript = ThreadTranscript(thread_id="t1", player_id="p1", messages=[], closed_reason="explicit")

    summary = await service.summarize_conversation(transcript)

    assert summary == ""
