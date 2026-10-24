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
        types = {t.lower(): t for t in self._profile.chart_types if t}
        chart_type: str | None = None
        difficulty_code: str | None = None

        # 먼저 원문 전체를 그대로 곡 이름으로 풀어본다(난이도 없음 → 기본 난이도).
        # "WORLD'S END"의 별칭에 bare "WE"가 있어, "We Are Us"처럼 실제로
        # 난이도 토큰처럼 보이는 단어로 시작/끝나는 곡 제목이 있다 — 토큰을
        # 먼저 떼어내 버리면 그런 곡을 못 찾는다. 전체 문자열이 그대로 곡
        # 하나로 풀리면 그게 맞는 답이니 토큰 떼기를 시도하지 않는다.
        try:
            song = await self._songdb.resolve(text, ctx)
        except ResolutionError:
            tokens = text.split()
            # 끝에서, 안 되면 앞에서 난이도 토큰(과 채보 종류 토큰)을 하나씩 뗀다.
            for from_end in (True, False):
                while tokens:
                    token = tokens[-1] if from_end else tokens[0]
                    long_name = self._terms.difficulty_token(token)
                    if long_name is not None and difficulty_code is None:
                        difficulty_code = self._profile.long_to_short.get(long_name, long_name)
                    elif token.lower() in types and chart_type is None:
                        chart_type = types[token.lower()]
                    else:
                        break
                    tokens = tokens[:-1] if from_end else tokens[1:]
                if difficulty_code is not None or chart_type is not None:
                    break

            song_query = " ".join(tokens) if tokens else text
            song = await self._songdb.resolve(song_query, ctx)

        return ResolvedChart(
            song=song, chart=self._pick_chart(song, difficulty_code, chart_type)
        )

    def _pick_chart(self, song: SongRecord, difficulty_code: str | None, chart_type: str | None) -> ChartRecord:
        code = difficulty_code or self._profile.default_difficulty_code
        available = [
            chart for chart in song.charts.values()
            if chart.is_available and chart.difficulty == code and (chart_type is None or chart.chart_type == chart_type)
        ]
        if not available and difficulty_code is None and self._profile.chart_types != ("",):
            # 난이도를 말하지 않았는데 기본 난이도가 없는 곡(maimai 宴 등)은 채보가 하나뿐이면 그것이다.
            only = [
                chart for chart in song.charts.values()
                if chart.is_available and (chart_type is None or chart.chart_type == chart_type)
            ]
            if len(only) == 1:
                return only[0]
        # 곡은 국제판에 있어도 그 채보만 삭제/미수록일 수 있다(예: DRAGONLADY의 WE) —
        # 없는 채보와 똑같이 다룬다.
        if not available:
            raise ResolutionError(message="그 난이도 없음")
        if len(available) > 1:
            # 같은 난이도에 채보 종류가 둘이면(maimai STD/DX) 어느 쪽인지 되묻는다.
            raise ResolutionError(
                candidates=[f"{song.title} {chart.chart_type} {chart.difficulty}" for chart in available]
            )
        return available[0]
