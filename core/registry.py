from __future__ import annotations

import ast
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path

from core.decorators import SkillCode, SlashOnlySpec, ToolSpec, collect_specs
from core.manifest import DefinedTag, SkillManifest, parse_manifest
from core.schema import build_arg_schema
from core.slash_rules import callback_arg_name_problems, slash_path_problems

logger = logging.getLogger(__name__)

# 뒤의 세 이름(db, player_data, registry_snapshot)은 executor.services에 든 진짜 서비스가
# 아니라 ToolContext의 전용 필드다. 그래도 스킬이 쓰려면 다른 서비스처럼 manifest.services에
# 적어야 한다 — core/slash.py의 _dispatch는 선언한 스킬에게만 채워 주고(아니면 None), 아래
# 정적 검사는 선언하지 않고 ctx.db 등을 쓰는 skill.py를 로드 단계에서 막는다.
CTX_FIELD_SERVICE_NAMES: frozenset[str] = frozenset({"db", "player_data", "registry_snapshot", "usage"})

KNOWN_SERVICE_NAMES: frozenset[str] = frozenset(
    {
        "players", "settings", "memory", "notify", "yuni", "secrets", "arcades",
        "websearch", "http", "chunithm_net", "songdb", "records", "terms",
        *CTX_FIELD_SERVICE_NAMES,
    }
)

# 00-core.md "이벤트 목록"에서 코어/서비스가 내는 것으로 적힌 이벤트.
# 3~4단계 전까지는 실제로 내는 코드가 없어도 "정의된 이벤트"로 취급한다.
CORE_EMITTED_EVENTS: frozenset[str] = frozenset(
    {"players.first_seen", "chunithm.login.expired"}
)

# admin/tester는 어떤 스킬도 defines_tags로 정의할 수 없다 — 빠졌을 때 항상 숨김
# (describe_lock이 정의를 못 찾으면 fail-closed) 처리되어야 하는 진짜 코어 전용 태그다.
# chunithm-linked/chunithm-synced는 여기 없다 — 상태 태그지만 설명은 chunithm-account의
# defines_tags가 소유한다("보이되 잠긴다" 안내문을 내려면 설명이 필요하다. 00-core.md 345행).
CORE_DEFINED_TAGS: frozenset[str] = frozenset({"admin", "tester"})


@dataclass(slots=True, kw_only=True)
class LoadedSkill:
    manifest: SkillManifest
    path: Path
    code: SkillCode


