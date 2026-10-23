# services/chunithm/profile.py
from __future__ import annotations

from services.game.profile import GameProfile

CHUNITHM = GameProfile(
    game="chunithm",
    label="CHUNITHM",
    chart_codes=("BAS", "ADV", "EXP", "MAS", "ULT", "WE"),
    # WORLD'S END는 쉬움·어려움 축에 없어 범위에 넣지 않는다.
    range_codes=("BAS", "ADV", "EXP", "MAS", "ULT"),
    long_to_short={
        "BASIC": "BAS", "ADVANCED": "ADV", "EXPERT": "EXP",
        "MASTER": "MAS", "ULTIMA": "ULT", "WORLD'S END": "WE",
    },
    default_difficulty_long="MASTER",
    default_difficulty_code="MAS",
    # 목록에 없는 새 버전은 정렬에서 값 없음으로 취급되므로 새 버전이 나오면 끝에 더한다.
    version_order=(
        "CHUNITHM", "CHUNITHM PLUS", "AIR", "AIR PLUS", "STAR", "STAR PLUS", "AMAZON", "AMAZON PLUS",
        "CRYSTAL", "CRYSTAL PLUS", "PARADISE", "PARADISE LOST", "NEW", "NEW PLUS", "SUN", "SUN PLUS",
        "LUMINOUS", "LUMINOUS PLUS", "VERSE", "X-VERSE", "X-VERSE-X", "Mate",
    ),
    best_frame_size=30,
    new_frame_size=20,
    # 후보곡 상승폭·남은 점수의 기준점은 SSS다(report.md "후보곡").
    candidate_target_score=1_007_500,
    synced_tag="chunithm-synced",
)
