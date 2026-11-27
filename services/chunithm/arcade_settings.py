# services/chunithm/arcade_settings.py
"""CHUNITHM-NET 설정 페이지(`/mobile/home/userOption/updateUserOption`)에서 이름 붙여 다루는 항목.
읽고 쓰는 틀은 게임 공통(`services/game/net_settings.py`). 저장본: docs/reference/chunithm-net/arcade_settings.html"""

from __future__ import annotations

from services.game.net_settings import SettingField

SETTINGS_FIELDS: tuple[SettingField, ...] = (
    SettingField(attr="speed", select_name="speed", label="스피드", short="스피드"),
    SettingField(attr="field_wall_position", select_name="fieldWallPosition", label="필드 월", short="필드 월"),
    SettingField(attr="play_timing_offset", select_name="playTimingOffset", label="판정 타이밍 A", short="판정A"),
    SettingField(attr="judge_timing_offset", select_name="judgeTimingOffset", label="판정 타이밍 B", short="판정B"),
    SettingField(attr="guide_sound", select_name="guideSound", label="가이드음", short="가이드음"),
)