class SkillRegistry:
    def __init__(self, skill_dirs: list[Path]) -> None:
        self._skill_dirs = skill_dirs
        self._loaded: dict[str, LoadedSkill] = {}
        self._failures: dict[str, str] = {}

    def load(self) -> None:
        self._loaded.clear()
        self._failures.clear()

        candidates = self._discover_dirs()
        parsed: list[tuple[Path, SkillManifest, SkillCode]] = []

        for skill_dir in candidates:
            try:
                manifest, code = self._load_one(skill_dir)
            except Exception as exc:  # noqa: BLE001 - 한 스킬의 어떤 실패도 레지스트리 전체를 죽이면 안 된다
                logger.warning("스킬 '%s' 로드 실패: %s", skill_dir.name, exc)
                self._failures[skill_dir.name] = str(exc)
                continue
            parsed.append((skill_dir, manifest, code))

        all_tag_defs = self._collect_tag_definitions(parsed)
        all_emits = self._collect_emitted_events(parsed)

        for skill_dir, manifest, code in parsed:
            if manifest.name in self._loaded:
                self._failures[skill_dir.name] = (
                    f"스킬 이름 '{manifest.name}'이 이미 등록된 스킬과 겹친다"
                )
                continue

            reason = self._validate_cross_skill(manifest, code, all_tag_defs, all_emits)
            if reason:
                self._failures[skill_dir.name] = reason
                continue

            self._loaded[manifest.name] = LoadedSkill(manifest=manifest, path=skill_dir, code=code)

        self._detect_slash_collisions()

    def enabled_skills(self) -> list[LoadedSkill]:
        return list(self._loaded.values())

    def failures(self) -> dict[str, str]:
        return dict(self._failures)

    def tag_definitions(self) -> dict[str, DefinedTag]:
        return self._collect_tag_definitions(
            [(s.path, s.manifest, s.code) for s in self._loaded.values()]
        )

    def tool_specs(self) -> list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]]:
        specs: list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]] = []
        for skill in self._loaded.values():
            for spec in [*skill.code.tools, *skill.code.slash_only_tools]:
                specs.append((skill, spec))
        return specs

    def _discover_dirs(self) -> list[Path]:
        # 각 root는 "스킬 여러 개를 담은 디렉터리"(skills/ 처럼) 또는 "스킬 디렉터리 그 자체"
        # 둘 다로 쓸 수 있다. 후자는 픽스처 테스트에서 개별 스킬 하나를 직접 가리킬 때 쓴다.
        dirs: list[Path] = []
        for root in self._skill_dirs:
            if not root.is_dir():
                continue
            if (root / "SKILL.md").is_file() or (root / "skill.py").is_file():
                dirs.append(root)
                continue
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and not entry.name.startswith("_"):
                    dirs.append(entry)
        return dirs

    def _load_one(self, skill_dir: Path) -> tuple[SkillManifest, SkillCode]:
        manifest_path = skill_dir / "SKILL.md"
        if not manifest_path.is_file():
            raise _SkillLoadError(f"'{skill_dir}'에 SKILL.md가 없다")

        manifest = parse_manifest(manifest_path.read_text())

        skill_py = skill_dir / "skill.py"
        if not skill_py.is_file():
            raise _SkillLoadError(f"'{skill_dir}'에 skill.py가 없다")

        undeclared = _find_undeclared_service_usage(skill_py, declared=set(manifest.services))
        if undeclared:
            raise _SkillLoadError(
                f"선언하지 않은 서비스를 쓴다: {', '.join(sorted(undeclared))} "
                f"(manifest.services에 추가해야 한다)"
            )

        module = _import_skill_module(skill_py, module_name=f"_yuni_skill_{manifest.name.replace('-', '_')}")
        code = collect_specs(module)

        for spec in [*code.tools, *code.slash_only_tools]:
            schema = build_arg_schema(spec.fn)  # 스키마 생성 불가면 SchemaBuildError 전파
            if spec.slash is not None:
                problems = slash_path_problems(spec.slash)
                problems += callback_arg_name_problems(arg.name for arg in schema.args)
                if problems:
                    raise _SkillLoadError(f"'{spec.name}': " + "; ".join(problems))

        self_conflict = _find_self_slash_conflict(_slash_paths(code))
        if self_conflict:
            raise _SkillLoadError(self_conflict)

        core_tag_redefined = {t.name for t in manifest.defines_tags} & CORE_DEFINED_TAGS
        if core_tag_redefined:
            raise _SkillLoadError(
                f"코어 태그를 다시 정의할 수 없다: {', '.join(sorted(core_tag_redefined))}"
            )

        declared_events = {h.event_name for h in code.event_handlers}
        undeclared_listens = declared_events - set(manifest.listens)
        if undeclared_listens:
            raise _SkillLoadError(
                f"manifest.listens에 없는 이벤트를 구독한다: {', '.join(sorted(undeclared_listens))}"
            )

        orphan_listens = set(manifest.listens) - declared_events
        if orphan_listens:
            raise _SkillLoadError(
                f"manifest.listens에 선언했지만 처리하는 @on 핸들러가 없다: "
                f"{', '.join(sorted(orphan_listens))}"
            )

        return manifest, code

    def _validate_cross_skill(
        self,
        manifest: SkillManifest,
        code: SkillCode,
        all_tag_defs: dict[str, DefinedTag],
        all_emits: frozenset[str],
    ) -> str | None:
        used_tags: set[str] = set()
        for spec in [*code.tools, *code.slash_only_tools]:
            used_tags |= set(spec.tags) | set(spec.tags_any)
        used_tags |= set(manifest.tags) | set(manifest.tags_any)

        known_tags = CORE_DEFINED_TAGS | set(all_tag_defs)
        unknown = used_tags - known_tags
        if unknown:
            return f"정의되지 않은 태그를 조건으로 쓴다: {', '.join(sorted(unknown))}"

        unknown_listens = set(manifest.listens) - all_emits
        if unknown_listens:
            return f"아무도 내지 않는 이벤트를 구독한다: {', '.join(sorted(unknown_listens))}"

        return None

    def _collect_tag_definitions(
        self, parsed: list[tuple[Path, SkillManifest, SkillCode]]
    ) -> dict[str, DefinedTag]:
        defs: dict[str, DefinedTag] = {}
        for _, manifest, _ in parsed:
            for tag in manifest.defines_tags:
                defs[tag.name] = tag
        return defs

    def _collect_emitted_events(
        self, parsed: list[tuple[Path, SkillManifest, SkillCode]]
    ) -> frozenset[str]:
        emitted = set(CORE_EMITTED_EVENTS)
        for _, manifest, _ in parsed:
            emitted |= set(manifest.emits)
        return frozenset(emitted)

    def _detect_slash_collisions(self) -> None:
        """스킬 사이의 슬래시 경로 충돌을 모든 깊이에서 찾는다(먼저 로드된 스킬이 이긴다).

        경로 하나는 "명령(leaf)"이고, 그 경로의 모든 앞부분은 "그룹"이다. 두 스킬 사이에서
        (1) 같은 경로를 둘 다 명령으로 쓰거나, (2) 한쪽이 명령으로 쓴 경로를 다른 쪽이 그룹으로
        쓰면 충돌이다 — 루트 토큰이든 그 아래 단계든 똑같이 본다. 한 스킬 안의 자기 충돌은
        _load_one이 이미 걸러냈다.

        스킬의 경로를 전부 먼저 검사하고, 충돌이 없을 때만 소유권을 등록한다. 그래서 비활성화된
        스킬의 경로가 남아 나중 스킬을 잘못 막는 일이 없다.
        """
        leaf_owner: dict[tuple[str, ...], str] = {}
        group_owner: dict[tuple[str, ...], str] = {}

        for skill_name in list(self._loaded):
            paths = _slash_paths(self._loaded[skill_name].code)
            reason = _find_cross_slash_conflict(paths, leaf_owner, group_owner)
            if reason:
                self.disable(skill_name, reason)
                continue
            for path in paths:
                leaf_owner.setdefault(path, skill_name)
                for i in range(1, len(path)):
                    group_owner.setdefault(path[:i], skill_name)

    def disable(self, skill_name: str, reason: str) -> None:
        """활성화된 스킬을 이유와 함께 비활성화한다. 로드 뒤 계약 검사(core/bot.py)도 쓴다."""
        skill = self._loaded.pop(skill_name, None)
        self._failures[skill.path.name if skill else skill_name] = reason


