# services/game/profile.py
"""게임마다 다른 값을 한 곳에 모은 자료형. 서비스는 프로필을 받아 동작하고 `if game == …`로 가르지 않는다.

게임을 더할 때는 `services/<게임>/profile.py`에 프로필 하나를 만들고 `services/game/registry.py`에 등록한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class GameProfile:
    game: str
    label: str
    # 사용자가 이 게임을 부르는 말(소문자). `game`·`label`은 따로 적지 않아도 된다.
    aliases: tuple[str, ...] = ()

    # 채보 난이도 코드를 낮은 쪽부터. 상수 없는 채보(WORLD'S END 등)도 기록·검색 대상이라 여기 들어 있다.
    chart_codes: tuple[str, ...]
    # 난이도 범위("익스퍼트 이상")가 훑는 순서. 쉬움·어려움 축에 없는 채보는 뺀다.
    range_codes: tuple[str, ...]
    # 같은 곡·난이도에 채보 종류가 따로 있는 게임(maimai STD/DX)의 종류. 없는 게임은 ("",).
    chart_types: tuple[str, ...] = ("",)
    # `terms`가 돌려주는 정식 긴 이름("MASTER") → 짧은 코드("MAS").
    long_to_short: Mapping[str, str]
    # 난이도를 말하지 않았을 때 쓰는 채보.
    default_difficulty_long: str
    default_difficulty_code: str

    # 수록 버전 이름을 출시 순서(오래된 것부터)로 나열한 것. 곡 DB의 `version` 값과 철자가 같다.
    version_order: tuple[str, ...]

    # 레이팅 프레임: 베스트와 신곡 각각 몇 곡을 세는가.
    best_frame_size: int
    new_frame_size: int
    # 후보곡 상승폭·남은 점수의 기준점이 되는 점수(츄니즘은 SSS).
    candidate_target_score: int

    # 값 용어(랭크·난이도·램프) 사전 파일과, 점수를 달성률(%)로 말하는 게임인지(maimai).
    terms_path: Path
    score_is_percent: bool = False

    # 레이팅 규칙. 곡 레이팅 함수는 (점수, 상수)를 받아 곡 레이팅을 돌려주고, 상수를 모르면 None("계산 불가")이다.
    chart_rating: Callable[[int, float | None], Decimal | None]
    # 곡 레이팅을 보일 때 소수 자리수(CHUNITHM 2, maimai 0).
    song_rating_places: int
    # 전체 레이팅 = 프레임 곡 레이팅 합 ÷ rating_divisor, overall_places 자리에서 버림(CHUNITHM 50·4, maimai 1·0).
    rating_divisor: int
    overall_places: int
    # NET이 보여 주는 레이팅의 자리수. 계산값을 이 자리에서 버려 표시값과 대조한다.
    displayed_places: int

    # 램프 저장 문자열 → 우열(클수록 좋다). 모르는 문자열은 0. 게임에 없는 램프 종류는 비워 둔다.
    clear_lamp_order: Mapping[str, int]
    combo_lamp_order: Mapping[str, int]
    chain_lamp_order: Mapping[str, int]

    # 첫 동기화를 마친 플레이어에게 붙는 상태 태그. 게임마다 따로 있다.
    synced_tag: str
