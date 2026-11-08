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
_SEARCH = ("song_find_songs",)

SCENARIOS: tuple[Scenario, ...] = (
    # 상수표 — 어떤 조건이 붙어도 보이는 표는 const_table을 거쳐야 한다(상수 묶음·난이도·점수 정렬).
    Scenario(name="상수표/상수 범위", question="15.0에서 15.5 상수표 보여줘", shown_from=_CONST_TABLE),
    Scenario(
        name="상수표/츄니 신곡 마스터·울티마 그림",
        question="츄니즘 신곡에 포함되는 곡 마스터/울티마만 상수표 그려줘",
        shown_from=_CONST_TABLE, must_call=("song_find_songs", "image_table_image"),
    ),
    Scenario(
        name="상수표/마이 신곡 마스터·리마스터 그림",
        question="마이마이 신곡에 포함되는 곡 마스터/리마스터만 상수표 그려줘",
        shown_from=_CONST_TABLE, must_call=("song_find_songs", "image_table_image"),
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
    # 후보곡·분석 — 호출을 본다. 상수 범위 밖의 조건은 find_songs 표로 받는다.
    Scenario(name="후보곡/상수 범위", question="15.0~15.5에서 레이팅 올릴 후보곡 뽑아줘", must_call_any=("report_candidates",)),
    Scenario(
        name="후보곡/레벨 조건", question="레벨 14 곡 중에 레이팅 올릴 후보곡 뽑아줘",
        must_call=("song_find_songs", "report_candidates"),
    ),
    Scenario(
        name="후보곡/신곡", question="신곡 중에서 레이팅 올릴 후보곡 보여줘",
        must_call=("song_find_songs", "report_candidates"),
    ),
    Scenario(name="분석/상수대", question="15대 상수 랭크 분포 보여줘", must_call_any=("report_analyze",)),
    Scenario(
        name="분석/레벨 조건", question="레벨 14 마스터 랭크 분포 보여줘",
        must_call=("song_find_songs", "report_analyze"),
    ),
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
    # 아래는 2026-11-07 약점 탐색(질문 339개)에서 실패가 잦던 유형이다.
    # 상대 날짜 — 프롬프트의 오늘 날짜로 기한을 정한다(되묻지 않는다).
    Scenario(name="날짜/연말", question="연말까지 레이팅 17.00 목표", must_call=("goals_set_goal",)),
    Scenario(name="날짜/요일", question="다음 주 금요일까지 레이팅 16.9 목표", must_call=("goals_set_goal",)),
    Scenario(name="날짜/개월", question="3개월 안에 레이팅 17.3", must_call=("goals_set_goal",)),
    # 애매한 "계정 끊어줘"는 멘션에서 확인 없이 실행되는 로그아웃을 부르지 않고 묻는다.
    Scenario(name="계정/모호한 해제", question="계정 끊어줘", must_not_call=("sega-account_logout",)),
    Scenario(name="계정/연결 해제", question="연결 해제", must_not_call=("sega-account_logout",)),
    Scenario(name="계정/분명한 로그아웃", question="세가 로그아웃", must_call=("sega-account_logout",)),
    # 약속과 기억 — 앞으로 할 일은 약속, 지금의 사실은 기억.
    Scenario(name="약속/약속해", question="약속해, 다음에 Chronomia 연습할게", must_call=("yuni-bond_promise_set",)),
    Scenario(
        name="약속/잊지 마", question="다음 번엔 15.4 곡 위주로 칠게, 잊지 마", must_not_call=("remember",),
    ),
    Scenario(name="기억/사실", question="나 오른손잡이야, 기억해", must_call=("remember",)),
    # 잇기
    Scenario(
        name="잇기/후보곡 투두", question="후보곡 10개 투두로 넣어줘, 목표는 SSS",
        must_call=("report_candidates", "goals_add_todos"),
    ),
    Scenario(
        name="잇기/보더 그림", question="15.0 마스 곡 SSS 보더 그림으로",
        must_call=("chunithm-border_add_borders", "image_table_image"),
    ),
    # 용어 — 사전에 있는 말은 사전 뜻으로 답한다(웹 검색으로 넘어가지 않는다).
    Scenario(
        name="용어/레이팅 계산", question="레이팅 계산 방식 알려줘",
        must_call=("terms_explain",), must_not_call=("web-qa_search",),
    ),
    Scenario(
        name="용어/오버파워", question="오버파워가 뭔데", must_call=("terms_explain",), must_not_call=("web-qa_search",),
    ),
    # 한글 곡명 — 한글 별칭으로 곡을 찾는다(곡 정보 도구든 곡명 검색이든).
    Scenario(name="한글곡명/줄임말", question="월뱅 마스터 상수", must_call_any=("song_chart", "song_info", "song_find_songs")),
    Scenario(name="한글곡명/발음", question="라미아 마스터 상수", must_call_any=("song_chart", "song_info", "song_find_songs")),
    # 관리 — 바꾸라고 한 값만 바꾼다.
    Scenario(name="관리/상한 하나", question="월 비용 상한 20달러로 바꿔줘", must_call=("admin_set_limits",)),
    # 기록·폴더가 있어야 끝까지 가는 흐름(평가 플레이어는 지어낸 기록과 폴더 "연습"을 받는다).
    Scenario(
        name="기록/베스트 그림", question="내 베스트 그림으로 뽑아 줘",
        shown_from=("report_best",), must_call=("image_table_image",),
    ),
    Scenario(
        name="기록/폴더 분석", question="연습 폴더 랭크 분포 보여줘",
        shown_from=("report_analyze",), must_call=("folders_show_folder",),
    ),
    Scenario(
        name="기록/점수 있는 상수표", question="15.0에서 15.5 상수표 점수 있는 것만",
        shown_from=_CONST_TABLE,
    ),
)
