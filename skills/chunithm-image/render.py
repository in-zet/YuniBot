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

_MARGIN = 24
_BAND_H = 46  # 묶음 표지 띠 높이

# --- 글꼴. Docker 이미지는 fonts-noto-cjk. `YUNI_FONT_REGULAR`/`YUNI_FONT_BOLD`로 글꼴 파일을 직접 줄 수도 있다(개발 PC용).
_NOTO_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_NOTO_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
_FALLBACK_FONTS = (
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
)
_KOREAN_FACE = 1  # NotoSansCJK.ttc 안의 얼굴 번호: 0 JP, 1 KR, 2 SC, 3 TC, 4 HK
FONT_SCALE = 1.5  # 글자 크기 배율(2026-10-21 실기: 너무 작아 1.5배). 이 모듈의 글자 크기 숫자는 배율 적용 전 값이다.

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

def _fs(size: int) -> int:
    """배율을 적용한 글자 크기(높이 계산용)."""
    return round(size * FONT_SCALE)


@lru_cache(maxsize=64)
def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = _fs(size)
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


_ICON_FRAME_INSET = 0.05  # 틀 테두리 안쪽에 캐릭터를 앉히는 여백(상자 한 변에 대한 비율)


def _draw_icon(canvas: Image.Image, box: tuple[int, int, int, int], character: bytes | None, frame: bytes | None) -> None:
    """플레이어 아이콘. CHUNITHM-NET처럼 틀 그림이 배경판(투명한 데가 없다)이고 캐릭터가 그 위에 얹힌다."""
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius=10, fill=_PANEL)
    frame_image = _open(frame)
    character_image = _open(character)
    if frame_image is not None:
        _paste_fit(canvas, frame_image, box)
    if character_image is None and frame_image is not None:
        return  # 틀만 있고 캐릭터를 못 받았으면 빈 사각형을 얹지 않는다
    inset = round((box[2] - box[0]) * _ICON_FRAME_INSET) if frame_image is not None else 0
    _paste_fit(canvas, character_image, (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset))


def _chip_width(text: str, size: int) -> int:
    return _width(_font(size, True), text) + _fs(10)


def _chip(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: tuple[int, int, int], *, size: int = 12,
    right_aligned: bool = False,
) -> int:
    """둥근 칩을 그리고 오른쪽 끝 x를 돌려준다. `right_aligned`면 `xy`의 x를 오른쪽 끝으로 본다."""
    font = _font(size, True)
    width = _chip_width(text, size)
    x, y = xy
    if right_aligned:
        x -= width
    draw.rounded_rectangle((x, y, x + width, y + _fs(size) + _fs(6)), radius=_fs(5), fill=fill)
    draw.text((x + _fs(5), y + _fs(2)), text, font=font, fill=(255, 255, 255))
    return x + width


def _number_badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], number: int, table_number: int | None = None) -> int:
    """순서 번호 띠를 그리고 오른쪽 끝 x를 돌려준다. 묶음 안 번호(`number`)와 표 번호가 다르면 뒤에 작게 `(표 번호)`."""
    font = _font(11, True)
    text = f"#{number}"
    extra = f"({table_number})" if table_number is not None and table_number != number else ""
    extra_font = _font(9, False)
    x, y = xy
    width = _width(font, text) + (_width(extra_font, extra) + _fs(4) if extra else 0) + _fs(8)
    draw.rounded_rectangle((x, y, x + width, y + _fs(17)), radius=_fs(4), fill=(0, 0, 0, 170))
    draw.text((x + _fs(4), y + _fs(2)), text, font=font, fill=(255, 255, 255))
    if extra:
        draw.text((x + _fs(4) + _width(font, text) + _fs(4), y + _fs(5)), extra, font=extra_font, fill=(200, 205, 225))
    return x + width


# ---------------------------------------------------------------- 쪽 나누기

