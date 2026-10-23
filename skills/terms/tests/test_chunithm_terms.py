# skills/terms/tests/test_chunithm_terms.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.tables import RowRef, render_text
from core.tool_types import ToolContext
from services.chunithm.terms import TermsService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_TERMS_SERVER_MIGRATION = (
    Path("migrations/core/009_terms_server.sql").read_text() + Path("migrations/core/021_terms_aliases.sql").read_text()
)


@dataclass(slots=True, kw_only=True)
class _FakeSettings:
    """실제 SettingsService 대신 쓰는 테스트용 가짜(goals 테스트들의 _FakeRecords와 같은 패턴)."""

    values: dict[tuple[str, str, str, str], Any] = field(default_factory=dict)

    async def get_value(self, *, scope: str, scope_id: str, skill: str, name: str, default: Any) -> Any:
        return self.values.get((scope, scope_id, skill, name), default)


@dataclass(slots=True, kw_only=True)
class _FakeNotify:
    posted: list[tuple[str, str]] = field(default_factory=list)

    async def post(self, channel_id: str, content: str) -> None:
        self.posted.append((channel_id, content))


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_terms_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


@pytest.fixture
async def terms() -> AsyncIterator[TermsService]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION + _TERMS_SERVER_MIGRATION)
    await conn.commit()
    try:
        yield TermsService(conn=conn)
    finally:
        await conn.close()


def _ctx(
    terms_service: TermsService, *, player_id: str = "p1", tags: frozenset[str] = frozenset(),
    guild_id: str | None = "g1", settings: _FakeSettings | None = None, notify: _FakeNotify | None = None,
    songdb: object | None = None,
) -> ToolContext:
    return ToolContext(
        player_id=player_id, tags=tags,
        services={
            "terms": terms_service, "settings": settings or _FakeSettings(), "notify": notify or _FakeNotify(),
            **({"songdb": songdb} if songdb is not None else {}),
        },
        guild_id=guild_id,
    )


