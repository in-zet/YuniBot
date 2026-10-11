from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.terms import TermsService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})

_TERMS_SERVER_MIGRATION = Path("migrations/core/009_terms_server.sql").read_text()
_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)


@pytest.fixture
def terms() -> TermsService:
    return TermsService()


@pytest.fixture
async def terms_with_conn() -> AsyncIterator[TermsService]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION + _TERMS_SERVER_MIGRATION)
    await conn.commit()
    try:
        yield TermsService(conn=conn)
    finally:
        await conn.close()


async def test_resolve_rank_canonical_name(terms: TermsService) -> None:
    resolved = await terms.resolve_rank("SSS+", _CTX)
    assert resolved.canonical == "SSS+"
    assert resolved.min_score == 1_009_000


async def test_resolve_rank_korean_alias(terms: TermsService) -> None:
    resolved = await terms.resolve_rank("트플플", _CTX)
    assert resolved.canonical == "SSS+"
    assert resolved.min_score == 1_009_000


async def test_resolve_rank_case_insensitive_canonical(terms: TermsService) -> None:
    resolved = await terms.resolve_rank("sss", _CTX)
    assert resolved.canonical == "SSS"
    assert resolved.min_score == 1_007_500


async def test_resolve_rank_unknown_raises_with_no_candidates(terms: TermsService) -> None:
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await terms.resolve_rank("이상한말", _CTX)


async def test_resolve_difficulty_korean_alias(terms: TermsService) -> None:
    resolved = await terms.resolve_difficulty("마스", _CTX)
    assert resolved.canonical == "MASTER"


async def test_resolve_difficulty_short_code(terms: TermsService) -> None:
    resolved = await terms.resolve_difficulty("MAS", _CTX)
    assert resolved.canonical == "MASTER"


async def test_resolve_difficulty_unknown_raises(terms: TermsService) -> None:
    with pytest.raises(ResolutionError):
        await terms.resolve_difficulty("모르는난이도", _CTX)


async def test_resolve_lamp_ajc_has_min_score(terms: TermsService) -> None:
    resolved = await terms.resolve_lamp("이론치", _CTX)
    assert resolved.canonical == "AJC"
    assert resolved.min_score == 1_010_000


async def test_resolve_lamp_full_combo_has_no_min_score(terms: TermsService) -> None:
    resolved = await terms.resolve_lamp("풀콤", _CTX)
    assert resolved.canonical == "FULL COMBO"
    assert resolved.min_score is None


async def test_resolve_score_plain_comma_number(terms: TermsService) -> None:
    assert await terms.resolve_score("1,007,500", _CTX) == 1_007_500


async def test_resolve_score_k_shorthand(terms: TermsService) -> None:
    assert await terms.resolve_score("1007.5k", _CTX) == 1_007_500


async def test_resolve_score_korean_number_words(terms: TermsService) -> None:
    assert await terms.resolve_score("100만7천5백", _CTX) == 1_007_500


async def test_resolve_score_korean_number_words_with_spaces(terms: TermsService) -> None:
    assert await terms.resolve_score("100만 7천 5백", _CTX) == 1_007_500


async def test_resolve_score_rank_name(terms: TermsService) -> None:
    assert await terms.resolve_score("트플플", _CTX) == 1_009_000


async def test_resolve_score_ajc_lamp_name(terms: TermsService) -> None:
    assert await terms.resolve_score("이론치", _CTX) == 1_010_000


async def test_resolve_score_unparseable_raises(terms: TermsService) -> None:
    with pytest.raises(ResolutionError, match="읽을 수 없는 점수"):
        await terms.resolve_score("풀콤", _CTX)  # 점수가 안 고정된 램프는 ScoreRef로 못 씀


async def test_difficulty_token_recognizes_alias_and_short_code(terms: TermsService) -> None:
    assert terms.difficulty_token("마스") == "MASTER"
    assert terms.difficulty_token("MAS") == "MASTER"
    assert terms.difficulty_token("크로노") is None