@dataclass(frozen=True, slots=True)
class Segment:
    """한 쪽에 들어가는 묶음 조각. `rows`는 (표 전체 번호, 행)."""

    title: str | None
    total: int
    summary: Mapping[str, str]
    rows: tuple[tuple[int, Row], ...]
    continued: bool = False
    first_number: int = 1  # 이 묶음 첫 행의 표 번호 — 묶음 안 번호는 `표 번호 - first_number + 1`

    def local_number(self, number: int) -> int:
        """그림에 크게 찍는 번호. 묶음마다 1부터 다시 센다(표 번호는 괄호로 병기)."""
        return number - self.first_number + 1


def split_pages(table: WorkTable, limit: int) -> list[list[Segment]]:
    """한 쪽에 `limit`행까지. 묶음 경계에서 자르고, 묶음 하나가 `limit`보다 크면 그 안에서 자른다."""
    numbered = iter(table.numbered_rows())
    units: list[Segment] = []
    for group in table.groups:
        rows = tuple(next(numbered) for _ in group.rows)
        first_number = rows[0][0] if rows else 1
        for start in range(0, max(len(rows), 1), limit):
            chunk = rows[start : start + limit]
            if chunk:
                units.append(
                    Segment(group.title, len(rows), group.summary, chunk, continued=start > 0, first_number=first_number)
                )
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
    """머리말에 그릴 플레이어 정보. 모르는 것은 None. `version`은 표를 만든 때의 가동 버전(레이팅표에만 쓴다)."""

    nickname: str | None = None
    level: int | None = None
    character: bytes | None = None
    frame: bytes | None = None
    version: str | None = None


_NICKNAME_KEY = "닉네임"
_LEVEL_KEY = "레벨"
_RATING_KEY = "레이팅"
_ICON = 120
_HEADER_PROBE_H = 600  # 머리말 높이를 재려고 그려 보는 임시 도화지


def header_rating(table: WorkTable) -> str | None:
    """표 요약의 최종 레이팅 — 레이팅표(요약에 `레이팅`이 있는 표)에서만 머리말 오른쪽 끝에 크게 그린다."""
    return table.summary.get(_RATING_KEY)


def _header_lines(table: WorkTable, profile: HeaderProfile | None) -> tuple[str | None, list[str], str | None]:
    """(큰 글자 줄, 작은 글자 줄들, 과정 줄). 닉네임·레벨은 큰 글자 줄로 올리고, 레이팅표의 최종 레이팅은
    따로 크게 그리므로 뺀다. 레이팅표면 가동 버전을 작은 줄 맨 앞에 둔다."""
    summary = dict(table.summary)
    nickname = summary.pop(_NICKNAME_KEY, None) or (profile.nickname if profile else None)
    level = profile.level if profile is not None and profile.level is not None else None
    level_text = f"Lv.{level}" if level is not None else summary.get(_LEVEL_KEY)
    summary.pop(_LEVEL_KEY, None)
    rating = summary.pop(_RATING_KEY, None)
    title = " ".join(part for part in (nickname, level_text) if part) or None
    details = [" · ".join(f"{key} {value}" for key, value in summary.items())] if summary else []
    if rating is not None and profile is not None and profile.version:
        details.insert(0, profile.version)
    steps = " → ".join(table.steps) if table.steps else None
    return title, details, steps


def _draw_final_rating(canvas: Image.Image, width: int, rating: str, y: int) -> tuple[int, int]:
    """머리말 오른쪽 끝에 최종 레이팅을 크게 그린다. (왼쪽 끝 x, 아래 끝 y)를 돌려준다."""
    draw = ImageDraw.Draw(canvas)
    label_font, font = _font(13, False), _font(40, True)
    value_width = _width(font, rating)
    right = width - _MARGIN
    draw.text((right - _width(label_font, "RATING"), y), "RATING", font=label_font, fill=_MUTED)
    value_y = y + _fs(13) + 4
    draw.text((right - value_width, value_y), rating, font=font, fill=_TEXT)
    return right - max(value_width, _width(label_font, "RATING")), value_y + _fs(40) + 8


