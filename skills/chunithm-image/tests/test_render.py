from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image, ImageDraw

from core.tables import EMPTY_CELL, Cell, TableBuilder, WorkTable, cell
from services.chunithm import table_columns as tc

_RENDER_PY = Path(__file__).parent.parent / "render.py"


def _load_render() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_image_render_under_test", _RENDER_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


render = _load_render()


def _is_white(pixel: object) -> bool:
    return isinstance(pixel, tuple) and min(pixel) >= 250


def _png(color: str = "red", size: tuple[int, int] = (190, 190)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _chart_table(count: int, *, groups: tuple[int, ...] | None = None, scores: bool = True) -> WorkTable:
    names = [tc.SONG, tc.DIFFICULTY, tc.CONST]
    if scores:
        names += [tc.SCORE, tc.RANK, tc.LAMP, tc.RATING]
    builder = TableBuilder(subject=None, unit="채보")
    tc.add_columns(builder, *names)
    builder.step("테스트 표")
    sizes = groups or (count,)
    index = 0
    for group_index, size in enumerate(sizes):
        if groups:
            builder.group(f"묶음{group_index + 1}", summary={"평균": "15.00"})
        for _ in range(size):
            cells: dict[str, Cell] = {
                tc.SONG: tc.song_cell(f"곡 {index}"),
                tc.DIFFICULTY: tc.difficulty_cell("MAS"),
                tc.CONST: tc.const_cell(15.0),
            }
            if scores:
                cells[tc.SCORE] = tc.score_cell(1_007_500)
                cells[tc.RANK] = tc.rank_cell("SSS", {"SSS": 1_007_500})
                cells[tc.LAMP] = tc.lamp_cell("ALL JUSTICE")
                cells[tc.RATING] = cell(16.0, "16.00")
            builder.row(tc.chart_ref(f"곡 {index}", "MAS"), cells)
            index += 1
    return builder.build()


def _size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        return image.size


def _numbers(table: WorkTable, pages: list[list[Any]]) -> list[list[int]]:
    return [[number for segment in page for number, _ in segment.rows] for page in pages]


def test_split_pages_cuts_at_group_boundaries() -> None:
    table = _chart_table(0, groups=(30, 30, 10))

    pages = render.split_pages(table, 50)

    assert [[(s.title, len(s.rows)) for s in page] for page in pages] == [
        [("묶음1", 30)],
        [("묶음2", 30), ("묶음3", 10)],
    ]


def test_split_pages_cuts_inside_group_larger_than_limit() -> None:
    table = _chart_table(120)

    pages = render.split_pages(table, 50)

    assert [sum(len(s.rows) for s in page) for page in pages] == [50, 50, 20]
    assert [page[0].continued for page in pages] == [False, True, True]
    assert _numbers(table, pages) == [list(range(1, 51)), list(range(51, 101)), list(range(101, 121))]


def test_split_pages_numbers_match_table_numbers() -> None:
    table = _chart_table(0, groups=(5, 5))
    pages = render.split_pages(table, 5)
    assert _numbers(table, pages) == [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]]
    assert [n for n, _ in table.numbered_rows()] == list(range(1, 11))


def test_card_page_is_8_lines_and_list_is_30_rows() -> None:
    assert render.CARD_PAGE_ROWS == 8
    assert render.LIST_PAGE_LIMIT == 30


def test_render_small_card_table_gives_one_png() -> None:
    table = _chart_table(4)
    jackets = {f"곡 {i}": _png() for i in range(4)}

    files = render.render_pages(table, layout="card", jackets=jackets)

    assert len(files) == 1
    name, data = files[0]
    assert name.endswith(".png")
    assert data.startswith(b"\x89PNG")
    width, height = _size(data)
    assert width >= 3 * render._CARD_W * render.CARD_OUTPUT_SCALE
    assert height > render._JACKET


