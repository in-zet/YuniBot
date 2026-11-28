# services/arcaea/profile.py
from __future__ import annotations

from pathlib import Path

from services.arcaea.rating import CLEAR_BONUS, chart_rating
from services.game.profile import GameProfile

ARCAEA = GameProfile(
    game="arcaea",
    label="Arcaea",
    aliases=("아르케아", "아케아"),
    chart_codes=("PST", "PRS", "FTR", "ETR", "BYD"),
    range_codes=("PST", "PRS", "FTR", "ETR", "BYD"),
    long_to_short={"PAST": "PST", "PRESENT": "PRS", "FUTURE": "FTR", "ETERNAL": "ETR", "BEYOND": "BYD"},
    default_difficulty_long="FUTURE",
    default_difficulty_code="FTR",
    # 곡 DB의 버전은 그 곡이 들어온 큰 버전("6.11.2" → "6.0")이다. 새 큰 버전이 나오면 끝에 더한다.
    version_order=("1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0"),
    # 7.0.0부터 포텐셜 = (베스트 50 + 그중 상위 10곡 한 번 더) ÷ 60, 셋째 자리에서 버림. 최근 기록·신곡 틀은 없다.
    best_frame_size=50,
    new_frame_size=0,
    best_label="베스트 50",
    new_label="",
    best_double_count=10,
    # 후보곡 기준점은 EX+(사용자 결정 2026-11-28).
    candidate_target_label="EX+",
    candidate_target_score=9_900_000,
    net_name="Arcaea Online",
    account="arcaea",
    login_command="/로그인 아르케아",
    rating_label="포텐셜",
    max_score=10_000_000,
    play_count_scope="올해",
    full_combo_lamp="FULL RECALL",
    all_justice_lamp="PURE MEMORY",
    all_justice_label="퓨어 메모리",
    has_overpower=False,
    terms_path=Path(__file__).parent / "data" / "terms.yaml",
    chart_rating=chart_rating,
    clear_lamp_rating_bonus={"TRACK LOST": -CLEAR_BONUS},
    song_rating_places=4,
    rating_divisor=60,
    overall_places=3,
    displayed_places=3,
    # 게임의 클리어 종류 하나를 둘로 나눠 담는다: 게이지(클리어 램프)와 FR·PM(콤보 램프).
    clear_lamp_order={"TRACK LOST": 0, "EASY CLEAR": 1, "NORMAL CLEAR": 2, "HARD CLEAR": 3},
    combo_lamp_order={"NONE": 0, "FULL RECALL": 1, "PURE MEMORY": 2},
    chain_lamp_order={},
    synced_tag="arcaea-synced",
)