def _draw_header(
    canvas: Image.Image, width: int, table: WorkTable, profile: HeaderProfile | None, *, stale: bool,
    page: int, pages: int,
) -> int:
    """머리말을 그리고 그 높이를 돌려준다. 첫 쪽만 전체, 나머지 쪽은 과정 줄과 쪽 번호만."""
    draw = ImageDraw.Draw(canvas)
    margin = _MARGIN
    title, details, steps = _header_lines(table, profile)
    y = 20
    x = margin
    notes: list[str] = []
    if stale:
        notes.append("동기화 전 값")
    if pages > 1:
        notes.append(f"{page}/{pages}쪽")
    notes_bottom = y
    if notes:
        font = _font(14, True)
        text = " · ".join(notes)
        draw.text((width - margin - _width(font, text), y), text, font=font, fill=_STALE if stale else _MUTED)
        notes_bottom = y + _fs(14) + 8
    if page == 1:
        rating = header_rating(table)
        reserved = 0  # 오른쪽 끝에 크게 그린 레이팅이 차지하는 폭 — 왼쪽 글줄은 이 안으로 안 들어간다.
        rating_bottom = y
        if rating is not None:
            left, rating_bottom = _draw_final_rating(canvas, width, rating, notes_bottom)
            reserved = width - left + 16
        has_icon = profile is not None and (profile.character is not None or profile.frame is not None)
        if has_icon:
            assert profile is not None
            _draw_icon(canvas, (margin, y, margin + _ICON, y + _ICON), profile.character, profile.frame)
            x = margin + _ICON + 16
        room = width - x - margin - reserved
        text_y = y
        if title:
            draw.text((x, text_y), _ellipsize(_font(30, True), title, room), font=_font(30, True), fill=_TEXT)
            text_y += _fs(30) + 12
        for line in details:
            draw.text((x, text_y), _ellipsize(_font(18, False), line, room), font=_font(18, False), fill=_TEXT)
            text_y += _fs(18) + 10
        if steps:
            draw.text((x, text_y), _ellipsize(_font(15, False), steps, room), font=_font(15, False), fill=_MUTED)
            text_y += _fs(15) + 8
        y = max(text_y, y + (_ICON if has_icon else 0), rating_bottom) + 6
    elif steps:
        room = width - 2 * margin - (_width(_font(14, True), " · ".join(notes)) + 16 if notes else 0)
        draw.text((margin, y), _ellipsize(_font(15, False), steps, room), font=_font(15, False), fill=_MUTED)
        y += _fs(15) + 10
    y = max(y, notes_bottom)
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
    draw.rounded_rectangle((_MARGIN, y, width - _MARGIN, y + _BAND_H), radius=10, fill=_BAND)
    draw.text((_MARGIN + 16, y + 8), _ellipsize(_font(18, True), text, width - 2 * _MARGIN - 32), font=_font(18, True), fill=_TEXT)
    return y + _BAND_H + 12


# ---------------------------------------------------------------- 카드

_JACKET = 190  # 재킷 원본 크기 — 이보다 크게 늘리지 않는다
_CARD_PAD = 8
_CARD_W = _JACKET + 2 * _CARD_PAD
_GAP = 12
_INFO_TOP = _CARD_PAD + _JACKET + 6
_INFO_LINE = 34
_TITLE_LINE = 26  # 재킷 안 곡명 줄 높이
_CARD_RATING_SIZE = 18  # 곡 레이팅만 다른 글자보다 크게(2026-10-21 실기)
_GRADIENT_MAX_ALPHA = 220


@dataclass(frozen=True, slots=True)
class _CardLines:
    """재킷 아래 줄: 첫 줄은 상수(왼쪽)·곡 레이팅(오른쪽 끝), 둘째 줄은 점수(왼쪽)·랭크(오른쪽 끝)."""

    const_rating: bool
    score_rank: bool

    @property
    def count(self) -> int:
        return int(self.const_rating) + int(self.score_rank)

    @property
    def height(self) -> int:
        return _INFO_TOP + self.count * _INFO_LINE + _CARD_PAD


def _card_lines(table: WorkTable) -> _CardLines:
    names = {column.name for column in table.columns}
    return _CardLines(
        const_rating=bool(names & {tc.CONST, tc.RATING}),
        score_rank=bool(names & {tc.SCORE, tc.RANK}),
    )


