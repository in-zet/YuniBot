# services/game/net_settings.py
"""NET 인게임 설정 페이지(츄니즘·maimai 모두 `<select>`를 한 폼으로 통째로 제출하는 방식)를 읽고 쓰는 공통 틀.

게임마다 다른 것은 어느 `<select>`를 이름 붙여 다루는가(`SettingField` 목록, `GameProfile.settings_fields`)뿐이다.
`arcade`(오락실별 이력)와 `settings`(지금 계정 조회·변경)가 같이 쓴다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser, LexborNode


class ChangeNotAppliedError(Exception):
    """NET에 바꾸라고 보냈는데 다시 읽어 보니 반영되지 않았다. 메시지는 사용자에게 그대로 보여 줄 수 있다."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SettingField:
    """이름 붙여 다루는 설정 하나. `attr`는 도구 인자·저장 이름, `label`은 화면 이름(data 키), `short`는 한 줄 표시용."""

    attr: str
    select_name: str
    label: str
    short: str


@dataclass(slots=True, kw_only=True)
class GameSettings:
    # attr → 선택된 option의 표시 텍스트(필드 목록 순서).
    named: dict[str, str]
    # select name → 표시 텍스트. 이름 붙인 항목을 포함해 페이지의 모든 설정("나머지 설정도 통째로 저장").
    raw: dict[str, str]


def _selected_option(select: LexborNode) -> LexborNode | None:
    return select.css_first("option[selected]", default=None) or select.css_first("option", default=None)


def parse_settings(soup: LexborHTMLParser, fields: Sequence[SettingField]) -> GameSettings:
    """설정 페이지의 `<select>`마다 선택된 `<option>`의 표시 텍스트를 뽑는다. 이름 붙인 항목이 없으면 구조가 바뀐 것이다."""
    raw: dict[str, str] = {}
    for select in soup.css("select"):
        name = select.attrs.get("name")
        if not name:
            continue
        option = _selected_option(select)
        if option is not None:
            raw[name] = option.text(strip=True)

    missing = [f.select_name for f in fields if f.select_name not in raw]
    if missing:
        raise ValueError(f"설정 페이지 구조가 바뀌었다 — 못 찾은 항목: {', '.join(missing)}")
    return GameSettings(named={f.attr: raw[f.select_name] for f in fields}, raw=raw)


def labeled(named: Mapping[str, str], fields: Sequence[SettingField]) -> dict[str, str]:
    """data에 내는 모양: 화면 이름 → 값. 내부 이름(play_timing_offset 등)은 모델이 A·B를 거꾸로 짝지은 적이 있다
    (2026-10-20 실기)."""
    return {f.label: named[f.attr] for f in fields if f.attr in named}


def settings_line(named: Mapping[str, str], fields: Sequence[SettingField]) -> str:
    return " / ".join(f"{f.short} {named[f.attr]}" for f in fields if f.attr in named)


def changed_labels(before: Mapping[str, str], after: Mapping[str, str], fields: Sequence[SettingField]) -> list[str]:
    return [f.label for f in fields if before.get(f.attr) != after.get(f.attr)]


def build_update_payload(
    soup: LexborHTMLParser, fields: Sequence[SettingField], changes: Mapping[str, str]
) -> dict[str, str]:
    """설정 페이지의 모든 `<select>`를 지금 선택된 `value`로 채운 폼 바디를 만들고, `changes`(attr → 표시 텍스트)만
    그 텍스트와 같은 `<option>`의 `value`로 바꾼다. 폼 전체를 한 번에 제출하는 방식이라 나머지는 그대로 되돌려 보내야
    안 바뀐다. 모르는 항목이나 선택지에 없는 값은 ValueError(사용자에게 그대로 보여 줄 한글 메시지)."""
    by_attr = {f.attr: f for f in fields}
    selects: dict[str, LexborNode] = {}
    payload: dict[str, str] = {}
    for select in soup.css("select"):
        name = select.attrs.get("name")
        if not name:
            continue
        selects[name] = select
        option = _selected_option(select)
        if option is not None:
            payload[name] = option.attrs.get("value") or ""

    for attr, requested in changes.items():
        field = by_attr.get(attr)
        select = selects.get(field.select_name) if field is not None else None
        if field is None or select is None:
            raise ValueError(f"모르는 설정 항목: {attr}")
        match = next((o for o in select.css("option") if o.text(strip=True) == requested), None)
        if match is None:
            raise ValueError(f"{field.label}에 '{requested}'라는 값은 없어.")
        payload[field.select_name] = match.attrs.get("value") or ""
    return payload


def raise_if_not_applied(
    changes: Mapping[str, str], settings: GameSettings, fields: Sequence[SettingField], net_name: str
) -> None:
    """다시 읽은 값이 요청과 다르면 `ChangeNotAppliedError`."""
    labels = {f.attr: f.label for f in fields}
    missed = [
        f"{labels[attr]}: 요청 {requested}, 지금 {settings.named.get(attr)}"
        for attr, requested in changes.items()
        if settings.named.get(attr) != requested
    ]
    if missed:
        raise ChangeNotAppliedError(f"{net_name}에 반영되지 않았어 — " + "; ".join(missed))
