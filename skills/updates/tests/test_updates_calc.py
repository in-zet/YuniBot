# skills/updates/tests/test_updates_calc.py
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

_SKILL_DIR = Path(__file__).parent.parent
_REFERENCE_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "reference" / "chunithm-updates"


def _load_calc() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_test_updates_calc", _SKILL_DIR / "updates_calc.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


calc = _load_calc()


# ---------------------------------------------------------------- 공지 저장본 파싱


def test_parse_jp_rss_reads_update_date_from_title_not_version_name() -> None:
    """일본판 제목은 "M/D(요일)"로 시작하고, 『』는 콜라보 이벤트 이름에도 쓰여서
    버전명으로 믿을 수 없다(실제 저장본에 『いよわ』가 있다) — 그래서 늘 None이다."""
    xml_text = (_REFERENCE_DIR / "jp_rss_sample.xml").read_text(encoding="utf-8")
    items = calc.parse_jp_rss(xml_text)
    assert len(items) == 5
    first = items[0]
    assert first.url == "https://info-chunithm.sega.jp/12743/"
    assert first.posted_at == date(2026, 10, 6)
    assert first.update_date == date(2026, 10, 8)
    assert all(item.version_name is None for item in items)
    # 『いよわ』가 들어 있는 네 번째 글도 버전명으로 안 뽑힌다.
    assert "『いよわ』" in items[3].title
    assert items[3].version_name is None


def test_parse_intl_news_reads_update_date_and_version_name() -> None:
    html_text = (_REFERENCE_DIR / "intl_news_sample.html").read_text(encoding="utf-8")
    items = calc.parse_intl_news(html_text)
    assert len(items) == 6

    version_notice = items[0]
    assert version_notice.update_date is None  # "Details of M/D Update" 형식이 아니다
    assert version_notice.version_name == "CHUNITHM Mate"

    update_notice = items[1]
    assert update_notice.posted_at == date(2026, 10, 7)
    assert update_notice.update_date == date(2026, 10, 8)
    assert update_notice.version_name == "CHUNITHM X-VERSE-X"

    # 한 자리 월("2026.9.30")도 끊기지 않고 읽는다.
    single_digit_month_notice = items[2]
    assert single_digit_month_notice.posted_at == date(2026, 9, 30)
    assert single_digit_month_notice.update_date == date(2026, 10, 1)


def test_parse_intl_news_raises_on_unknown_structure() -> None:
    try:
        calc.parse_intl_news("<html><body>구조가 완전히 바뀐 페이지</body></html>")
    except ValueError:
        return
    raise AssertionError("구조가 바뀌면 ValueError를 내야 한다")


# ---------------------------------------------------------------- 날짜·버전명 추출


def test_extract_jp_update_date_handles_year_wrap() -> None:
    # 12월 말에 게시된 글이 1월 초 업데이트를 가리키면 해가 넘어간다.
    posted = date(2026, 12, 30)
    assert calc.extract_jp_update_date("1/2(金)新マップ登場！", posted) == date(2027, 1, 2)


def test_extract_intl_update_date_requires_the_phrase() -> None:
    assert calc.extract_intl_update_date("Information of New Version", date(2026, 10, 1)) is None


def test_extract_version_name_reads_only_double_corner_brackets() -> None:
    assert calc.extract_version_name("『CHUNITHM Mate』Details of 1/1 Update") == "CHUNITHM Mate"
    assert calc.extract_version_name("「Mate ep. IV」登場！") is None


# ---------------------------------------------------------------- 곡 목록 비교


def test_diff_song_ids_without_previous_snapshot_reports_nothing() -> None:
    diff = calc.diff_song_ids(None, {"1": "곡A", "2": "곡B"})
    assert diff.added == []
    assert diff.removed == []


def test_diff_song_ids_finds_added_and_removed_by_id() -> None:
    previous = {"1": "곡A", "2": "곡B"}
    current = {"1": "곡A", "3": "곡C"}
    diff = calc.diff_song_ids(previous, current)
    assert diff.added == [("3", "곡C")]
    assert diff.removed == [("2", "곡B")]


def test_diff_song_ids_ignores_title_only_changes_for_same_id() -> None:
    """id가 같으면 리네임으로 보고 추가·삭제로 세지 않는다(제목만 비교하면 틀린다)."""
    diff = calc.diff_song_ids({"1": "옛 제목"}, {"1": "새 제목"})
    assert diff.added == []
    assert diff.removed == []