def _draw_difficulty(draw: ImageDraw.ImageDraw, right_top: tuple[int, int], row: Row) -> None:
    code = row.cell(tc.DIFFICULTY)
    if code.filled:
        _chip(draw, right_top, code.text, _DIFFICULTY_COLORS.get(code.text, (90, 90, 110)), right_aligned=True)


def _draw_lamp(draw: ImageDraw.ImageDraw, xy: tuple[int, int], row: Row) -> int | None:
    lamp = row.cell(tc.LAMP)
    label = _LAMP_LABELS.get(lamp.text) if lamp.filled else None
    if label is None:
        return None
    return _chip(draw, xy, label, _LAMP_COLORS[label], size=11)


def _draw_title_over_jacket(canvas: Image.Image, box: tuple[int, int, int, int], title: str) -> None:
    """재킷 아래쪽에 검은 그라데이션을 깔고 흰 글씨로 곡명을 넣는다(두 줄까지)."""
    x0, _, x1, y1 = box
    font = _font(14, True)
    lines = _wrap_two(font, title, x1 - x0 - 16)
    height = len(lines) * _TITLE_LINE + 22
    gradient = Image.new("RGBA", (x1 - x0, height))
    pixels = ImageDraw.Draw(gradient)
    for row in range(height):
        pixels.line((0, row, x1 - x0, row), fill=(0, 0, 0, round(_GRADIENT_MAX_ALPHA * row / (height - 1))))
    canvas.alpha_composite(gradient, (x0, y1 - height))
    draw = ImageDraw.Draw(canvas)
    top = y1 - 8 - len(lines) * _TITLE_LINE
    for index, line in enumerate(lines):
        draw.text((x0 + 8, top + index * _TITLE_LINE), line, font=font, fill=(255, 255, 255))


