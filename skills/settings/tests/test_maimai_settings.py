# skills/settings/tests/test_maimai_settings.py
"""settings의 maimai DX 동작(2026-11-27, plan 6-8). 서비스는 maimai DX NET 자리에 낀 가짜다."""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

from core.events import EventBus
from core.tool_types import ToolContext
from services.game.net_settings import GameSettings
from services.game.songdb import SongRecord

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


@pytest.fixture
def skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_settings_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_NAMED = {
    "speed": "5.75", "touch_speed": "5.50", "slide_timing": "0.0",
    "play_timing_offset": "0.0", "judge_timing_offset": "0.6", "guide_sound": "OFF",
}


@dataclass(slots=True, kw_only=True)
class _FakeMaimaiNet:
    named: dict[str, str] = field(default_factory=lambda: dict(_NAMED))
    favorites: list[str] = field(default_factory=list)
    writes: list[list[str]] = field(default_factory=list)

    async def get_arcade_settings(self, player_id: str, *, ctx: ToolContext | None = None) -> GameSettings:
        return GameSettings(named=dict(self.named), raw={})

    async def update_arcade_settings(
        self, player_id: str, *, changes: dict[str, str], ctx: ToolContext | None = None
    ) -> GameSettings:
        self.named.update(changes)
        return GameSettings(named=dict(self.named), raw={})

    async def get_favorite_music(self, player_id: str, *, ctx: ToolContext | None = None) -> list[str]:
        return list(self.favorites)

    async def set_favorite_music(self, player_id: str, titles: list[str], *, ctx: ToolContext | None = None) -> None:
        self.writes.append(list(titles))
        self.favorites = list(titles)


@dataclass(slots=True, kw_only=True)
class _FakeSongdb:
    songs: dict[str, SongRecord] = field(default_factory=dict)

    def song_by_title(self, title: str) -> SongRecord | None:
        return self.songs.get(title)


def _ctx(net: _FakeMaimaiNet, songdb: _FakeSongdb | None = None) -> ToolContext:
    return ToolContext(
        player_id="p1", tags=frozenset(), game="maimai", event_bus=EventBus(),
        services={"chunithm_net": net, "songdb": songdb or _FakeSongdb()},
    )


async def test_current_settings_uses_maimai_labels(skill: ModuleType) -> None:
    result = await skill.current_settings(_ctx(_FakeMaimaiNet()))

    assert result.data["settings"]["노트 속도"] == "5.75" and result.data["settings"]["터치 속도"] == "5.50"
    assert result.display is not None
    assert result.display.content == "노트 속도 5.75 / 터치 속도 5.50 / 슬라이드 0.0 / 판정A 0.0 / 판정B 0.6 / 가이드음 OFF"


async def test_change_settings_takes_maimai_only_fields(skill: ModuleType) -> None:
    net = _FakeMaimaiNet()

    result = await skill.change_settings(_ctx(net), touch_speed="6.00", judge_timing_offset="0.5")

    assert result.error is None
    assert result.data["changed"] == ["터치 속도", "판정 타이밍 B"]
    assert net.named["touch_speed"] == "6.00"


async def test_change_settings_refuses_a_chunithm_only_field(skill: ModuleType) -> None:
    net = _FakeMaimaiNet()

    result = await skill.change_settings(_ctx(net), field_wall_position="3")

    assert result.error == "maimai DX 설정에는 필드 월 항목이 없어."
    assert net.named == _NAMED


async def test_favorites_go_by_title(skill: ModuleType) -> None:
    song = SongRecord(title="AiAe", aliases=(), artist="", id=123)
    net = _FakeMaimaiNet(favorites=["テトリス"])
    songdb = _FakeSongdb(songs={"AiAe": song, "テトリス": SongRecord(title="テトリス", aliases=(), artist="", id=7)})

    added = await skill.add_favorite(_ctx(net, songdb), song=song)
    again = await skill.add_favorite(_ctx(net, songdb), song=song)
    listed = await skill.list_favorites(_ctx(net, songdb))

    assert added.data == {"title": "AiAe", "added": True} and net.writes == [["テトリス", "AiAe"]]
    assert again.data == {"title": "AiAe", "added": False} and len(net.writes) == 1
    assert listed.data["titles"] == ["テトリス", "AiAe"]


async def test_list_favorites_shows_a_title_missing_from_the_song_db(skill: ModuleType) -> None:
    result = await skill.list_favorites(_ctx(_FakeMaimaiNet(favorites=["모르는 곡"])))

    assert result.data["titles"] == ["모르는 곡"]
    assert result.table is not None and result.table.rows[0].cell("곡 이름(원문)").text == "모르는 곡"


async def test_remove_and_clear_favorites_go_by_title(skill: ModuleType) -> None:
    from core.tables import TableBuilder
    from services.game import table_columns as tc

    songs = {title: SongRecord(title=title, aliases=(), artist="", id=i) for i, title in enumerate(["A", "B", "C"])}
    net = _FakeMaimaiNet(favorites=["A", "B", "C"])
    builder = TableBuilder(unit="곡")
    tc.add_columns(builder, tc.SONG)
    builder.step("즐겨찾기")
    for title in ("A", "C"):
        builder.row(tc.song_ref(title), {tc.SONG: tc.song_cell(title)})

    removed = await skill.remove_favorites(_ctx(net, _FakeSongdb(songs=songs)), builder.build())
    cleared = await skill.clear_favorites(_ctx(net, _FakeSongdb(songs=songs)))

    assert removed.data["titles"] == ["A", "C"] and net.writes[0] == ["B"]
    assert cleared.data == {"count": 1} and net.writes[1] == [] and net.favorites == []