def test_card_grid_is_at_most_ten_wide_and_output_is_scaled_down() -> None:
    table = _chart_table(80)
    jackets = {f"곡 {i}": _png() for i in range(80)}

    (_, data), = render.render_pages(table, layout="card", jackets=jackets)

    width, height = _size(data)
    full_width = 2 * render._MARGIN + 10 * render._CARD_W + 9 * render._GAP
    assert width == round(full_width * render.CARD_OUTPUT_SCALE)
    assert height < 2600


def test_list_output_is_not_scaled() -> None:
    (_, data), = render.render_pages(_chart_table(3), layout="list", jackets={})

    assert _size(data)[0] == render._LIST_W


def test_split_pages_counts_grid_lines_not_cards() -> None:
    table = _chart_table(0, groups=(10, 3))  # 8열: 묶음1 2줄, 묶음2 1줄

    assert [[len(s.rows) for s in page] for page in render.split_pages(table, 2, 8)] == [[10], [3]]
    assert [[len(s.rows) for s in page] for page in render.split_pages(table, 3, 8)] == [[10, 3]]


def test_split_pages_cuts_big_group_by_lines() -> None:
    pages = render.split_pages(_chart_table(200), 8, 10)

    assert [sum(len(s.rows) for s in page) for page in pages] == [80, 80, 40]


def test_render_card_splits_at_8_lines() -> None:
    assert len(render.render_pages(_chart_table(80), layout="card", jackets={})) == 1
    files = render.render_pages(_chart_table(81), layout="card", jackets={})

    assert len(files) == 2
    assert [name for name, _ in files] == sorted({name for name, _ in files})
    assert all(name.endswith(".png") for name, _ in files)


def test_render_list_splits_at_30() -> None:
    assert len(render.render_pages(_chart_table(30), layout="list", jackets={})) == 1
    assert len(render.render_pages(_chart_table(31), layout="list", jackets={})) == 2


def test_card_jacket_fills_the_card_width() -> None:
    assert render._JACKET == render._CARD_W


def test_missing_and_broken_jackets_draw_placeholders() -> None:
    table = _chart_table(3)
    jackets = {"곡 0": None, "곡 1": b"not an image"}  # 곡 2는 아예 없다

    for layout in ("card", "list"):
        files = render.render_pages(table, layout=layout, jackets=jackets)
        assert len(files) == 1


def test_song_level_rows_draw_title_only() -> None:
    builder = TableBuilder(subject=None, unit="곡")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY, tc.ARTIST, tc.VERSION)
    builder.row(
        tc.song_ref("곡 A"),
        {tc.SONG: tc.song_cell("곡 A"), tc.DIFFICULTY: EMPTY_CELL,
         tc.ARTIST: tc.text_cell("누군가"), tc.VERSION: tc.text_cell("LUMINOUS")},
    )
    table = builder.build()

    for layout in ("card", "list"):
        files = render.render_pages(table, layout=layout, jackets={"곡 A": _png()})
        assert len(files) == 1


def test_header_with_and_without_profile_differs_in_height() -> None:
    table = _chart_table(3)
    plain = render.render_pages(table, layout="card", jackets={})[0][1]
    profile = render.HeaderProfile(nickname="유니", level=87, character=_png("blue", (70, 70)), frame=_png("green", (70, 70)))
    with_header = render.render_pages(table, layout="card", jackets={}, profile=profile)[0][1]

    assert _size(with_header)[1] > _size(plain)[1]


def test_header_lines_pull_nickname_and_level_up_and_keep_other_summary() -> None:
    builder = TableBuilder(subject="p1", unit="채보")
    tc.add_columns(builder, tc.SONG)
    builder.summary({"닉네임": "유니", "레벨": "80", "레이팅": "16.52"})
    builder.step("내 베스트")
    builder.row(tc.song_ref("곡"), {tc.SONG: tc.song_cell("곡")})
    table = builder.build()

    title, details, steps = render._header_lines(table, render.HeaderProfile(level=87))

    assert title == "유니 Lv.87"  # 저장된 프로필 레벨이 표 요약의 레벨보다 앞선다
    assert details == []  # 최종 레이팅은 머리말 오른쪽 끝에 따로 크게 그린다
    assert render.header_rating(table) == "16.52"
    assert steps == "내 베스트"


