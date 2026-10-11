import json
from pathlib import Path

import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.songdb import SongDbService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})
_CHUNIMARU_SAMPLE = Path("docs/reference/chunimaru/songdb_sample.json")


@pytest.fixture
def songdb() -> SongDbService:
    return SongDbService()


async def test_resolve_by_exact_title(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.title == "B.B.K.K.B.K.K."
    assert "MAS" in song.charts


async def test_resolve_by_alias_case_insensitive(songdb: SongDbService) -> None:
    song = await songdb.resolve("RFTS", _CTX)
    assert song.title == "Reach For The Stars"


async def test_resolve_unknown_raises_not_found(songdb: SongDbService) -> None:
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("이세상에없는곡이름절대", _CTX)


async def test_resolve_ambiguous_raises_with_candidates(songdb: SongDbService) -> None:
    # 로컬 시드에 같은 부분 문자열을 공유하는 곡이 다수 있을 만한 흔한 단어로 검증한다.
    # ("Grab your sword"는 songdb_sample.json에도 있어 여러 경로에서 재사용 가능)
    with pytest.raises(ResolutionError) as exc_info:
        await songdb.resolve("a", _CTX)  # 매우 흔한 글자 -> 후보 다수
    assert exc_info.value.candidates  # 최소 하나 이상의 후보가 들어있어야 한다


async def test_const_for_and_note_count_for(songdb: SongDbService) -> None:
    const = songdb.const_for("B.B.K.K.B.K.K.", "MAS")
    note_count = songdb.note_count_for("B.B.K.K.B.K.K.", "MAS")
    assert const == 12.5
    assert note_count is not None and note_count > 0


async def test_const_for_unknown_chart_is_none(songdb: SongDbService) -> None:
    assert songdb.const_for("前前前世", "ULT") is None


async def test_before_chunimaru_overlay_removed_song_is_unavailable(songdb: SongDbService) -> None:
    # "天体観測"은 로컬 시드의 available=false, removed=true라 덧씌우기 전에도 안 잡혀야 한다.
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("天体観測", _CTX)


async def test_chunimaru_overlay_marks_songs_available_and_flags_const_unknown(
    songdb: SongDbService,
) -> None:
    chunimaru_data = json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8"))
    songdb._apply_chunimaru(chunimaru_data)

    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.charts["EXP"].is_const_unknown is True

    # 이 trimmed 샘플에 없는 "天体観測"은 여전히 국제판 미수록으로 남아야 한다.
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("天体観測", _CTX)


async def test_chunimaru_overlay_marks_new_songs(songdb: SongDbService) -> None:
    chunimaru_data = json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8"))
    songdb._apply_chunimaru(chunimaru_data)

    song = await songdb.resolve("Ave Mujica", _CTX)
    assert song.is_new is True


async def test_refresh_from_chunimaru_failure_keeps_previous_state(songdb: SongDbService) -> None:
    class _FailingHttp:
        async def get_json(self, url: str) -> None:
            raise RuntimeError("network down")

    # 실패해도 예외가 새지 않고(기동이 안 죽는다), 이전 상태(seed 기준 availability)가 유지된다.
    await songdb.refresh_from_chunimaru(_FailingHttp())
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("天体観測", _CTX)
