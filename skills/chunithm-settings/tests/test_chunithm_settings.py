# skills/chunithm-settings/tests/test_chunithm_settings.py
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest
from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError

from core.events import EventBus
from core.tables import render_text
from core.tool_types import ToolContext
from services.chunithm.arcade_settings import ArcadeSettings
from services.chunithm.songdb import SongRecord

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_settings_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


def _settings(**overrides: str) -> ArcadeSettings:
    named = {
        "speed": "1.00", "field_wall_position": "0", "play_timing_offset": "0.0",
        "judge_timing_offset": "0.0", "guide_sound": "5",
    }
    named.update(overrides)
    return ArcadeSettings(raw={**named, "extraField": "x"}, **named)


@dataclass(slots=True, kw_only=True)
class _FakeChunithmNet:
    settings: ArcadeSettings | None = None
    favorites: list[int] = field(default_factory=list)
    error: Exception | None = None
    update_error: Exception | None = None

    async def get_arcade_settings(self, player_id: str, *, ctx: ToolContext | None = None) -> ArcadeSettings:
        if self.error is not None:
            raise self.error
        assert self.settings is not None
        return self.settings

    async def update_arcade_settings(
        self, player_id: str, *, changes: dict[str, str], ctx: ToolContext | None = None
    ) -> ArcadeSettings:
        if self.update_error is not None:
            raise self.update_error
        if self.error is not None:
            raise self.error
        assert self.settings is not None
        named = {
            "speed": self.settings.speed, "field_wall_position": self.settings.field_wall_position,
            "play_timing_offset": self.settings.play_timing_offset,
            "judge_timing_offset": self.settings.judge_timing_offset, "guide_sound": self.settings.guide_sound,
        }
        named.update(changes)
        self.settings = ArcadeSettings(raw={**named, "extraField": "x"}, **named)
        return self.settings

    async def get_favorite_music(self, player_id: str, *, ctx: ToolContext | None = None) -> list[int]:
        if self.error is not None:
            raise self.error
        return list(self.favorites)

    async def set_favorite_music(self, player_id: str, ids: list[int], *, ctx: ToolContext | None = None) -> None:
        if self.error is not None:
            raise self.error
        self.favorites = list(ids)


@dataclass(slots=True, kw_only=True)
class _FakeSongdb:
    songs_by_id: dict[int, SongRecord] = field(default_factory=dict)

    def by_id(self, song_id: int) -> SongRecord | None:
        return self.songs_by_id.get(song_id)


def _song(title: str, song_id: int) -> SongRecord:
    return SongRecord(title=title, aliases=(), artist="", id=song_id)


def _ctx(*, chunithm_net: _FakeChunithmNet | None = None, songdb: _FakeSongdb | None = None) -> ToolContext:
    services: dict[str, object] = {
        "chunithm_net": chunithm_net or _FakeChunithmNet(), "songdb": songdb or _FakeSongdb(),
    }
    return ToolContext(player_id="p1", tags=frozenset(), services=services, event_bus=EventBus())


# ---------------------------------------------------------------- current_settings


async def test_current_settings_reads_live(skill: ModuleType) -> None:
    ctx = _ctx(chunithm_net=_FakeChunithmNet(settings=_settings(speed="1.50")))

    result = await skill.current_settings(ctx)

    assert result.error is None
    assert result.data["settings"]["스피드"] == "1.50"
    assert result.display is not None and "1.50" in result.display.content
    assert result.display_mode == "required"  # 모델이 값을 다시 옮겨 적다 A·B를 바꿔 말한 적이 있어 표시물로 고정한다


async def test_settings_data_uses_screen_labels_not_internal_names(skill: ModuleType) -> None:
    net = _FakeChunithmNet(settings=_settings(play_timing_offset="0.0", judge_timing_offset="0.8"))

    result = await skill.current_settings(_ctx(chunithm_net=net))

    assert result.data["settings"]["판정 타이밍 A"] == "0.0"
    assert result.data["settings"]["판정 타이밍 B"] == "0.8"
    assert "play_timing_offset" not in result.data["settings"]


@pytest.mark.parametrize(
    "error", [ConnectionError("로그인되어 있지 않다"), MaintenanceError(), AuthenticationError("만료")]
)
async def test_current_settings_reports_read_failures(skill: ModuleType, error: Exception) -> None:
    ctx = _ctx(chunithm_net=_FakeChunithmNet(error=error))

    result = await skill.current_settings(ctx)

    assert result.error is not None


# ---------------------------------------------------------------- change_settings


