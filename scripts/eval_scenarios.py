"""해석 평가 시나리오. 기대값은 "지금 동작"이 아니라 "맞는 동작"이다 — 실패는 고칠 거리다.

shown_from: 사용자에게 보인 표(그림으로 그렸으면 그 표, 아니면 턴의 마지막 표)를 만든 도구 계보에
            이 중 하나가 있어야 한다. 표를 받아 새 표를 만든 도구는 받은 표의 계보를 물려받는다.
must_call_any: 이 중 하나는 불려야 한다.
must_call: 전부 불려야 한다.
must_not_call: 하나도 불리면 안 된다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, kw_only=True, frozen=True)
class Scenario:
    name: str
    question: str
    shown_from: tuple[str, ...] = ()
    must_call_any: tuple[str, ...] = ()
    must_call: tuple[str, ...] = ()
    must_not_call: tuple[str, ...] = ()


_CONST_TABLE = ("report_const_table",)
_SEARCH = ("song_find_songs", "song_find_by_const", "song_find_by_version")

SCENARIOS: tuple[Scenario, ...] = (
    # 상수표 — 어떤 조건이 붙어도 보이는 표는 const_table을 거쳐야 한다(상수 묶음·난이도·점수 정렬).
    Scenario(name="상수표/상수 범위", question="15.0에서 15.5 상수표 보여줘", shown_from=_CONST_TABLE),
    Scenario(
        name="상수표/츄니 신곡 마스터·울티마 그림",
        question="츄니즘 신곡에 포함되는 곡 마스터/울티마만 상수표 그려줘",
        shown_from=_CONST_TABLE, must_call=("image_table_image",),
    ),
    Scenario(
        name="상수표/마이 신곡 마스터·리마스터 그림",
        question="마이마이 신곡에 포함되는 곡 마스터/리마스터만 상수표 그려줘",
        shown_from=_CONST_TABLE, must_call=("image_table_image",),
    ),
    Scenario(name="상수표/레벨 조건", question="레벨 14+ 마스터 상수표 보여줘", shown_from=_CONST_TABLE),
    Scenario(name="상수표/버전 조건", question="LUMINOUS PLUS 버전 곡들 상수표 보여줘", shown_from=_CONST_TABLE),
    Scenario(
        name="상수표/노트 수 조건", question="15 상수 중에 노트 수 1500개 넘는 것만 상수표로 보여줘",
        shown_from=_CONST_TABLE,
    ),
    Scenario(
        name="상수표/낱말 없이", question="15.5 이상 마스터를 상수별로 묶어서 내 점수랑 같이 보여줘",
        shown_from=_CONST_TABLE,
    ),
    # 곡 검색 — 상수표가 아닌 단순 검색은 song 검색 도구로.
    Scenario(name="검색/상수 범위", question="15.2에서 15.4 사이 마스 곡 보여 줘", shown_from=_SEARCH),
    Scenario(
        name="검색/조건 둘", question="15.0에서 15.2 상수 중에 노트 수 1000개 넘는 곡 뭐 있어?",
        shown_from=("song_find_songs",),
    ),
    Scenario(name="검색/버전", question="LUMINOUS PLUS 버전 곡 다 보여 줘", shown_from=_SEARCH),
    # 후보곡·분석 — 기록 없는 평가 플레이어라 표가 안 나올 수 있어 호출만 본다.
    Scenario(name="후보곡/상수 범위", question="15.0~15.5에서 레이팅 올릴 후보곡 뽑아줘", must_call_any=("report_candidates",)),
    Scenario(name="후보곡/레벨 조건", question="레벨 14 곡 중에 레이팅 올릴 후보곡 뽑아줘", must_call_any=("report_candidates",)),
    Scenario(name="후보곡/신곡", question="신곡 중에서 레이팅 올릴 후보곡 보여줘", must_call_any=("report_candidates",)),
    Scenario(name="분석/상수대", question="15대 상수 랭크 분포 보여줘", must_call_any=("report_analyze",)),
    Scenario(name="분석/레벨 조건", question="레벨 14 마스터 랭크 분포 보여줘", must_call_any=("report_analyze",)),
    # "신곡"은 뜻이 둘이다 — 업데이트 일정의 신곡과 지금 버전의 신곡.
    Scenario(
        name="신곡/업데이트 일정", question="다음 업데이트 신곡 뭐 나와?",
        must_call_any=("updates_new_songs",), must_not_call=("song_find_songs",),
    ),
    Scenario(
        name="신곡/지금 버전", question="지금 버전 신곡 마스터 상수 보여줘",
        shown_from=_SEARCH + _CONST_TABLE, must_not_call=("updates_new_songs",),
    ),
    Scenario(
        name="보더/채보 하나", question="크로노 마스 SSS 보더 알려줘",
        must_call_any=("chunithm-border_lookup", "chunithm-border_add_borders"),
    ),
)
