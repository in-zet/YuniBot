from pathlib import Path

from core.contract_checks import check_table_prefix, run_all_contract_checks
from core.decorators import SkillCode
from core.manifest import SkillManifest
from core.registry import LoadedSkill, SkillRegistry

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _skill_with_migrations(tmp_path: Path, *, skill_name: str, sql: str) -> LoadedSkill:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_init.sql").write_text(sql)
    manifest = SkillManifest(
        name=skill_name,
        description="테스트용",
        examples=["예시"],
        category="메모",
        game="common",
        version=1,
    )
    return LoadedSkill(manifest=manifest, path=tmp_path, code=SkillCode())


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


def test_check_table_prefix_extracts_quoted_and_bracketed_table_names(tmp_path: Path) -> None:
    # 따옴표(", `, [...])로 감싼 테이블 이름도 맨 단어와 동일하게 추출해야 한다.
    # 특히 'CREATE TABLE IF NOT EXISTS "..."'는 과거 버전 정규식에서 'IF NOT EXISTS'가
    # 길이 0으로 역추적되며 바로 다음 단어 'IF'를 테이블 이름으로 잘못 캡처하는 버그가 있었다.
    skill = _skill_with_migrations(
        tmp_path,
        skill_name="widgets",
        sql=(
            'CREATE TABLE "widgets_ok" (id INTEGER PRIMARY KEY);\n'
            'CREATE TABLE IF NOT EXISTS "widgets_also_ok" (id INTEGER PRIMARY KEY);\n'
            "CREATE TABLE `widgets_backtick` (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE IF NOT EXISTS `widgets_backtick_also` (id INTEGER PRIMARY KEY);\n"
            "CREATE TABLE [widgets_bracket] (id INTEGER PRIMARY KEY);\n"
        ),
    )

    violations = check_table_prefix(skill)

    assert violations == []


def test_check_table_prefix_still_flags_mismatched_quoted_table_name(tmp_path: Path) -> None:
    # 따옴표를 썼다고 검사를 건너뛰거나(무시), 'IF'처럼 엉뚱한 단어를 테이블 이름으로
    # 잘못 캡처해서도 안 된다 - 실제 위반 테이블 이름이 메시지에 그대로 나와야 한다.
    skill = _skill_with_migrations(
        tmp_path,
        skill_name="widgets",
        sql='CREATE TABLE IF NOT EXISTS "totally_unrelated" (id INTEGER PRIMARY KEY);\n',
    )

    violations = check_table_prefix(skill)

    assert any("totally_unrelated" in v and "접두사" in v for v in violations)
    assert not any("'IF'" in v for v in violations)