def _fit_score_font(score: str, rank: str | None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """점수와 랭크가 한 줄에 같이 들어가도록 점수 글자 크기를 줄여 고른다."""
    rank_width = _width(_font(15, True), rank) + 10 if rank else 0
    for size in (17, 16, 15, 14):
        font = _font(size, True)
        if _width(font, score) + rank_width <= _JACKET:
            return font
    return _font(13, True)


def _draw_card(
    canvas: Image.Image, origin: tuple[int, int], number: int, table_number: int, row: Row,
    jacket: Image.Image | None, lines: _CardLines,
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    x, y = origin
    draw.rounded_rectangle((x, y, x + _CARD_W, y + lines.height), radius=12, fill=_PANEL)
    jx, jy = x + _CARD_PAD, y + _CARD_PAD
    box = (jx, jy, jx + _JACKET, jy + _JACKET)
    _paste_fit(canvas, jacket, box)
    _draw_title_over_jacket(canvas, box, row.cell(tc.SONG).text)
    draw = ImageDraw.Draw(canvas, "RGBA")
    badge_end = _number_badge(draw, (jx + 4, jy + 4), number, table_number)
    _draw_lamp(draw, (badge_end + 4, jy + 4), row)
    _draw_difficulty(draw, (jx + _JACKET - 4, jy + 4), row)

    left, right = jx, jx + _JACKET
    cy = y + _INFO_TOP
    if lines.const_rating:
        const = row.cell(tc.CONST)
        if const.text:
            draw.text((left, cy), f"상수 {const.text}", font=_font(13, False), fill=_MUTED)
        rating = row.cell(tc.RATING)
        if rating.filled:
            font = _font(_CARD_RATING_SIZE, True)
            width = _width(font, rating.text)
            draw.text((right - width, cy - 3), rating.text, font=font, fill=_TEXT)
        cy += _INFO_LINE
    if lines.score_rank:
        score, rank = row.cell(tc.SCORE), row.cell(tc.RANK)
        rank_text = rank.text if rank.filled else None
        if score.filled:
            draw.text((left, cy - 2), score.text, font=_fit_score_font(score.text, rank_text), fill=_TEXT)
        elif score.text:
            draw.text((left, cy), score.text, font=_font(13, False), fill=_MUTED)
        if rank_text:
            font = _font(15, True)
            draw.text((right - _width(font, rank_text), cy), rank_text, font=font, fill=_rank_color(rank_text))


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
            height += _BAND_H + 12
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
            _draw_card(
                canvas, origin, segment.local_number(number), number, row,
                jackets.get(row.cell(tc.SONG).text), lines,
            )
        y += -(-len(segment.rows) // columns) * (lines.height + _GAP) + 8
    return canvas


# ---------------------------------------------------------------- 목록

_LIST_W = 1240
_LIST_ROW_H = 90
_LIST_JACKET = 80


def _draw_list_row(
    canvas: Image.Image, y: int, number: int, table_number: int, row: Row, jacket: Image.Image | None,
    names: set[str],
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((_MARGIN, y, _LIST_W - _MARGIN, y + _LIST_ROW_H - 6), radius=10, fill=_PANEL)
    if table_number == number:
        draw.text((_MARGIN + 14, y + 26), f"#{number}", font=_font(13, True), fill=_MUTED)
    else:  # 묶음 안 번호가 크게, 표 번호는 그 아래 작게(행을 가리킬 때 쓰는 번호)
        draw.text((_MARGIN + 14, y + 14), f"#{number}", font=_font(13, True), fill=_MUTED)
        draw.text((_MARGIN + 14, y + 46), f"({table_number})", font=_font(10, False), fill=_MUTED)
    jx = _MARGIN + 82
    _paste_fit(canvas, jacket, (jx, y + 2, jx + _LIST_JACKET, y + 2 + _LIST_JACKET - 4))

    # 오른쪽부터 채운다: 곡 레이팅 · 램프 · 랭크 · 점수.
    right = _LIST_W - _MARGIN - 18
    if tc.RATING in names:
        rating = row.cell(tc.RATING)
        if rating.text:
            font = _font(19, True)
            draw.text((right - _width(font, rating.text), y + 20), rating.text, font=font, fill=_TEXT)
        right -= 114
    if tc.LAMP in names:
        _draw_lamp(draw, (right - 64, y + 26), row)
        right -= 86
    if tc.RANK in names:
        rank = row.cell(tc.RANK)
        if rank.filled:
            font = _font(17, True)
            draw.text((right - _width(font, rank.text), y + 22), rank.text, font=font, fill=_rank_color(rank.text))
        right -= 100
    if tc.SCORE in names:
        score = row.cell(tc.SCORE)
        font = _font(18, True) if score.filled else _font(14, False)
        if score.text:
            draw.text((right - _width(font, score.text), y + 22), score.text, font=font, fill=_TEXT if score.filled else _MUTED)
        right -= 200

    text_x = jx + _LIST_JACKET + 20
    title_font = _font(17, True)
    draw.text((text_x, y + 10), _ellipsize(title_font, row.cell(tc.SONG).text, right - text_x), font=title_font, fill=_TEXT)
    cursor = text_x
    code = row.cell(tc.DIFFICULTY)
    if code.filled:
        cursor = _chip(draw, (cursor, y + 48), code.text, _DIFFICULTY_COLORS.get(code.text, (90, 90, 110))) + 10
    if tc.CONST in names and row.cell(tc.CONST).text:
        draw.text((cursor, y + 51), f"상수 {row.cell(tc.CONST).text}", font=_font(13, False), fill=_MUTED)


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
        height += (0 if bare else _BAND_H + 12) + len(segment.rows) * _LIST_ROW_H + 8

    canvas = Image.new("RGBA", (_LIST_W, height), _BG)
    y = _draw_header(canvas, _LIST_W, table, profile, stale=stale, page=page, pages=pages)
    draw = ImageDraw.Draw(canvas)
    for segment in segments:
        if not bare:
            y = _draw_band(draw, _LIST_W, y, segment, table.unit)
        for number, row in segment.rows:
            _draw_list_row(
                canvas, y, segment.local_number(number), number, row, jackets.get(row.cell(tc.SONG).text), names
            )
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
