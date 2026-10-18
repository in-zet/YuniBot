import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.chart_ref import ChartRefResolver
from services.chunithm.songdb import SongDbService
from services.chunithm.terms import TermsService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})


@pytest.fixture
def resolver() -> ChartRefResolver:
    return ChartRefResolver(songdb=SongDbService(), terms=TermsService())


async def test_difficulty_suffix_is_parsed_out(resolver: ChartRefResolver) -> None:
    resolved = await resolver.resolve("B.B.K.K.B.K.K. 마스", _CTX)
    assert resolved.song.title == "B.B.K.K.B.K.K."
    assert resolved.chart.difficulty == "MAS"


async def test_difficulty_prefix_is_parsed_out(resolver: ChartRefResolver) -> None:
    resolved = await resolver.resolve("마스 B.B.K.K.B.K.K.", _CTX)
    assert resolved.song.title == "B.B.K.K.B.K.K."
    assert resolved.chart.difficulty == "MAS"


async def test_no_difficulty_token_defaults_to_master(resolver: ChartRefResolver) -> None:
    resolved = await resolver.resolve("B.B.K.K.B.K.K.", _CTX)
    assert resolved.chart.difficulty == "MAS"


async def test_missing_difficulty_on_song_raises(resolver: ChartRefResolver) -> None:
    # "B.B.K.K.B.K.K. 울티마"는 실제 시드에서 ULT 채보가 존재해(const 13.7,
    # charts = BAS/ADV/EXP/MAS/ULT/WE) 이 테스트의 전제(그 난이도가 없다)가
    # 거짓이 된다. "前前前世"는 확인된 단일, 중복 없는, available
    # 엔트리로 charts가 BAS/ADV/EXP/MAS뿐이라 ULT/WE가 실제로 없다 — 테스트
    # 의도(진짜로 없는 난이도를 물으면 raise)를 그대로 유지하면서 실제 시드
    # 데이터와 어긋나지 않는 조합으로 교체한다.
    with pytest.raises(ResolutionError, match="그 난이도 없음"):
        await resolver.resolve("前前前世 울티마", _CTX)


async def test_unknown_song_raises_not_found(resolver: ChartRefResolver) -> None:
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await resolver.resolve("이세상에없는곡절대", _CTX)


async def test_song_title_starting_with_difficulty_like_word_resolves_directly(
    resolver: ChartRefResolver,
) -> None:
    # "WORLD'S END"의 별칭에 bare "WE"가 있어, "We Are Us"처럼 실제 제목이
    # 난이도 토큰처럼 보이는 단어로 시작하는 곡이 있다. 토큰을 먼저 떼어내기
    # 전에 원문 전체를 곡 이름으로 먼저 풀어야 이런 곡도 (난이도 없음 →
    # MASTER 기본값으로) 올바르게 찾을 수 있다.
    resolved = await resolver.resolve("We Are Us", _CTX)
    assert resolved.song.title == "We Are Us"
    assert resolved.chart.difficulty == "MAS"


async def test_unavailable_individual_chart_raises_even_when_song_itself_is_available(
    resolver: ChartRefResolver,
) -> None:
    # 실제 시드: "DRAGONLADY"는 id=19(available, BAS/ADV/EXP/MAS)와
    # id=8067(available=false, removed=true, WE)로 나뉘어 있다. 곡은 찾을 수 있지만
    # WE 채보는 삭제된 엔트리에서만 왔으므로 고를 수 없어야 한다.
    resolved = await resolver.resolve("DRAGONLADY 마스", _CTX)
    assert resolved.chart.difficulty == "MAS"
    with pytest.raises(ResolutionError, match="그 난이도 없음"):
        await resolver.resolve("DRAGONLADY 월엔", _CTX)


async def test_chunimaru_reported_chart_becomes_available() -> None:
    # Chunimaru(국제판 목록)가 그 채보를 알려 주면 시드에서 삭제로 표시돼 있어도 고를 수 있다.
    songdb = SongDbService()
    songdb._apply_chunimaru(
        {"songs": [{"title": "DRAGONLADY", "charts": {"WE": {"const": None, "isConstUnknown": True}}}]}
    )
    resolver = ChartRefResolver(songdb=songdb, terms=TermsService())
    resolved = await resolver.resolve("DRAGONLADY 월엔", _CTX)
    assert resolved.chart.difficulty == "WE"
