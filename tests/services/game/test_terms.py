from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.game.terms import TermsService
from services.maimai.profile import MAIMAI

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})

_TERMS_SERVER_MIGRATION = (
    Path("migrations/core/009_terms_server.sql").read_text() + Path("migrations/core/021_terms_aliases.sql").read_text()
    + Path("migrations/core/028_terms_aliases_game.sql").read_text()
)
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


async def test_explain_linked_verse_by_korean_aliases(terms: TermsService) -> None:
    """2026-10-21: 링버를 VERSE 버전으로 오해한 사고. 수록 버전이 아니라 해금 시스템이라고 답해야 한다."""
    for alias in ("링버", "링크드 버스", "링크드버스", "linked verse"):
        explanation = await terms.explain(alias, _CTX)
        assert explanation is not None and explanation.canonical == "Linked VERSE"
        assert "수록 버전 이름이 아니다" in (explanation.meaning or "")
        assert "나무위키" in explanation.source


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


async def test_propose_is_immediately_visible(terms_with_conn: TermsService) -> None:
    """신고제(2026-10-18): 승인을 기다리지 않고 바로 explain에 보인다."""
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=["테스트별칭"], suggested_by="p1"
    )

    explanation = await terms_with_conn.explain("테스트은어", guild_ctx)
    assert explanation is not None
    assert explanation.scope == "이 서버"


async def test_approved_server_term_is_explained_and_listed(terms_with_conn: TermsService) -> None:
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=["테스트별칭"], suggested_by="p1"
    )

    explanation = await terms_with_conn.explain("테스트별칭", guild_ctx)
    assert explanation is not None
    assert explanation.canonical == "테스트은어"
    assert explanation.scope == "이 서버"

    items = await terms_with_conn.list_terms(None, guild_ctx)
    assert any(item.canonical == "테스트은어" for item in items)


async def test_server_term_is_not_visible_in_another_guild(terms_with_conn: TermsService) -> None:
    await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=[], suggested_by="p1"
    )

    other_guild_ctx = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id="g2")
    assert await terms_with_conn.explain("테스트은어", other_guild_ctx) is None


async def test_global_term_wins_over_same_named_server_term(terms_with_conn: TermsService) -> None:
    await terms_with_conn.propose(
        guild_id="g1", term="SSS+", meaning="서버에서 다르게 부르는 뜻", aliases=[], suggested_by="p1"
    )

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


async def test_review_toggles_back_and_forth(terms_with_conn: TermsService) -> None:
    """신고제(2026-10-18): review는 더 이상 "한 번만 심사"가 아니라 켜고 끄는 토글이다 —
    admin이 철회했다가 되돌리는 흐름을 여러 번 반복할 수 있다."""
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="뜻", aliases=[], suggested_by="p1"
    )
    revoked = await terms_with_conn.review(proposal_id, approve=False, reviewed_by="admin1")
    assert revoked is not None and revoked.status == "rejected"

    restored = await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin2")
    assert restored is not None and restored.status == "approved"
    assert restored.reviewed_by == "admin2"

    stored = await terms_with_conn.get_proposal(proposal_id)
    assert stored is not None and stored.status == "approved"


async def test_review_unknown_proposal_returns_none(terms_with_conn: TermsService) -> None:
    assert await terms_with_conn.review("없는id", approve=True, reviewed_by="admin1") is None


def test_rank_scores_maps_canonical_rank_to_base_score(terms: TermsService) -> None:
    scores = terms.rank_scores()
    assert scores["SSS"] == 1_007_500
    assert scores["SSS+"] == 1_009_000
    assert "AJC" not in scores  # 램프는 랭크가 아니다


async def test_saved_alias_resolves_rank_difficulty_and_lamp(terms_with_conn: TermsService) -> None:
    """확인받아 저장한 별칭은 다음부터 묻지 않고 해석된다 — 난이도·랭크·램프 모두."""
    await terms_with_conn.add_alias(kind="난이도", alias="마스터", canonical="MAS", added_by="p1")
    saved = await terms_with_conn.add_alias(kind="랭크", alias="삼에스", canonical="sss", added_by="p1")
    await terms_with_conn.add_alias(kind="램프", alias="올저", canonical="ALL JUSTICE", added_by="p1")

    assert saved == "SSS"
    assert terms_with_conn.difficulty_token("마스터") == "MASTER"
    assert (await terms_with_conn.resolve_rank("삼에스", _CTX)).canonical == "SSS"
    assert (await terms_with_conn.resolve_lamp("올저", _CTX)).canonical == "ALL JUSTICE"


async def test_saved_aliases_load_after_a_restart(terms_with_conn: TermsService) -> None:
    await terms_with_conn.add_alias(kind="rank", alias="삼에스", canonical="SSS", added_by="p1")
    await terms_with_conn.add_alias(kind="artist", alias="랑이", canonical="Some Artist", added_by="p1")

    restarted = TermsService(conn=terms_with_conn._conn)
    await restarted.load_aliases()

    assert (await restarted.resolve_rank("삼에스", _CTX)).canonical == "SSS"
    assert restarted.artist_alias("랑이") == "Some Artist"


