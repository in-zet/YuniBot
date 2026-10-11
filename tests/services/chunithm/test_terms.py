import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.terms import TermsService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})


@pytest.fixture
def terms() -> TermsService:
    return TermsService()


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
