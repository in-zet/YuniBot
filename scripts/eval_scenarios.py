"""해석 평가 시나리오. 기대값은 "지금 동작"이 아니라 "맞는 동작"이다 — 실패는 고칠 거리다.

shown_from: 사용자에게 보인 표(그림으로 그렸으면 그 표, 아니면 턴의 마지막 표)를 만든 도구 계보에
            이 중 하나가 있어야 한다. 표를 받아 새 표를 만든 도구는 받은 표의 계보를 물려받는다.
must_call_any: 이 중 하나는 불려야 한다.
must_call: 전부 불려야 한다.
must_not_call: 하나도 불리면 안 된다.
must_args: (도구, 인자, 글자) — 그 도구를 부른 호출 중 하나는 그 인자 값(목록이면 항목을 이어 붙인 글)에 그 글자가 들어 있어야 한다.
           글자는 "|"로 나눠 여러 개를 줄 수 있다(하나만 들어 있으면 된다).
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
    must_args: tuple[tuple[str, str, str], ...] = ()
    # 대화 스레드 안의 지침처럼 프롬프트 맥락에 더 넣을 글(튜토리얼 DM 대화 시험용).
    context: str = ""



def _tutorial_context(history: str) -> str:
    """튜토리얼 DM 대화의 실제 지침(skills/tutorial/skill.py)에 지금까지의 대화를 붙인다."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "skills" / "tutorial" / "skill.py"
    spec = importlib.util.spec_from_file_location("_eval_tutorial_skill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return f"{module._guidance()}\n{history}"  # pyright: ignore[reportPrivateUsage]


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
    # 구곡·신곡 상위 — 숨은 `구분` 칸으로 거르고 table_concat으로 합친다(2026-11-22, 버전 이름으로 거르다 상한에 걸렸다).
    Scenario(
        name="검색/구곡·신곡 상수 상위",
        question="모든 구곡 중 상수 높은 순 30개, 모든 신곡 중 상수 높은 순 20개 뽑아줘",
        shown_from=("table_concat",), must_call=("song_find_songs", "table_concat"),
    ),
    Scenario(
        name="기록/구곡·신곡 상수 상위 AJC 가정",
        question="모든 구곡 중 상수 높은 순 30개랑 모든 신곡 중 상수 높은 순 20개가 전부 AJC라고 가정한 레이팅표 그려줘",
        shown_from=("report_what_if",), must_call=("table_concat", "report_what_if", "image_table_image"),
    ),
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
    # 한글로만 들은 곡 이름 — 못 찾으면 되묻기 전에 원제 글자(한자·가타카나·영문)로 찾아 본다.
    Scenario(
        name="곡 찾기/한글 이름을 한자로", question="봉염의135초 츄니즘 최고기록 알려줘",
        must_call=("song_find_songs",),
    ),
    Scenario(
        name="곡 찾기/한글 이름을 영문으로", question="글로리우스 크라운 츄니즘 마스터 상수 알려줘",
        must_call_any=("song_find_songs", "song_chart"),
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
    # 튜토리얼 — 허락을 받기 전에는 DM을 열지 않는다(2026-11-22). 구체적인 질문은 튜토리얼 없이 답한다.
    Scenario(
        name="튜토리얼/허락 먼저", question="나 이거 처음 쓰는데 어떻게 쓰는 거야?",
        must_not_call=("tutorial_tutorial_start",),
    ),
    Scenario(
        name="튜토리얼/DM으로 해 달라", question="처음 써 보는데 DM으로 튜토리얼 진행해 줘",
        must_call=("tutorial_tutorial_start",),
    ),
    Scenario(
        name="튜토리얼/애매한 답에도 안내",
        question="다",
        context=_tutorial_context(
            "[지금까지의 대화] 유니: 주 게임이 뭐야? / 나: 츄니즘 / (로그인·동기화·레이팅표를 마쳤다) / "
            "유니: 자주 쓸 기능이 뭐야? 레이팅표 그림, 곡 상수 찾기, 동기화 중 하나부터 알려 줄게~"
        ),
        must_not_call=("end_conversation",),
    ),
    Scenario(
        name="튜토리얼/동기화 뒤 레이팅표",
        question="응 그려줘",
        context=_tutorial_context(
            "[지금까지의 대화] 유니: 주 게임이 뭐야? / 나: 츄니즘 / 유니: 로그인은 /로그인으로 해 줘 / 나: 했어 / "
            "유니: 기록 가져올까? / 나: 응 / (동기화가 끝났다, tutorial_status: 로그인·동기화 모두 true) / "
            "유니: 동기화 끝났어! 이제 레이팅표 그려 볼까?"
        ),
        must_call_any=("image_table_image", "report_best"),
    ),
    Scenario(
        name="튜토리얼/주 게임 답에 한 단계만",
        question="츄니즘이야",
        context=_tutorial_context(
            "[지금까지의 대화] 유니: 안녕! 나는 유니야. 이 대화는 요약해서 기억해 둘게. 주로 치는 게임이 뭐야? "
            "(이 플레이어는 이미 로그인·동기화를 마쳤다)"
        ),
        must_call=("tutorial_tutorial_set_main_game",),
        must_not_call=("report_best", "image_table_image", "sync_sync_run"),
    ),
    Scenario(
        name="튜토리얼/구체적 질문", question="동기화는 어떻게 해?", must_not_call=("tutorial_tutorial_start",),
    ),
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
    # 채보 하나를 가리키는 별칭("쿠로디닉" = Melodiniq ULTIMA)은 채보 찾기로 넘겨야 그 난이도가 골라진다.
    Scenario(name="한글곡명/채보 별칭", question="쿠로디닉 상수 몇이야", must_call=("song_chart",)),
    # 관리 — 바꾸라고 한 값만 바꾼다.
    Scenario(name="관리/상한 하나", question="월 비용 상한 20달러로 바꿔줘", must_call=("admin_set_limits",)),
    # 기록·폴더가 있어야 끝까지 가는 흐름(평가 플레이어는 지어낸 기록과 폴더 "연습"을 받는다).
    Scenario(
        name="기록/베스트 그림", question="내 베스트 그림으로 뽑아 줘",
        shown_from=("report_best",), must_call=("image_table_image",),
    ),
    # 여러 곡의 가정 점수는 what_if 한 번에 — 곡마다 찾다가 도구 호출 상한에 걸렸다(2026-11-21).
    Scenario(
        name="기록/가정 레이팅표 그림",
        question="메기도 1003866, 아이워나 1007270, 유루시테 1005039, 라미아 1005536, 도미너스 1003258을 가정한 레이팅표 그려줘",
        shown_from=("report_what_if",), must_call=("report_what_if", "image_table_image"),
        must_not_call=("rating-calc_song_rating",),
    ),
    # "마스터"를 난이도로 못 읽어 곡을 못 찾았다(2026-11-21).
    Scenario(
        name="기록/가정 마스터 표기",
        question="유니즘 마스터, 울티마 모두 트플플, 멜로디닉 마스터, 울티마 모두 트플플을 가정한 레이팅표 그려줘",
        shown_from=("report_what_if",), must_call=("report_what_if", "image_table_image"),
    ),
    # 레이팅표 50채보를 늘어놓다 개수를 틀리고 상한에 걸렸다(2026-11-21) — 표를 통째로 넘긴다. AJC는 되묻지 않는다.
    Scenario(
        name="기록/레이팅표 전부 AJC",
        question="지금 레이팅표에 있는 곡이 전부 AJC라고 가정해서 레이팅표 그려줘",
        shown_from=("report_what_if",), must_call=("report_what_if", "image_table_image"),
        must_args=(("report_what_if", "table", "t"),),
    ),
    # "그려줘"만 있고 "그림·레이팅표"가 없으면 그림 스킬이 안 펼쳐져 "그림 기능은 지금 쓸 수 없어"라고 답했다(2026-11-22).
    Scenario(
        name="그림/그려줘만", question="메기도 마스 1009000 가정해서 그려줘",
        shown_from=("report_what_if",), must_call=("report_what_if", "image_table_image"),
    ),
    # 마이마이 — 평가 플레이어는 지어낸 maimai 기록도 받는다(AP·AP+가 섞여 AP 보너스 계산도 거친다).
    Scenario(
        name="마이/베스트 그림", question="마이마이 내 베스트 그림으로 뽑아 줘",
        shown_from=("report_best",), must_call=("image_table_image",),
    ),
    Scenario(
        name="마이/AP 가정 레이팅표",
        question="마이마이에서 PANDORA PARADOXXX 리마스터 100.6% 올퍼로 치면 레이팅표 어떻게 돼? 그려줘",
        shown_from=("report_what_if",), must_call=("report_what_if", "image_table_image"),
        must_args=(("report_what_if", "lamps", "AP|퍼"),),
    ),
    # 이전 버전 기준 레이팅표 — best의 version으로 한 번에(2026-11-21).
    Scenario(
        name="기록/이전 버전 레이팅표 그림", question="엑스버스 기준으로 내 레이팅표 그려줘",
        shown_from=("report_best",), must_call=("image_table_image",), must_args=(("report_best", "version", "X-VERSE"),),
    ),
    Scenario(
        name="마이/이전 버전 레이팅표", question="마이마이 프리즘 때 기준으로 레이팅표 보여줘",
        shown_from=("report_best",), must_args=(("report_best", "version", "PRiSM"),),
    ),
    # 설정 — maimai는 같은 도구에 게임 인자와 maimai 전용 항목(2026-11-27, plan 6-8).
    Scenario(
        name="설정/마이 터치 속도", question="마이마이 터치 속도 6.00으로 바꿔줘",
        must_call=("settings_change_settings",),
        must_args=(("settings_change_settings", "touch_speed", "6.00"), ("settings_change_settings", "game", "마이|maimai")),
    ),
    Scenario(
        name="설정/츄니 스피드", question="스피드 9.00으로 바꿔줘",
        must_call=("settings_change_settings",), must_args=(("settings_change_settings", "speed", "9.00"),),
    ),
    Scenario(
        name="오락실/마이 세팅", question="겜플 마이마이 세팅 알려 줘",
        must_call=("arcade_get_settings",), must_args=(("arcade_get_settings", "game", "마이|maimai"),),
    ),
    Scenario(
        name="설정/즐겨찾기 전체 삭제", question="즐겨찾기 전부 지워줘",
        must_call=("settings_clear_favorites",), must_not_call=("settings_remove_favorite",),
    ),
    Scenario(
        name="설정/즐겨찾기 일괄 삭제", question="15.0 상수 곡들 즐겨찾기에서 다 빼줘",
        must_call=("settings_remove_favorites",), must_not_call=("settings_clear_favorites", "settings_remove_favorite"),
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
