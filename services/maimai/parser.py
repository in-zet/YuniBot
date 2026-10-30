# services/maimai/parser.py
"""maimai DX NET(maimaidx-eng.com) HTML 파서. 순수 함수만 둔다(네트워크·DB 없음).

시험 표본은 `docs/reference/maimai-net/`(2026-10-30 실측, 개인 정보를 지움)다. 페이지 구조는 TrueRou/maimai.py의
`page_parser.py`를 참고하되 selectolax로 다시 짰다. 채보 하나는 (곡 이름, 종류 STD/DX, 난이도)로 정해지고,
달성률은 ×10000 정수(100.5000% = 1_005_000)로 돌려준다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser, LexborNode

JST = ZoneInfo("Asia/Tokyo")

# 목록 항목의 클래스 `music_<난이도>_score_back`에서 읽는다. 宴(UTAGE)은 난이도 이름이 비어 있다.
_DIFFICULTY_BY_CLASS = {
    "basic": "BAS", "advanced": "ADV", "expert": "EXP", "master": "MAS", "remaster": "REM", "": "UTA",
}
# 최근 플레이 항목은 난이도 그림 `diff_<이름>.png`에서 읽는다.
_DIFFICULTY_BY_ICON = {"basic": "BAS", "advanced": "ADV", "expert": "EXP", "master": "MAS", "remaster": "REM", "utage": "UTA"}

_RANK_BY_ICON = {
    "sssp": "SSS+", "sss": "SSS", "ssp": "SS+", "ss": "SS", "sp": "S+", "s": "S",
    "aaa": "AAA", "aa": "AA", "a": "A", "bbb": "BBB", "bb": "BB", "b": "B", "c": "C", "d": "D",
}
# 램프 그림 이름. 최근 플레이 쪽이 `…plus`·`fsd`로 쓰는 것으로 보이는 이름도 받아 둔다(표본에는 램프가 없었다).
_COMBO_BY_ICON = {"fc": "FC", "fcp": "FC+", "ap": "AP", "app": "AP+"}
_SYNC_BY_ICON = {"sync": "SYNC", "fs": "FS", "fsp": "FS+", "fdx": "FDX", "fdxp": "FDX+"}
_NO_LAMP = ""

_ERROR_CODE = re.compile(r"ERROR CODE[：:]\s*(\d+)")
_PERCENT = re.compile(r"(\d+)\.(\d{4})%")


class MaimaiParseError(ValueError):
    """페이지가 기대한 모양이 아니다(사이트가 바뀌었을 수 있다)."""


@dataclass(slots=True, kw_only=True, frozen=True)
class MaimaiProfile:
    name: str
    rating: int
    icon_url: str | None


@dataclass(slots=True, kw_only=True, frozen=True)
class ScoreEntry:
    """채보 하나의 기록. 레이팅 대상곡 페이지는 램프·DX 점수를 주지 않아 그 칸이 비어 있다."""

    title: str
    chart_type: str  # "STD" | "DX"
    difficulty: str  # "BAS" … "REM" | "UTA"
    level: str
    achievement: int
    rank: str
    combo_lamp: str = _NO_LAMP
    sync_lamp: str = _NO_LAMP
    dx_score: int | None = None
    dx_score_max: int | None = None
    version_label: str | None = None  # 곡이 속한 버전 머리글(버전순 목록일 때)


@dataclass(slots=True, kw_only=True, frozen=True)
class RatingTargets:
    new: list[ScoreEntry]  # "Songs for Rating(New)" — 신곡 15
    old: list[ScoreEntry]  # "Songs for Rating(Others)" — 구곡 35


@dataclass(slots=True, kw_only=True, frozen=True)
class PlayEntry:
    """최근 플레이 한 번."""

    track: int
    played_at: datetime  # JST
    title: str
    chart_type: str
    difficulty: str
    level: str
    achievement: int
    rank: str
    dx_score: int
    dx_score_max: int
    combo_lamp: str
    sync_lamp: str
    new_record: bool


def _doc(html: str | bytes) -> LexborHTMLParser:
    return LexborHTMLParser(html)


def error_code(html: str | bytes) -> int | None:
    """오류 페이지(`/maimai-mobile/error/`)의 오류 코드. 오류 페이지가 아니면 None."""
    node = _doc(html).css_first("div.container_red")
    if node is None:
        return None
    found = _ERROR_CODE.search(node.text())
    return int(found.group(1)) if found else None


def _icon_name(node: LexborNode | None) -> str:
    """`…/music_icon_aaa.png?ver=1.65` → `aaa`, `…/playlog/fc_dummy.png` → `fc_dummy`."""
    if node is None:
        return ""
    src = (node.attributes.get("src") or "").split("?", 1)[0]
    stem = src.rsplit("/", 1)[-1].removesuffix(".png")
    return stem.removeprefix("music_icon_")


def _normalized(name: str) -> str:
    # 최근 플레이 쪽 그림은 `splus`·`fsdp`처럼 쓴다. 목록 쪽 이름(`sp`·`fdxp`)으로 맞춘다.
    if name.endswith("plus"):
        name = name.removesuffix("plus") + "p"
    return name.replace("fsd", "fdx")


def _lamp(name: str, table: dict[str, str]) -> str:
    return table.get(_normalized(name), _NO_LAMP)


def _rank(name: str) -> str:
    rank = _RANK_BY_ICON.get(_normalized(name))
    if rank is None:
        raise MaimaiParseError(f"모르는 랭크 그림이다: {name!r}")
    return rank


def _chart_type(node: LexborNode | None) -> str:
    name = _icon_name(node)
    if name == "music_dx":
        return "DX"
    if name == "music_standard":
        return "STD"
    raise MaimaiParseError(f"채보 종류 그림을 읽지 못했다: {name!r}")


def _achievement(text: str) -> int:
    found = _PERCENT.search(text.replace("\xa0", " "))
    if found is None:
        raise MaimaiParseError(f"달성률을 읽지 못했다: {text!r}")
    return int(found.group(1)) * 10000 + int(found.group(2))


def _dx_score(text: str) -> tuple[int, int]:
    found = re.search(r"([\d,]+)\s*/\s*([\d,]+)", text)
    if found is None:
        raise MaimaiParseError(f"DX 점수를 읽지 못했다: {text!r}")
    return int(found.group(1).replace(",", "")), int(found.group(2).replace(",", ""))


def _text(node: LexborNode | None) -> str:
    return node.text(strip=True) if node is not None else ""


def parse_profile(html: str | bytes) -> MaimaiProfile:
    """홈·플레이어 데이터·레이팅 대상곡 페이지 맨 위의 프로필 블록."""
    doc = _doc(html)
    name = doc.css_first("div.name_block")
    rating = doc.css_first("div.rating_block")
    if name is None or rating is None:
        raise MaimaiParseError("프로필 블록을 찾지 못했다")
    rating_text = rating.text(strip=True)
    if not rating_text.isdigit():
        raise MaimaiParseError(f"레이팅을 읽지 못했다: {rating_text!r}")
    icon = doc.css_first("div.basic_block img.w_112")
    return MaimaiProfile(
        name=name.text(strip=True), rating=int(rating_text), icon_url=icon.attributes.get("src") if icon else None
    )


def _score_back_entries(doc: LexborHTMLParser) -> list[LexborNode]:
    return [
        node for node in doc.css("div")
        if (node.attributes.get("class") or "").split(" ", 1)[0].endswith("_score_back")
    ]


def _difficulty_of(node: LexborNode) -> str:
    first = (node.attributes.get("class") or "").split(" ", 1)[0]
    name = first.removeprefix("music_").removesuffix("_score_back")
    try:
        return _DIFFICULTY_BY_CLASS[name]
    except KeyError:
        raise MaimaiParseError(f"모르는 난이도 칸이다: {first!r}") from None


def _version_label(node: LexborNode) -> str | None:
    """항목 앞에서 가장 가까운 버전 머리글(`div.screw_block`)."""
    previous = node.prev
    while previous is not None:
        if "screw_block" in (previous.attributes.get("class") or ""):
            return _text(previous)
        previous = previous.prev
    return None


def parse_music_list(html: str | bytes) -> list[ScoreEntry]:
    """난이도별 기록 목록(`record/musicSort/search/`). 안 친 곡은 점수 칸이 없어 건너뛴다."""
    doc = _doc(html)
    entries: list[ScoreEntry] = []
    for node in _score_back_entries(doc):
        blocks = node.css("div.music_score_block")
        if not blocks or not _PERCENT.search(blocks[0].text()):
            continue  # 기록 없음
        dx, dx_max = _dx_score(blocks[1].text()) if len(blocks) > 1 else (None, None)
        # 오른쪽 위젯 순서(HTML 순서): 싱크, 콤보, 랭크. 램프가 없으면 `music_icon_back` 자리표시자다.
        sync_icon, combo_icon, rank_icon = node.css("img.h_30.f_r")[:3]
        entries.append(
            ScoreEntry(
                title=_text(node.css_first("div.music_name_block")),
                chart_type=_chart_type(node.css_first("img.music_kind_icon")),
                difficulty=_difficulty_of(node),
                level=_text(node.css_first("div.music_lv_block")),
                achievement=_achievement(blocks[0].text()),
                rank=_rank(_icon_name(rank_icon)),
                combo_lamp=_lamp(_icon_name(combo_icon), _COMBO_BY_ICON),
                sync_lamp=_lamp(_icon_name(sync_icon), _SYNC_BY_ICON),
                dx_score=dx,
                dx_score_max=dx_max,
                version_label=_version_label(node),
            )
        )
    return entries


def parse_rating_targets(html: str | bytes) -> RatingTargets:
    """레이팅 대상곡 페이지(`home/ratingTargetMusic/`)의 신곡·구곡 목록."""
    doc = _doc(html)
    groups: dict[str, list[ScoreEntry]] = {"New": [], "Others": []}
    current: list[ScoreEntry] | None = None
    for node in doc.css("div.screw_block, div[class*=_score_back]"):
        classes = node.attributes.get("class") or ""
        if "screw_block" in classes:
            found = re.fullmatch(r"Songs for Rating\((New|Others)\)", node.text(strip=True))
            current = groups[found.group(1)] if found else None
            continue
        if current is None or not classes.split(" ", 1)[0].endswith("_score_back"):
            continue
        score = node.css_first("div.music_score_block")
        if score is None:
            raise MaimaiParseError("레이팅 대상곡 항목에 달성률 칸이 없다")
        current.append(
            ScoreEntry(
                title=_text(node.css_first("div.music_name_block")),
                chart_type=_chart_type(node.css_first("img.music_kind_icon")),
                difficulty=_difficulty_of(node),
                level=_text(node.css_first("div.music_lv_block")),
                achievement=_achievement(score.text()),
                rank=_rank(_icon_name(node.css_first("img.ratingtarget_scorerank_img"))),
            )
        )
    return RatingTargets(new=groups["New"], old=groups["Others"])


def parse_recent(html: str | bytes) -> list[PlayEntry]:
    """최근 플레이(`record/`) — 최대 50개, 최근 것이 먼저."""
    doc = _doc(html)
    plays: list[PlayEntry] = []
    for top in doc.css("div.playlog_top_container"):
        node = top.parent
        if node is None:
            continue
        label = top.css_first("div.sub_title")
        found = re.search(r"TRACK\s*(\d+)\D+(\d{4}/\d{2}/\d{2} \d{2}:\d{2})", label.text() if label else "")
        if found is None:
            raise MaimaiParseError("최근 플레이의 트랙·시각을 읽지 못했다")
        difficulty_icon = _icon_name(top.css_first("img.playlog_diff")).removeprefix("diff_")
        title_block = node.css_first("div.basic_block")
        level_node = node.css_first("div.playlog_level_icon")
        title = title_block.text(deep=False, strip=True) if title_block is not None else ""
        dx, dx_max = _dx_score(_text(node.css_first("div.playlog_score_block div.white")))
        lamp_icons = node.css("div.playlog_result_innerblock img.h_35")
        fc_icon, sync_icon = lamp_icons[:2] if len(lamp_icons) >= 2 else (None, None)
        plays.append(
            PlayEntry(
                track=int(found.group(1)),
                played_at=datetime.strptime(found.group(2), "%Y/%m/%d %H:%M").replace(tzinfo=JST),
                title=title,
                chart_type=_chart_type(node.css_first("img.playlog_music_kind_icon")),
                difficulty=_DIFFICULTY_BY_ICON.get(difficulty_icon, ""),
                level=_text(level_node),
                achievement=_achievement(_text(node.css_first("div.playlog_achievement_txt"))),
                rank=_rank(_icon_name(node.css_first("img.playlog_scorerank"))),
                dx_score=dx,
                dx_score_max=dx_max,
                combo_lamp=_lamp(_icon_name(fc_icon), _COMBO_BY_ICON),
                sync_lamp=_lamp(_icon_name(sync_icon), _SYNC_BY_ICON),
                new_record=node.css_first("img.playlog_achievement_newrecord") is not None,
            )
        )
    return plays

