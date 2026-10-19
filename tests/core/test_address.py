# tests/core/test_address.py
from core.address import AddressResolver, build_address


def test_memory_nickname_wins_and_gets_no_honorific() -> None:
    assert build_address(memory_nickname="디짐", source_nicknames=["넷닉"], discord_name="서버닉") == "디짐"


def test_chunithm_net_nickname_beats_discord_name_and_gets_honorific() -> None:
    assert build_address(memory_nickname="", source_nicknames=["넷닉"], discord_name="서버닉") == "넷닉상"


def test_discord_name_is_the_last_resort() -> None:
    assert build_address(memory_nickname="", source_nicknames=[None], discord_name="서버닉") == "서버닉상"


def test_none_when_nobody_has_a_name() -> None:
    assert build_address(memory_nickname="", source_nicknames=[None, "  "], discord_name=None) is None


def test_honorific_is_not_doubled() -> None:
    assert build_address(memory_nickname="", source_nicknames=["유키상"], discord_name=None) == "유키상"


def test_names_are_cleaned_before_use() -> None:
    assert build_address(memory_nickname="", source_nicknames=["@everyone\n"], discord_name=None) == "everyone상"


class _Store:
    def __init__(self, nickname: str) -> None:
        self._nickname = nickname

    async def get_nickname(self, player_id: str) -> str:
        return self._nickname


async def test_resolver_uses_sources_in_order_and_skips_failures() -> None:
    async def broken(player_id: str) -> str | None:
        raise RuntimeError("접속 실패")

    async def net(player_id: str) -> str | None:
        return "넷닉"

    resolver = AddressResolver(memory=_Store(""), sources=[broken, net])
    assert await resolver.resolve("p1", discord_name="서버닉") == "넷닉상"


async def test_resolver_does_not_query_sources_when_memory_has_a_nickname() -> None:
    async def net(player_id: str) -> str | None:
        raise AssertionError("호칭이 있으면 부르지 않는다")

    resolver = AddressResolver(memory=_Store("디짐"), sources=[net])
    assert await resolver.resolve("p1", discord_name="서버닉") == "디짐"


async def test_resolver_without_sources_falls_back_to_discord_name() -> None:
    resolver = AddressResolver(memory=_Store(""))
    assert await resolver.resolve("p1", discord_name="서버닉") == "서버닉상"
    assert await resolver.resolve("p1") is None
