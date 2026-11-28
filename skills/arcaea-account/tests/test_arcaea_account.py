# skills/arcaea-account/tests/test_arcaea_account.py
"""Arcaea 로그인 스킬. NET 서비스는 가짜다(진짜는 tests/services/arcaea/test_arcaea_net.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from chuni_penguin.adapters.errors import AuthenticationError

from core.tool_types import ToolContext
from services.arcaea.arcaea_net import NotSubscribedError
from services.common.account_cookies import LoginStatus

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


@pytest.fixture
def skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_arcaea_account_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNet:
    def __init__(self, *, fail: Exception | None = None, linked: bool = False) -> None:
        self.fail, self.linked = fail, linked
        self.calls: list[tuple[str, ...]] = []

    async def login_with_cookie(self, player_id: str, cookie: str):
        self.calls.append(("cookie", cookie))
        if self.fail:
            raise self.fail
        return SimpleNamespace(username="Hikari")

    async def login_with_credentials(self, player_id: str, username: str, password: str):
        self.calls.append(("password", username, password))
        if self.fail:
            raise self.fail
        return SimpleNamespace(username="Hikari")

    async def status(self, player_id: str) -> LoginStatus:
        return LoginStatus(linked=self.linked, nickname="Hikari" if self.linked else None)

    async def logout(self, player_id: str, ctx: Any = None) -> None:
        self.calls.append(("logout",))


class _FakeRecords:
    async def last_sync_at(self, player_id: str) -> str | None:
        return None


class _Followup:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str, *, ephemeral: bool = False, view: Any = None) -> None:
        assert ephemeral
        self.sent.append(content)


class _Interaction:
    def __init__(self) -> None:
        self.followup = _Followup()


def _ctx(net: _FakeNet) -> ToolContext:
    return ToolContext(player_id="p1", tags=frozenset(), game="arcaea",
                       services={"chunithm_net": net, "records": _FakeRecords()})


async def test_cookie_wins_over_id_and_password(skill: ModuleType) -> None:
    net, interaction = _FakeNet(), _Interaction()

    await skill._attempt_login(_ctx(net), interaction, username="me", password="pw", cookie="sid=abc")

    assert net.calls == [("cookie", "sid=abc")]
    assert "Arcaea 로그인 완료! (Hikari)" in interaction.followup.sent[0]


async def test_id_and_password_login(skill: ModuleType) -> None:
    net, interaction = _FakeNet(), _Interaction()

    await skill._attempt_login(_ctx(net), interaction, username="me", password="pw", cookie="")

    assert net.calls == [("password", "me", "pw")]


@pytest.mark.parametrize(
    ("fail", "cookie", "expected"),
    [
        (AuthenticationError(), "", "아이디(또는 이메일)와 비밀번호"),
        (AuthenticationError(), "sid=x", "쿠키로는"),
        (NotSubscribedError(), "sid=x", "구독"),
        (RuntimeError("secret sid=x"), "sid=x", "잠시 뒤에"),
    ],
)
async def test_failures_never_echo_what_was_typed(skill: ModuleType, fail: Exception, cookie: str, expected: str) -> None:
    interaction = _Interaction()

    await skill._attempt_login(_ctx(_FakeNet(fail=fail)), interaction, username="me", password="pw", cookie=cookie)

    [message] = interaction.followup.sent
    assert expected in message and "sid=x" not in message and "pw" not in message


async def test_status_and_logout(skill: ModuleType) -> None:
    net = _FakeNet(linked=True)

    result = await skill.status(_ctx(net))
    assert result.data["linked"] and result.display.content.startswith("Arcaea 로그인: 되어있음 (Hikari)")

    result = await skill.logout(_ctx(net))
    assert result.data == {"logged_out": True} and net.calls == [("logout",)]
