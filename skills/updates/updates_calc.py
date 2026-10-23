# skills/updates/updates_calc.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from selectolax.lexbor import LexborHTMLParser

# 버전명은 『』(국제판 공지가 쓰는 괄호)에서만 뽑는다. 일본판 제목은 곡·맵 이름에
# 「」를 쓰고 『』는 안 써서, 같은 정규식이 두 지역 모두에 안전하게 쓰인다(2026-10-18 확인).
_VERSION_NAME_RE = re.compile(r"『([^』]+)』")
# 일본판: 제목이 "M/D(요일)..."로 시작한다. 국제판: "...Details of M/D Update".
_JP_UPDATE_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})\(")
_INTL_UPDATE_DATE_RE = re.compile(r"Details of (\d{1,2})/(\d{1,2}) Update", re.IGNORECASE)
_INTL_POSTED_DATE_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}")
# 년도 표기가 없는 "M/D"가 게시일보다 6일 이상 전으로 풀리면 실제로는 다음 해다(연말연초 경계).
_YEAR_WRAP_TOLERANCE_DAYS = 5


@dataclass(slots=True, kw_only=True, frozen=True)
class NoticeItem:
    url: str
    title: str
    posted_at: date
    update_date: date | None
    version_name: str | None


@dataclass(slots=True, kw_only=True, frozen=True)
class SongDiff:
    added: list[tuple[str, str]]  # (id, title), id 오름차순
    removed: list[tuple[str, str]]


def extract_version_name(title: str) -> str | None:
    match = _VERSION_NAME_RE.search(title)
    return match.group(1) if match else None


def _resolve_month_day(month: int, day: int, posted_at: date) -> date:
    candidate = date(posted_at.year, month, day)
    if candidate < posted_at - timedelta(days=_YEAR_WRAP_TOLERANCE_DAYS):
        candidate = date(posted_at.year + 1, month, day)
    return candidate


def extract_jp_update_date(title: str, posted_at: date) -> date | None:
    match = _JP_UPDATE_DATE_RE.match(title)
    if not match:
        return None
    return _resolve_month_day(int(match.group(1)), int(match.group(2)), posted_at)


def extract_intl_update_date(title: str, posted_at: date) -> date | None:
    match = _INTL_UPDATE_DATE_RE.search(title)
    if not match:
        return None
    return _resolve_month_day(int(match.group(1)), int(match.group(2)), posted_at)


def parse_jp_rss(xml_text: str) -> list[NoticeItem]:
    """일본판 공지 RSS(`docs/reference/chunithm-updates/jp_rss_sample.xml` 구조)를 읽는다."""
    root = ElementTree.fromstring(xml_text)
    items: list[NoticeItem] = []
    for item in root.iterfind("./channel/item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_date_el = item.find("pubDate")
        if title_el is None or title_el.text is None or link_el is None or link_el.text is None:
            continue
        title = title_el.text
        posted_at = (
            parsedate_to_datetime(pub_date_el.text).date()
            if pub_date_el is not None and pub_date_el.text
            else date.today()  # noqa: DTZ011 - 저장된 글에 pubDate가 없을 때만 쓰는 방어적 기본값
        )
        items.append(
            NoticeItem(
                url=link_el.text,
                title=title,
                posted_at=posted_at,
                update_date=extract_jp_update_date(title, posted_at),
                # 일본판 제목은 콜라보 이벤트 이름에도 『』를 쓴다(예: "『いよわ』リバイバル
                # イベント開催" — 버전명이 아니다). 버전명은 국제판 공지에서만 믿을 수
                # 있게 뽑힌다(2026-10-18 저장본으로 확인) — 일본판은 늘 None으로 둔다.
                version_name=None,
            )
        )
    return items


def parse_intl_news(html_text: str) -> list[NoticeItem]:
    """국제판 공지 페이지(`docs/reference/chunithm-updates/intl_news_sample.html` 구조)를 읽는다.
    구조가 바뀌면 ValueError — 부르는 쪽이 잡아서 "곡 목록 비교만으로 알린다"로 넘어간다."""
    soup = LexborHTMLParser(html_text)
    posts = soup.css("li.news--list__item a.news--list__post")
    if not posts:
        raise ValueError("국제판 공지 페이지 구조가 바뀌었다 — news--list__item을 못 찾았다")

    items: list[NoticeItem] = []
    for post in posts:
        url = post.attrs.get("href")
        date_node = post.css_first("div.news--date")
        title_node = post.css_first("p.news--title")
        if url is None or date_node is None or title_node is None:
            continue
        # news--date 안에 "2026.10.15" 뒤 요일 span이 공백 없이 바로 붙어 있고, 월·일은
        # 0으로 안 채운다("2026.9.30") — 글자 수로 자르면 한 자리 월에서 깨진다.
        date_match = _INTL_POSTED_DATE_RE.match(date_node.text(strip=True))
        if date_match is None:
            continue
        posted_at = datetime.strptime(date_match.group(), "%Y.%m.%d").date()  # noqa: DTZ007 - 날짜만 쓰고 버린다
        title = title_node.text(strip=True)
        items.append(
            NoticeItem(
                url=url,
                title=title,
                posted_at=posted_at,
                update_date=extract_intl_update_date(title, posted_at),
                version_name=extract_version_name(title),
            )
        )
    return items


def diff_song_ids(previous: dict[str, str] | None, current: dict[str, str]) -> SongDiff:
    """이전 저장본(id → title)과 지금 곡 목록을 id로 비교한다. 이전 저장본이 없으면
    (처음 켰을 때) 기준점만 잡고 추가·삭제는 없는 것으로 본다 — 수록곡 전부를 "신곡"으로
    알리지 않기 위해서다."""
    if previous is None:
        return SongDiff(added=[], removed=[])
    added_ids = sorted(set(current) - set(previous))
    removed_ids = sorted(set(previous) - set(current))
    return SongDiff(
        added=[(song_id, current[song_id]) for song_id in added_ids],
        removed=[(song_id, previous[song_id]) for song_id in removed_ids],
    )
