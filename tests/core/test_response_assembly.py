import dataclasses

from core.agent import AgentTurnResult
from core.response_assembly import assemble_response, extract_numbers, flatten_numbers
from core.tables import RowRef, TableBuilder, cell
from core.tool_types import Display


def test_extract_numbers_reads_integers_and_decimals_with_commas() -> None:
    assert extract_numbers("1,007,500점, 3.20어택") == {1007500.0, 3.20}


def test_extract_numbers_ignores_small_plain_integers() -> None:
    # 순위 번호·프레임 크기·날짜 같은 작은 정수는 대조 대상이 아니다. 소수와 큰 수는 그대로다.
    assert extract_numbers("1. 곡 A 2. 곡 B, 베스트 30, 신곡 20, 10월 5일") == set()
    assert extract_numbers("레이팅 16.9726, 51곡, 점수 1,007,500") == {16.9726, 51.0, 1007500.0}


def test_extract_numbers_ignores_zero_and_one() -> None:
    assert extract_numbers("한 번에 0개 남음, 1등") == set()


def test_flatten_numbers_walks_nested_dicts_and_lists() -> None:
    data = [{"borders": {"SSS+": "12.5"}, "scores": [1007500, 3.2]}]
    assert flatten_numbers(data) == {12.5, 1007500.0, 3.2}


def test_assemble_response_passes_when_all_numbers_in_data() -> None:
    result = AgentTurnResult(
        text="보더는 12어택이다.",
        collected_data=[{"borders": {"SSS+": 12}}],
        optional_displays={"d1": Display(content="표")},
    )
    assembled = assemble_response(result)
    assert assembled.numeric_violation is False
    assert assembled.displays == []  # optional이라 모델이 [[show:..]]를 안 쓰면 안 붙는다


def test_assemble_response_violation_does_not_force_optional_displays() -> None:
    # 실기 2026-10-18: 목표 하나만 고쳤는데 id 해석용으로 불렀을 뿐인 list_goals까지
    # 위반이라는 이유로 통째로 새어 나왔다 — 위반은 경고 로그로만 남고, 표시물은 여전히
    # [[show:..]]로 모델이 직접 고른 것만 붙는다.
    result = AgentTurnResult(
        text="보더는 15.5어택이다.",  # 15.5는 data에 없는 숫자
        collected_data=[{"borders": {"SSS+": 12}}],
        optional_displays={"d1": Display(content="표1"), "d2": Display(content="표2")},
    )
    assembled = assemble_response(result)
    assert assembled.numeric_violation is True
    assert assembled.displays == []


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


def test_assemble_response_required_displays_survive_violation_without_show_tag() -> None:
    # 숫자 위반이 나도 required 표시물은 그대로 붙는다. optional 표시물은 [[show:..]]가
    # 없으면 위반 여부와 무관하게 안 붙는다.
    result = AgentTurnResult(
        text="15.5어택이다.",  # data에 없는 숫자 — 위반
        collected_data=[{"borders": {"SSS+": 12}}],
        required_displays=[Display(content="필수 표")],
        optional_displays={"d1": Display(content="표1")},
    )
    assembled = assemble_response(result)
    assert assembled.numeric_violation is True
    assert assembled.displays == [Display(content="필수 표")]


def test_required_displays_stay_even_with_no_table_tag() -> None:
    # [[no_table]]은 작업 표만 뺀다. 도구가 직접 낸 required 표시물(출처 등)은 그대로 붙는다.
    result = AgentTurnResult(
        text="17 넘는 곡은 세 곡이야. [[no_table]]",
        collected_data=[{"rating": 17.27}],
        required_displays=[Display(content="출처")],
    )
    assembled = assemble_response(result)
    assert "[[no_table]]" not in assembled.text
    assert assembled.displays == [Display(content="출처")]


# ---- 작업 표 글자 출력 ----


def _table_result(*, rows: int = 2, **kwargs: object) -> AgentTurnResult:
    builder = TableBuilder(unit="채보").column("song", "곡", bare=True).step("15 이상")
    for i in range(rows):
        builder.row(RowRef("k", str(i)), {"song": cell(f"S{i}")})
    table = dataclasses.replace(builder.build(), id="t4")
    return AgentTurnResult(text="9곡이야", last_table_id="t4", last_table=table, turn_table_ids=["t4"], **kwargs)  # pyright: ignore[reportArgumentType]


def test_last_table_is_drawn_as_a_required_display() -> None:
    assembled = assemble_response(_table_result())

    assert len(assembled.displays) == 1
    display = assembled.displays[0]
    assert display.content == "15 이상\n1. S0\n2. S1"
    assert display.files == []


def test_table_goes_after_other_required_displays() -> None:
    result = _table_result()
    result.required_displays.append(Display(content="출처"))

    assert [d.content for d in assemble_response(result).displays] == ["출처", "15 이상\n1. S0\n2. S1"]


def test_no_table_tag_skips_the_table() -> None:
    result = _table_result()
    result.text = "그냥 말만 할게 [[no_table]]"

    assembled = assemble_response(result)
    assert assembled.displays == []
    assert "[[no_table]]" not in assembled.text


def test_a_table_already_drawn_as_a_picture_is_not_drawn_as_text() -> None:
    assert assemble_response(_table_result(drawn_table_ids=["t4"])).displays == []


def test_a_picture_of_an_earlier_table_does_not_hide_the_last_one() -> None:
    assert len(assemble_response(_table_result(drawn_table_ids=["t1"])).displays) == 1


def test_stale_table_header_and_long_table_file() -> None:
    result = _table_result(rows=11, last_table_stale=True)
    display = assemble_response(result).displays[0]

    assert (display.content or "").startswith("(동기화 전 값)\n15 이상\n11채보")
    assert [f.name for f in display.files] == ["15이상.txt"]
    assert b"11. S10" in display.files[0].data


def test_no_table_means_no_table_display() -> None:
    assert assemble_response(AgentTurnResult(text="안녕")).displays == []