def test_header_lines_put_current_version_first_only_for_rating_table() -> None:
    builder = TableBuilder(subject="p1", unit="채보")
    tc.add_columns(builder, tc.SONG)
    builder.summary({"닉네임": "유니", "레이팅": "16.52"})
    builder.row(tc.song_ref("곡"), {tc.SONG: tc.song_cell("곡")})
    rating_table = builder.build()
    other = _chart_table(2)
    profile = render.HeaderProfile(version="X-VERSE-X")

    assert render._header_lines(rating_table, profile)[1] == ["X-VERSE-X"]
    assert render._header_lines(other, profile)[1] == []


def test_final_rating_is_drawn_in_header_and_makes_it_taller() -> None:
    def table(summary: dict[str, str]) -> WorkTable:
        builder = TableBuilder(subject=None, unit="채보")
        tc.add_columns(builder, tc.SONG)
        builder.summary(summary)
        builder.row(tc.song_ref("곡"), {tc.SONG: tc.song_cell("곡")})
        return builder.build()

    plain = render.render_pages(table({"닉네임": "유니"}), layout="card", jackets={})[0][1]
    rated = render.render_pages(table({"닉네임": "유니", "레이팅": "16.52"}), layout="card", jackets={})[0][1]

    assert _size(rated)[1] > _size(plain)[1]


def test_font_scale_is_one_and_a_half() -> None:
    assert render.FONT_SCALE == 1.5
    assert render._fs(14) == 21


def test_card_gives_title_gradient_that_fades_into_card_background() -> None:
    image = _card_png(difficulty="MAS")
    top = _card_top(image)
    x = render._MARGIN + render._CARD_W - 20  # 곡명 글자가 닿지 않는 오른쪽 가장자리
    assert _is_white(image.getpixel((x, top + 40))), "흰 재킷이 보여야 한다"
    bottom = image.getpixel((x, top + render._JACKET - 2))
    panel = render._tinted_panel("MAS")
    assert isinstance(bottom, tuple) and all(abs(c - p) <= 24 for c, p in zip(bottom, panel, strict=True))


def test_card_has_two_info_lines_and_height_follows() -> None:
    both = render._card_lines(_chart_table(1))
    assert (both.const_rating, both.score_rank) == (True, True)
    assert both.height == render._INFO_TOP + 2 * render._INFO_LINE + render._CARD_BOTTOM_PAD
    names_only = render._card_lines(_chart_table(1, scores=False))
    assert (names_only.const_rating, names_only.score_rank) == (True, False)


def test_card_value_size_is_largest_that_fits_or_the_minimum() -> None:
    def fits(size: int) -> bool:
        bold, plain = render._font(size, True), render._font(size, False)
        return max(
            render._width(bold, "1,010,000") + render._width(bold, "SSS+") + 10,
            render._width(plain, "상수 15.5") + render._width(bold, "17.00") + 10,
        ) <= render._CARD_W - 2 * render._CARD_PAD

    size = render._card_value_size()

    assert size in (*render._CARD_VALUE_SIZES, render._CARD_VALUE_MIN_SIZE)
    assert fits(size) or size == render._CARD_VALUE_MIN_SIZE
    assert all(not fits(bigger) for bigger in render._CARD_VALUE_SIZES if bigger > size)


