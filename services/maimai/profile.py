# services/maimai/profile.py
from __future__ import annotations

from services.game.profile import GameProfile
from services.maimai.rating import chart_rating

# otoge-db 버전 코드의 앞 세 자리 → 버전 이름(출시 순서). 5로 끝나는 코드는 PLUS다(예: 26500 = CiRCLE PLUS).
# 뒤 두 자리는 그 버전 안의 업데이트 차수다. 2026-10-23 music-ex-intl.json의 버전 27가지 전부와 대조했다.
# 목록에 없는 새 버전은 이름 대신 코드 세 자리가 버전으로 들어가므로 새 버전이 나오면 끝에 더한다.
VERSION_NAMES: dict[str, str] = {
    "100": "maimai", "110": "maimai PLUS", "120": "GreeN", "130": "GreeN PLUS",
    "140": "ORANGE", "150": "ORANGE PLUS", "160": "PiNK", "170": "PiNK PLUS",
    "180": "MURASAKI", "185": "MURASAKI PLUS", "190": "MiLK", "195": "MiLK PLUS", "199": "FiNALE",
    "200": "maimai DX", "205": "maimai DX PLUS", "210": "Splash", "215": "Splash PLUS",
    "220": "UNiVERSE", "225": "UNiVERSE PLUS", "230": "FESTiVAL", "235": "FESTiVAL PLUS",
    "240": "BUDDiES", "245": "BUDDiES PLUS", "250": "PRiSM", "255": "PRiSM PLUS",
    "260": "CiRCLE", "265": "CiRCLE PLUS",
}

MAIMAI = GameProfile(
    game="maimai",
    label="maimai DX",
    # 宴(UTAGE)은 츄니즘 WE와 같은 취급이다: 상수 없는 채보로 기록·검색은 하고 범위에서는 뺀다.
    chart_codes=("BAS", "ADV", "EXP", "MAS", "REM", "UTA"),
    range_codes=("BAS", "ADV", "EXP", "MAS", "REM"),
    chart_types=("STD", "DX"),
    long_to_short={
        "BASIC": "BAS", "ADVANCED": "ADV", "EXPERT": "EXP",
        "MASTER": "MAS", "Re:MASTER": "REM", "UTAGE": "UTA",
    },
    default_difficulty_long="MASTER",
    default_difficulty_code="MAS",
    version_order=tuple(VERSION_NAMES.values()),
    best_frame_size=35,
    new_frame_size=15,
    # 후보곡 기준점은 SSS+(달성률 100.5%)다. 점수는 달성률 ×10000으로 저장한다. Part 5에서 레이팅 공식과 함께 다시 본다.
    candidate_target_score=1_005_000,
    # 레이팅은 구곡 35 + 신곡 15의 곡 레이팅(정수) 합이다. 나누지 않는다.
    chart_rating=chart_rating,
    rating_divisor=1,
    overall_places=0,
    displayed_places=0,
    # 램프 문자열은 어댑터(Part 3)가 이 이름으로 맞춰 넘긴다. 클리어는 달성률 랭크가 대신해 램프가 없다.
    clear_lamp_order={},
    combo_lamp_order={"NONE": 0, "FC": 1, "FC+": 2, "AP": 3, "AP+": 4},
    chain_lamp_order={"NONE": 0, "SYNC": 1, "FS": 2, "FS+": 3, "FDX": 4, "FDX+": 5},
    synced_tag="maimai-synced",
)
