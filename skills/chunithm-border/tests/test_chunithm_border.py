# skills/chunithm-border/tests/test_chunithm_border.py
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from core.tables import TableBuilder, WorkTable
from core.tool_types import ToolContext
from services.chunithm.terms import ResolvedRank
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart
from services.game.songdb import ChartRecord, SongRecord

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_border_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chart(note_count: int | None) -> ResolvedChart:
    song = SongRecord(title="테스트곡", aliases=(), artist="테스터", id=1)
    return ResolvedChart(song=song, chart=ChartRecord(difficulty="MAS", const=13.0, note_count=note_count))


class _FakeRecords:
    def __init__(self, *, best_score: int | None) -> None:
        self._best_score = best_score

    @staticmethod
    def allowed_attacks(note_count, target_score):
        from services.game.records_calc import allowed_attacks

        return allowed_attacks(note_count, target_score)

    @staticmethod
    def remaining_to_target(note_count, current_score, target_score):
        from services.game.records_calc import remaining_to_target

        return remaining_to_target(note_count, current_score, target_score)

    async def best_score(self, player_id: str, *, song_title: str, difficulty: str):
        return self._best_score


def _ctx(records: _FakeRecords) -> ToolContext:
    return ToolContext(player_id="p1", tags=frozenset(), services={"records": records})


async def test_lookup_without_rank_shows_all_four_borders() -> None:
    skill = _load_skill()
    result = await skill.lookup(_ctx(_FakeRecords(best_score=None)), chart=_chart(3000), rank=None)

    assert result.error is None
    assert set(result.data["borders"]) == {"SSS+", "SSS", "SS+", "SS"}
    assert result.data["borders"]["SSS"] == "14.70"
    assert result.data["borders"]["SSS+"] == "5.88"


async def test_lookup_with_specific_rank() -> None:
    skill = _load_skill()
    result = await skill.lookup(
        _ctx(_FakeRecords(best_score=None)), chart=_chart(3000), rank=ResolvedRank(canonical="SSS+", min_score=1_009_000)
    )

    assert result.data["borders"] == {"SSS+": "5.88"}


async def test_lookup_unknown_note_count_is_not_computable() -> None:
    skill = _load_skill()
    result = await skill.lookup(_ctx(_FakeRecords(best_score=None)), chart=_chart(None), rank=None)

    assert all(v == "계산 불가" for v in result.data["borders"].values())


async def test_remaining_reports_score_only_by_default() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=1_000_000)), chart=_chart(3000),
        rank=ResolvedRank(canonical="SSS", min_score=1_007_500),
    )

    assert result.error is None
    assert result.data["remaining_score"] == 7_500
    assert "remaining_attacks" not in result.data
    assert result.display is not None and "어택" not in (result.display.content or "")
    assert "어택" in (result.display.slash_content or "")  # /남은어택은 어택을 명시한 명령이다


async def test_remaining_adds_attacks_when_asked() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=1_000_000)), chart=_chart(3000),
        rank=ResolvedRank(canonical="SSS", min_score=1_007_500), include_attacks=True,
    )

    assert Decimal(result.data["remaining_attacks"]) > 0
    assert result.display is not None and "어택" in (result.display.content or "")


async def test_remaining_when_already_past_target() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=1_008_000)), chart=_chart(3000),
        rank=ResolvedRank(canonical="SS", min_score=1_000_000),
    )

    assert result.error is None
    assert result.data["already_achieved"] is True
    assert result.data["remaining_score"] <= 0


async def test_remaining_without_any_play_record_is_an_error() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=None)), chart=_chart(3000),
        rank=ResolvedRank(canonical="SSS", min_score=1_007_500),
    )

    assert result.error is not None
    assert "기록" in result.error


async def test_remaining_unknown_note_count_is_an_error() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=1_000_000)), chart=_chart(None),
        rank=ResolvedRank(canonical="SSS", min_score=1_007_500),
    )

    assert result.error is not None
    assert "계산 불가" in result.error