def test_card_value_texts_share_one_font_size(monkeypatch: pytest.MonkeyPatch) -> None:
    drawn: dict[str, float] = {}
    original = ImageDraw.ImageDraw.text

    def spy(self: ImageDraw.ImageDraw, xy: Any, text: Any, *args: Any, **kwargs: Any) -> Any:
        font = kwargs.get("font")
        if isinstance(text, str) and font is not None:
            drawn[text] = getattr(font, "size", 0)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    render.render_pages(_chart_table(1), layout="card", jackets={})

    values = [drawn["상수 15.0"], drawn["1,007,500"], drawn["SSS"], drawn["16.00"]]
    assert len(set(values)) == 1


def test_stale_and_page_notes_do_not_crash_and_multi_page_names_are_numbered() -> None:
    files = render.render_pages(_chart_table(100), layout="card", jackets={}, stale=True)
    assert [name.rsplit("_", 1)[-1] for name, _ in files] == ["1.png", "2.png"]


@pytest.mark.parametrize("layout", ["card", "list"])
def test_table_without_score_columns_renders(layout: str) -> None:
    files = render.render_pages(_chart_table(5, scores=False), layout=layout, jackets={})
    assert len(files) == 1


def test_local_number_restarts_per_group_and_keeps_table_number() -> None:
    table = _chart_table(0, groups=(3, 2))

    pages = render.split_pages(table, 50)

    segments = [segment for page in pages for segment in page]
    assert [segment.first_number for segment in segments] == [1, 4]
    assert [[segment.local_number(number) for number, _ in segment.rows] for segment in segments] == [[1, 2, 3], [1, 2]]
    assert [[number for number, _ in segment.rows] for segment in segments] == [[1, 2, 3], [4, 5]]


def test_local_number_continues_in_split_chunk_of_one_group() -> None:
    table = _chart_table(0, groups=(5, 4))

    segments = [segment for page in render.split_pages(table, 3) for segment in page]

    second_group = [segment for segment in segments if segment.title == "묶음2"]
    assert [[segment.local_number(number) for number, _ in segment.rows] for segment in second_group] == [[1, 2, 3], [4]]
    assert all(segment.first_number == 6 for segment in second_group)


def test_number_badge_is_wider_when_table_number_differs() -> None:
    image = Image.new("RGBA", (300, 60))
    draw = render.ImageDraw.Draw(image)

    same = render._number_badge(draw, (0, 0), 3, 3)
    differs = render._number_badge(draw, (0, 0), 3, 33)

    assert differs > same
    assert render._number_badge(draw, (0, 0), 3) == same


@pytest.mark.parametrize("layout", ["card", "list"])
def test_grouped_table_renders_with_local_numbers(layout: str) -> None:
    files = render.render_pages(_chart_table(0, groups=(3, 2)), layout=layout, jackets={})
    assert len(files) == 1


