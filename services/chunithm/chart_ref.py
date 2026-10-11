from __future__ import annotations

from dataclasses import dataclass

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.songdb import ChartRecord, SongDbService, SongRecord
from services.chunithm.terms import TermsService

_DEFAULT_DIFFICULTY = "MASTER"
_DEFAULT_DIFFICULTY_CODE = "MAS"

# terms.difficulty_token()이 돌려주는 정식 긴 이름("MASTER")을 로컬 시드가 쓰는
# 짧은 코드("MAS")로 바꾼다 — songdb의 ChartRecord.difficulty는 짧은 코드를 쓴다.
_LONG_TO_SHORT = {
    "BASIC": "BAS", "ADVANCED": "ADV", "EXPERT": "EXP",
    "MASTER": "MAS", "ULTIMA": "ULT", "WORLD'S END": "WE",
}


@dataclass(slots=True, kw_only=True)
class ResolvedChart:
    song: SongRecord
    chart: ChartRecord


class ChartRefResolver:
    """SongQuery + 난이도가 합쳐진 원문 하나(예: "크로노 마스")를 채보로 바꾼다."""

    def __init__(self, *, songdb: SongDbService, terms: TermsService) -> None:
        self._songdb = songdb
        self._terms = terms

    async def resolve(self, raw: str, ctx: ToolContext) -> ResolvedChart:
        tokens = raw.strip().split()
        difficulty_code = _DEFAULT_DIFFICULTY_CODE
        song_tokens = tokens

        if tokens:
            last_token_difficulty = self._terms.difficulty_token(tokens[-1])
            first_token_difficulty = self._terms.difficulty_token(tokens[0])
            if last_token_difficulty is not None:
                difficulty_code = _LONG_TO_SHORT.get(last_token_difficulty, last_token_difficulty)
                song_tokens = tokens[:-1]
            elif first_token_difficulty is not None:
                difficulty_code = _LONG_TO_SHORT.get(first_token_difficulty, first_token_difficulty)
                song_tokens = tokens[1:]

        song_query = " ".join(song_tokens) if song_tokens else raw.strip()
        song = await self._songdb.resolve(song_query, ctx)

        chart = song.charts.get(difficulty_code)
        if chart is None:
            raise ResolutionError(message="그 난이도 없음")
        return ResolvedChart(song=song, chart=chart)
