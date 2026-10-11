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


async def test_duplicate_same_difficulty_prefers_available_entry(songdb: SongDbService) -> None:
    # "B.B.K.K.B.K.K."의 WE 채보는 시드에 두 번 들어있다: id=8020(available=true,
    # note_count=1051)과, 파일 순서상 더 나중인 id=8022(available=false/removed,
    # note_count=2102). 파일 순서가 아니라 available 여부로 이겨야 하므로
    # 1051이 맞다 — 2102(삭제된 채보)가 나오면 머지가 파일 순서로 덮어쓴 것이다.
    note_count = songdb.note_count_for("B.B.K.K.B.K.K.", "WE")
    assert note_count == 1051


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


async def test_apply_chunimaru_rejects_empty_songs_list(songdb: SongDbService) -> None:
    with pytest.raises(ValueError):
        songdb._apply_chunimaru({"songs": []})


async def test_refresh_from_chunimaru_with_empty_response_keeps_previous_state(
    songdb: SongDbService,
) -> None:
    class _EmptyHttp:
        async def get_json(self, url: str) -> dict[str, object]:
            return {"songs": []}

    await songdb.refresh_from_chunimaru(_EmptyHttp())  # 예외가 새지 않는다
    # 빈 응답으로 가용성이 통째로 지워지면 안 된다.
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.title == "B.B.K.K.B.K.K."


async def test_refresh_from_chunimaru_with_malformed_response_keeps_previous_state(
    songdb: SongDbService,
) -> None:
    class _MalformedHttp:
        async def get_json(self, url: str) -> dict[str, object]:
            # 첫 엔트리는 정상, 둘째 엔트리에 title이 없다 — 중간에 KeyError가 나도
            # 첫 엔트리 반영분까지 포함해 아무것도 바뀌면 안 된다.
            return {
                "songs": [
                    {"title": "B.B.K.K.B.K.K.", "isNew": True, "charts": {"MAS": {"const": 99.9}}},
                    {"no_title_key": True},
                ]
            }

    await songdb.refresh_from_chunimaru(_MalformedHttp())  # must not raise
    # 이전 상태(시드 기준)가 그대로 남아야 한다 — 天体観測은 여전히 "찾지 못함".
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("天体観測", _CTX)
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.charts["MAS"].const == 12.5
    assert song.is_new is False


async def test_seed_loads_bpm_version_level_and_sdvxin(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.bpm is not None
    assert song.version is not None
    mas = song.charts["MAS"]
    assert mas.level is not None


async def test_find_by_const_returns_sorted_hits_in_range(songdb: SongDbService) -> None:
    hits = songdb.find_by_const(min_const=12.0, max_const=13.0, difficulty="MAS")
    assert hits  # 로컬 시드에 12~13 상수의 MAS 채보가 있어야 한다
    # find_by_const는 이미 const가 None인 채보를 걸러낸다; 아래 필터는 그 사실을
    # 타입 체커에게도 알려주기 위한 것이다(동작에는 영향 없음).
    consts = [c for hit in hits if (c := hit.chart.const) is not None]
    assert len(consts) == len(hits)
    assert consts == sorted(consts)
    assert all(12.0 <= c <= 13.0 for c in consts)


async def test_find_by_const_excludes_unavailable_songs(songdb: SongDbService) -> None:
    hits = songdb.find_by_const(min_const=0.0, max_const=99.0, difficulty="BAS")
    assert all(hit.song.title != "天体観測" for hit in hits)  # 국제판 미수록곡은 제외


async def test_chart_link_uses_sdvxin_mapping_when_present(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    link = songdb.chart_link(song, song.charts["MAS"])
    assert link.has_mapping is True
    assert "sdvx.in" in link.url


async def test_chart_link_falls_back_to_youtube_without_mapping(songdb: SongDbService) -> None:
    from services.chunithm.songdb import ChartRecord, SongRecord

    fake_song = SongRecord(title="테스트곡", aliases=(), artist="", charts={})
    fake_chart = ChartRecord(difficulty="MAS", const=10.0, note_count=1000, sdvxin=None)

    link = songdb.chart_link(fake_song, fake_chart)

    assert link.has_mapping is False
    assert "youtube.com" in link.url


async def test_chunimaru_only_song_becomes_resolvable(songdb: SongDbService) -> None:
    synthetic = {
        "songs": [
            {
                "title": "__새곡_테스트전용__",
                "artist": "test artist",
                "isNew": True,
                "charts": {"MAS": {"const": 13.0, "isConstUnknown": False}},
            }
        ]
    }
    songdb._apply_chunimaru(synthetic)
    song = await songdb.resolve("__새곡_테스트전용__", _CTX)
    assert song.charts["MAS"].const == 13.0
    assert song.charts["MAS"].note_count is None
    assert song.is_new is True
    assert songdb.const_for("__새곡_테스트전용__", "MAS") == 13.0
