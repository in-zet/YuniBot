# skills/chunithm-border/tests/test_chunithm_border.py
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from core.tool_types import ToolContext
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.songdb import ChartRecord, SongRecord
from services.chunithm.terms import ResolvedRank

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_border_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chart(note_count: int | None) -> ResolvedChart:
    song = SongRecord(title="테스트곡", aliases=(), artist="테스터")
    return ResolvedChart(song=song, chart=ChartRecord(difficulty="MAS", const=13.0, note_count=note_count))


class _FakeRecords:
    def __init__(self, *, best_score: int | None) -> None:
        self._best_score = best_score

    @staticmethod
    def allowed_attacks(note_count, target_score):
        from services.chunithm.records_calc import allowed_attacks

        return allowed_attacks(note_count, target_score)

    @staticmethod
    def remaining_to_target(note_count, current_score, target_score):
        from services.chunithm.records_calc import remaining_to_target

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
    """chunithm-synced는 chunithm-account의 defines_tags가 설명을 소유한다.
    두 스킬을 함께 real SkillRegistry로 읽어야 (1) chunithm-border의 tags=["chunithm-synced"]가
    "정의되지 않은 태그" 검사를 통과하고, (2) describe_lock이 쓸 설명이 채워진다."""
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.registry import SkillRegistry
    from core.resolvers import ResolverRegistry
    from core.tool_types import ChartRef, RankRef

    registry = SkillRegistry([Path("skills/chunithm-account"), Path("skills/chunithm-border")])
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
