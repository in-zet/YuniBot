from core.agent import AgentTurnResult
from core.response_assembly import assemble_response, extract_numbers, flatten_numbers
from core.tool_types import Display


def test_extract_numbers_reads_integers_and_decimals_with_commas() -> None:
    assert extract_numbers("1,007,500점, 3.20어택") == {1007500.0, 3.20}


def test_extract_numbers_ignores_zero_and_one() -> None:
    assert extract_numbers("한 번에 0개 남음, 1등") == set()


def test_flatten_numbers_walks_nested_dicts_and_lists() -> None:
    data = [{"borders": {"SSS+": "12"}, "scores": [1007500, 3.2]}]
    assert flatten_numbers(data) == {12.0, 1007500.0, 3.2}


def test_assemble_response_passes_when_all_numbers_in_data() -> None:
    result = AgentTurnResult(
        text="보더는 12어택이다.",
        collected_data=[{"borders": {"SSS+": 12}}],
        optional_displays={"d1": Display(content="표")},
    )
    assembled = assemble_response(result)
    assert assembled.numeric_violation is False
    assert assembled.displays == []  # optional이라 모델이 [[show:..]]를 안 쓰면 안 붙는다


def test_assemble_response_forces_all_optional_displays_on_violation() -> None:
    result = AgentTurnResult(
        text="보더는 15어택이다.",  # 15는 data에 없는 숫자
        collected_data=[{"borders": {"SSS+": 12}}],
        optional_displays={"d1": Display(content="표1"), "d2": Display(content="표2")},
    )
    assembled = assemble_response(result)
    assert assembled.numeric_violation is True
    assert len(assembled.displays) == 2


def test_assemble_response_shows_display_requested_via_show_tag() -> None:
    result = AgentTurnResult(
        text="자세히 보자. [[show:d1]]",
        collected_data=[{"borders": {"SSS+": 12}}],
        optional_displays={"d1": Display(content="표1")},
    )
    assembled = assemble_response(result)
    assert "[[show:d1]]" not in assembled.text
    assert assembled.displays == [Display(content="표1")]
