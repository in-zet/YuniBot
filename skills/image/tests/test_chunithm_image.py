from __future__ import annotations

import importlib.util
import io
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from core.tables import TableBuilder, WorkTable
from core.tool_types import ToolContext
from services.game import table_columns as tc
from services.game.records import PlayerProfile

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_image_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(slots=True, kw_only=True)
class _FakeSongDb:
    jackets: dict[str, str | None] = field(default_factory=dict)
    fallbacks: dict[str, str] = field(default_factory=dict)
    version: str | None = None

    def jacket_for(self, title: str) -> str | None:
        return self.jackets.get(title)

    def jacket_fallback_for(self, title: str) -> str | None:
        return self.fallbacks.get(title)

    def current_version(self) -> str | None:
        return self.version


@dataclass(slots=True, kw_only=True)
class _FakeImageCache:
    available: dict[str, bytes] = field(default_factory=dict)
    requested: list[str | None] = field(default_factory=list)

    async def fetch_many(self, urls: Iterable[str | None]) -> Mapping[str, bytes | None]:
        urls = list(urls)
        self.requested = urls
        return {url: self.available.get(url) for url in urls if url}


@dataclass(slots=True, kw_only=True)
class _FakeRecords:
    profile: PlayerProfile | None = None
    nickname: str | None = None
    profile_calls: list[str] = field(default_factory=list)

    async def player_profile(self, player_id: str) -> PlayerProfile | None:
        self.profile_calls.append(player_id)
        return self.profile

    async def latest_sync_summary(self, player_id: str) -> object | None:
        return SimpleNamespace(nickname=self.nickname) if self.nickname else None


def _table(*, subject: str | None, titles: tuple[str, ...] = ("곡 A", "곡 B"), summary: dict[str, str] | None = None) -> WorkTable:
    builder = TableBuilder(subject=subject, unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY, tc.CONST)
    builder.step("테스트")
    if summary:
        builder.summary(summary)
    for title in titles:
        builder.row(
            tc.chart_ref(title, "MAS"),
            {tc.SONG: tc.song_cell(title), tc.DIFFICULTY: tc.difficulty_cell("MAS"), tc.CONST: tc.const_cell(15.0)},
        )
    return replace(builder.build(), id="t1")


def _ctx(*, songdb: _FakeSongDb, cache: _FakeImageCache, records: _FakeRecords) -> ToolContext:
    return ToolContext(
        player_id="p1",
        tags=frozenset(),
        services={"songdb": songdb, "image_cache": cache, "records": records},
    )


async def test_table_image_returns_png_files_and_marks_table_drawn(skill: ModuleType) -> None:
    songdb = _FakeSongDb(jackets={"곡 A": "a.jpg", "곡 B": "b.jpg"})
    cache = _FakeImageCache(
        available={"https://chunithm-net-eng.com/mobile/img/a.jpg": _png()},
    )
    ctx = _ctx(songdb=songdb, cache=cache, records=_FakeRecords())

    result = await skill.table_image(ctx, _table(subject=None))

    assert result.error is None
    assert result.drew_table == "t1"
    assert result.display is not None
    assert [f.name.endswith(".png") for f in result.display.files] == [True]
    assert result.display.files[0].data.startswith(b"\x89PNG")
    assert result.data["images"] == 1
    assert result.data["rows"] == 2
    assert result.data["missing_jackets"] == 1  # b.jpg는 받지 못했다


async def test_table_image_falls_back_to_chunimaru_jacket_when_chunithm_net_fails(skill: ModuleType) -> None:
    """CHUNITHM-NET 재킷을 못 받으면(사이트 장애, 2026-10-22) Chunimaru 주소로 한 번 더 받는다."""
    songdb = _FakeSongDb(
        jackets={"곡 A": "a.jpg", "곡 B": "b.jpg"}, fallbacks={"곡 A": "https://chunimaru.example/a.webp", "곡 B": "https://chunimaru.example/b.webp"},
    )
    cache = _FakeImageCache(
        available={"https://chunithm-net-eng.com/mobile/img/a.jpg": _png(), "https://chunimaru.example/b.webp": _png()},
    )
    ctx = _ctx(songdb=songdb, cache=cache, records=_FakeRecords())

    result = await skill.table_image(ctx, _table(subject=None))

    assert result.data["missing_jackets"] == 0
    assert cache.requested == ["https://chunimaru.example/b.webp"]  # 받은 곡 A는 대체 주소를 받지 않는다


