from pathlib import Path

from core.contract_checks import run_all_contract_checks
from core.registry import SkillRegistry

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_valid_skill_has_no_contract_violations() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok"])
    registry.load()

    violations = run_all_contract_checks(registry)
    assert violations.get("practice", []) == []


def test_bad_table_prefix_is_flagged() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "bad_table_prefix"])
    registry.load()
    assert registry.enabled_skills(), "레지스트리 자체는 통과해야 계약 테스트가 접두사를 잡을 수 있다"

    violations = run_all_contract_checks(registry)
    assert any("접두사" in v for v in violations["bad-table-prefix"])


def test_bad_migration_filename_is_flagged() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "bad_migration_filename"])
    registry.load()

    violations = run_all_contract_checks(registry)
    assert any("파일 이름" in v for v in violations["bad-migration-filename"])


def test_template_skill_is_excluded_from_registry_and_checks() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    violations = run_all_contract_checks(registry)
    assert "_template" not in violations
