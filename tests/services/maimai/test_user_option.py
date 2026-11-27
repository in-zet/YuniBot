# tests/services/maimai/test_user_option.py
from __future__ import annotations

from pathlib import Path

import pytest

from services.maimai.user_option import (
    FavoriteEntry,
    favorite_payload,
    form_token,
    parse_favorite_page,
)

_FAVORITE_HTML = Path("docs/reference/maimai-net/favorite_update.html").read_text(encoding="utf-8")


def test_parse_favorite_page_lists_every_song_with_its_checked_state() -> None:
    entries = parse_favorite_page(_FAVORITE_HTML)

    assert len(entries) == 20
    assert [e.title for e in entries if e.checked] == ["テトリス", "運命線上のアリア"]
    assert entries[0] == FavoriteEntry(title="愛♡スクリ～ム！", value="MUSIC0001", checked=False)


def test_form_token_reads_the_hidden_token() -> None:
    assert form_token(_FAVORITE_HTML) == "TOKEN"


def test_favorite_payload_keeps_checked_entries_and_adds_by_title() -> None:
    entries = [
        FavoriteEntry(title="Link", value="L1", checked=True),
        FavoriteEntry(title="Link", value="L2", checked=False),
        FavoriteEntry(title="AiAe", value="A", checked=False),
    ]

    payload = favorite_payload(entries, ["Link", "AiAe"], "T")

    # 이미 즐겨찾기인 같은 제목 곡은 체크된 칸 그대로 — 어느 쪽인지 묻지 않는다.
    assert payload == {"idx": "99", "music[]": ["L1", "A"], "token": "T"}


def test_favorite_payload_refuses_an_ambiguous_new_title() -> None:
    entries = [FavoriteEntry(title="Link", value="L1", checked=False), FavoriteEntry(title="Link", value="L2", checked=False)]

    with pytest.raises(ValueError, match="같은 제목 곡이 둘"):
        favorite_payload(entries, ["Link"], "T")


def test_favorite_payload_refuses_an_unknown_title_and_more_than_30() -> None:
    entries = [FavoriteEntry(title=f"곡{i}", value=str(i), checked=False) for i in range(31)]

    with pytest.raises(ValueError, match="목록에 '없는 곡'"):
        favorite_payload(entries, ["없는 곡"], "T")
    with pytest.raises(ValueError, match="30곡까지"):
        favorite_payload(entries, [f"곡{i}" for i in range(31)], "T")
