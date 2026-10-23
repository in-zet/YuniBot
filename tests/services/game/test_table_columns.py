# tests/services/game/test_table_columns.py
from __future__ import annotations

from decimal import Decimal

import pytest

from core.tables import TableBuilder, TableError
from services.chunithm.songdb import ChartRecord, SongRecord
from services.game import table_columns as tc
from services.game.records import PbRow

RANKS = {"SSS": 1_007_500, "S": 975_000}


def _song() -> SongRecord:
    return SongRecord(title="곡A", aliases=(), artist="작곡가A", id=1, version="LUMINOUS")


def _chart() -> ChartRecord:
    return ChartRecord(difficulty="MAS", const=15.0, note_count=1234, level="15")


def _pb() -> PbRow:
    return PbRow(
        song_title="곡A", difficulty="MAS", score=1_008_000, rank="SSS", clear_lamp="CLEAR",
        combo_lamp="ALL JUSTICE", chain_lamp="NONE", const=15.0, rating=Decimal("17.50"),
        session_id=None, updated_at="2026-10-20",
    )


def test_chart_ref_key_round_trips() -> None:
    ref = tc.chart_ref("a \"quoted\" 곡", "MAS")
    assert ref.kind == tc.CHART
    assert tc.chart_key(ref) == ("a \"quoted\" 곡", "MAS")


def test_chart_key_rejects_song_row() -> None:
    with pytest.raises(TableError):
        tc.chart_key(tc.song_ref("곡A"))


def test_add_columns_uses_common_definitions() -> None:
    table = tc.add_columns(TableBuilder(), tc.SONG, tc.DIFFICULTY, tc.RANK, tc.LAMP, tc.CONST).build()
    by_name = {c.name: c for c in table.columns}
    assert by_name[tc.SONG].bare and by_name[tc.DIFFICULTY].bare
    assert by_name[tc.DIFFICULTY].value_type == "DifficultyRef"
    assert by_name[tc.RANK].value_type == "RankRef"
    assert by_name[tc.LAMP].value_type == "LampRef"
    assert by_name[tc.CONST].value_type == "number"


def test_add_columns_rejects_unknown_name() -> None:
    with pytest.raises(TableError):
        tc.add_columns(TableBuilder(), "없는 칸")


def test_difficulty_cell_raw_is_canonical_name_text_is_short_code() -> None:
    c = tc.difficulty_cell("MAS")
    assert (c.raw, c.text) == ("MASTER", "MAS")
    assert not tc.difficulty_cell(None).filled


def test_const_cell_text_and_estimate_and_unknown() -> None:
    assert tc.const_cell(15.0).text == "15.0"
    assert tc.const_cell(14.7, estimated=True).text == "14.7(추정)"
    unknown = tc.const_cell(None)
    assert unknown.raw is None and unknown.text == "-"


def test_score_cell_commas_and_no_record() -> None:
    assert tc.score_cell(1_007_500).text == "1,007,500"
    none = tc.score_cell(None)
    assert none.raw is None and none.text == "기록 없음"


def test_rank_cell_raw_is_base_score() -> None:
    c = tc.rank_cell("SSS", RANKS)
    assert (c.raw, c.text) == (1_007_500, "SSS")
    assert tc.rank_cell("???", RANKS).raw is None


def test_lamp_cell_distinguishes_no_record_from_no_lamp() -> None:
    assert tc.lamp_cell(None).raw is None
    assert tc.lamp_cell("NONE").raw == "NONE"
    assert tc.lamp_cell("FULL COMBO").raw == "FULL COMBO"


def test_rating_cell_text_is_unchanged_decimal() -> None:
    assert tc.rating_cell(Decimal("16.98")).text == "16.98"
    assert tc.rating_cell(None).text == "계산 불가"
    assert tc.rating_cell(None, has_record=False).text == "-"


def test_chart_cells_has_requested_columns_only() -> None:
    cells = tc.chart_cells(_song(), _chart(), [tc.SONG, tc.DIFFICULTY, tc.CONST, tc.VERSION])
    assert set(cells) == {tc.SONG, tc.DIFFICULTY, tc.CONST, tc.VERSION}
    assert cells[tc.VERSION].text == "LUMINOUS"


def test_song_cells_leave_difficulty_empty() -> None:
    cells = tc.song_cells(_song(), [tc.SONG, tc.DIFFICULTY, tc.ARTIST])
    assert cells[tc.SONG].filled and cells[tc.ARTIST].filled
    assert not cells[tc.DIFFICULTY].filled


def test_pb_cells_with_and_without_record() -> None:
    have = tc.pb_cells(_pb(), RANKS)
    assert have[tc.SCORE].raw == 1_008_000 and have[tc.RANK].raw == 1_007_500
    assert have[tc.LAMP].raw == "ALL JUSTICE" and have[tc.RATING].raw == 17.5
    none = tc.pb_cells(None, RANKS)
    assert all(c.raw is None for c in none.values())
    assert none[tc.SCORE].text == "기록 없음"


def test_partial_row_hint_names_difficulty_and_song() -> None:
    (hint,) = tc.PARTIAL_ROW_HINTS
    assert (hint.column, hint.given) == (tc.DIFFICULTY, tc.SONG)


def test_partial_hint_gives_charts_of_advice_when_song_rows_are_dropped() -> None:
    from core.tables import TableNeeds, TableStore, resolve_table_arg

    builder = TableBuilder(unit="곡")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.row(tc.song_ref("곡A"), tc.song_cells(_song(), [tc.SONG, tc.DIFFICULTY]))
    builder.row(
        tc.chart_ref("곡A", "MAS"), tc.chart_cells(_song(), _chart(), [tc.SONG, tc.DIFFICULTY])
    )
    store = TableStore()
    table_id = store.put("p1", "c1", builder.build())

    arg = resolve_table_arg(
        table_id, store=store, player_id="p1", channel_id="c1",
        needs=TableNeeds(nonempty=[tc.SONG, tc.DIFFICULTY]), mutates=False,
        partial_row_hints=tc.PARTIAL_ROW_HINTS,
    )

    assert arg.table.row_count == 1
    assert "곡 단위 행(난이도 없음) 1개 뺌 — charts_of로 난이도를 정할 수 있다" in arg.table.steps