async def test_remaining_locked_without_chunithm_synced_shows_how_to_get_it() -> None:
    """chunithm-synced는 sega-account의 defines_tags가 설명을 소유한다.
    두 스킬을 함께 real SkillRegistry로 읽어야 (1) chunithm-border의 tags=["chunithm-synced"]가
    "정의되지 않은 태그" 검사를 통과하고, (2) describe_lock이 쓸 설명이 채워진다."""
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.registry import SkillRegistry
    from core.resolvers import ResolverRegistry
    from core.tool_types import ChartRef, RankRef

    registry = SkillRegistry([Path("skills/sega-account"), Path("skills/chunithm-border")])
    registry.load()
    assert registry.failures() == {}

    [(skill, spec)] = [(s, sp) for s, sp in registry.tool_specs() if sp.name == "remaining"]

    # 태그 검사는 ChartRef/RankRef 해석보다 뒤에 있어 해석기가 등록돼 있어야 그 지점까지
    # 간다 — 값 자체은 태그 검사가 막아 spec.fn에 안 쓰인다.
    class _StubChartResolver:
        async def resolve(self, raw: str, ctx: ToolContext) -> ResolvedChart:
            return _chart(3000)

    class _StubRankResolver:
        async def resolve(self, raw: str, ctx: ToolContext) -> ResolvedRank:
            return ResolvedRank(canonical="SSS", min_score=1_007_500)

    resolvers = ResolverRegistry()
    resolvers.register(ChartRef, _StubChartResolver())
    resolvers.register(RankRef, _StubRankResolver())

    executor = ToolExecutor(
        resolvers=resolvers,
        tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(),
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(
        spec, ctx,
        {"chart": "테스트곡", "rank": "SSS"},
        skill_tags=skill.manifest.tags, skill_name="chunithm-border",
    )

    assert outcome.result is not None
    assert outcome.result.error is not None
    assert outcome.result.error != "모르는 명령이에요"
    assert "동기화" in outcome.result.error


# ---------------------------------------------------------------- add_borders / add_remaining


class _FakeSongDb:
    def __init__(self, note_counts: dict[str, int | None]) -> None:
        self._songs = {
            title: SongRecord(
                title=title, aliases=(), artist="", id=i,
                charts={"MAS": ChartRecord(difficulty="MAS", const=13.0, note_count=count)},
            )
            for i, (title, count) in enumerate(note_counts.items())
        }

    def song_by_title(self, title: str) -> SongRecord | None:
        return self._songs.get(title)


class _TableRecords(_FakeRecords):
    def __init__(self, scores: dict[str, int]) -> None:
        super().__init__(best_score=None)
        self._scores = scores

    async def pb_rows_for(self, player_id: str, keys):
        from services.game.records import PbRow

        return {
            key: PbRow(
                song_title=key[0], difficulty=key[1], score=self._scores[key[0]], rank="S", clear_lamp="CLEAR",
                combo_lamp="NONE", chain_lamp="NONE", const=13.0, rating=None, session_id=None, updated_at="",
            )
            for key in keys if key[0] in self._scores
        }


def _table_ctx(note_counts: dict[str, int | None], scores: dict[str, int] | None = None) -> ToolContext:
    return ToolContext(
        player_id="p1", tags=frozenset(),
        services={"records": _TableRecords(scores or {}), "songdb": _FakeSongDb(note_counts)},
    )


def _chart_table(*titles: str) -> WorkTable:
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.step("테스트 표")
    for title in titles:
        builder.row(tc.chart_ref(title, "MAS"), {tc.SONG: tc.song_cell(title), tc.DIFFICULTY: tc.difficulty_cell("MAS")})
    return builder.build()


_SSS = ResolvedRank(canonical="SSS", min_score=1_007_500)


async def test_add_borders_adds_allowed_attacks_and_marks_unknown_note_counts() -> None:
    skill = _load_skill()
    ctx = _table_ctx({"곡 A": 3000, "곡 B": None})

    result = await skill.add_borders(ctx, table=_chart_table("곡 A", "곡 B"), rank=_SSS)

    assert result.error is None and result.table is not None
    table = result.table
    assert table.steps == ("테스트 표", "SSS 보더")
    known, unknown = table.rows
    assert known.cell("SSS 허용 어택").raw == 14.7 and known.cell("SSS 허용 어택").text == "14.70"
    assert unknown.cell("SSS 허용 어택").raw is None and unknown.cell("SSS 허용 어택").text == "계산 불가"
    assert result.data == {"rank": "SSS", "count": 2, "unknown": 1}


async def test_add_remaining_reports_remaining_score_per_chart() -> None:
    skill = _load_skill()
    ctx = _table_ctx(
        {"곡 A": 3000, "곡 B": 3000, "곡 C": 3000, "곡 D": None},
        {"곡 A": 1_000_000, "곡 C": 1_008_000, "곡 D": 1_000_000},
    )

    result = await skill.add_remaining(ctx, table=_chart_table("곡 A", "곡 B", "곡 C", "곡 D"), rank=_SSS)

    assert result.error is None and result.table is not None
    name = "SSS까지 남은 점수"
    a, b, c, d = result.table.rows
    assert a.cell(name).raw == 7_500 and a.cell(name).text == "7500점"
    assert b.cell(name).raw is None and b.cell(name).text == "기록 없음"
    assert c.cell(name).raw == 0 and c.cell(name).text == "달성"
    assert d.cell(name).raw is None and d.cell(name).text == "계산 불가"
    assert result.data["with_record"] == 3


async def test_border_tools_keep_rows_and_leave_the_source_table_alone() -> None:
    skill = _load_skill()
    ctx = _table_ctx({"곡 A": 3000}, {"곡 A": 1_000_000})
    source = _chart_table("곡 A")

    borders = await skill.add_borders(ctx, table=source, rank=_SSS)
    both = await skill.add_remaining(ctx, table=borders.table, rank=_SSS)

    assert both.table is not None
    assert both.table.rows[0].ref == source.rows[0].ref
    assert [c.name for c in both.table.columns] == [tc.SONG, tc.DIFFICULTY, "SSS 허용 어택", "SSS까지 남은 점수"]
    assert source.column_by_name("SSS 허용 어택") is None
