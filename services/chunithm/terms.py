from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from core.resolvers import ResolutionError
from core.tool_types import ToolContext

_DATA_PATH = Path(__file__).parent / "data" / "terms.yaml"

_HANGUL_SCALE: dict[str, int] = {"만": 10_000, "천": 1_000, "백": 100, "십": 10}

CATEGORY_LABELS: dict[str, str] = {
    "rank": "랭크",
    "difficulty": "난이도",
    "lamp": "램프",
    "concept": "개념",
    "technique": "기술",
    "judgment": "판정",
}
_LABEL_TO_CATEGORY: dict[str, str] = {label: key for key, label in CATEGORY_LABELS.items()}


@dataclass(slots=True, kw_only=True)
class ResolvedRank:
    canonical: str
    min_score: int


@dataclass(slots=True, kw_only=True)
class ResolvedDifficulty:
    canonical: str


@dataclass(slots=True, kw_only=True)
class ResolvedLamp:
    canonical: str
    min_score: int | None


@dataclass(slots=True, kw_only=True)
class TermExplanation:
    canonical: str
    category_label: str
    aliases: tuple[str, ...]
    value: int | None
    meaning: str | None
    source: str
    scope: str


@dataclass(slots=True, kw_only=True)
class TermListItem:
    canonical: str
    category_label: str
    aliases: tuple[str, ...]
    value: int | None


@dataclass(slots=True, kw_only=True)
class _Entry:
    canonical: str
    category: str
    value: int | None
    aliases: tuple[str, ...]
    meaning: str | None = None
    source: str | None = None


def _load_entries(data_path: Path) -> list[_Entry]:
    raw = yaml.safe_load(data_path.read_text(encoding="utf-8")) or []
    return [
        _Entry(
            canonical=item["canonical"],
            category=item["category"],
            value=item.get("value"),
            aliases=tuple(item.get("aliases") or ()),
            meaning=item.get("meaning"),
            source=item.get("source"),
        )
        for item in raw
    ]


def _alias_map(entries: list[_Entry], category: str) -> dict[str, str]:
    """그 category 항목들의 (별칭 + canonical 자기 자신, 전부 소문자) -> canonical.
    별칭과 canonical 둘 다 소문자 키로 넣어 대소문자 무시 조회를 만든다."""
    mapping: dict[str, str] = {}
    for entry in entries:
        if entry.category != category:
            continue
        mapping[entry.canonical.lower()] = entry.canonical
        for alias in entry.aliases:
            mapping[alias.lower()] = entry.canonical
    return mapping


def _value_map(entries: list[_Entry], category: str) -> dict[str, int | None]:
    return {entry.canonical: entry.value for entry in entries if entry.category == category}


