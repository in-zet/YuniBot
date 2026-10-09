from __future__ import annotations

import ast
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path

from core.decorators import SkillCode, SlashOnlySpec, ToolSpec, collect_specs
from core.manifest import DefinedTag, SkillManifest, parse_manifest
from core.schema import build_arg_schema

logger = logging.getLogger(__name__)

KNOWN_SERVICE_NAMES: frozenset[str] = frozenset(
    {
        "players", "settings", "memory", "notify", "yuni", "secrets", "arcades",
        "websearch", "http", "chunithm_net", "songdb", "records", "terms",
    }
)

# 00-core.md "이벤트 목록"에서 코어/서비스가 내는 것으로 적힌 이벤트.
# 3~4단계 전까지는 실제로 내는 코드가 없어도 "정의된 이벤트"로 취급한다.
CORE_EMITTED_EVENTS: frozenset[str] = frozenset(
    {"players.first_seen", "chunithm.login.expired"}
)

CORE_DEFINED_TAGS: frozenset[str] = frozenset({"admin", "tester", "chunithm-linked", "chunithm-synced"})


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
            build_arg_schema(spec.fn)  # 스키마 생성 불가면 SchemaBuildError 전파

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
        # 토큰 단위로 경로를 모아, "단독 명령으로 쓴 이름"과 "그룹(하위 명령 보유)으로 쓴 이름"이
        # 겹치는지, 완전히 같은 경로가 두 번 쓰였는지를 검사한다.
        path_owner: dict[tuple[str, ...], str] = {}
        root_is_group: dict[str, bool] = {}
        root_owner: dict[str, str] = {}

        for skill_name in list(self._loaded):
            skill = self._loaded[skill_name]
            paths = [
                tuple(spec.slash.split())
                for spec in [*skill.code.tools, *skill.code.slash_only_tools]
                if spec.slash
            ]
            for path in paths:
                root = path[0]
                is_group = len(path) > 1

                if root in root_owner and root_owner[root] != skill_name:
                    if root_is_group[root] != is_group:
                        self._disable(
                            skill_name,
                            f"슬래시 명령 '{root}'이 단독 명령과 그룹으로 동시에 쓰였다 "
                            f"(이미 '{root_owner[root]}' 스킬이 다른 형태로 등록함)",
                        )
                        self._purge_owner_entries(skill_name, path_owner, root_owner, root_is_group)
                        break
                else:
                    root_owner.setdefault(root, skill_name)
                    root_is_group.setdefault(root, is_group)

                if path in path_owner and path_owner[path] != skill_name:
                    self._disable(
                        skill_name,
                        f"슬래시 경로 '{' '.join(path)}'가 '{path_owner[path]}' 스킬과 겹친다",
                    )
                    self._purge_owner_entries(skill_name, path_owner, root_owner, root_is_group)
                    break
                path_owner.setdefault(path, skill_name)

    @staticmethod
    def _purge_owner_entries(
        skill_name: str,
        path_owner: dict[tuple[str, ...], str],
        root_owner: dict[str, str],
        root_is_group: dict[str, bool],
    ) -> None:
        # 이 스킬이 비활성화되기 전까지 등록해 둔 소유권을 모두 지운다. 지우지 않으면 나중에 로드되는
        # 스킬이 "이미 존재하지 않는 스킬"과 충돌한다고 잘못 판정된다.
        for path in [p for p, owner in path_owner.items() if owner == skill_name]:
            del path_owner[path]
        for root in [r for r, owner in root_owner.items() if owner == skill_name]:
            del root_owner[root]
            root_is_group.pop(root, None)

    def _disable(self, skill_name: str, reason: str) -> None:
        skill = self._loaded.pop(skill_name, None)
        self._failures[skill.path.name if skill else skill_name] = reason


class _SkillLoadError(Exception):
    pass


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
