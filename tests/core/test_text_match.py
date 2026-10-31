from __future__ import annotations

import pytest

from core.text_match import (
    match_key,
    text_contains,
    text_ends_with,
    text_equals,
    text_starts_with,
)


def test_match_key_ignores_width_case_spaces_and_punctuation() -> None:
    assert match_key("最終鬼畜妹フランドール・Ｓ") == match_key("最終鬼畜妹フランドールs")
    assert match_key("Hello, World!") == "helloworld"
    assert match_key("ｱｲｳ") == match_key("アイウ")  # 반각 가나


def test_match_key_keeps_symbol_only_text() -> None:
    assert match_key("∞") == "∞"  # 기호뿐인 글이 빈 글이 되면 모든 곡에 맞아 버린다
    assert match_key(" ? ") == "?"


@pytest.mark.parametrize(
    ("hay", "needle"),
    [("最終鬼畜妹フランドール・Ｓ", "フランドールs"), ("Chrono Magia", "chronomagia"), ("A・B", "ａ b")],
)
def test_contains_matches_loosely(hay: str, needle: str) -> None:
    assert text_contains(hay, needle)


def test_contains_misses_other_text() -> None:
    assert not text_contains("最終鬼畜妹フランドール・Ｓ", "レミリア")


def test_symbol_only_needle_is_searched_as_it_is() -> None:
    assert text_contains("∞ Infinity", "∞")
    assert not text_contains("Infinity", "∞")


def test_starts_with_ends_with_and_equals() -> None:
    title = "超最終鬼畜妹フランドール・S"
    assert text_starts_with(title, "超最終")
    assert not text_starts_with(title, "最終")
    assert text_ends_with(title, "フランドールS")
    assert not text_ends_with(title, "フランドール")
    assert text_equals(title, "超最終鬼畜妹フランドールＳ")
    assert not text_equals(title, "最終鬼畜妹フランドール・S")