class TermsService:
    """값 용어(랭크·난이도·램프·점수)의 정식 이름·기준 점수·별칭. 정식 이름과 값은
    terms.yaml에 데이터로 있다 — 이 파일이 유일한 출처고, 여기서 다시 계산하거나
    vendored 코드로 되비추지 않는다(테스트가 교차 검증은 따로 한다)."""

    def __init__(self, data_path: Path = _DATA_PATH) -> None:
        entries = _load_entries(data_path)
        self._entries = entries
        self._rank_aliases = _alias_map(entries, "rank")
        self._rank_values = _value_map(entries, "rank")
        self._difficulty_aliases = _alias_map(entries, "difficulty")
        self._lamp_aliases = _alias_map(entries, "lamp")
        self._lamp_values = _value_map(entries, "lamp")

    async def resolve_rank(self, raw: str, ctx: ToolContext) -> ResolvedRank:
        canonical = self._rank_aliases.get(raw.strip().lower())
        if canonical is None:
            raise ResolutionError(message="찾지 못함")
        value = self._rank_values[canonical]
        assert value is not None  # terms.yaml 불변조건: 랭크는 전부 value를 갖는다
        return ResolvedRank(canonical=canonical, min_score=value)

    async def resolve_difficulty(self, raw: str, ctx: ToolContext) -> ResolvedDifficulty:
        token = self.difficulty_token(raw)
        if token is None:
            raise ResolutionError(message="모르는 난이도")
        return ResolvedDifficulty(canonical=token)

    def difficulty_token(self, text: str) -> str | None:
        """별칭이나 짧은 코드(둘 다 terms.yaml의 aliases에 있다)를 정식 난이도
        문자열로 바꾼다. 모르면 None(ChartRef 해석기가 "이 토큰은 난이도가 아니라
        곡 이름의 일부"로 되돌릴 수 있게)."""
        return self._difficulty_aliases.get(text.strip().lower())

    async def resolve_lamp(self, raw: str, ctx: ToolContext) -> ResolvedLamp:
        canonical = self._lamp_aliases.get(raw.strip().lower())
        if canonical is None:
            raise ResolutionError(message="모르는 램프")
        return ResolvedLamp(canonical=canonical, min_score=self._lamp_values.get(canonical))

    async def explain(self, raw: str, ctx: ToolContext) -> TermExplanation | None:
        """값 용어든 개념/기술/판정 용어든 정식 이름이나 별칭(대소문자 무시)으로
        찾아 뜻과 출처를 돌려준다. 없으면 None. 서버 은어는 아직 보지 않는다."""
        text = raw.strip().lower()
        for entry in self._entries:
            if entry.canonical.lower() != text and text not in (a.lower() for a in entry.aliases):
                continue
            meaning = entry.meaning
            if meaning is None and entry.category == "rank" and entry.value is not None:
                meaning = f"{entry.value:,}점 이상"
            return TermExplanation(
                canonical=entry.canonical,
                category_label=CATEGORY_LABELS.get(entry.category, entry.category),
                aliases=entry.aliases,
                value=entry.value,
                meaning=meaning,
                source=entry.source or "전역 정의",
                scope="전역",
            )
        return None

    async def list_terms(self, category: str | None, ctx: ToolContext) -> list[TermListItem]:
        """분류(한글 라벨 "랭크"/"기술" 등, 또는 내부 키)로 거른 용어 목록.
        category가 None이면 전부. 모르는 분류면 ValueError."""
        key = None if category is None else _normalize_category(category)
        return [
            TermListItem(
                canonical=entry.canonical,
                category_label=CATEGORY_LABELS.get(entry.category, entry.category),
                aliases=entry.aliases,
                value=entry.value,
            )
            for entry in self._entries
            if key is None or entry.category == key
        ]

    async def resolve_score(self, raw: str, ctx: ToolContext) -> int:
        text = raw.strip()

        numeric = _parse_numeric_score(text)
        if numeric is not None:
            return numeric

        rank_canonical = self._rank_aliases.get(text.lower())
        if rank_canonical is not None:
            value = self._rank_values[rank_canonical]
            assert value is not None
            return value

        lamp_canonical = self._lamp_aliases.get(text.lower())
        if lamp_canonical is not None:
            min_score = self._lamp_values.get(lamp_canonical)
            if min_score is not None:
                return min_score

        raise ResolutionError(message="읽을 수 없는 점수")


def _normalize_category(raw: str) -> str:
    text = raw.strip()
    if text in CATEGORY_LABELS:
        return text
    if text in _LABEL_TO_CATEGORY:
        return _LABEL_TO_CATEGORY[text]
    raise ValueError(f"모르는 분류: {text}")


def _parse_numeric_score(text: str) -> int | None:
    stripped = text.replace(",", "")

    if stripped.lower().endswith("k"):
        try:
            return int(Decimal(stripped[:-1]) * 1000)
        except InvalidOperation:
            return None

    if stripped.isdigit():
        return int(stripped)

    hangul = _parse_hangul_number(text.replace(" ", "").replace(",", ""))
    if hangul is not None:
        return hangul

    return None


def _parse_hangul_number(text: str) -> int | None:
    if not text or not any(ch in _HANGUL_SCALE for ch in text):
        return None

    total = 0
    current = ""
    for ch in text:
        if ch.isdigit():
            current += ch
            continue
        if ch in _HANGUL_SCALE:
            n = int(current) if current else 1
            total += n * _HANGUL_SCALE[ch]
            current = ""
            continue
        return None  # 모르는 글자가 섞이면 한글 숫자가 아니다
    if current:
        total += int(current)
    return total
