# skills/chunithm-image/render.py
"""작업 표를 Pillow로 그림(PNG)으로 그린다. 입출력(그림 받기·DB)은 하지 않는다 — skill.py가 받아 둔 그림 바이트를 넘긴다.

그림 도구는 배치만 고른다: 묶음·표지·정렬·요약은 표가 가진 그대로 그리고, 행 번호는 글자 출력과 같은 번호를 찍는다.
칸은 공통 칸 이름(`table_columns`)으로 알아보고 있는 칸만 그린다. 글자는 셀의 보일 글자를 그대로 쓴다."""

from __future__ import annotations

import io
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from core.tables import Row, WorkTable, table_file_name
from services.chunithm import table_columns as tc

logger = logging.getLogger(__name__)

Layout = Literal["card", "list"]

CARD_PAGE_LIMIT = 50
LIST_PAGE_LIMIT = 30

# --- 글꼴. Docker 이미지는 fonts-noto-cjk. `YUNI_FONT_REGULAR`/`YUNI_FONT_BOLD`로 글꼴 파일을 직접 줄 수도 있다(개발 PC용).
_NOTO_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_NOTO_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
_FALLBACK_FONTS = (
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)
_KOREAN_FACE = 1  # NotoSansCJK.ttc 안의 얼굴 번호: 0 JP, 1 KR, 2 SC, 3 TC, 4 HK

# --- 색(우리 배치용 팔레트)
_BG = (22, 25, 36)
_PANEL = (36, 40, 58)
_BAND = (52, 58, 84)
_TEXT = (240, 242, 250)
_MUTED = (150, 158, 184)
_STALE = (255, 170, 80)
_PLACEHOLDER = (58, 63, 88)

_DIFFICULTY_COLORS = {
    "BAS": (46, 160, 90),
    "ADV": (226, 150, 40),
    "EXP": (214, 66, 66),
    "MAS": (150, 80, 200),
    "ULT": (60, 60, 70),
    "WE": (210, 100, 170),
}
_LAMP_LABELS = {"AJC": "AJC", "ALL JUSTICE": "AJ", "FULL COMBO": "FC"}
_LAMP_COLORS = {"AJC": (240, 190, 40), "AJ": (232, 160, 50), "FC": (70, 160, 220)}


def _rank_color(rank: str) -> tuple[int, int, int]:
    if rank.startswith("SSS"):
        return (255, 205, 70)
    if rank.startswith(("SS", "S")):
        return (214, 220, 236)
    if rank.startswith("A"):
        return (120, 200, 130)
    if rank.startswith("B"):
        return (110, 160, 230)
    return _MUTED


# ---------------------------------------------------------------- 글꼴·글자

@lru_cache(maxsize=32)
def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    override = os.environ.get("YUNI_FONT_BOLD" if bold else "YUNI_FONT_REGULAR")
    primary = _NOTO_BOLD if bold else _NOTO_REGULAR
    candidates = [(primary, (_KOREAN_FACE, 0)), *((p, (0,)) for p in _FALLBACK_FONTS)]
    if override:
        candidates.insert(0, (override, (0,)))
    for path, faces in candidates:
        if not Path(path).is_file():
            continue
        for face in faces:
            try:
                return ImageFont.truetype(path, size, index=face)
            except OSError:
                continue
    logger.warning("CJK 글꼴을 찾지 못해 기본 글꼴로 그린다(한글이 깨질 수 있다)")
    return ImageFont.load_default(size)


def _width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> int:
    return round(font.getlength(text))


def _ellipsize(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str, max_width: int) -> str:
    if _width(font, text) <= max_width:
        return text
    while text and _width(font, text + "…") > max_width:
        text = text[:-1]
    return text + "…"


def _wrap_two(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str, max_width: int) -> list[str]:
    """글자 단위로 두 줄까지 나눈다(한글·일본어는 띄어쓰기가 없다). 넘치면 둘째 줄 끝을 줄인다."""
    first = ""
    for index, char in enumerate(text):
        if _width(font, first + char) > max_width:
            return [first, _ellipsize(font, text[index:], max_width)]
        first += char
    return [first]


# ---------------------------------------------------------------- 그림 다루기

def _open(data: bytes | None) -> Image.Image | None:
    if data is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.convert("RGBA")
    except (OSError, ValueError):
        return None


