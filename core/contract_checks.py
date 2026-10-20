"""CLAUDE.md/00-core.md가 명시한, 레지스트리 로드 이후에만 확인할 수 있는 계약 규칙 검사.

레지스트리(`core.registry.SkillRegistry`)는 매니페스트 유효성, 선언하지 않은 서비스 사용,
정의 없는 태그, 슬래시 경로 충돌 등은 로드 시점에 이미 검사해 어긴 스킬을 비활성화한다.
이 모듈은 성공적으로 로드되어 활성화된 스킬을 대상으로, 레지스트리가 보지 않는 두 가지
(테이블 접두사, 마이그레이션 파일 이름 규칙)와 docstring 존재를 한 번 더 모아서 검사하는
단일 진입점을 제공한다.
"""

from __future__ import annotations

import re

from core.registry import LoadedSkill, SkillRegistry
from core.schema import build_arg_schema
from core.tool_types import TableRef

# "CREATE TABLE" / "CREATE TABLE IF NOT EXISTS" 뒤에 오는 테이블 이름을 찾는다.
# 테이블 이름은 맨 단어(bare word)이거나 "...", `...`, [...] 로 감싼 식별자일 수 있다.
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\"(?P<dq>[^\"]+)\"|`(?P<bq>[^`]+)`|\[(?P<br>[^\]]+)\]|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))",
    re.IGNORECASE,
)

_FILENAME_RE = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


def _expected_table_prefix(skill_name: str) -> str:
    stripped = skill_name.removeprefix("chunithm-")
    return stripped.replace("-", "_") + "_"


def check_table_prefix(skill: LoadedSkill) -> list[str]:
    violations: list[str] = []
    expected = _expected_table_prefix(skill.manifest.name)
    migrations_dir = skill.path / "migrations"
    if not migrations_dir.is_dir():
        return violations

    for sql_file in migrations_dir.glob("*.sql"):
        for match in _CREATE_TABLE_RE.finditer(sql_file.read_text()):
            table_name = match.group("dq") or match.group("bq") or match.group("br") or match.group("bare")
            if not table_name.startswith(expected):
                violations.append(
                    f"{sql_file.name}의 테이블 '{table_name}'이 접두사 '{expected}'로 시작하지 않는다"
                )
    return violations


def check_migration_filenames(skill: LoadedSkill) -> list[str]:
    violations: list[str] = []
    migrations_dir = skill.path / "migrations"
    if not migrations_dir.is_dir():
        return violations

    for sql_file in migrations_dir.glob("*.sql"):
        if not _FILENAME_RE.match(sql_file.name):
            violations.append(
                f"마이그레이션 파일 이름이 'NNN_설명.sql' 규칙을 따르지 않는다: {sql_file.name}"
            )
    return violations


def check_docstrings(skill: LoadedSkill) -> list[str]:
    violations: list[str] = []
    for spec in [*skill.code.tools, *skill.code.slash_only_tools]:
        if not (spec.fn.__doc__ and spec.fn.__doc__.strip()):
            violations.append(f"'{spec.name}'에 docstring이 없다")
    return violations


def check_table_args_have_no_slash(skill: LoadedSkill) -> list[str]:
    """표(TableRef)를 받는 도구는 슬래시를 가질 수 없다 — 슬래시는 표를 보관하지 않아 표 id를 줄 수 없다."""
    violations: list[str] = []
    for spec in [*skill.code.tools, *skill.code.slash_only_tools]:
        if spec.slash is None:
            continue
        if any(arg.py_type is TableRef for arg in build_arg_schema(spec.fn).args):
            violations.append(f"'{spec.name}'은 TableRef 인자를 받는데 슬래시가 있다")
    return violations


def run_all_contract_checks(registry: SkillRegistry) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for skill in registry.enabled_skills():
        violations: list[str] = []
        violations += check_table_prefix(skill)
        violations += check_migration_filenames(skill)
        violations += check_docstrings(skill)
        violations += check_table_args_have_no_slash(skill)
        results[skill.manifest.name] = violations
    return results
