from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import aiosqlite
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
class ServerTermProposal:
    id: str
    guild_id: str
    term: str
    meaning: str
    aliases: tuple[str, ...]
    suggested_by: str
    status: str
    reviewed_by: str | None
    created_at: str
    reviewed_at: str | None


def _row_to_proposal(row: aiosqlite.Row) -> ServerTermProposal:
    return ServerTermProposal(
        id=row["id"],
        guild_id=row["guild_id"],
        term=row["term"],
        meaning=row["meaning"],
        aliases=tuple(json.loads(row["aliases"])),
        suggested_by=row["suggested_by"],
        status=row["status"],
        reviewed_by=row["reviewed_by"],
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
    )


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

    def __init__(self, data_path: Path = _DATA_PATH, *, conn: aiosqlite.Connection | None = None) -> None:
        entries = _load_entries(data_path)
        self._entries = entries
        self._rank_aliases = _alias_map(entries, "rank")
        self._rank_values = _value_map(entries, "rank")
        self._difficulty_aliases = _alias_map(entries, "difficulty")
        self._lamp_aliases = _alias_map(entries, "lamp")
        self._lamp_values = _value_map(entries, "lamp")
        self._conn = conn
        if self._conn is not None:
            self._conn.row_factory = aiosqlite.Row

    def rank_scores(self) -> dict[str, int]:
        """랭크 정식 이름 -> 기준 점수. 작업 표의 랭크 칸 원래 값을 만들 때 쓴다."""
        return {name: value for name, value in self._rank_values.items() if value is not None}

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
        찾아 뜻과 출처를 돌려준다. 전역에서 못 찾으면 이 서버의 승인된 은어도 본다.
        둘 다 없으면 None."""
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
        if self._conn is not None and ctx.guild_id is not None:
            proposal = await self._find_approved_server_term(ctx.guild_id, text)
            if proposal is not None:
                return TermExplanation(
                    canonical=proposal.term,
                    category_label="서버 은어",
                    aliases=proposal.aliases,
                    value=None,
                    meaning=proposal.meaning,
                    source="서버 사용",
                    scope="이 서버",
                )
        return None

    async def list_terms(self, category: str | None, ctx: ToolContext) -> list[TermListItem]:
        """분류(한글 라벨 "랭크"/"기술" 등, 또는 내부 키)로 거른 용어 목록.
        category가 None이면 전부(이 서버의 승인된 은어도 함께). 모르는 분류면 ValueError."""
        key = None if category is None else _normalize_category(category)
        items = [
            TermListItem(
                canonical=entry.canonical,
                category_label=CATEGORY_LABELS.get(entry.category, entry.category),
                aliases=entry.aliases,
                value=entry.value,
            )
            for entry in self._entries
            if key is None or entry.category == key
        ]
        if self._conn is not None and ctx.guild_id is not None and key is None:
            for proposal in await self._approved_server_terms(ctx.guild_id):
                items.append(
                    TermListItem(
                        canonical=proposal.term,
                        category_label="서버 은어",
                        aliases=proposal.aliases,
                        value=None,
                    )
                )
        return items

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

    async def propose(
        self, *, guild_id: str, term: str, meaning: str, aliases: list[str], suggested_by: str
    ) -> str:
        """이 서버에 은어를 등록하고(상태 "approved") id를 돌려준다. 신고제라 승인을
        기다리지 않고 바로 explain/list_terms에 보인다 — admin은 review()로 나중에
        끄거나(철회) 되돌릴 수 있다."""
        assert self._conn is not None
        proposal_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO terms_server (id, guild_id, term, meaning, aliases, suggested_by, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'approved', ?)",
            (proposal_id, guild_id, term, meaning, json.dumps(aliases), suggested_by, now),
        )
        await self._conn.commit()
        return proposal_id

    async def get_proposal(self, proposal_id: str) -> ServerTermProposal | None:
        assert self._conn is not None
        cursor = await self._conn.execute("SELECT * FROM terms_server WHERE id = ?", (proposal_id,))
        row = await cursor.fetchone()
        return _row_to_proposal(row) if row is not None else None

    async def review(self, proposal_id: str, *, approve: bool, reviewed_by: str) -> ServerTermProposal | None:
        """admin이 등록된 은어를 껐다(approve=False, 철회) 켰다(approve=True, 되돌리기) 한다.
        신고제라 기본은 이미 켜져 있다 — 이 메서드는 부적절한 등록을 끄거나, 잘못 끈 것을
        다시 켜는 용도다. 제안이 없으면 None."""
        assert self._conn is not None
        proposal = await self.get_proposal(proposal_id)
        if proposal is None:
            return None
        now = datetime.now(UTC).isoformat()
        status = "approved" if approve else "rejected"
        await self._conn.execute(
            "UPDATE terms_server SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (status, reviewed_by, now, proposal_id),
        )
        await self._conn.commit()
        return await self.get_proposal(proposal_id)

    async def _approved_server_terms(self, guild_id: str) -> list[ServerTermProposal]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT * FROM terms_server WHERE guild_id = ? AND status = 'approved' ORDER BY term",
            (guild_id,),
        )
        return [_row_to_proposal(row) for row in await cursor.fetchall()]

    async def _find_approved_server_term(self, guild_id: str, text: str) -> ServerTermProposal | None:
        for proposal in await self._approved_server_terms(guild_id):
            if proposal.term.lower() == text or text in (a.lower() for a in proposal.aliases):
                return proposal
        return None


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
