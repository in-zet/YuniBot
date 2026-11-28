from core.skill_loading import SkillLoadState


def test_instructions_mode_starts_with_instructions_for_every_skill() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border", "song"})
    assert state.level_for("chunithm-border") == "instructions"
    assert state.opened_skills() == set()


def test_full_mode_starts_with_card_only_until_opened() -> None:
    state = SkillLoadState(mode="full", known_skills={"chunithm-border"})
    assert state.level_for("chunithm-border") == "card"
    state.open("chunithm-border")
    assert state.level_for("chunithm-border") == "full"
    assert state.opened_skills() == {"chunithm-border"}


def test_instructions_mode_open_upgrades_to_full() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border"})
    state.open("chunithm-border")
    assert state.level_for("chunithm-border") == "full"


def test_unknown_skill_defaults_to_card() -> None:
    state = SkillLoadState(mode="full", known_skills={"chunithm-border"})
    assert state.level_for("does-not-exist") == "card"


def test_opened_skills_carried_over_start_open_and_unknown_ones_are_dropped() -> None:
    state = SkillLoadState(mode="full", known_skills={"a", "b"}, opened=["a", "gone"])
    assert state.level_for("a") == "full"
    assert state.opened_skills() == {"a"}


def test_over_the_cap_carried_skills_fold_oldest_first() -> None:
    state = SkillLoadState(mode="full", known_skills={"a", "b", "c"}, opened=["a", "b", "c"], max_open=2)
    assert state.ordered_skills() == ["b", "c"]
    assert state.level_for("a") == "card"


def test_skills_opened_in_this_request_are_never_folded_mid_request() -> None:
    """2026-11-28 실기: 이어 받은 스킬로 상한이 찬 채 트리거로 report를 펼치고, 모델이 open_skill(folders)를 부르자
    report가 접혀 플카 도구가 사라졌다. 이번 요청에서 펼친 스킬은 상한을 넘어도 끝까지 남는다."""
    carried = ["sync", "updates", "yuni-lore"]
    state = SkillLoadState(
        mode="full", known_skills={*carried, "report", "song", "folders"}, opened=carried, max_open=3,
    )
    state.open("report")
    state.open("song")
    state.open("folders")

    assert {"report", "song", "folders"} <= state.opened_skills()
    assert state.opened_skills() == {"report", "song", "folders"}  # 이어 받은 것만 접혔다


def test_touch_pins_a_carried_skill_for_this_request() -> None:
    state = SkillLoadState(mode="full", known_skills={"a", "b", "c"}, opened=["a", "b"], max_open=2)
    state.touch("a")
    state.open("c")
    assert state.ordered_skills() == ["a", "c"]


def test_keep_holds_the_thread_skill_even_when_it_is_the_oldest() -> None:
    state = SkillLoadState(
        mode="full", known_skills={"thread", "a", "b"}, opened=["thread", "a", "b"], keep=["thread"], max_open=2,
    )
    state.open("b")
    assert "thread" in state.opened_skills() and "a" not in state.opened_skills()


def test_touch_does_not_open_a_closed_skill() -> None:
    state = SkillLoadState(mode="full", known_skills={"a"})
    state.touch("a")
    assert state.opened_skills() == set()