def _paste_fit(canvas: Image.Image, image: Image.Image | None, box: tuple[int, int, int, int]) -> None:
    """`box` 안에 비율을 지켜 맞춰 붙인다. 그림이 없으면(받기 실패) 빈 사각형."""
    x0, y0, x1, y1 = box
    if image is None:
        ImageDraw.Draw(canvas).rounded_rectangle(box, radius=6, fill=_PLACEHOLDER)
        return
    scale = min((x1 - x0) / image.width, (y1 - y0) / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    resized = image.resize(size, Image.Resampling.LANCZOS)
    canvas.alpha_composite(resized, (x0 + (x1 - x0 - size[0]) // 2, y0 + (y1 - y0 - size[1]) // 2))


def _chip(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: tuple[int, int, int], *, size: int = 12
) -> int:
    """둥근 칩을 그리고 오른쪽 끝 x를 돌려준다."""
    font = _font(size, True)
    width = _width(font, text) + 10
    x, y = xy
    draw.rounded_rectangle((x, y, x + width, y + size + 6), radius=5, fill=fill)
    draw.text((x + 5, y + 2), text, font=font, fill=(255, 255, 255))
    return x + width


def _number_badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], number: int) -> None:
    font = _font(11, True)
    text = f"#{number}"
    x, y = xy
    draw.rounded_rectangle((x, y, x + _width(font, text) + 8, y + 17), radius=4, fill=(0, 0, 0, 170))
    draw.text((x + 4, y + 2), text, font=font, fill=(255, 255, 255))


# ---------------------------------------------------------------- 쪽 나누기

@dataclass(frozen=True, slots=True)
class Segment:
    """한 쪽에 들어가는 묶음 조각. `rows`는 (표 전체 번호, 행)."""

    title: str | None
    total: int
    summary: Mapping[str, str]
    rows: tuple[tuple[int, Row], ...]
    continued: bool = False


def split_pages(table: WorkTable, limit: int) -> list[list[Segment]]:
    """한 쪽에 `limit`행까지. 묶음 경계에서 자르고, 묶음 하나가 `limit`보다 크면 그 안에서 자른다."""
    numbered = iter(table.numbered_rows())
    units: list[Segment] = []
    for group in table.groups:
        rows = tuple(next(numbered) for _ in group.rows)
        for start in range(0, max(len(rows), 1), limit):
            chunk = rows[start : start + limit]
            if chunk:
                units.append(Segment(group.title, len(rows), group.summary, chunk, continued=start > 0))
    pages: list[list[Segment]] = []
    used = 0
    for unit in units:
        if not pages or used + len(unit.rows) > limit:
            pages.append([])
            used = 0
        pages[-1].append(unit)
        used += len(unit.rows)
    return pages


# ---------------------------------------------------------------- 머리말

@dataclass(frozen=True, slots=True)
class HeaderProfile:
    """머리말에 그릴 플레이어 정보. 모르는 것은 None."""

    nickname: str | None = None
    level: int | None = None
    character: bytes | None = None
    frame: bytes | None = None


_NICKNAME_KEY = "닉네임"
_LEVEL_KEY = "레벨"
_ICON = 88
_HEADER_PROBE_H = 400  # 머리말 높이를 재려고 그려 보는 임시 도화지


def _header_lines(table: WorkTable, profile: HeaderProfile | None) -> tuple[str | None, list[str], str | None]:
    """(큰 글자 줄, 작은 글자 줄들, 과정 줄). 닉네임·레벨은 큰 글자 줄로 올리고 나머지 요약은 작은 줄로 둔다."""
    summary = dict(table.summary)
    nickname = summary.pop(_NICKNAME_KEY, None) or (profile.nickname if profile else None)
    level = profile.level if profile is not None and profile.level is not None else None
    level_text = f"Lv.{level}" if level is not None else summary.get(_LEVEL_KEY)
    summary.pop(_LEVEL_KEY, None)
    title = " ".join(part for part in (nickname, level_text) if part) or None
    details = [" · ".join(f"{key} {value}" for key, value in summary.items())] if summary else []
    steps = " → ".join(table.steps) if table.steps else None
    return title, details, steps


def _draw_header(
    canvas: Image.Image, width: int, table: WorkTable, profile: HeaderProfile | None, *, stale: bool,
    page: int, pages: int,
) -> int:
    """머리말을 그리고 그 높이를 돌려준다. 첫 쪽만 전체, 나머지 쪽은 과정 줄과 쪽 번호만."""
    draw = ImageDraw.Draw(canvas)
    margin = 24
    title, details, steps = _header_lines(table, profile)
    y = 20
    x = margin
    if page == 1:
        has_icon = profile is not None and (profile.character is not None or profile.frame is not None)
        if has_icon:
            assert profile is not None
            box = (margin, y, margin + _ICON, y + _ICON)
            draw.rounded_rectangle(box, radius=8, fill=_PANEL)
            _paste_fit(canvas, _open(profile.character), box)
            frame = _open(profile.frame)
            if frame is not None:
                _paste_fit(canvas, frame, box)
            x = margin + _ICON + 16
        text_y = y
        if title:
            draw.text((x, text_y), title, font=_font(30, True), fill=_TEXT)
            text_y += 42
        for line in details:
            draw.text((x, text_y), _ellipsize(_font(18, False), line, width - x - margin), font=_font(18, False), fill=_TEXT)
            text_y += 26
        if steps:
            draw.text((x, text_y), _ellipsize(_font(15, False), steps, width - x - margin), font=_font(15, False), fill=_MUTED)
            text_y += 22
        y = max(text_y, y + (_ICON if has_icon else 0)) + 6
    elif steps:
        draw.text((margin, y), _ellipsize(_font(15, False), steps, width - 2 * margin - 80), font=_font(15, False), fill=_MUTED)
        y += 26
    notes: list[str] = []
    if stale:
        notes.append("동기화 전 값")
    if pages > 1:
        notes.append(f"{page}/{pages}쪽")
    if notes:
        font = _font(14, True)
        text = " · ".join(notes)
        draw.text((width - margin - _width(font, text), 22), text, font=font, fill=_STALE if stale else _MUTED)
        y = max(y, 52)
    return y + 8


def _draw_band(draw: ImageDraw.ImageDraw, width: int, y: int, segment: Segment, unit: str) -> int:
    """묶음 표지 띠. 제목·개수·요약."""
    title = segment.title or ""
    if segment.continued:
        title = f"{title} (이어서)" if title else "(이어서)"
    parts = [title] if title else []
    parts.append(f"{segment.total}{unit}")
    parts.extend(f"{key} {value}" for key, value in segment.summary.items())
    text = " · ".join(parts)
    draw.rounded_rectangle((24, y, width - 24, y + 36), radius=8, fill=_BAND)
    draw.text((38, y + 7), _ellipsize(_font(18, True), text, width - 76), font=_font(18, True), fill=_TEXT)
    return y + 36 + 12


# ---------------------------------------------------------------- 카드

_CARD_W = 176
_CARD_PAD = 8
_JACKET = 160  # 재킷 원본이 190×190이라 이보다 크게 늘리지 않는다
_GAP = 12
_MARGIN = 24
_TITLE_TOP = _CARD_PAD + _JACKET + 6
_INFO_TOP = _TITLE_TOP + 2 * 18 + 4
_INFO_LINE = 22


@dataclass(frozen=True, slots=True)
class _CardLines:
    const: bool
    score: bool
    detail: bool

    @property
    def count(self) -> int:
        return int(self.const) + int(self.score) + int(self.detail)

    @property
    def height(self) -> int:
        return _INFO_TOP + self.count * _INFO_LINE + _CARD_PAD


def _card_lines(table: WorkTable) -> _CardLines:
    names = {column.name for column in table.columns}
    return _CardLines(
        const=tc.CONST in names,
        score=tc.SCORE in names,
        detail=bool(names & {tc.RANK, tc.LAMP, tc.RATING}),
    )


def _draw_difficulty(draw: ImageDraw.ImageDraw, xy: tuple[int, int], row: Row) -> None:
    code = row.cell(tc.DIFFICULTY)
    if code.filled:
        _chip(draw, xy, code.text, _DIFFICULTY_COLORS.get(code.text, (90, 90, 110)))


def _draw_lamp(draw: ImageDraw.ImageDraw, xy: tuple[int, int], row: Row) -> int | None:
    lamp = row.cell(tc.LAMP)
    label = _LAMP_LABELS.get(lamp.text) if lamp.filled else None
    if label is None:
        return None
    return _chip(draw, xy, label, _LAMP_COLORS[label], size=11)


def _draw_card(
    canvas: Image.Image, origin: tuple[int, int], number: int, row: Row, jacket: Image.Image | None, lines: _CardLines
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    x, y = origin
    draw.rounded_rectangle((x, y, x + _CARD_W, y + lines.height), radius=10, fill=_PANEL)
    jx, jy = x + _CARD_PAD, y + _CARD_PAD
    _paste_fit(canvas, jacket, (jx, jy, jx + _JACKET, jy + _JACKET))
    _number_badge(draw, (jx + 4, jy + 4), number)
    _draw_difficulty(draw, (jx + 4, jy + _JACKET - 24), row)

    title_font = _font(14, True)
    for index, line in enumerate(_wrap_two(title_font, row.cell(tc.SONG).text, _JACKET)):
        draw.text((jx, y + _TITLE_TOP + index * 18), line, font=title_font, fill=_TEXT)

    cy = y + _INFO_TOP
    left, right = jx, jx + _JACKET
    if lines.const:
        const = row.cell(tc.CONST)
        if const.text:
            draw.text((left, cy), f"상수 {const.text}", font=_font(13, False), fill=_MUTED)
        cy += _INFO_LINE
    if lines.score:
        score = row.cell(tc.SCORE)
        if score.filled:
            draw.text((left, cy - 2), score.text, font=_font(17, True), fill=_TEXT)
        elif score.text:
            draw.text((left, cy), score.text, font=_font(13, False), fill=_MUTED)
        cy += _INFO_LINE
    if lines.detail:
        rank = row.cell(tc.RANK)
        cursor = left
        if rank.filled:
            draw.text((cursor, cy), rank.text, font=_font(15, True), fill=_rank_color(rank.text))
            cursor += _width(_font(15, True), rank.text) + 6
        lamp_end = _draw_lamp(draw, (cursor, cy), row)
        cursor = lamp_end if lamp_end is not None else cursor
        rating = row.cell(tc.RATING)
        if rating.filled:
            font = _font(14, True)
            text = rating.text
            width = _width(font, text)
            label_font = _font(11, False)
            if right - width - _width(label_font, "RT ") - 6 > cursor:
                draw.text((right - width - _width(label_font, "RT "), cy + 3), "RT", font=label_font, fill=_MUTED)
            draw.text((right - width, cy), text, font=font, fill=_TEXT)


def _draw_cards(
    segments: Sequence[Segment], table: WorkTable, jackets: Mapping[str, Image.Image | None],
    profile: HeaderProfile | None, *, stale: bool, page: int, pages: int,
) -> Image.Image:
    lines = _card_lines(table)
    count = sum(len(segment.rows) for segment in segments)
    columns = min(5, max(3, count))
    width = _MARGIN * 2 + columns * _CARD_W + (columns - 1) * _GAP
    bare = len(table.groups) == 1 and table.groups[0].title is None

    # 높이를 먼저 센다.
    probe = Image.new("RGBA", (width, _HEADER_PROBE_H))
    header_height = _draw_header(probe, width, table, profile, stale=stale, page=page, pages=pages)
    height = header_height
    for segment in segments:
        if not bare:
            height += 36 + 12
        rows = -(-len(segment.rows) // columns)
        height += rows * (lines.height + _GAP) + 8
    height += _MARGIN - 8

    canvas = Image.new("RGBA", (width, height), _BG)
    y = _draw_header(canvas, width, table, profile, stale=stale, page=page, pages=pages)
    draw = ImageDraw.Draw(canvas)
    for segment in segments:
        if not bare:
            y = _draw_band(draw, width, y, segment, table.unit)
        for index, (number, row) in enumerate(segment.rows):
            col, line = index % columns, index // columns
            origin = (_MARGIN + col * (_CARD_W + _GAP), y + line * (lines.height + _GAP))
            _draw_card(canvas, origin, number, row, jackets.get(row.cell(tc.SONG).text), lines)
        y += -(-len(segment.rows) // columns) * (lines.height + _GAP) + 8
    return canvas


# ---------------------------------------------------------------- 목록

_LIST_W = 920
_LIST_ROW_H = 64
_LIST_JACKET = 56


def _draw_list_row(
    canvas: Image.Image, y: int, number: int, row: Row, jacket: Image.Image | None, names: set[str]
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((_MARGIN, y, _LIST_W - _MARGIN, y + _LIST_ROW_H - 6), radius=8, fill=_PANEL)
    draw.text((_MARGIN + 10, y + 18), f"#{number}", font=_font(13, True), fill=_MUTED)
    jx = _MARGIN + 54
    _paste_fit(canvas, jacket, (jx, y + 1, jx + _LIST_JACKET, y + 1 + _LIST_JACKET - 2))

    # 오른쪽부터 채운다: 곡 레이팅 · 램프 · 랭크 · 점수.
    right = _LIST_W - _MARGIN - 12
    if tc.RATING in names:
        rating = row.cell(tc.RATING)
        if rating.text:
            font = _font(16, True)
            draw.text((right - _width(font, rating.text), y + 16), rating.text, font=font, fill=_TEXT)
        right -= 76
    if tc.LAMP in names:
        _draw_lamp(draw, (right - 40, y + 19), row)
        right -= 54
    if tc.RANK in names:
        rank = row.cell(tc.RANK)
        if rank.filled:
            font = _font(17, True)
            draw.text((right - _width(font, rank.text), y + 15), rank.text, font=font, fill=_rank_color(rank.text))
        right -= 62
    if tc.SCORE in names:
        score = row.cell(tc.SCORE)
        font = _font(18, True) if score.filled else _font(14, False)
        if score.text:
            draw.text((right - _width(font, score.text), y + 15), score.text, font=font, fill=_TEXT if score.filled else _MUTED)
        right -= 130

    text_x = jx + _LIST_JACKET + 14
    title_font = _font(17, True)
    draw.text((text_x, y + 8), _ellipsize(title_font, row.cell(tc.SONG).text, right - text_x), font=title_font, fill=_TEXT)
    cursor = text_x
    code = row.cell(tc.DIFFICULTY)
    if code.filled:
        cursor = _chip(draw, (cursor, y + 32), code.text, _DIFFICULTY_COLORS.get(code.text, (90, 90, 110))) + 8
    if tc.CONST in names and row.cell(tc.CONST).text:
        draw.text((cursor, y + 33), f"상수 {row.cell(tc.CONST).text}", font=_font(13, False), fill=_MUTED)


def _draw_list(
    segments: Sequence[Segment], table: WorkTable, jackets: Mapping[str, Image.Image | None],
    profile: HeaderProfile | None, *, stale: bool, page: int, pages: int,
) -> Image.Image:
    names = {column.name for column in table.columns}
    bare = len(table.groups) == 1 and table.groups[0].title is None
    probe = Image.new("RGBA", (_LIST_W, _HEADER_PROBE_H))
    header_height = _draw_header(probe, _LIST_W, table, profile, stale=stale, page=page, pages=pages)
    height = header_height + _MARGIN
    for segment in segments:
        height += (0 if bare else 48) + len(segment.rows) * _LIST_ROW_H + 8

    canvas = Image.new("RGBA", (_LIST_W, height), _BG)
    y = _draw_header(canvas, _LIST_W, table, profile, stale=stale, page=page, pages=pages)
    draw = ImageDraw.Draw(canvas)
    for segment in segments:
        if not bare:
            y = _draw_band(draw, _LIST_W, y, segment, table.unit)
        for number, row in segment.rows:
            _draw_list_row(canvas, y, number, row, jackets.get(row.cell(tc.SONG).text), names)
            y += _LIST_ROW_H
        y += 8
    return canvas


# ---------------------------------------------------------------- 공개 함수

def render_pages(
    table: WorkTable,
    *,
    layout: Layout,
    jackets: Mapping[str, bytes | None],
    profile: HeaderProfile | None = None,
    stale: bool = False,
) -> list[tuple[str, bytes]]:
    """표를 그림으로 그려 (파일 이름, PNG 바이트) 목록으로 돌려준다. 쪽 상한을 넘으면 여러 장이다.
    `jackets`는 곡 제목 → 재킷 그림 바이트(받기 실패·없음은 None 또는 빠짐 → 빈 사각형)."""
    limit = CARD_PAGE_LIMIT if layout == "card" else LIST_PAGE_LIMIT
    opened = {title: _open(data) for title, data in jackets.items()}
    pages = split_pages(table, limit)
    draw_page = _draw_cards if layout == "card" else _draw_list
    stem = table_file_name(table).removesuffix(".txt")
    files: list[tuple[str, bytes]] = []
    for index, segments in enumerate(pages, start=1):
        image = draw_page(segments, table, opened, profile, stale=stale, page=index, pages=len(pages))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        name = f"{stem}.png" if len(pages) == 1 else f"{stem}_{index}.png"
        files.append((name, buffer.getvalue()))
    return files
