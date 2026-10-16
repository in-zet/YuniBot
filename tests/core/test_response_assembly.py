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


def test_assemble_response_always_includes_required_displays_without_violation() -> None:
    # C1 회귀: display_mode="required" 표시물(예: chunithm-song_info, admin_skills)은
    # 모델이 [[show:..]]를 안 써도, 숫자 위반이 없어도 항상 붙어야 한다 — optional 표시물과
    # 달리 "강제 표시 모드"는 00-core.md의 기본값이고 응답 조립을 거치지 않는다.
    result = AgentTurnResult(
        text="곡 정보를 봤다.",
        collected_data=[{"song": "크로노"}],
        required_displays=[Display(content="곡 정보 표")],
    )
    assembled = assemble_response(result)
    assert assembled.numeric_violation is False
    assert assembled.displays == [Display(content="곡 정보 표")]


def test_assemble_response_required_displays_come_first_and_survive_violation() -> None:
    # 숫자 위반이 나도 required 표시물은 그대로 앞에 오고, optional 표시물은 기존 규칙대로
    # (위반이면 전부) 뒤에 따라온다.
    result = AgentTurnResult(
        text="15어택이다.",  # data에 없는 숫자 — 위반
        collected_data=[{"borders": {"SSS+": 12}}],
        required_displays=[Display(content="필수 표")],
        optional_displays={"d1": Display(content="표1")},
    )
    assembled = assemble_response(result)
    assert assembled.numeric_violation is True
    assert assembled.displays == [Display(content="필수 표"), Display(content="표1")]
