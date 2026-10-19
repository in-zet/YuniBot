from core.skill_loading import SkillLoadState


def test_instructions_mode_starts_with_instructions_for_every_skill() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border", "chunithm-song"})
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


def test_over_the_cap_the_least_recently_used_skill_is_folded() -> None:
    state = SkillLoadState(mode="full", known_skills={"a", "b", "c"}, max_open=2)
    state.open("a")
    state.open("b")
    state.open("c")
    assert state.ordered_skills() == ["b", "c"]
    assert state.level_for("a") == "card"


def test_touch_keeps_a_used_skill_from_being_folded_first() -> None:
    state = SkillLoadState(mode="full", known_skills={"a", "b", "c"}, max_open=2)
    state.open("a")
    state.open("b")
    state.touch("a")
    state.open("c")
    assert state.ordered_skills() == ["a", "c"]


def test_touch_does_not_open_a_closed_skill() -> None:
    state = SkillLoadState(mode="full", known_skills={"a"})
    state.touch("a")
    assert state.opened_skills() == set()