async def test_explain_known_term(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.explain(_ctx(terms), term="트플플")

    assert result.error is None
    assert result.data["canonical"] == "SSS+"


async def test_explain_unknown_term_is_an_error(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.explain(_ctx(terms), term="아무말")

    assert result.error is not None


async def test_list_known_category(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.list(_ctx(terms), category="랭크")

    assert result.error is None
    assert result.data["count"] == 14
    table = result.table
    assert table is not None and table.unit == "용어" and table.row_count == 14
    assert [c.label for c in table.columns] == ["용어", "분류"]
    assert table.steps == ("용어 목록 (랭크)",)
    first = table.rows[0]
    assert first.ref == RowRef("terms.term", first.cell("용어").raw)
    assert first.cell("분류").text == "랭크"
    rendered = render_text(table)  # 11행 이상이라 목록은 첨부 파일로 나간다
    assert rendered.file is not None and rendered.file[1].decode().splitlines()[-1].startswith("14. 용어 ")


async def test_list_unknown_category_is_an_error(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.list(_ctx(terms), category="없는분류")

    assert result.error is not None


async def test_suggest_is_immediately_approved(terms: TermsService) -> None:
    """신고제(2026-10-18): 승인을 기다리지 않고 바로 반영된다."""
    skill = _load_skill()
    result = await skill.suggest(_ctx(terms), term="테스트은어", meaning="테스트 뜻", aliases="별칭1, 별칭2")

    assert result.error is None
    proposal_id = result.data["proposal_id"]
    proposal = await terms.get_proposal(proposal_id)
    assert proposal is not None and proposal.status == "approved"
    assert proposal.aliases == ("별칭1", "별칭2")


async def test_suggest_reports_to_configured_channel(terms: TermsService) -> None:
    settings = _FakeSettings(values={("guild", "g1", "admin", "보고 채널"): "chan1"})
    notify = _FakeNotify()
    skill = _load_skill()
    result = await skill.suggest(_ctx(terms, settings=settings, notify=notify), term="테스트은어", meaning="뜻", aliases="")

    assert result.error is None
    assert len(notify.posted) == 1
    channel_id, content = notify.posted[0]
    assert channel_id == "chan1"
    assert "테스트은어" in content


async def test_suggest_without_configured_channel_does_not_notify(terms: TermsService) -> None:
    notify = _FakeNotify()
    skill = _load_skill()
    await skill.suggest(_ctx(terms, notify=notify), term="테스트은어", meaning="뜻", aliases="")

    assert notify.posted == []


async def test_review_requires_admin_tag_via_executor(terms: TermsService) -> None:
    """review 함수 자체는 태그를 모른다(실행기가 막는다) — 그래서 이 테스트는
    core.executor.ToolExecutor를 통해 호출해야 admin 태그 검사가 실제로 걸리는지 확인할 수 있다."""
    from core.decorators import ToolSpec
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry

    skill = _load_skill()
    suggestion = await skill.suggest(_ctx(terms), term="테스트은어", meaning="뜻", aliases="")
    proposal_id = suggestion.data["proposal_id"]

    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={},
        services={"terms": terms},
    )
    spec: ToolSpec = skill.review.__yuni_tool__  # pyright: ignore[reportFunctionMemberAccess]
    ctx = ToolContext(player_id="not-admin", tags=frozenset(), services={"terms": terms}, guild_id="g1")

    # approve=False(철회)를 admin 아닌 사람이 시도 — 막혀서 등록은 그대로 approved로 남는다.
    outcome = await executor.invoke(spec, ctx, {"proposal_id": proposal_id, "approve": False})

    assert outcome.result is not None and outcome.result.error is not None
    proposal = await terms.get_proposal(proposal_id)
    assert proposal is not None and proposal.status == "approved"


async def test_review_can_revoke_and_restore(terms: TermsService) -> None:
    skill = _load_skill()
    suggestion = await skill.suggest(_ctx(terms), term="테스트은어", meaning="뜻", aliases="")
    proposal_id = suggestion.data["proposal_id"]
    admin_ctx = _ctx(terms, player_id="admin1", tags=frozenset({"admin"}))

    revoked = await skill.review(admin_ctx, proposal_id=proposal_id, approve=False)
    assert revoked.error is None
    proposal = await terms.get_proposal(proposal_id)
    assert proposal is not None and proposal.status == "rejected"

    restored = await skill.review(admin_ctx, proposal_id=proposal_id, approve=True)
    assert restored.error is None
    proposal = await terms.get_proposal(proposal_id)
    assert proposal is not None and proposal.status == "approved"


class _FakeSongDb:
    def __init__(self, artists: list[str]) -> None:
        self._artists = artists

    def find_by_artist(self, *, name: str) -> list[str]:
        return [a for a in self._artists if name.lower() in a.lower()]

    def all_artists(self) -> list[str]:
        return self._artists


async def test_alias_add_saves_a_confirmed_rank_alias(skill: ModuleType, terms: TermsService) -> None:
    ctx = _ctx(terms)

    result = await skill.alias_add(ctx, kind="랭크", alias="삼에스", canonical="SSS")

    assert result.error is None
    assert result.data == {"kind": "랭크", "alias": "삼에스", "canonical": "SSS"}
    assert (await terms.resolve_rank("삼에스", ctx)).canonical == "SSS"


async def test_alias_add_checks_the_artist_exists_and_suggests_similar(skill: ModuleType, terms: TermsService) -> None:
    ctx = _ctx(terms, songdb=_FakeSongDb(["Camellia", "Kobaryo"]))

    missing = await skill.alias_add(ctx, kind="작곡가", alias="카멜", canonical="Camelia")
    saved = await skill.alias_add(ctx, kind="작곡가", alias="카멜", canonical="Camellia")

    assert missing.error is not None and "Camellia" in missing.error
    assert saved.error is None and terms.artist_alias("카멜") == "Camellia"


async def test_alias_add_rejects_unknown_kind(skill: ModuleType, terms: TermsService) -> None:
    result = await skill.alias_add(_ctx(terms), kind="곡", alias="x", canonical="y")

    assert result.error is not None and "작곡가" in result.error


async def test_alias_remove_only_by_adder_or_admin(skill: ModuleType, terms: TermsService) -> None:
    await skill.alias_add(_ctx(terms, player_id="p1"), kind="랭크", alias="삼에스", canonical="SSS")

    stranger = await skill.alias_remove(_ctx(terms, player_id="p2"), kind="랭크", alias="삼에스")
    admin = await skill.alias_remove(_ctx(terms, player_id="p3", tags=frozenset({"admin"})), kind="랭크", alias="삼에스")

    assert stranger.error is not None
    assert admin.error is None
