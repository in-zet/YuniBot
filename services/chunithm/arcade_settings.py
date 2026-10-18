# services/chunithm/arcade_settings.py
from __future__ import annotations

from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser

# chuni-penguin에는 이 페이지(설정 페이지)를 읽는 파서가 없다 — 국제판 전용으로 새로 만든다.
# 저장본: docs/reference/chunithm-net/arcade_settings.html
_NAMED_FIELDS: dict[str, str] = {
    "speed": "speed",
    "fieldWallPosition": "field_wall_position",
    "playTimingOffset": "play_timing_offset",
    "judgeTimingOffset": "judge_timing_offset",
    "guideSound": "guide_sound",
}


@dataclass(slots=True, kw_only=True)
class ArcadeSettings:
    speed: str
    field_wall_position: str
    play_timing_offset: str
    judge_timing_offset: str
    guide_sound: str
    # select name → 선택된 option의 표시 텍스트. 설계서가 이름 붙인 다섯 개를 포함해
    # 페이지에 보이는 모든 설정이 들어 있다("나머지 설정도 통째로 저장").
    raw: dict[str, str]


def parse_arcade_settings(soup: LexborHTMLParser) -> ArcadeSettings:
    """설정 페이지(`/mobile/home/userOption/updateUserOption`)의 `<select>`마다 선택된
    `<option>`의 표시 텍스트를 뽑는다. 기체가 두 벌 보이는 경우는 같은 name이 두 번
    나오지 않는 한(미확인) 뒤에 나온 값이 앞 값을 덮어쓴다."""
    raw: dict[str, str] = {}
    for select in soup.css("select"):
        name = select.attrs.get("name")
        if not name:
            continue
        option = select.css_first("option[selected]", default=None) or select.css_first(
            "option", default=None
        )
        if option is None:
            continue
        raw[name] = option.text(strip=True)

    missing = [name for name in _NAMED_FIELDS if name not in raw]
    if missing:
        raise ValueError(f"설정 페이지 구조가 바뀌었다 — 못 찾은 항목: {', '.join(missing)}")

    named = {attr: raw[name] for name, attr in _NAMED_FIELDS.items()}
    return ArcadeSettings(raw=raw, **named)
