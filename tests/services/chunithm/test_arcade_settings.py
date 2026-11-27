# tests/services/chunithm/test_arcade_settings.py
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser

from services.chunithm.arcade_settings import SETTINGS_FIELDS
from services.game.net_settings import build_update_payload as _build
from services.game.net_settings import parse_settings
from services.game.net_settings import settings_line as _line

_HTML = Path("docs/reference/chunithm-net/arcade_settings.html").read_text(encoding="utf-8")


def test_parses_named_fields_from_saved_settings_page() -> None:
    settings = parse_settings(LexborHTMLParser(_HTML), SETTINGS_FIELDS)

    assert settings.named["speed"] == "8.25"
    assert settings.named["field_wall_position"] == "3"
    assert settings.named["judge_timing_offset"] == "0.8"
    assert settings.named["guide_sound"] == "1"


def test_raw_contains_every_select_on_the_page() -> None:
    settings = parse_settings(LexborHTMLParser(_HTML), SETTINGS_FIELDS)

    assert len(settings.raw) == 28
    assert settings.raw["speed"] == "8.25"
    # 이름 붙인 다섯 개뿐 아니라 설계서가 "나머지"로 두기로 한 항목도 들어 있다.
    assert "judgeCritical" in settings.raw


def test_missing_named_field_raises() -> None:
    html = "<html><body><select name=\"speed\"><option value=\"0\" selected>1.00</option></select></body></html>"

    with pytest.raises(ValueError, match="못 찾은 항목"):
        parse_settings(LexborHTMLParser(html), SETTINGS_FIELDS)


def test_named_from_settings_and_settings_line() -> None:
    settings = parse_settings(LexborHTMLParser(_HTML), SETTINGS_FIELDS)
    named = settings.named

    assert named == {
        "speed": "8.25", "field_wall_position": "3", "play_timing_offset": "0.0",
        "judge_timing_offset": "0.8", "guide_sound": "1",
    }
    assert _line(named, SETTINGS_FIELDS) == "스피드 8.25 / 필드 월 3 / 판정A 0.0 / 판정B 0.8 / 가이드음 1"


def test_build_update_payload_keeps_untouched_fields_and_changes_requested_ones() -> None:
    soup = LexborHTMLParser(_HTML)

    payload = _build(soup, SETTINGS_FIELDS, {"speed": "9.00", "guide_sound": "5"})

    # speed 9.00과 guideSound 5에 해당하는 <option value>로 바뀐다.
    assert payload["speed"] == "32"
    assert payload["guideSound"] == "5"
    # 요청하지 않은 항목은 지금 선택된 value 그대로 — fieldWallPosition=3은 option value="3".
    assert payload["fieldWallPosition"] == "3"
    assert payload["judgeTimingOffset"] == "28"
    # 이름 붙은 다섯 개 말고 다른 설정도 전부 폼에 실린다.
    assert payload["judgeCritical"] == "2"


def test_build_update_payload_unknown_display_value_raises() -> None:
    soup = LexborHTMLParser(_HTML)

    with pytest.raises(ValueError, match="가이드음"):
        _build(soup, SETTINGS_FIELDS, {"guide_sound": "존재하지않는값"})


def test_build_update_payload_unknown_attr_raises() -> None:
    soup = LexborHTMLParser(_HTML)

    with pytest.raises(ValueError, match="모르는 설정 항목"):
        _build(soup, SETTINGS_FIELDS, {"not_a_real_field": "1"})
