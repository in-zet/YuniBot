# tests/core/test_permissions.py
import pytest

from core.manifest import DefinedTag
from core.permissions import (
    InMemoryTagProvider,
    TagRequirement,
    check_tags,
    describe_lock,
)


def test_check_tags_all_required() -> None:
    req = TagRequirement(tags=["admin", "tester"])
    assert check_tags(req, {"admin", "tester", "extra"}) is True
    assert check_tags(req, {"admin"}) is False


def test_check_tags_any_of() -> None:
    req = TagRequirement(tags_any=["a", "b"])
    assert check_tags(req, {"b"}) is True
    assert check_tags(req, {"c"}) is False


def test_check_tags_no_requirement_passes() -> None:
    assert check_tags(TagRequirement(), set()) is True


@pytest.mark.asyncio
async def test_in_memory_tag_provider_grant_revoke() -> None:
    provider = InMemoryTagProvider()
    assert await provider.tags_for("p1") == set()

    provider.grant("p1", "chunithm-linked")
    assert await provider.tags_for("p1") == {"chunithm-linked"}

    provider.revoke("p1", "chunithm-linked")
    assert await provider.tags_for("p1") == set()


def test_describe_lock_visible_status_tag_has_guidance() -> None:
    defs = {
        "chunithm-linked": DefinedTag(
            name="chunithm-linked",
            description="CHUNITHM-NET 연동됨",
            how_to_get="/계정 연결 로그인으로 로그인하면 얻는다",
            hidden=False,
        )
    }
    lock = describe_lock(missing_tags=["chunithm-linked"], tag_definitions=defs)
    assert lock.visible is True
    assert "로그인" in (lock.message or "")


def test_describe_lock_hidden_tag_gives_no_guidance() -> None:
    defs = {
        "practice-secret": DefinedTag(
            name="practice-secret",
            description="데모용 부여 태그",
            how_to_get="관리자가 직접 부여",
            hidden=True,
        )
    }
    lock = describe_lock(missing_tags=["practice-secret"], tag_definitions=defs)
    assert lock.visible is False
    assert lock.message is None
