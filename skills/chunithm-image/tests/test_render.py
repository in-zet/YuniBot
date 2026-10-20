from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image

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


def test_card_page_limit_is_50_and_list_is_30() -> None:
    assert render.CARD_PAGE_LIMIT == 50
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
    assert width >= 3 * render._CARD_W
    assert height > render._JACKET


def test_render_card_splits_at_50() -> None:
    files = render.render_pages(_chart_table(51), layout="card", jackets={})

    assert len(files) == 2
    assert [name for name, _ in files] == sorted({name for name, _ in files})
    assert all(name.endswith(".png") for name, _ in files)


def test_render_list_splits_at_30() -> None:
    assert len(render.render_pages(_chart_table(30), layout="list", jackets={})) == 1
    assert len(render.render_pages(_chart_table(31), layout="list", jackets={})) == 2


def test_card_jacket_is_not_enlarged_beyond_original() -> None:
    assert render._JACKET <= 190


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
    assert details == ["레이팅 16.52"]
    assert steps == "내 베스트"


def test_stale_and_page_notes_do_not_crash_and_multi_page_names_are_numbered() -> None:
    files = render.render_pages(_chart_table(60), layout="card", jackets={}, stale=True)
    assert [name.rsplit("_", 1)[-1] for name, _ in files] == ["1.png", "2.png"]


@pytest.mark.parametrize("layout", ["card", "list"])
def test_table_without_score_columns_renders(layout: str) -> None:
    files = render.render_pages(_chart_table(5, scores=False), layout=layout, jackets={})
    assert len(files) == 1