async def test_alias_rejects_unknown_canonical_and_meaning_conflicts(terms_with_conn: TermsService) -> None:
    with pytest.raises(ValueError, match="이름이 아니다"):
        await terms_with_conn.add_alias(kind="랭크", alias="삼에스", canonical="ZZZ", added_by="p1")
    with pytest.raises(ValueError, match="이미"):
        await terms_with_conn.add_alias(kind="랭크", alias="SS+", canonical="SSS", added_by="p1")  # 원래 다른 랭크 이름
    await terms_with_conn.add_alias(kind="랭크", alias="삼에스", canonical="SSS", added_by="p1")
    with pytest.raises(ValueError, match="이미"):
        await terms_with_conn.add_alias(kind="랭크", alias="삼에스", canonical="SS+", added_by="p2")
    with pytest.raises(ValueError, match="모르는 별칭 종류"):
        await terms_with_conn.add_alias(kind="곡", alias="x", canonical="y", added_by="p1")


async def test_resaving_the_same_alias_keeps_the_first_owner(terms_with_conn: TermsService) -> None:
    await terms_with_conn.add_alias(kind="랭크", alias="삼에스", canonical="SSS", added_by="p1")
    await terms_with_conn.add_alias(kind="랭크", alias="삼에스", canonical="SSS", added_by="p2")

    row = await terms_with_conn.get_alias(kind="랭크", alias="삼에스")
    assert row is not None and row.added_by == "p1"


async def test_remove_alias_stops_resolving(terms_with_conn: TermsService) -> None:
    await terms_with_conn.add_alias(kind="랭크", alias="삼에스", canonical="SSS", added_by="p1")

    await terms_with_conn.remove_alias(kind="랭크", alias="삼에스")

    with pytest.raises(ResolutionError):
        await terms_with_conn.resolve_rank("삼에스", _CTX)
    assert await terms_with_conn.get_alias(kind="랭크", alias="삼에스") is None


# --- maimai DX ---------------------------------------------------------------


@pytest.fixture
def maimai_terms() -> TermsService:
    return TermsService(MAIMAI.terms_path, game="maimai", percent_scores=MAIMAI.score_is_percent)


async def test_maimai_same_rank_name_has_its_own_value(terms: TermsService, maimai_terms: TermsService) -> None:
    assert (await terms.resolve_rank("SSS", _CTX)).min_score == 1_007_500
    assert (await maimai_terms.resolve_rank("SSS", _CTX)).min_score == 1_000_000
    assert (await maimai_terms.resolve_rank("sss+", _CTX)).min_score == 1_005_000


async def test_maimai_ranks_cover_14_steps_in_descending_order(maimai_terms: TermsService) -> None:
    scores = list(maimai_terms.rank_scores().values())
    assert len(scores) == 14
    assert scores == sorted(scores, reverse=True)


async def test_maimai_difficulty_codes_match_profile(maimai_terms: TermsService) -> None:
    for long_name, code in MAIMAI.long_to_short.items():
        assert (await maimai_terms.resolve_difficulty(code, _CTX)).canonical == long_name
        assert (await maimai_terms.resolve_difficulty(long_name, _CTX)).canonical == long_name


async def test_maimai_lamps_cover_profile_orders(maimai_terms: TermsService) -> None:
    for name in (*MAIMAI.combo_lamp_order, *MAIMAI.chain_lamp_order):
        if name == "NONE":
            continue
        assert (await maimai_terms.resolve_lamp(name, _CTX)).canonical == name


@pytest.mark.parametrize(
    ("text", "expected"),
    [("99.5%", 995_000), ("100.5", 1_005_000), ("99", 990_000), ("100.4999%", 1_004_999), ("SSS+", 1_005_000)],
)
async def test_maimai_scores_are_read_as_achievement(maimai_terms: TermsService, text: str, expected: int) -> None:
    assert await maimai_terms.resolve_score(text, _CTX) == expected


async def test_maimai_explain_rank_uses_percent(maimai_terms: TermsService) -> None:
    explanation = await maimai_terms.explain("SS+", _CTX)
    assert explanation is not None and explanation.meaning == "달성률 99.5000% 이상"


async def test_aliases_are_saved_per_game(terms_with_conn: TermsService) -> None:
    conn = terms_with_conn._conn
    assert conn is not None
    maimai = TermsService(MAIMAI.terms_path, conn=conn, game="maimai", percent_scores=True)
    await terms_with_conn.add_alias(kind="rank", alias="트플", canonical="SSS", added_by="p1")
    await maimai.add_alias(kind="rank", alias="트플", canonical="SSS+", added_by="p1")

    reloaded_chunithm = TermsService(conn=conn)
    reloaded_maimai = TermsService(MAIMAI.terms_path, conn=conn, game="maimai", percent_scores=True)
    await reloaded_chunithm.load_aliases()
    await reloaded_maimai.load_aliases()

    assert (await reloaded_chunithm.resolve_rank("트플", _CTX)).canonical == "SSS"
    assert (await reloaded_maimai.resolve_rank("트플", _CTX)).canonical == "SSS+"