async def test_table_image_without_subject_does_not_touch_profile(skill: ModuleType) -> None:
    records = _FakeRecords()
    ctx = _ctx(songdb=_FakeSongDb(), cache=_FakeImageCache(), records=records)

    await skill.table_image(ctx, _table(subject=None))

    assert records.profile_calls == []


async def test_table_image_with_subject_fetches_character_and_frame(skill: ModuleType) -> None:
    records = _FakeRecords(
        profile=PlayerProfile(
            level=87, reincarnation_stars=0,
            character_image_url="https://img.test/c.png", character_frame_url="https://img.test/f.png",
            updated_at="2026-10-20T00:00:00",
        ),
        nickname="유니",
    )
    cache = _FakeImageCache(available={"https://img.test/c.png": _png(), "https://img.test/f.png": _png()})
    ctx = _ctx(songdb=_FakeSongDb(), cache=cache, records=records)

    result = await skill.table_image(ctx, _table(subject="p2"))

    assert records.profile_calls == ["p2"]
    assert "https://img.test/c.png" in cache.requested
    assert "https://img.test/f.png" in cache.requested
    assert result.error is None


async def test_table_image_subject_without_profile_still_draws(skill: ModuleType) -> None:
    ctx = _ctx(songdb=_FakeSongDb(), cache=_FakeImageCache(), records=_FakeRecords())

    result = await skill.table_image(ctx, _table(subject="p2"))

    assert result.error is None
    assert result.display is not None and len(result.display.files) == 1


async def test_table_image_splits_many_rows_into_several_files(skill: ModuleType) -> None:
    titles = tuple(f"곡 {i}" for i in range(31))
    ctx = _ctx(songdb=_FakeSongDb(), cache=_FakeImageCache(), records=_FakeRecords())

    result = await skill.table_image(ctx, _table(subject=None, titles=titles), layout=skill.Layout.LIST)

    assert result.display is not None
    assert len(result.display.files) == 2
    assert result.data["images"] == 2


def test_table_image_declares_table_needs(skill: ModuleType) -> None:
    from core.schema import build_arg_schema

    table_arg = next(a for a in build_arg_schema(skill.table_image).args if a.name == "table")
    assert table_arg.table_needs is not None
    assert tc.SONG in table_arg.table_needs.nonempty


def _rating_table(skill: ModuleType, *, with_rating: bool) -> WorkTable:
    builder = TableBuilder(subject=None, unit="채보")
    tc.add_columns(builder, tc.SONG)
    builder.summary({"레이팅": "16.52"} if with_rating else {"평균": "15.0"})
    builder.row(tc.song_ref("곡 A"), {tc.SONG: tc.song_cell("곡 A")})
    return builder.build()


async def test_current_version_goes_to_header_only_for_rating_table(skill: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    real = skill.render.render_pages

    def spy(table: WorkTable, **kwargs: object) -> list[tuple[str, bytes]]:
        seen.append(kwargs["profile"])
        return real(table, **kwargs)

    monkeypatch.setattr(skill.render, "render_pages", spy)
    ctx = _ctx(songdb=_FakeSongDb(version="X-VERSE-X"), cache=_FakeImageCache(), records=_FakeRecords())

    await skill.table_image(ctx, _rating_table(skill, with_rating=True))
    await skill.table_image(ctx, _rating_table(skill, with_rating=False))

    assert seen[0] is not None and seen[0].version == "X-VERSE-X"
    assert seen[1] is None


async def test_table_image_reports_drawn_columns(skill: ModuleType) -> None:
    ctx = _ctx(songdb=_FakeSongDb(), cache=_FakeImageCache(), records=_FakeRecords())

    result = await skill.table_image(ctx, _table(subject=None))

    assert result.data["drawn_columns"] == ["곡", "난이도", "상수"]


async def test_table_image_returns_error_when_extra_columns_do_not_fit(skill: ModuleType) -> None:
    builder = TableBuilder(subject=None, unit="채보")
    extras = (tc.NOTE_COUNT, tc.ARTIST, tc.VERSION, tc.LEVEL, tc.SONG_RAW_NAME)
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY, *extras)
    builder.row(tc.chart_ref("곡 A", "MAS"), {tc.SONG: tc.song_cell("곡 A"), tc.DIFFICULTY: tc.difficulty_cell("MAS")})
    table = replace(builder.build(), id="t1")
    cache = _FakeImageCache()
    ctx = _ctx(songdb=_FakeSongDb(), cache=cache, records=_FakeRecords())

    result = await skill.table_image(ctx, table)

    assert result.error is not None and "table_query" in result.error
    assert result.display is None
    assert cache.requested == []  # 재킷도 받지 않고 바로 돌려준다
