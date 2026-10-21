# core/fuzzy.py
from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

_SEPARATORS = re.compile(r"[\s\-_.·・:：!！?？'\"]+")


def normalize_name(text: str) -> str:
    """이름을 견줄 때 쓰는 꼴: 소문자, 공백·구두점 없음. "광주 게임플라자"와 "광주게임플라자"가 같아진다."""
    return _SEPARATORS.sub("", text).lower()


def similar_names(text: str, names: Iterable[str], *, limit: int = 3, cutoff: float = 0.5) -> list[str]:
    """`names` 중 `text`와 비슷한 것을 비슷한 순서로 최대 `limit`개. 정규화한 꼴이 같거나 서로 포함하면
    무조건 후보이고, 아니면 글자 유사도(difflib)가 `cutoff` 이상일 때만 후보다. 짐작해서 되묻는 데 쓴다."""
    target = normalize_name(text)
    if not target:
        return []
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(target)
    scored: list[tuple[float, str]] = []
    for name in dict.fromkeys(names):
        candidate = normalize_name(name)
        if not candidate:
            continue
        if candidate == target:
            score = 1.0
        elif target in candidate or candidate in target:
            score = 0.9
        else:
            matcher.set_seq1(candidate)
            # 곡 제목처럼 후보가 수천 개일 수 있어, 싼 상한(ratio의 위쪽 경계)으로 먼저 거른다.
            if matcher.real_quick_ratio() < cutoff or matcher.quick_ratio() < cutoff:
                continue
            score = matcher.ratio()
            if score < cutoff:
                continue
        scored.append((score, name))
    scored.sort(key=lambda item: -item[0])
    return [name for _, name in scored[:limit]]
