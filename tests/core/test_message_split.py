from __future__ import annotations

from core.message_split import split_message


def test_short_text_stays_one_chunk() -> None:
    assert split_message("안녕\n유니") == ["안녕\n유니"]


def test_long_text_splits_on_line_boundaries_within_limit() -> None:
    lines = [f"{i}. " + "가" * 40 for i in range(100)]
    chunks = split_message("\n".join(lines), limit=500)

    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == "\n".join(lines)


def test_single_overlong_line_is_cut_by_characters() -> None:
    chunks = split_message("가" * 1050, limit=500)

    assert [len(c) for c in chunks] == [500, 500, 50]