async def test_change_settings_with_no_arguments_errors(skill: ModuleType) -> None:
    ctx = _ctx(chunithm_net=_FakeChunithmNet(settings=_settings()))

    result = await skill.change_settings(ctx)

    assert result.error is not None


async def test_change_settings_updates_only_requested_fields(skill: ModuleType) -> None:
    net = _FakeChunithmNet(settings=_settings(speed="1.00", guide_sound="5"))
    ctx = _ctx(chunithm_net=net)

    result = await skill.change_settings(ctx, speed="9.00")

    assert result.error is None
    assert result.data["changed"] == ["스피드"]
    assert result.data["settings"]["스피드"] == "9.00"
    assert result.data["settings"]["가이드음"] == "5"
    assert result.display_mode == "required"


async def test_change_settings_unknown_value_returns_error(skill: ModuleType) -> None:
    net = _FakeChunithmNet(settings=_settings(), update_error=ValueError("스피드에 '존재안함'이라는 값은 없어."))
    ctx = _ctx(chunithm_net=net)

    result = await skill.change_settings(ctx, speed="존재안함")

    assert result.error is not None and "존재안함" in result.error


@pytest.mark.parametrize(
    "error", [ConnectionError("로그인되어 있지 않다"), MaintenanceError(), AuthenticationError("만료")]
)
async def test_change_settings_reports_failures(skill: ModuleType, error: Exception) -> None:
    ctx = _ctx(chunithm_net=_FakeChunithmNet(settings=_settings(), error=error))

    result = await skill.change_settings(ctx, speed="9.00")

    assert result.error is not None


# ---------------------------------------------------------------- list_favorites


async def test_list_favorites_resolves_titles_by_id(skill: ModuleType) -> None:
    net = _FakeChunithmNet(favorites=[3, 999])
    songdb = _FakeSongdb(songs_by_id={3: _song("B.B.K.K.B.K.K.", 3)})
    ctx = _ctx(chunithm_net=net, songdb=songdb)

    result = await skill.list_favorites(ctx)

    assert result.error is None
    assert result.data["titles"] == ["B.B.K.K.B.K.K.", "(모르는 곡 id 999)"]
    table = result.table
    assert table is not None and table.unit == "곡"
    known, unknown = table.rows
    assert known.ref.kind == "chunithm.song" and known.cell("곡").raw == "B.B.K.K.B.K.K."
    assert not unknown.cell("곡").filled  # 곡 칸이 필요한 도구에서는 빠진다
    assert unknown.cell("곡 이름(원문)").text == "(모르는 곡 id 999)"
    assert render_text(table).content.splitlines()[-1] == "2. 곡 이름(원문) (모르는 곡 id 999)"


async def test_list_favorites_empty(skill: ModuleType) -> None:
    ctx = _ctx(chunithm_net=_FakeChunithmNet(favorites=[]))

    result = await skill.list_favorites(ctx)

    assert result.error is None
    assert result.data["titles"] == []
    assert result.table is None and result.display is not None


# ---------------------------------------------------------------- add_favorite / remove_favorite


async def test_add_favorite_appends_to_existing_list(skill: ModuleType) -> None:
    net = _FakeChunithmNet(favorites=[3])
    ctx = _ctx(chunithm_net=net)
    song = _song("Chronomia", 42)

    result = await skill.add_favorite(ctx, song=song)

    assert result.error is None
    assert result.data["added"] is True
    assert net.favorites == [3, 42]


async def test_add_favorite_already_present_does_not_duplicate(skill: ModuleType) -> None:
    net = _FakeChunithmNet(favorites=[42])
    ctx = _ctx(chunithm_net=net)
    song = _song("Chronomia", 42)

    result = await skill.add_favorite(ctx, song=song)

    assert result.data["added"] is False
    assert net.favorites == [42]


async def test_remove_favorite_drops_id(skill: ModuleType) -> None:
    net = _FakeChunithmNet(favorites=[3, 42])
    ctx = _ctx(chunithm_net=net)
    song = _song("Chronomia", 42)

    result = await skill.remove_favorite(ctx, song=song)

    assert result.error is None
    assert result.data["removed"] is True
    assert net.favorites == [3]


async def test_remove_favorite_not_present_is_noop(skill: ModuleType) -> None:
    net = _FakeChunithmNet(favorites=[3])
    ctx = _ctx(chunithm_net=net)
    song = _song("Chronomia", 42)

    result = await skill.remove_favorite(ctx, song=song)

    assert result.data["removed"] is False
    assert net.favorites == [3]
