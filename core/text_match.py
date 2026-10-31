# core/text_match.py
"""글자 칸 비교(2026-10-31 결정). 곡명처럼 같은 이름이 전각·반각, 대소문자, 공백, 가운뎃점(`・`)만 달리 적힌 것을
같은 글로 본다("フランドール・Ｓ"와 "フランドールS"). `table_query`의 글자 칸 비교와 곡 검색의 제목 조건이 같은 함수를 쓴다."""
from __future__ import annotations

import unicodedata


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def match_key(text: str) -> str:
    """비교용 글. 전각·반각과 대소문자를 맞추고 글자·숫자만 남긴다(공백·구두점·기호는 뺀다).
    기호만 있는 글(예 `∞`)은 다 사라지므로, 그때는 공백만 뺀 글을 쓴다."""
    folded = _folded(text)
    key = "".join(ch for ch in folded if ch.isalnum())
    return key or "".join(folded.split())


def _keys(hay: str, needle: str) -> tuple[str, str]:
    """찾는 글(needle)이 글자·숫자를 가지면 둘 다 `match_key`로, 기호뿐이면 둘 다 공백만 뺀 글로 맞춘다."""
    if any(ch.isalnum() for ch in _folded(needle)):
        return match_key(hay), match_key(needle)
    return "".join(_folded(hay).split()), "".join(_folded(needle).split())


def text_equals(hay: str, needle: str) -> bool:
    left, right = _keys(hay, needle)
    return left == right


def text_contains(hay: str, needle: str) -> bool:
    left, right = _keys(hay, needle)
    return right in left


def text_starts_with(hay: str, needle: str) -> bool:
    left, right = _keys(hay, needle)
    return left.startswith(right)


def text_ends_with(hay: str, needle: str) -> bool:
    left, right = _keys(hay, needle)
    return left.endswith(right)
