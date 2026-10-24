from __future__ import annotations

from dataclasses import dataclass

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.game.songdb import ChartRecord, SongDb, SongRecord
from services.game.terms import TermsService


@dataclass(slots=True, kw_only=True)
class ResolvedChart:
    song: SongRecord
    chart: ChartRecord


class ChartRefResolver:
    """SongQuery + 난이도가 합쳐진 원문 하나(예: "크로노 마스")를 채보로 바꾼다."""

    def __init__(self, *, songdb: SongDb, terms: TermsService) -> None:
        self._songdb = songdb
        self._terms = terms
        self._profile = songdb.profile

    async def resolve(self, raw: str, ctx: ToolContext) -> ResolvedChart:
        text = raw.strip()

        # 먼저 원문 전체를 그대로 곡 이름으로 풀어본다(난이도 없음 → 기본 난이도).
        # "WORLD'S END"의 별칭에 bare "WE"가 있어, "We Are Us"처럼 실제로
        # 난이도 토큰처럼 보이는 단어로 시작/끝나는 곡 제목이 있다 — 토큰을
        # 먼저 떼어내 버리면 그런 곡을 못 찾는다. 전체 문자열이 그대로 곡
        # 하나로 풀리면 그게 맞는 답이니 토큰 떼기를 시도하지 않는다.
        try:
            song = await self._songdb.resolve(text, ctx)
            difficulty_code = self._profile.default_difficulty_code
        except ResolutionError:
            tokens = text.split()
            difficulty_code = self._profile.default_difficulty_code
            song_tokens = tokens

            if tokens:
                last_token_difficulty = self._terms.difficulty_token(tokens[-1])
                first_token_difficulty = self._terms.difficulty_token(tokens[0])
                if last_token_difficulty is not None:
                    difficulty_code = self._profile.long_to_short.get(
                        last_token_difficulty, last_token_difficulty
                    )
                    song_tokens = tokens[:-1]
                elif first_token_difficulty is not None:
                    difficulty_code = self._profile.long_to_short.get(
                        first_token_difficulty, first_token_difficulty
                    )
                    song_tokens = tokens[1:]

            song_query = " ".join(song_tokens) if song_tokens else text
            song = await self._songdb.resolve(song_query, ctx)

        chart = song.charts.get(difficulty_code)
        # 곡은 국제판에 있어도 그 채보만 삭제/미수록일 수 있다(예: DRAGONLADY의 WE) —
        # 없는 채보와 똑같이 다룬다.
        if chart is None or not chart.is_available:
            raise ResolutionError(message="그 난이도 없음")
        return ResolvedChart(song=song, chart=chart)
