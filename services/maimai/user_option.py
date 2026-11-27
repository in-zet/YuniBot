# services/maimai/user_option.py
"""maimai DX NET의 설정(`home/userOption/…`) 페이지: 인게임 설정에서 이름 붙여 다루는 항목과 즐겨찾기 곡.
읽고 쓰는 틀은 게임 공통(`services/game/net_settings.py`). 표본: docs/reference/maimai-net/user_option.html,
favorite_update.html (2026-11-27 실측)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser

from services.game.net_settings import SettingField

# 츄니즘 설정과 같은 자리(속도·판정 타이밍 A·B·가이드음)는 같은 attr를 쓴다 — 도구 인자가 두 게임 공통이다.
# 나머지 30여 항목(노트 디자인·효과음 등)은 이름 붙이지 않는다(2026-11-27 사용자 결정).
SETTINGS_FIELDS: tuple[SettingField, ...] = (
    SettingField(attr="speed", select_name="noteSpeed", label="노트 속도", short="노트 속도"),
    SettingField(attr="touch_speed", select_name="touchSpeed", label="터치 속도", short="터치 속도"),
    SettingField(attr="slide_timing", select_name="slideSpeed", label="슬라이드 타이밍", short="슬라이드"),
    SettingField(attr="play_timing_offset", select_name="adjustTiming", label="판정 타이밍 A", short="판정A"),
    SettingField(attr="judge_timing_offset", select_name="judgeTiming", label="판정 타이밍 B", short="판정B"),
    SettingField(attr="guide_sound", select_name="ansVolume", label="가이드음", short="가이드음"),
)

# 즐겨찾기는 30곡까지다(페이지의 "FAVORITE SONGS n/30", 31번째부터는 체크 칸이 잠긴다).
FAVORITE_LIMIT = 30


@dataclass(frozen=True, slots=True, kw_only=True)
class FavoriteEntry:
    """즐겨찾기 바꾸기 페이지의 곡 하나. `value`는 사이트가 준 불투명한 곡 값이라 제목으로 맞춘다."""

    title: str
    value: str
    checked: bool


def parse_favorite_page(html: bytes | str) -> list[FavoriteEntry]:
    """`home/userOption/favorite/updateMusic`: 수록곡 전부가 장르별 체크 칸으로 나오고, 지금 즐겨찾기는 체크돼 있다.
    채보 종류(DX/STD) 구분 없이 곡 단위다."""
    soup = LexborHTMLParser(html)
    entries: list[FavoriteEntry] = []
    for box in soup.css("div.favorite_checkbox"):
        checkbox = box.css_first('input[name="music[]"]')
        name = box.css_first("div.favorite_music_name")
        if checkbox is None or name is None:
            continue
        entries.append(
            FavoriteEntry(
                title=name.text(strip=True),
                value=checkbox.attrs.get("value") or "",
                checked="checked" in checkbox.attrs,
            )
        )
    if not entries:
        raise ValueError("즐겨찾기 페이지 구조가 바뀌었다 — 곡 체크 칸을 못 찾았다")
    return entries


def favorite_payload(entries: Sequence[FavoriteEntry], titles: Sequence[str], token: str) -> dict[str, str | list[str]]:
    """`titles`(바꾼 뒤의 전체 목록)를 체크한 폼 바디. 이미 즐겨찾기인 곡은 체크된 칸을 그대로 쓰고, 새로 더하는 곡은
    제목으로 칸을 찾는다. 새로 더하는 곡의 제목이 둘이면 어느 쪽인지 몰라 거절하고(실측 1389곡 중 `Link` 하나), 페이지에
    없는 제목과 30곡 초과도 ValueError(사용자에게 그대로 보여 줄 한글 메시지)다."""
    by_title: dict[str, list[FavoriteEntry]] = {}
    for entry in entries:
        by_title.setdefault(entry.title, []).append(entry)
    wanted = list(dict.fromkeys(titles))
    if len(wanted) > FAVORITE_LIMIT:
        raise ValueError(f"즐겨찾기는 {FAVORITE_LIMIT}곡까지야(바꾸면 {len(wanted)}곡).")
    values: list[str] = []
    for title in wanted:
        kept = [entry for entry in by_title.get(title, []) if entry.checked]
        if kept:
            values.extend(entry.value for entry in kept)
            continue
        matches = by_title.get(title, [])
        if not matches:
            raise ValueError(f"maimai DX NET 즐겨찾기 목록에 '{title}'이(가) 없어.")
        if len(matches) > 1:
            raise ValueError(f"'{title}'은(는) 같은 제목 곡이 둘이라 즐겨찾기를 여기서 못 바꿔. maimai DX NET에서 직접 골라 줘.")
        values.append(matches[0].value)
    # `idx`는 장르 고르기 칸(99 = 전체)이다. 폼에 있는 값 그대로 보낸다.
    return {"idx": "99", "music[]": values, "token": token}


def form_token(html: bytes | str) -> str:
    """설정 폼에 실린 요청 토큰(`<input name="token">`)."""
    node = LexborHTMLParser(html).css_first('input[name="token"]')
    token = node.attrs.get("value") if node is not None else None
    if not token:
        raise ValueError("설정 페이지에 요청 토큰이 없다")
    return token
