# tests/services/maimai/test_parser.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from services.maimai import parser
from services.maimai.parser import JST, MaimaiParseError

_DIR = Path("docs/reference/maimai-net")


def _html(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


def test_profile_comes_from_the_top_block_of_a_page() -> None:
    profile = parser.parse_profile(_html("rating_target.html"))

    assert (profile.name, profile.rating) == ("テストプレイヤー", 8564)
    assert profile.icon_url == "https://maimaidx-eng.com/maimai-mobile/img/Icon/0000000000000000.png"


def test_profile_of_a_page_without_the_block_is_a_parse_error() -> None:
    with pytest.raises(MaimaiParseError):
        parser.parse_profile("<html><body>점검</body></html>")


def test_rating_targets_split_new_and_others_and_ignore_selection_sections() -> None:
    targets = parser.parse_rating_targets(_html("rating_target.html"))

    assert [e.title for e in targets.new] == ["テトリス", "Restricted Access", "Unwelcome School", "うい麦畑でつかまえて"]
    assert [e.title for e in targets.old] == ["神っぽいな", "きゅびびびびずむ", "ラビットホール"]
    first = targets.new[0]
    assert (first.chart_type, first.difficulty, first.level, first.achievement, first.rank) == (
        "DX", "MAS", "12", 993336, "SS",
    )
    assert first.dx_score is None  # 이 페이지에는 DX 점수·램프가 없다


def test_achievement_is_an_integer_of_ten_thousandths() -> None:
    targets = parser.parse_rating_targets(_html("rating_target.html"))

    assert [e.achievement for e in targets.new] == [993336, 980394, 985283, 816324]
    assert parser._achievement("100.5000%") == 1_005_000
    assert parser._achievement("\xa0 0.0100%") == 100


def test_music_list_reads_score_dx_score_and_version_header() -> None:
    entries = parser.parse_music_list(_html("music_master.html"))

    assert len(entries) == 7
    first = entries[0]
    assert (first.title, first.chart_type, first.difficulty, first.level) == ("Tell Your World", "STD", "MAS", "12")
    assert (first.achievement, first.rank) == (940693, "AAA")
    assert (first.dx_score, first.dx_score_max) == (827, 1152)
    assert first.version_label == "maimai PLUS"
    assert (first.combo_lamp, first.sync_lamp) == ("", "")


def test_music_list_tells_std_and_dx_apart_by_the_kind_icon() -> None:
    entries = parser.parse_music_list(_html("music_master.html"))

    assert [e.chart_type for e in entries] == ["STD"] * 5 + ["DX"] * 2


def test_re_master_page_has_its_own_difficulty() -> None:
    (entry,) = parser.parse_music_list(_html("music_remaster.html"))

    assert entry.difficulty == "REM"


def test_unplayed_songs_have_no_score_block_and_are_skipped() -> None:
    assert parser.parse_music_list(_html("music_utage.html")) == []
    assert parser.parse_music_list(_html("music_empty.html")) == []


_UTAGE_PLAYED = """
<div class="screw_block m_15 f_15 p_s">BUDDiES PLUS</div>
<div class="music__score_back pointer w_450 m_15 p_3 f_0"><form>
<img src="x/diff_.png"><img src="x/music_standard.png" class="music_kind_icon f_r"/>
<div class="music_lv_block f_r t_c f_14">13</div>
<div class="music_name_block t_l f_13 break">[奏]ヒトガタ</div>
<div class="music_score_block w_112 t_r f_l f_12">101.0000%</div>
<div class="music_score_block w_190 t_r f_l f_12"><img src="x/deluxscore.png"/>1,200 / 1,500</div>
<img src="x/music_icon_back.png?ver=1" class="h_30 f_r"/>
<img src="x/music_icon_app.png?ver=1" class="h_30 f_r"/>
<img src="x/music_icon_sssp.png?ver=1" class="h_30 f_r"/>
</form></div>
"""


def test_utage_difficulty_class_is_empty_and_means_uta() -> None:
    (entry,) = parser.parse_music_list(_UTAGE_PLAYED)

    assert (entry.title, entry.difficulty, entry.chart_type) == ("[奏]ヒトガタ", "UTA", "STD")
    assert (entry.achievement, entry.rank, entry.combo_lamp) == (1_010_000, "SSS+", "AP+")


@pytest.mark.parametrize(
    ("icon", "expected"),
    [("fc", "FC"), ("fcp", "FC+"), ("ap", "AP"), ("app", "AP+"), ("fc_dummy", ""), ("back", "")],
)
def test_combo_lamp_icons(icon: str, expected: str) -> None:
    assert parser._lamp(icon, parser._COMBO_BY_ICON) == expected


@pytest.mark.parametrize(
    ("icon", "expected"),
    [("sync", "SYNC"), ("fs", "FS"), ("fsp", "FS+"), ("fdx", "FDX"), ("fdxp", "FDX+"), ("fsd", "FDX"),
     ("fsdplus", "FDX+"), ("fsplus", "FS+"), ("sync_dummy", ""), ("back", "")],
)
def test_sync_lamp_icons_accept_both_spellings(icon: str, expected: str) -> None:
    assert parser._lamp(icon, parser._SYNC_BY_ICON) == expected


@pytest.mark.parametrize(
    ("icon", "expected"),
    [("sssp", "SSS+"), ("sssplus", "SSS+"), ("splus", "S+"), ("sp", "S+"), ("aa", "AA"), ("d", "D")],
)
def test_rank_icons(icon: str, expected: str) -> None:
    assert parser._rank(parser._normalized(icon)) == expected


def test_unknown_rank_icon_is_a_parse_error() -> None:
    with pytest.raises(MaimaiParseError):
        parser._rank("zzz")


def test_recent_plays_have_track_time_in_jst_and_dx_score() -> None:
    plays = parser.parse_recent(_html("recent.html"))

    assert len(plays) == 6
    first = plays[0]
    assert first.track == 3
    assert first.played_at == datetime(2026, 10, 24, 13, 38, tzinfo=JST)
    assert (first.title, first.chart_type, first.difficulty, first.level) == ("蒼穹舞楽", "DX", "EXP", "12+")
    assert (first.achievement, first.rank, first.dx_score, first.dx_score_max) == (929762, "AA", 1488, 2064)
    assert first.new_record and (first.combo_lamp, first.sync_lamp) == ("", "")


def test_recent_plays_include_standard_charts() -> None:
    plays = parser.parse_recent(_html("recent.html"))

    assert [p.chart_type for p in plays] == ["DX", "DX", "DX", "DX", "STD", "STD"]


def test_recent_play_lamps_are_read_from_the_two_result_icons() -> None:
    html = _html("recent.html").replace("fc_dummy.png", "app.png", 1).replace("sync_dummy.png", "fsdp.png", 1)

    first = parser.parse_recent(html)[0]

    assert (first.combo_lamp, first.sync_lamp) == ("AP+", "FDX+")


def test_error_code_is_read_from_the_error_page_only() -> None:
    assert parser.error_code(_html("error_session.html")) == 100001
    assert parser.error_code(_html("recent.html")) is None
