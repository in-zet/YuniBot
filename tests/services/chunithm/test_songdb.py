import json
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.songdb import VERSION_ORDER, SongDbService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})
_CHUNIMARU_SAMPLE = Path("docs/reference/chunimaru/songdb_sample.json")


@pytest.fixture
def songdb() -> SongDbService:
    return SongDbService()


@pytest.fixture
async def songdb_with_conn() -> AsyncIterator[SongDbService]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(
        "".join(
            Path(f"migrations/core/{name}.sql").read_text()
            for name in ("005_arcades", "011_songdb_aliases", "017_arcade_aliases_guild_scope", "020_aliases_global")
        )
    )
    await conn.commit()
    try:
        yield SongDbService(conn=conn)
    finally:
        await conn.close()


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


async def test_by_id_finds_song_by_sega_music_id(songdb: SongDbService) -> None:
    song = songdb.by_id(3)
    assert song is not None
    assert song.title == "B.B.K.K.B.K.K."


async def test_by_id_unknown_returns_none(songdb: SongDbService) -> None:
    assert songdb.by_id(999_999) is None


async def test_by_id_finds_song_added_only_by_chunimaru_overlay(songdb: SongDbService) -> None:
    songdb._apply_chunimaru(json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8")))

    song = songdb.by_id(2897)
    assert song is not None
    assert song.title == "Ave Mujica"


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


async def test_is_new_for_follows_chunimaru_overlay(songdb: SongDbService) -> None:
    # 덧씌우기 전에는 시드에 신곡 표시가 없으니 False다.
    assert songdb.is_new_for("Ave Mujica") is False

    songdb._apply_chunimaru(json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8")))

    assert songdb.is_new_for("Ave Mujica") is True
    assert songdb.is_new_for("B.B.K.K.B.K.K.") is False


async def test_is_new_for_unknown_title_is_false(songdb: SongDbService) -> None:
    assert songdb.is_new_for("존재하지않는곡") is False


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


async def test_find_by_const_without_difficulty_returns_all_difficulties(songdb: SongDbService) -> None:
    # 2026-10-18 결정: 상수는 채보 단위로 이미 유일하게 정해지는 값이라, 난이도를 비우면
    # 그 범위에 드는 모든 난이도의 채보를 묶어야 한다("15.0 폴더" 같은 실기 요청).
    hits = songdb.find_by_const(min_const=10.0, max_const=13.7)
    target_hits = [h for h in hits if h.song.title == "B.B.K.K.B.K.K."]
    assert {"EXP", "MAS", "ULT"}.issubset({h.chart.difficulty for h in target_hits})


async def test_find_by_level_returns_matching_hits(songdb: SongDbService) -> None:
    hits = songdb.find_by_level(level="12+", difficulty="MAS")
    assert hits
    assert all(hit.chart.level == "12+" for hit in hits)
    assert any(hit.song.title == "B.B.K.K.B.K.K." for hit in hits)
    consts = [c for hit in hits if (c := hit.chart.const) is not None]
    assert consts == sorted(consts)


async def test_find_by_level_excludes_unavailable_song() -> None:
    songdb = SongDbService()
    # 天体観測은 국제판 미수록(available=False)인데, MAS 표기 레벨이 "9+"다.
    hits = songdb.find_by_level(level="9+", difficulty="MAS")
    assert all(hit.song.title != "天体観測" for hit in hits)


async def test_find_by_level_returns_empty_when_nothing_matches(songdb: SongDbService) -> None:
    hits = songdb.find_by_level(level="그런레벨없음", difficulty="MAS")
    assert hits == []


async def test_find_by_level_without_difficulty_returns_all_difficulties(songdb: SongDbService) -> None:
    hits = songdb.find_by_level(level="12+")
    assert {"ADV", "EXP", "MAS", "ULT"}.issubset({h.chart.difficulty for h in hits})


async def test_find_by_version_with_difficulty_filters_to_one_chart(songdb: SongDbService) -> None:
    hits = songdb.find_by_version(version="LUMINOUS", difficulty="MAS")
    assert hits
    assert all(hit.song.version == "LUMINOUS" for hit in hits)
    assert all(hit.chart.difficulty == "MAS" for hit in hits)
    assert any(hit.song.title == "可愛くてごめん" for hit in hits)


async def test_find_by_version_without_difficulty_returns_all_available_charts(songdb: SongDbService) -> None:
    hits = songdb.find_by_version(version="LUMINOUS")
    target_hits = [h for h in hits if h.song.title == "可愛くてごめん"]
    # 이 곡은 BAS/ADV/EXP/MAS가 모두 국제판에서 고를 수 있다 — 난이도를 비우면 그 전부가
    # 나와야 한다(find_by_version의 "버전 전체" 의미). WE 등 다른 난이도가 더 있어도 된다.
    assert {"BAS", "ADV", "EXP", "MAS"}.issubset({h.chart.difficulty for h in target_hits})


async def test_find_by_version_excludes_unavailable_song(songdb: SongDbService) -> None:
    hits = songdb.find_by_version(version="LUMINOUS")
    assert all(hit.song.title != "ALIVE" for hit in hits)  # 같은 버전이지만 국제판 미수록


async def test_find_by_version_returns_empty_when_nothing_matches(songdb: SongDbService) -> None:
    hits = songdb.find_by_version(version="그런버전없음")
    assert hits == []


async def test_find_by_artist_returns_sorted_songs(songdb: SongDbService) -> None:
    songs = songdb.find_by_artist(name="nora2r")
    assert songs
    titles = [song.title for song in songs]
    assert titles == sorted(titles)
    assert "B.B.K.K.B.K.K." in titles


async def test_find_by_artist_is_case_insensitive(songdb: SongDbService) -> None:
    songs = songdb.find_by_artist(name="NORA2R")
    assert any(song.title == "B.B.K.K.B.K.K." for song in songs)


async def test_find_by_artist_matches_collab_credit_substring(songdb: SongDbService) -> None:
    # "MEGATON BLAST"의 크레딧은 "BlackY vs. Yooh" — 콜라보 한쪽 이름만으로도 찾아야 한다.
    songs = songdb.find_by_artist(name="Yooh")
    assert any(song.title == "MEGATON BLAST" for song in songs)


async def test_find_by_artist_excludes_unavailable_song(songdb: SongDbService) -> None:
    # "天体観測"(BUMP OF CHICKEN [cover])은 국제판 미수록이다.
    songs = songdb.find_by_artist(name="BUMP OF CHICKEN")
    assert all(song.title != "天体観測" for song in songs)


async def test_find_by_artist_returns_empty_when_nothing_matches(songdb: SongDbService) -> None:
    songs = songdb.find_by_artist(name="그런작곡가없음")
    assert songs == []


async def test_find_songs_combines_const_range_and_note_count_as_and(songdb: SongDbService) -> None:
    # "B.B.K.K.B.K.K." MAS는 상수 12.5·노트 960개다. 같은 상수 범위에서 노트 수 하한을
    # 그 값보다 올리면 조건 하나만으로는 안 빠지던 채보가 AND 조합에서는 빠져야 한다.
    const = songdb.const_for("B.B.K.K.B.K.K.", "MAS")
    notes = songdb.note_count_for("B.B.K.K.B.K.K.", "MAS")
    assert const is not None and notes is not None

    hits = songdb.find_songs(min_const=const, max_const=const, min_note_count=notes)
    assert any(hit.song.title == "B.B.K.K.B.K.K." and hit.chart.difficulty == "MAS" for hit in hits)

    hits_excluded = songdb.find_songs(min_const=const, max_const=const, min_note_count=notes + 1)
    assert not any(hit.song.title == "B.B.K.K.B.K.K." for hit in hits_excluded)


async def test_find_songs_level_only_matches_that_level(songdb: SongDbService) -> None:
    # "B.B.K.K.B.K.K." MAS는 표기 레벨 "12+"다(상수 12.5).
    hits = songdb.find_songs(level="12+")
    assert hits
    assert all(hit.chart.level == "12+" for hit in hits)
    assert any(hit.song.title == "B.B.K.K.B.K.K." for hit in hits)


async def test_find_songs_version_and_const_combo(songdb: SongDbService) -> None:
    hits = songdb.find_songs(version="CHUNITHM", min_const=12.0, max_const=13.0)
    assert any(hit.song.title == "B.B.K.K.B.K.K." for hit in hits)
    assert all(hit.song.version == "CHUNITHM" for hit in hits)


async def test_find_songs_accepts_several_difficulties_and_matches_either(songdb: SongDbService) -> None:
    mas = songdb.find_songs(difficulty="MAS", min_const=12.0, max_const=13.0)
    ult = songdb.find_songs(difficulty="ULT", min_const=12.0, max_const=13.0)
    both = songdb.find_songs(difficulty=["MAS", "ULT", "MAS"], min_const=12.0, max_const=13.0)

    assert {hit.chart.difficulty for hit in both} <= {"MAS", "ULT"}
    assert len(both) == len(mas) + len(ult)  # 같은 난이도를 두 번 줘도 두 번 세지 않는다
    assert mas  # B.B.K.K.B.K.K. MAS(12.5)


def test_version_order_covers_every_version_in_the_song_db(songdb: SongDbService) -> None:
    versions = {song.version for song in songdb._songs.values() if song.version}  # pyright: ignore[reportPrivateUsage]
    assert versions <= set(VERSION_ORDER)
    assert len(set(VERSION_ORDER)) == len(VERSION_ORDER)


async def test_find_songs_returns_empty_when_nothing_matches(songdb: SongDbService) -> None:
    assert songdb.find_songs(min_const=99.0, max_const=99.0) == []


async def test_find_songs_without_any_filter_returns_more_than_a_narrowed_query(
    songdb: SongDbService,
) -> None:
    # 서비스 메서드 자체는 조건이 하나도 없어도 거절하지 않는다(그 판단은 스킬 도구가 한다) —
    # 조건을 준 조합보다 결과가 더 많아야 한다.
    narrowed = songdb.find_songs(min_const=12.0, max_const=13.0)
    everything = songdb.find_songs()
    assert len(everything) > len(narrowed)


async def test_find_songs_order_is_const_ascending_unknown_last_then_title(songdb: SongDbService) -> None:
    hits = songdb.find_songs(min_const=12.0, max_const=13.0)
    keys = [(hit.chart.const, hit.song.title) for hit in hits]
    assert keys == sorted(keys)
    everything = songdb.find_songs()
    consts = [hit.chart.const for hit in everything]
    known = [c for c in consts if c is not None]
    assert consts[: len(known)] == known and all(c is None for c in consts[len(known) :])


async def test_chart_link_uses_sdvxin_mapping_when_present(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    link = songdb.chart_link(song, song.charts["MAS"])
    assert link.has_mapping is True
    assert "sdvx.in" in link.url


async def test_chart_link_falls_back_to_youtube_without_mapping(songdb: SongDbService) -> None:
    from services.chunithm.songdb import ChartRecord, SongRecord

    fake_song = SongRecord(title="테스트곡", aliases=(), artist="", id=1, charts={})
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


async def test_resolve_without_conn_ignores_server_alias(songdb: SongDbService) -> None:
    # conn 없이 만든 서비스는(이 모듈의 대부분 테스트처럼) 서버 별칭 기능이 아예 없다 —
    # 그래도 전역 해석은 그대로 동작해야 한다.
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("크로노", _CTX)


async def test_add_and_resolve_server_alias(songdb_with_conn: SongDbService) -> None:
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    await songdb_with_conn.add_alias(alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")

    song = await songdb_with_conn.resolve("크로노", ctx)

    assert song.title == "B.B.K.K.B.K.K."


async def test_alias_applies_to_every_guild_and_to_dms(songdb_with_conn: SongDbService) -> None:
    """별칭은 봇 전역이다(2026-10-21) — 다른 서버나 서버 밖(DM)에서도 같은 곡으로 풀린다."""
    await songdb_with_conn.add_alias(alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")
    other_guild_ctx = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id="g2")
    dm_ctx = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id=None)

    assert (await songdb_with_conn.resolve("크로노", other_guild_ctx)).title == "B.B.K.K.B.K.K."
    assert (await songdb_with_conn.resolve("크로노", dm_ctx)).title == "B.B.K.K.B.K.K."


async def test_resolve_suggests_similar_titles_when_nothing_contains_the_text(songdb: SongDbService) -> None:
    """부분 일치가 없어도 글자가 비슷한 제목은 후보로 준다(모델이 되묻는 재료)."""
    with pytest.raises(ResolutionError) as excinfo:
        await songdb.resolve("B.B.K.K.B.K.K", _CTX)  # 끝의 점 하나가 빠진 오타
    assert "B.B.K.K.B.K.K." in excinfo.value.candidates


async def test_add_alias_already_used_for_different_song_raises(songdb_with_conn: SongDbService) -> None:
    await songdb_with_conn.add_alias(alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")

    with pytest.raises(ValueError, match="이미"):
        await songdb_with_conn.add_alias(alias="크로노", song_title="Reach For The Stars", added_by="p2")


async def test_get_and_remove_alias(songdb_with_conn: SongDbService) -> None:
    await songdb_with_conn.add_alias(alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")

    row = await songdb_with_conn.get_alias(alias="크로노")
    assert row is not None
    assert row.added_by == "p1"

    await songdb_with_conn.remove_alias(alias="크로노")
    assert await songdb_with_conn.get_alias(alias="크로노") is None


async def test_server_alias_lookup_is_case_insensitive(songdb_with_conn: SongDbService) -> None:
    # "RFTS" 대문자로 등록해도 소문자 "rfts"로 조회/해석/삭제돼야 한다 — 전역(시드)
    # 별칭과 같은 우선순위 묶음이므로 대소문자 무시가 일관되어야 한다.
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    await songdb_with_conn.add_alias(alias="RFTS", song_title="Reach For The Stars", added_by="p1")

    row = await songdb_with_conn.get_alias(alias="rfts")
    assert row is not None
    assert row.song_title == "Reach For The Stars"

    song = await songdb_with_conn.resolve("rfts", ctx)
    assert song.title == "Reach For The Stars"

    # 대문자로 다시 add_alias를 호출해도 같은 별칭으로 인식해(song_title이 같으므로)
    # 충돌로 보지 않아야 한다.
    await songdb_with_conn.add_alias(alias="rfts", song_title="Reach For The Stars", added_by="p2")

    await songdb_with_conn.remove_alias(alias="RFTS")
    assert await songdb_with_conn.get_alias(alias="rfts") is None


async def test_add_alias_same_song_reregister_does_not_steal_ownership(
    songdb_with_conn: SongDbService,
) -> None:
    # 같은 별칭을 같은 곡으로 다시 등록하는 것(멱등 재등록)은 허용되지만,
    # added_by/created_at은 원래 추가한 사람 걸로 남아야 한다. 안 그러면 누구나 재등록만
    # 으로 alias_remove의 "추가한 사람이나 admin만 지운다" 제한을 비껴갈 수 있다.
    await songdb_with_conn.add_alias(alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="playerA")

    row_before = await songdb_with_conn.get_alias(alias="크로노")
    assert row_before is not None and row_before.added_by == "playerA"

    # player B가 같은 별칭을, 같은 곡으로 재등록한다 — 이건 기존 충돌 검사를 통과한다
    # (song_title이 같으므로). 그래도 소유자는 바뀌면 안 된다.
    await songdb_with_conn.add_alias(alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="playerB")

    row_after = await songdb_with_conn.get_alias(alias="크로노")
    assert row_after is not None
    assert row_after.added_by == "playerA"


async def test_add_alias_colliding_with_other_songs_exact_title_raises(
    songdb_with_conn: SongDbService,
) -> None:
    # "Reach For The Stars"는 실제 시드에 있는 다른 곡의 정식 제목이다.
    # "B.B.K.K.B.K.K."에 그 이름을 서버 별칭으로 등록하면, 이후 resolve()가 그 서버에서
    # "Reach For The Stars"를 "B.B.K.K.B.K.K."로 잘못 돌려주게 된다 — 막아야 한다.
    with pytest.raises(ValueError, match="이미"):
        await songdb_with_conn.add_alias(
            alias="Reach For The Stars", song_title="B.B.K.K.B.K.K.", added_by="p1"
        )


async def test_add_alias_colliding_with_other_songs_global_alias_raises(
    songdb_with_conn: SongDbService,
) -> None:
    # "RFTS"는 실제 시드에서 "Reach For The Stars"의 전역(시드) 별칭이다.
    # "B.B.K.K.B.K.K."에 서버 별칭으로 등록하려 하면 막아야 한다(서버 별칭이 먼저 검사돼
    # 전역 별칭을 가리게 된다).
    with pytest.raises(ValueError, match="이미"):
        await songdb_with_conn.add_alias(
            alias="RFTS", song_title="B.B.K.K.B.K.K.", added_by="p1"
        )


async def test_add_alias_matching_own_songs_global_alias_is_allowed(
    songdb_with_conn: SongDbService,
) -> None:
    # "RFTS"를 그 별칭의 원래 주인인 "Reach For The Stars" 자신에게 서버 별칭으로
    # 등록하는 것은 중복이지만 충돌이 아니다 — 허용한다("같은 곡" 예외).
    await songdb_with_conn.add_alias(
        alias="RFTS", song_title="Reach For The Stars", added_by="p1"
    )
    row = await songdb_with_conn.get_alias(alias="RFTS")
    assert row is not None
    assert row.song_title == "Reach For The Stars"


async def test_find_by_const_without_max_has_no_upper_bound(songdb: SongDbService) -> None:
    hits = songdb.find_by_const(min_const=12.0, difficulty="MAS")
    bounded = songdb.find_by_const(min_const=12.0, max_const=13.0, difficulty="MAS")
    assert len(hits) > len(bounded)
    assert all(hit.chart.const is not None and hit.chart.const >= 12.0 for hit in hits)


async def test_find_by_const_new_only_keeps_only_new_songs(songdb: SongDbService) -> None:
    songdb._apply_chunimaru(json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8")))

    hits = songdb.find_by_const(min_const=0.0, new_only=True)

    assert hits
    assert all(hit.song.is_new for hit in hits)
    assert any(hit.song.title == "Ave Mujica" for hit in hits)


async def test_seed_loads_jacket_file_name(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.jacket is not None
    assert "/" not in song.jacket  # 주소가 아니라 파일 이름이다


async def test_chunimaru_only_song_gets_chunimaru_jacket_url(songdb: SongDbService) -> None:
    synthetic = {
        "songs": [
            {
                "idx": 99_999,
                "title": "시드에 없는 신곡",
                "artist": "누군가",
                "isNew": True,
                "jacket": "https://chunimaru.example/jackets/zzz.webp",
                "charts": {"MAS": {"const": 14.0, "isConstUnknown": False}},
            }
        ]
    }
    songdb._apply_chunimaru(synthetic)

    song = songdb.by_id(99_999)
    assert song is not None
    assert song.jacket == "https://chunimaru.example/jackets/zzz.webp"


async def test_chunimaru_jacket_url_is_kept_as_fallback_for_seed_songs(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    before = song.jacket
    songdb._apply_chunimaru({
        "songs": [{
            "idx": song.id, "title": song.title, "artist": song.artist, "isNew": False,
            "jacket": "https://chunimaru.example/jackets/bbkk.webp",
            "charts": {"MAS": {"const": 14.0, "isConstUnknown": False}},
        }]
    })

    assert songdb.jacket_for(song.title) == before  # 시드 재킷이 먼저다
    assert songdb.jacket_fallback_for(song.title) == "https://chunimaru.example/jackets/bbkk.webp"
    assert songdb.jacket_fallback_for("없는 곡") is None


async def test_chunimaru_overlay_keeps_seed_jacket(songdb: SongDbService) -> None:
    before = (await songdb.resolve("B.B.K.K.B.K.K.", _CTX)).jacket
    songdb._apply_chunimaru(json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8")))

    assert (await songdb.resolve("B.B.K.K.B.K.K.", _CTX)).jacket == before


def test_song_by_title_finds_by_exact_title_only(songdb: SongDbService) -> None:
    song = songdb.song_by_title("B.B.K.K.B.K.K.")
    assert song is not None and song.title == "B.B.K.K.B.K.K."
    assert songdb.song_by_title("b.b.k.k.b.k.k.") is None  # 별칭·대소문자 풀이는 resolve의 일
    assert songdb.song_by_title("없는 곡") is None


def test_linked_verse_titles_all_exist_in_songdb(songdb: SongDbService) -> None:
    """linked_verse.yaml의 제목이 곡 DB 제목과 글자까지 같아야 한다(오타가 나면 조용히 빠지므로 대조한다)."""
    from services.chunithm.songdb import (
        _load_linked_verse,  # pyright: ignore[reportPrivateUsage]
    )

    titles = [t for _gate, ts in _load_linked_verse(Path("services/chunithm/data/linked_verse.yaml")) for t in ts]
    assert len(titles) == len(set(titles)) == 24
    missing = [t for t in titles if songdb.song_by_title(t) is None]
    assert missing == []
    assert {hit.song.title for hit in songdb.find_linked_verse()} == set(titles)


def test_find_linked_verse_filters_by_gate_and_difficulty(songdb: SongDbService) -> None:
    hits = songdb.find_linked_verse(gate="전도 악곡", difficulty="MAS")
    assert len(hits) == 11
    assert {hit.gate for hit in hits} == {"전도 악곡"}
    assert {hit.chart.difficulty for hit in hits} == {"MAS"}
    with pytest.raises(ValueError, match="모르는 게이트"):
        songdb.find_linked_verse(gate="없는게이트")
    assert songdb.linked_verse_gates()[0] == "ORIGIN"


async def test_current_version_is_most_common_version_among_new_songs(songdb: SongDbService) -> None:
    assert songdb.current_version() is None  # 신곡 표시가 없다

    songdb._apply_chunimaru(json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8")))
    new_songs = [song for song in songdb._songs.values() if song.is_new and song.title in songdb._available_intl]
    assert len(new_songs) >= 2
    for index, song in enumerate(new_songs):
        song.version = "VERSE" if index == 0 else "X-VERSE-X"
    assert songdb.current_version() == "X-VERSE-X"

    for song in new_songs:
        song.version = None
    assert songdb.current_version() is None  # 신곡이 있어도 수록 버전을 모르면 None
