"""Re-vendor chuni-penguin's CHUNITHM-NET adapter into third_party/chuni_penguin/.

Usage: uv run scripts/sync_upstream.py [--ref <commit-sha>]

What this does:
1. Clones beer-psi/chuni-penguin (at --ref, or latest default branch).
2. Applies every third_party/patches/*.patch to the clone in place.
3. Imports chuni_penguin.adapters.chunithm_net from the patched clone and
   records every chuni_penguin.* module Python actually loaded - that import
   closure IS the file list we vendor. We don't hand-maintain a path list:
   if a future upstream change drags in a new dependency, it shows up here
   automatically (and if that dependency is unwanted, it's a patch target).
4. Copies that closure, plus a short list of non-Python data files the
   closure reads from disk, into third_party/chuni_penguin/.
5. Writes third_party/chuni_penguin/UPSTREAM with the commit and patch list.

If a patch no longer applies, or the import closure fails, this script
exits non-zero with the upstream error - that's the signal a human needs to
re-diff the patch against the new upstream version.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

UPSTREAM_URL = "https://github.com/beer-psi/chuni-penguin.git"

# chuni_penguin.adapters.chunithm_net is the only module we need the import
# closure of: it's the actual CHUNITHM-NET adapter + everything it needs
# (types, constants, utils, calculation). See docs/design/00-core.md's
# "조사로 확인한 전제" section for why this is safe post-patch (no DB/discord
# coupling left) and tests/third_party/test_chunithm_net_vendoring.py for the
# regression check that it stays that way.
ENTRYPOINT_MODULES = ("chuni_penguin.adapters.chunithm_net",)

# Files the entrypoint closure reads from disk at runtime but never
# `import`s, so they can't be auto-discovered via sys.modules.
EXTRA_DATA_FILES = (
    "chuni_penguin/adapters/chunithm_net/assets/titles.json",
    "chuni_penguin/database/seeds/songs.json",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
THIRD_PARTY_DIR = REPO_ROOT / "third_party" / "chuni_penguin"
PATCHES_DIR = REPO_ROOT / "third_party" / "patches"


def run(cmd: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)

    if result.returncode != 0:
        msg = f"command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}"
        raise RuntimeError(msg)

    return result.stdout


def clone_upstream(tmp: Path, ref: str | None) -> str:
    _ = run(["git", "clone", UPSTREAM_URL, str(tmp)])

    if ref is not None:
        _ = run(["git", "checkout", ref], cwd=tmp)

    return run(["git", "rev-parse", "HEAD"], cwd=tmp).strip()


def apply_patches(tmp: Path) -> list[str]:
    applied: list[str] = []

    for patch in sorted(PATCHES_DIR.glob("*.patch")):
        check = subprocess.run(
            ["git", "apply", "--check", str(patch)],
            cwd=tmp,
            check=False,
            capture_output=True,
            text=True,
        )

        if check.returncode != 0:
            msg = (
                f"patch {patch.name} no longer applies cleanly to this upstream "
                f"commit. Re-diff it by hand against the new version and update "
                f"it before re-running this script.\n{check.stderr}"
            )
            raise RuntimeError(msg)

        _ = run(["git", "apply", str(patch)], cwd=tmp)
        applied.append(patch.name)

    return applied


def discover_import_closure(tmp: Path) -> list[Path]:
    sys.path.insert(0, str(tmp))

    try:
        for module_name in ENTRYPOINT_MODULES:
            __import__(module_name)
    finally:
        sys.path.remove(str(tmp))

    files: list[Path] = []

    for name, module in sorted(sys.modules.items()):
        if not name.startswith("chuni_penguin"):
            continue

        file: str | None = getattr(module, "__file__", None)

        if file is None:
            continue

        path = Path(file)

        if tmp in path.parents or path == tmp:
            files.append(path.relative_to(tmp))

    return files


def vendor(tmp: Path, files: list[Path]) -> None:
    """가져올 파일을 전부 임시 폴더에 먼저 모은 뒤 기존 벤더 폴더와 바꾼다. 복사 중간에 실패해도(upstream에 추가 데이터
    파일이 없어졌다는 등) `third_party/chuni_penguin`이 비거나 반쪽이 되지 않는다."""
    wanted = [*files, *(Path(p) for p in EXTRA_DATA_FILES)]
    missing = [str(rel) for rel in wanted if not (tmp / rel).is_file()]
    if missing:
        raise SystemExit(f"upstream에 없는 파일이 있어 가져오지 않는다: {', '.join(missing)}")
    outside = [str(rel) for rel in wanted if THIRD_PARTY_DIR.name not in rel.parts[:1]]
    if outside:
        raise SystemExit(f"{THIRD_PARTY_DIR.name} 밖의 파일은 바꿔치기 대상이 아니다: {', '.join(outside)}")

    staging_root = THIRD_PARTY_DIR.parent / ".sync_staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    try:
        for rel_path in wanted:
            dst = staging_root / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copy2(tmp / rel_path, dst)
        _ = shutil.copy2(tmp / "LICENSE", staging_root / THIRD_PARTY_DIR.name / "UPSTREAM_LICENSE")

        if THIRD_PARTY_DIR.exists():
            shutil.rmtree(THIRD_PARTY_DIR)
        _ = (staging_root / THIRD_PARTY_DIR.name).replace(THIRD_PARTY_DIR)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def write_upstream_file(commit: str, applied_patches: list[str]) -> None:
    lines = [
        f"source: {UPSTREAM_URL}",
        f"commit: {commit}",
        f"synced_at: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "patches:",
        *[f"  - third_party/patches/{name}" for name in applied_patches],
        "",
    ]
    _ = (THIRD_PARTY_DIR / "UPSTREAM").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--ref",
        default=None,
        help="Upstream commit to pin to (default: latest default branch HEAD)",
    )
    args = parser.parse_args()
    ref: str | None = args.ref  # pyright: ignore[reportAny]

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        commit = clone_upstream(tmp, ref)
        applied_patches = apply_patches(tmp)
        files = discover_import_closure(tmp)
        vendor(tmp, files)
        write_upstream_file(commit, applied_patches)

    print(f"vendored {len(files)} files from commit {commit}")
    print(f"applied patches: {', '.join(applied_patches) or '(none)'}")


if __name__ == "__main__":
    main()