async def test_all_rank_values_match_vendored_rank_min_score(terms: TermsService) -> None:
    """terms.yaml에 손으로 적은 기준 점수(14개 전부, 별칭 없는 8개 포함)가
    vendored chuni_penguin.types.Rank와 어긋나면 여기서 바로 걸린다 — terms.yaml이
    값의 유일한 출처지만, 숫자 자체가 틀리면 레이팅·보더 계산 전체가 틀어지므로
    한 번은 교차 검증해 둔다."""
    from chuni_penguin.types import Rank

    for rank in Rank:
        canonical = str(rank)
        resolved = await terms.resolve_rank(canonical, _CTX)
        assert resolved.min_score == rank.min_score, canonical


async def test_ajc_value_matches_max_score(terms: TermsService) -> None:
    resolved = await terms.resolve_lamp("AJC", _CTX)
    assert resolved.min_score == 1_010_000


async def test_explain_rank_synthesizes_meaning_from_value(terms: TermsService) -> None:
    explanation = await terms.explain("트플플", _CTX)
    assert explanation is not None
    assert explanation.canonical == "SSS+"
    assert explanation.category_label == "랭크"
    assert explanation.meaning is not None and "1,009,000" in explanation.meaning
    assert explanation.scope == "전역"


async def test_explain_concept_term_from_yaml(terms: TermsService) -> None:
    explanation = await terms.explain("비비기", _CTX)
    assert explanation is not None
    assert explanation.canonical == "안미츠"
    assert explanation.category_label == "기술"
    assert "저스티스 크리티컬" in (explanation.meaning or "")


async def test_explain_unknown_term_returns_none(terms: TermsService) -> None:
    assert await terms.explain("이런용어는없다", _CTX) is None


async def test_list_terms_filters_by_category(terms: TermsService) -> None:
    items = await terms.list_terms("랭크", _CTX)
    assert items
    assert all(item.category_label == "랭크" for item in items)
    assert len(items) == 14  # terms.yaml의 랭크 개수


async def test_list_terms_unknown_category_raises(terms: TermsService) -> None:
    with pytest.raises(ValueError, match="모르는 분류"):
        await terms.list_terms("없는분류", _CTX)


async def test_list_terms_without_category_returns_everything(terms: TermsService) -> None:
    all_items = await terms.list_terms(None, _CTX)
    rank_items = await terms.list_terms("랭크", _CTX)
    assert len(all_items) > len(rank_items)


async def test_propose_and_explain_before_approval_is_not_visible(terms_with_conn: TermsService) -> None:
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=["테스트별칭"], suggested_by="p1"
    )

    assert await terms_with_conn.explain("테스트은어", guild_ctx) is None


async def test_approved_server_term_is_explained_and_listed(terms_with_conn: TermsService) -> None:
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=["테스트별칭"], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    explanation = await terms_with_conn.explain("테스트별칭", guild_ctx)
    assert explanation is not None
    assert explanation.canonical == "테스트은어"
    assert explanation.scope == "이 서버"

    items = await terms_with_conn.list_terms(None, guild_ctx)
    assert any(item.canonical == "테스트은어" for item in items)


async def test_server_term_is_not_visible_in_another_guild(terms_with_conn: TermsService) -> None:
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=[], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    other_guild_ctx = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id="g2")
    assert await terms_with_conn.explain("테스트은어", other_guild_ctx) is None


async def test_global_term_wins_over_same_named_server_term(terms_with_conn: TermsService) -> None:
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="SSS+", meaning="서버에서 다르게 부르는 뜻", aliases=[], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    explanation = await terms_with_conn.explain("SSS+", guild_ctx)
    assert explanation is not None
    assert explanation.scope == "전역"  # 전역 랭크가 이긴다


async def test_review_reject_keeps_term_invisible(terms_with_conn: TermsService) -> None:
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=[], suggested_by="p1"
    )
    rejected = await terms_with_conn.review(proposal_id, approve=False, reviewed_by="admin1")

    assert rejected is not None and rejected.status == "rejected"
    assert await terms_with_conn.explain("테스트은어", guild_ctx) is None


async def test_review_already_reviewed_proposal_returns_none(terms_with_conn: TermsService) -> None:
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="뜻", aliases=[], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    second = await terms_with_conn.review(proposal_id, approve=False, reviewed_by="admin1")
    assert second is None
