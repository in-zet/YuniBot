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