def _rgb(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    pixel = image.convert("RGB").getpixel((x, y))
    assert isinstance(pixel, tuple)
    return (pixel[0], pixel[1], pixel[2])


def test_icon_draws_the_character_on_top_of_the_opaque_frame() -> None:
    canvas = Image.new("RGBA", (120, 120), (0, 0, 0, 255))

    render._draw_icon(canvas, (0, 0, 120, 120), _png("red"), _png("green"))  # pyright: ignore[reportPrivateUsage]

    assert _rgb(canvas, 60, 60) == (255, 0, 0)  # 가운데는 캐릭터
    assert _rgb(canvas, 3, 60) == (0, 128, 0)  # 가장자리는 틀


def test_icon_with_frame_only_or_character_only_or_failed_character() -> None:
    frame_only = Image.new("RGBA", (120, 120), (0, 0, 0, 255))
    render._draw_icon(frame_only, (0, 0, 120, 120), None, _png("green"))  # pyright: ignore[reportPrivateUsage]
    assert _rgb(frame_only, 60, 60) == (0, 128, 0)  # 캐릭터를 못 받아도 틀 위에 빈 사각형을 얹지 않는다
    character_only = Image.new("RGBA", (120, 120), (0, 0, 0, 255))
    render._draw_icon(character_only, (0, 0, 120, 120), _png("red"), None)  # pyright: ignore[reportPrivateUsage]
    assert _rgb(character_only, 3, 60) == (255, 0, 0)  # 틀이 없으면 상자를 가득 채운다


def _card_png(difficulty: str = "MAS", lamp: str | None = None, rank: str = "SSS") -> Image.Image:
    builder = TableBuilder(subject=None, unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY, tc.CONST, tc.SCORE, tc.RANK, tc.LAMP, tc.RATING)
    builder.step("테스트 표")
    builder.row(
        tc.chart_ref("곡 0", difficulty),
        {
            tc.SONG: tc.song_cell("곡 0"),
            tc.DIFFICULTY: tc.difficulty_cell(difficulty),
            tc.CONST: tc.const_cell(15.0),
            tc.SCORE: tc.score_cell(1_007_500),
            tc.RANK: tc.rank_cell(rank, {rank: 1_007_500}),
            tc.LAMP: tc.lamp_cell(lamp),
            tc.RATING: cell(16.0, "16.00"),
        },
    )
    (_, data), = render.render_pages(builder.build(), layout="card", jackets={"곡 0": _png("white")})
    return Image.open(io.BytesIO(data)).convert("RGB")


def _card_top(image: Image.Image) -> int:
    """카드 위쪽 가장자리 세로줄에서 처음으로 배경과 달라지는 y(= 카드 맨 위, 외곽선)."""
    x = render._MARGIN + render._CARD_W - 20
    return next(y for y in range(image.height) if image.getpixel((x, y)) != render._BG)


def test_every_rank_has_its_own_text_color() -> None:
    ranks = ["D", "C", "B", "BB", "BBB", "A", "AA", "AAA", "S", "S+", "SS", "SS+", "SSS", "SSS+"]
    colors = [render._rank_color(rank) for rank in ranks]

    assert len(set(colors)) == len(ranks)
    assert render._rank_color("???") == render._MUTED


def test_card_background_is_colored_by_difficulty() -> None:
    colors = {}
    for code in ("BAS", "ADV", "EXP", "MAS", "ULT"):
        image = _card_png(code)
        # 사선 왼쪽의 빈 바탕: 외곽선 안쪽, 글자 여백 바깥
        colors[code] = image.getpixel((render._MARGIN + 7, _card_top(image) + render._JACKET + 40))
        assert colors[code] == render._tinted_panel(code)

    assert len(set(colors.values())) == 5
    assert render._tinted_panel(None) == render._PANEL


def test_card_has_diagonal_pattern_lighter_on_right_of_the_line() -> None:
    image = _card_png()
    y = _card_top(image) + render._JACKET + 40
    left = image.getpixel((render._MARGIN + 7, y))
    right = image.getpixel((render._MARGIN + render._CARD_W - 8, y))

    assert isinstance(left, tuple) and isinstance(right, tuple)
    assert sum(right) > sum(left)


@pytest.mark.parametrize("lamp,label", [("FULL COMBO", "FC"), ("ALL JUSTICE", "AJ"), ("AJC", "AJC")])
def test_lamp_adds_thin_border_with_lamp_color(lamp: str, label: str) -> None:
    with_lamp = _card_png(lamp=lamp)
    without = _card_png(lamp=None)
    # 카드 왼쪽 가장자리 한가운데(재킷 패딩 바깥)
    def edge(image: Image.Image) -> object:
        return image.getpixel((render._MARGIN + 1, _card_top(image) + 100))

    assert edge(with_lamp) == render._LAMP_COLORS[label]
    assert edge(without) != render._LAMP_COLORS[label]


def test_title_gradient_reaches_higher_up_the_jacket() -> None:
    image = _card_png()
    x = render._MARGIN + render._CARD_W - 20
    top = _card_top(image)
    inside = image.getpixel((x, top + render._JACKET - 50))  # 곡명 한 줄 + 22였던 예전 범위(약 48) 밖
    outside = image.getpixel((x, top + render._JACKET - render._TITLE_LINE - render._GRADIENT_EXTRA - 10))

    assert isinstance(inside, tuple) and isinstance(outside, tuple)
    assert min(inside) < 250
    assert min(outside) >= 250  # 그라데이션 위쪽은 재킷 그대로


def test_english_titles_wrap_at_spaces() -> None:
    font = render._font(14, True)
    width = render._width(font, "Ultra Sonic H")  # "Hearts" 중간에서 넘친다

    assert render._wrap_two(font, "Ultra Sonic Hearts", width) == ["Ultra Sonic", "Hearts"]
    assert render._wrap_two(font, "ガチャガチャきゅ〜と・ふぃぎゅ@メイト", render._width(font, "ガチャガチャ"))[0] == "ガチャガチャ"
    assert render._wrap_two(font, "Supercalifragilistic", render._width(font, "Supercal"))[0] == "Supercal"


def test_bundled_font_is_used_and_covers_korean_and_japanese() -> None:
    assert render._BUNDLED_FONT.is_file()
    font = render._font(14, True)
    path = getattr(font, "path", "")

    assert str(path).endswith("PretendardJP-Bold.otf")
    for text in ("상수", "極楽浄土", "ガチャ"):
        assert render._width(font, text) > 0


@pytest.mark.parametrize(
    "rating,name",
    [("0.00", "초록"), ("3.99", "초록"), ("4.00", "주황"), ("6.99", "주황"), ("7.00", "빨강"), ("10.00", "자주"),
     ("11.99", "자주"), ("12.00", "동"), ("13.24", "동"), ("13.25", "은"), ("14.49", "은"), ("14.50", "금"),
     ("15.24", "금"), ("15.25", "백금"), ("15.99", "백금"), ("16.00", "무지개"), ("16.99", "무지개"),
     ("17.00", "무지개·극"), ("17.0024", "무지개·극")],
)
def test_rating_tier_names_follow_chunithm_table(rating: str, name: str) -> None:
    tier = render.rating_tier(rating)

    assert tier is not None and tier[0] == name
    assert (tier[1] is None) == name.startswith("무지개")


def test_rating_tier_is_none_for_non_numbers() -> None:
    assert render.rating_tier("-") is None
    assert render.rating_tier("") is None


def _rating_header(rating: str) -> Image.Image:
    builder = TableBuilder(subject=None, unit="채보")
    tc.add_columns(builder, tc.SONG)
    builder.summary({"레이팅": rating})
    builder.row(tc.chart_ref("곡 0", "MAS"), {tc.SONG: tc.song_cell("곡 0")})
    (_, data), = render.render_pages(builder.build(), layout="list", jackets={})
    return Image.open(io.BytesIO(data)).convert("RGB")


def _colors_in_rating_area(image: Image.Image) -> set[tuple[int, int, int]]:
    area = image.crop((image.width - render._MARGIN - 200, 30, image.width - render._MARGIN, 100))
    colors: set[tuple[int, int, int]] = set()
    for x in range(area.width):
        for y in range(area.height):
            pixel = area.getpixel((x, y))
            if isinstance(pixel, tuple) and len(pixel) == 3 and max(pixel) > 150:
                colors.add((pixel[0], pixel[1], pixel[2]))
    return colors


def test_solid_tier_uses_one_hue_but_rainbow_uses_many() -> None:
    gold = _colors_in_rating_area(_rating_header("14.60"))
    rainbow = _colors_in_rating_area(_rating_header("16.50"))

    assert len(rainbow) > 3 * len(gold)
    assert any(pixel[0] > 200 for pixel in rainbow) and any(pixel[2] > 200 for pixel in rainbow)


def test_extreme_rainbow_differs_from_plain_rainbow() -> None:
    assert _colors_in_rating_area(_rating_header("17.00")) != _colors_in_rating_area(_rating_header("16.99"))
