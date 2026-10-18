# tests/services/chunithm/test_arcade_settings.py
from pathlib import Path

import pytest
from selectolax.lexbor import LexborHTMLParser

from services.chunithm.arcade_settings import parse_arcade_settings

_HTML = Path("docs/reference/chunithm-net/arcade_settings.html").read_text(encoding="utf-8")


def test_parses_named_fields_from_saved_settings_page() -> None:
    settings = parse_arcade_settings(LexborHTMLParser(_HTML))

    assert settings.speed == "8.25"
    assert settings.field_wall_position == "3"
    assert settings.judge_timing_offset == "0.8"
    assert settings.guide_sound == "1"


def test_raw_contains_every_select_on_the_page() -> None:
    settings = parse_arcade_settings(LexborHTMLParser(_HTML))

    assert len(settings.raw) == 28
    assert settings.raw["speed"] == "8.25"
    # 이름 붙인 다섯 개뿐 아니라 설계서가 "나머지"로 두기로 한 항목도 들어 있다.
    assert "judgeCritical" in settings.raw


def test_missing_named_field_raises() -> None:
    html = "<html><body><select name=\"speed\"><option value=\"0\" selected>1.00</option></select></body></html>"

    with pytest.raises(ValueError, match="못 찾은 항목"):
        parse_arcade_settings(LexborHTMLParser(html))