class _SkillLoadError(Exception):
    pass


def _slash_paths(code: SkillCode) -> list[tuple[str, ...]]:
    return [
        tuple(spec.slash.split())
        for spec in [*code.tools, *code.slash_only_tools]
        if spec.slash is not None
    ]


def _fmt(path: tuple[str, ...]) -> str:
    return " ".join(path)


def _find_self_slash_conflict(paths: list[tuple[str, ...]]) -> str | None:
    """한 스킬 안에서 같은 경로를 두 번 쓰거나, 명령으로 쓴 경로를 그룹으로도 쓰는지 본다."""
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        if path in seen:
            return f"슬래시 경로 '{_fmt(path)}'를 한 스킬에서 두 번 썼다"
        seen.add(path)
    for path in paths:
        for i in range(1, len(path)):
            if path[:i] in seen:
                return (
                    f"슬래시 이름 '{_fmt(path[:i])}'을 단독 명령과 그룹('{_fmt(path)}')으로 "
                    f"한 스킬에서 동시에 썼다"
                )
    return None


def _find_cross_slash_conflict(
    paths: list[tuple[str, ...]],
    leaf_owner: dict[tuple[str, ...], str],
    group_owner: dict[tuple[str, ...], str],
) -> str | None:
    for path in paths:
        if path in leaf_owner:
            return f"슬래시 경로 '{_fmt(path)}'가 '{leaf_owner[path]}' 스킬과 겹친다"
        if path in group_owner:
            return (
                f"슬래시 이름 '{_fmt(path)}'을 단독 명령으로 쓰지만 '{group_owner[path]}' 스킬이 "
                f"이미 그룹으로 쓴다"
            )
        for i in range(1, len(path)):
            prefix = path[:i]
            if prefix in leaf_owner:
                return (
                    f"슬래시 이름 '{_fmt(prefix)}'을 그룹('{_fmt(path)}')으로 쓰지만 "
                    f"'{leaf_owner[prefix]}' 스킬이 이미 단독 명령으로 쓴다"
                )
    return None


def _import_skill_module(skill_py: Path, *, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, skill_py)
    if spec is None or spec.loader is None:
        raise _SkillLoadError(f"'{skill_py}'를 import할 수 없다")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_undeclared_service_usage(skill_py: Path, *, declared: set[str]) -> set[str]:
    """skill.py를 파싱해 'ctx.<서비스>' 접근 중 manifest.services에 없는 것을 찾는다."""
    tree = ast.parse(skill_py.read_text(), filename=str(skill_py))
    undeclared: set[str] = set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ctx"
            and node.attr in KNOWN_SERVICE_NAMES
            and node.attr not in declared
        ):
            undeclared.add(node.attr)

    return undeclared
