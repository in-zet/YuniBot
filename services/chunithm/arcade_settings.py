# services/chunithm/arcade_settings.py
from __future__ import annotations

from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser, LexborNode

# chuni-penguin에는 이 페이지(설정 페이지)를 읽는 파서가 없다 — 국제판 전용으로 새로 만든다.
# 저장본: docs/reference/chunithm-net/arcade_settings.html
_NAMED_FIELDS: dict[str, str] = {
    "speed": "speed",
    "fieldWallPosition": "field_wall_position",
    "playTimingOffset": "play_timing_offset",
    "judgeTimingOffset": "judge_timing_offset",
    "guideSound": "guide_sound",
}
_ATTR_TO_SELECT_NAME: dict[str, str] = {attr: name for name, attr in _NAMED_FIELDS.items()}

# arcade(오락실별 방문 이력)와 settings(지금 계정 설정 조회·변경)가
# 똑같이 쓰는 표시 포맷 — 복사하지 않고 여기 하나만 둔다.
FIELD_LABELS: dict[str, str] = {
    "speed": "스피드",
    "field_wall_position": "필드 월",
    "play_timing_offset": "판정 타이밍 A",
    "judge_timing_offset": "판정 타이밍 B",
    "guide_sound": "가이드음",
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


def named_from_settings(settings: ArcadeSettings) -> dict[str, str]:
    return {
        "speed": settings.speed, "field_wall_position": settings.field_wall_position,
        "play_timing_offset": settings.play_timing_offset, "judge_timing_offset": settings.judge_timing_offset,
        "guide_sound": settings.guide_sound,
    }


def settings_line(named: dict[str, str]) -> str:
    return (
        f"스피드 {named['speed']} / 필드 월 {named['field_wall_position']} / "
        f"판정A {named['play_timing_offset']} / 판정B {named['judge_timing_offset']} / "
        f"가이드음 {named['guide_sound']}"
    )


def build_update_payload(soup: LexborHTMLParser, changes: dict[str, str]) -> dict[str, str]:
    """설정 페이지의 모든 `<select>`를 지금 선택된 `value`로 채운 폼 바디를 만들고,
    `changes`(이름 붙인 속성 다섯 개 중 일부)만 요청한 표시 텍스트와 같은 `<option>`의
    `value`로 바꿔치기한다. 나머지 항목은 그대로 되돌려 보내야 설정이 안 바뀐다 —
    CHUNITHM-NET의 설정 페이지는 폼 전체를 한 번에 제출하는 방식이다. 요청한 표시
    텍스트가 그 select의 어떤 option과도 안 맞으면 ValueError(사용자에게 그대로 보여줄
    수 있는 한글 메시지)."""
    selects: dict[str, LexborNode] = {}
    payload: dict[str, str] = {}
    for select in soup.css("select"):
        name = select.attrs.get("name")
        if not name:
            continue
        selects[name] = select
        option = select.css_first("option[selected]", default=None) or select.css_first(
            "option", default=None
        )
        if option is not None:
            payload[name] = option.attrs.get("value") or ""

    for attr, requested in changes.items():
        select_name = _ATTR_TO_SELECT_NAME.get(attr)
        if select_name is None:
            raise ValueError(f"모르는 설정 항목: {attr}")
        select = selects.get(select_name)
        if select is None:
            raise ValueError(f"모르는 설정 항목: {attr}")
        match = next((o for o in select.css("option") if o.text(strip=True) == requested), None)
        if match is None:
            raise ValueError(f"{FIELD_LABELS[attr]}에 '{requested}'라는 값은 없어.")
        payload[select_name] = match.attrs.get("value") or ""

    return payload
