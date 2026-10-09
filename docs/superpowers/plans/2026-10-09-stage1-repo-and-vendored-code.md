# 1단계: 저장소와 가져온 코드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 유니 봇의 새 저장소를 만들고, chuni-penguin의 CHUNITHM-NET 어댑터를 `third_party/chuni_penguin/`에 가져와 쓸 수 있는 상태로 만든다. `scripts/sync_upstream.py`로 재동기화가 가능하게 하고, Docker Compose로 뜨는 것까지 확인한다.

**Architecture:** chuni-penguin의 어댑터는 자기 자신의 SQLAlchemy DB(곡 메타데이터 보강, PB upsert, 숨김곡 보정)에 묶여 있다는 게 조사로 드러났다. 이 DB 결합 부분만 `third_party/patches/`의 패치로 제거하고(패치는 "그 부분만 다시 쓴다"의 구체적 구현이다), 나머지는 통째로 가져온다. `scripts/sync_upstream.py`는 업스트림을 클론 → 패치 적용 → 실제로 import해서 생긴 `chuni_penguin.*` 모듈 전체(=의존 파일 범위)를 그대로 복사하는 방식으로, 의존 파일 목록을 손으로 유지하지 않는다.

**Tech Stack:** Python 3.14, uv, discord.py, httpx, httpx-aiohttp, selectolax, aiolimiter, hishel, msgspec, aiosqlite, pytest, ruff, basedpyright, Docker Compose.

**Spec:** `docs/design/00-core.md` ("저장소 구조와 개발 규칙", "작업 순서"의 1단계, "미확인 사항과 리스크"), `CLAUDE.md`.

## Global Constraints

- `third_party/`의 원본 파일 자체는 손으로 고치지 않는다. 동작을 바꿔야 하면 `third_party/patches/*.patch`에 패치로 남기고 `sync_upstream.py`가 복사 후 자동 적용한다.
- 원본이 바뀌면 `scripts/sync_upstream.py`로 통째 교체하고, `UPSTREAM` 파일에 원본 커밋을 남긴다.
- 원본 어댑터가 요구하는 라이브러리와 "버전"을 그대로 따른다(가능한 한 upstream의 `uv.lock`에 고정된 버전). 원본이 SQLAlchemy를 import하면 그건 `third_party` 때문에만 의존성으로 두고 우리 코드에서는 쓰지 않는다 — 단 이번엔 패치로 SQLAlchemy 의존 자체가 사라졌다.
- `.env`, `data/`, 쿠키가 든 파일은 커밋하지 않는다.
- Python 3.14, discord.py, aiosqlite + SQLite, httpx, uv, ruff, basedpyright, pytest, Docker Compose. ORM/alembic은 쓰지 않는다(이번 단계는 third_party 바깥에 DB 코드를 아직 안 쓴다).
- 미니 PC는 우분투 x86-64. Docker Compose, 재시작 정책 `unless-stopped`. 포트 개방 없음.
- 지금 단계 밖의 기능(코어, 서비스, 스킬)은 만들지 않는다. 1단계가 쓰지 않는 폴더(`core/`, `services/`, `skills/`, `prompts/`, `migrations/`)는 아직 만들지 않는다.

## Review Focus

- **업스트림이 바뀌어 패치가 안 먹는 경우**: `sync_upstream.py`가 조용히 옛 파일을 남기거나 반쪽만 교체하면 안 된다 — 패치 적용 실패 시 스크립트는 반드시 0이 아닌 종료 코드로 죽어야 한다(Task 2에서 고의로 깨진 패치로 검증).
- **`sync_upstream.py`를 반복 실행했을 때 파일이 누적되는 경우**: 매번 `third_party/chuni_penguin/`을 완전히 비우고 다시 채워야 하며, 두 번 연속 실행해도 파일 목록이 똑같아야 한다(Task 2에서 검증).
- **vendored 코드가 import는 되지만 실제로 파싱이 깨지는 경우**: "import는 성공하지만 아무 메서드도 못 쓴다"는 가장 속기 쉬운 실패 형태다. 실제 CHUNITHM-NET HTML 저장본으로 파싱까지 해보는 스모크 테스트가 있어야 한다(Task 3).
- **패치가 지운 `self.database` 사용처가 나중에 upstream 변경으로 또 생기는 경우**: 패치가 건드린 줄 밖에서 새로 생긴 `self.database` 사용은 patch가 "깔끔히 적용됨"으로 통과해도 여전히 우리 환경에서는 깨진다. import/인스턴스화만으로는 못 잡으므로, vendored adapter.py에 `self.database`/`self.discord_id` 읽기가 생성자 바깥에 없는지 정적으로 확인하는 테스트를 둔다(Task 3).
- **`.env`나 데이터가 커밋되는 경우**: `.gitignore`를 먼저 만들고 나서 파일을 만들어야 하며, 첫 커밋 전에 `git status`로 무엇이 올라가는지 반드시 확인한다(Task 1, Task 5).

---

## Task 1: 저장소 뼈대 만들기

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml` (uv가 생성)
- Create: `.env` (로컬 전용, 커밋 안 됨 — 지금은 내용 없이 존재만 함)
- Modify: 없음 (기존 `README.md`, `CLAUDE.md`, `docs/`는 그대로 둔다)

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces: `uv run <command>`로 실행 가능한 Python 3.14 프로젝트. 이후 모든 작업이 이 프로젝트 위에서 돈다.

- [ ] **Step 1: git 저장소 초기화**

```bash
cd /home/dn/projects/YuniBot
git init
git status
```

`docs/`, `README.md`, `CLAUDE.md`, `yuni-bot-design.tar`가 untracked로 보여야 한다.

- [ ] **Step 2: `.gitignore` 작성 (커밋 전에 먼저)**

```gitignore
# Python
__pycache__/
*.pyc
.venv/
*.egg-info/

# uv
.python-version

# 런타임 데이터와 비밀 값
.env
data/

# 에디터
.vscode/
.idea/

# 설계서 원본 압축 파일 (docs/design/으로 이미 풀려 있음)
yuni-bot-design.tar
```

- [ ] **Step 3: uv 프로젝트 초기화**

```bash
uv init --name yunibot --python 3.14 --no-readme .
```

생성된 `pyproject.toml`의 `[project]`에 `requires-python = ">=3.14"`가 들어있는지 확인한다. `main.py`가 생성됐다면 삭제한다(아직 쓸 코드가 없다):

```bash
rm -f main.py
```

- [ ] **Step 4: vendored 코드가 요구하는 의존성을 정확한 버전으로 추가**

chuni-penguin의 `uv.lock`에 고정된 버전을 그대로 따른다(아래 Task 2에서 실제로 vendoring할 때 검증된 조합이다). `hishel`은 `0.1.3`에서 `1.x`로 가면서 내부 모듈 구조가 바뀌어 꼭 버전을 고정해야 한다. `httpx-aiohttp`는 upstream이 쓰는 버전(`0.1.0`)이 그들 저장소 안의 로컬 패키지라 PyPI에 없으므로, PyPI에 공개된 버전으로 대신한다.

```bash
uv add "discord-py~=2.7" "httpx~=0.28.1" "httpx-aiohttp" "selectolax>=0.4.7" \
  "aiolimiter~=1.2" "hishel==0.1.3" "platformdirs~=4.5" "msgspec~=0.20.0" \
  "aiosqlite>=0.21"
uv add --dev pytest ruff basedpyright
```

- [ ] **Step 5: pytest가 `third_party/`를 import 경로로 보게 설정**

vendored 코드는 전부 `chuni_penguin.xxx` 절대 import를 쓴다(원본을 손대지 않기로 했으므로 이 import 경로를 바꿀 수 없다). `third_party/`를 `sys.path`에 넣어야 `import chuni_penguin...`이 풀린다. `pyproject.toml`에 추가:

```toml
[tool.pytest.ini_options]
pythonpath = ["third_party"]
```

- [ ] **Step 6: 커밋 전 확인**

```bash
git status
```

`.env`나 `data/`가 안 보여야 한다. 아직 커밋하지 않는다(Task 2 이후 한 번에 커밋하거나, 원한다면 여기서 뼈대만 커밋해도 된다 — 사용자에게 확인).

---

## Task 2: chuni-penguin 어댑터 vendoring + `sync_upstream.py`

**Files:**
- Create: `scripts/sync_upstream.py`
- Create: `third_party/patches/0001-chunithm-net-adapter-no-local-db.patch`
- Create: `third_party/patches/README.md`
- Create (스크립트가 생성): `third_party/chuni_penguin/**`, `third_party/chuni_penguin/UPSTREAM`, `third_party/chuni_penguin/UPSTREAM_LICENSE`

**Interfaces:**
- Consumes: Task 1에서 만든 uv 프로젝트(의존성이 설치돼 있어야 import 클로저 탐색이 된다).
- Produces: `third_party/chuni_penguin/adapters/chunithm_net/adapter.py`의 `ChunithmNetAdapter` 클래스. 이후 스테이지(4단계 `chunithm_net` 서비스)가 `database=None`을 넘겨 생성자를 호출할 수 있다(패치 후 `self.database`를 읽는 코드가 하나도 안 남기 때문).

**사전 조사로 확인한 사실** (이 Task의 근거):

- `ChunithmNetAdapter`를 아무 수정 없이 import하면 `chuni_penguin.adapters.utils`(→ `chuni_penguin.database` 전체, `chuni_penguin.logging`, `chuni_penguin.config`)까지 끌려온다. `chuni_penguin.config`는 import 시점에 `bot.ini`를 읽어 `Config(...)`를 생성하는데, 파일이 없으면 `KeyError: 'bot'`로 바로 죽는다(직접 재현해서 확인함).
- 점수를 돌려주는 메서드들(`get_recent_scores`, `get_personal_bests`, `get_best30`, `get_new20`, `get_rating_breakdown` 등)은 전부 `_process_records`를 거치는데, 이게 chuni-penguin 자신의 SQLAlchemy `Song`/`PersonalBest` 테이블에 레코드를 보강하고 업서트한다. 이 보강(곡 메타데이터, 레이팅 계산)은 우리 설계의 `songdb`/`records` 서비스가 따로 하기로 되어 있으므로 애초에 중복이다.
- 패치로 이 DB 결합 4곳(`_process_records`, `_get_hidden_personal_bests`, `get_rating_breakdown`의 숨김곡 보정, ONGEKI 레이팅 분기)만 제거하면 `chuni_penguin.database`, `chuni_penguin.logging`, `chuni_penguin.config`가 전부 필요 없어지고, SQLAlchemy와 `structlog` 의존성도 사라진다. 실제로 import 테스트와 HTML 저장본 파싱 테스트로 검증했다.
- 남는 의존 파일 범위는 `adapters/`(패치된 `chunithm_net/adapter.py` 포함), `types/`, `constants.py`, `utils/`(hishel, httpcore, platformdirs 필요), `calculation/`(순수 함수, `types/song.py`가 직접 씀) 뿐이다. discord.py는 `discord.utils.MISSING` 등 몇 군데서 쓰지만 우리도 쓰는 라이브러리라 문제 없다.
- 선택적 네이티브(Rust) 모듈 `penguin-native`는 `oggopus.py`(음성 기능)에만 쓰이고 pyproject의 `speedup`(선택) 그룹에만 있다 — 우리에게 필수 아님, vendoring 대상에서 제외.
- 시드 데이터 `database/seeds/songs.json`은 SQLAlchemy와 무관한 순수 JSON 리스트(곡/채보/상수/노트수/sdvx.in 매핑)라 그대로 데이터로 가져올 수 있다.

- [ ] **Step 1: 패치 파일 작성**

`third_party/patches/0001-chunithm-net-adapter-no-local-db.patch`:

```diff
diff --git a/chuni_penguin/adapters/chunithm_net/adapter.py b/chuni_penguin/adapters/chunithm_net/adapter.py
index a22a900..2f9c92b 100644
--- a/chuni_penguin/adapters/chunithm_net/adapter.py
+++ b/chuni_penguin/adapters/chunithm_net/adapter.py
@@ -14,10 +14,6 @@ from selectolax.lexbor import LexborHTMLParser
 from chuni_penguin.adapters._hooks import raise_on_server_errors
 from chuni_penguin.adapters.base import NetworkAdapter
 from chuni_penguin.adapters.errors import AlreadyFriends, InvalidFriendCode
-from chuni_penguin.adapters.utils import (
-    calculate_ongeki_rating_breakdown,
-    process_records,
-)
 from chuni_penguin.constants import CURRENT_CHUNITHM_VERSION, ChunithmVersion
 from chuni_penguin.types import (
     CourseRecord,
@@ -141,26 +137,12 @@ class ChunithmNetAdapter(NetworkAdapter):
         return LexborHTMLParser(resp.content, is_fragment=False)
 
     async def _process_records[T: Score](self, records: Sequence[T]) -> list[T]:
-        records = await process_records(
-            self.database, self.discord_id, self.NAME, records
-        )
-        song_ids = [r.song.id for r in records]
-        ult_song_ids = [
-            r.song.id for r in records if r.chart.difficulty == Difficulty.ultima
-        ]
-
-        if len(song_ids) > 0:
-            await self.database.songs.set_available(song_ids, available=True)
-            await self.database.charts.set_available(
-                song_ids, available=True, is_ultima=False
-            )
-
-        if len(ult_song_ids) > 0:
-            await self.database.charts.set_available(
-                ult_song_ids, available=True, is_ultima=True
-            )
-
-        return records
+        # YUNIBOT PATCH (see third_party/patches/README.md): upstream hydrates
+        # records against chuni-penguin's own SQLAlchemy DB (song metadata,
+        # rating/overpower calc, personal-best upsert) and marks songs/charts
+        # "available" there. We never wire up that DB, so return the raw
+        # parsed records as-is; our own songdb/records services hydrate them.
+        return list(records)
 
     @property
     def _token(self):
@@ -222,20 +204,10 @@ class ChunithmNetAdapter(NetworkAdapter):
     async def _get_hidden_personal_bests(
         self, level: str | None = None, difficulty: Difficulty | None = None
     ):
-        hidden_charts = await self.database.charts.get_hidden_on_chuninet(
-            level=level, difficulty=difficulty
-        )
-        hidden_song_ids = {c.song_id for c in hidden_charts}
-        pbs: list[PersonalBest] = []
-
-        for song_id in hidden_song_ids:
-            # get the records for the hidden chart's song id
-            hidden_records = await self.get_personal_bests_on_song(song_id)
-
-            # and insert it into our records, if a record is not already there
-            pbs.extend(hidden_records)
-
-        return pbs
+        # YUNIBOT PATCH: upstream tracks which charts it has flagged hidden on
+        # CHUNITHM-NET in its own DB, then re-fetches PBs for those songs. We
+        # don't have that table, so there is nothing to add back here.
+        return []
 
     async def get_personal_bests(
         self,
@@ -436,7 +408,11 @@ class ChunithmNetAdapter(NetworkAdapter):
                 key=lambda x: new20_charts.index((x.song.id, x.chart.difficulty))
             )
 
-            hidden_songs = await self.database.songs.get_hidden_on_chuninet()
+            # YUNIBOT PATCH: upstream looks up songs it has flagged hidden
+            # on CHUNITHM-NET in its own DB. We don't have that table, so this
+            # workaround is disabled; our own songdb service should take over
+            # the hidden-song accounting it needs.
+            hidden_songs = []
 
             # Sometimes, SEGA likes to hide some scores from appearing in
             # CHUNITHM-NET. This is a workaround. Basically:
@@ -542,9 +518,6 @@ class ChunithmNetAdapter(NetworkAdapter):
                 },
             )
 
-        if rating_type in (RatingType.ongeki, RatingType.ongeki_naive):
-            return calculate_ongeki_rating_breakdown(rating_type, pbs)
-
         msg = f"Unknown RatingType variant {rating_type!r}"
         raise RuntimeError(msg)
```

- [ ] **Step 2: 패치 설명 문서**

`third_party/patches/README.md`:

```markdown
# third_party 패치

`third_party/` 파일 자체는 손으로 고치지 않는다(CLAUDE.md). 원본과 다르게
동작해야 할 부분이 있으면 여기에 `.patch` 파일로 남기고, `scripts/sync_upstream.py`가
업스트림을 복사한 뒤 자동으로 적용한다. 패치가 더 이상 깔끔히 적용되지 않으면
`sync_upstream.py`가 실패하고, 그때 사람이 새 업스트림 버전에 맞춰 다시 diff를 뜬다.

## 0001-chunithm-net-adapter-no-local-db.patch

`ChunithmNetAdapter`의 점수 조회 메서드는 원래 chuni-penguin 자신의
SQLAlchemy DB에 묶여 있다(곡 메타데이터 보강, 레이팅 계산, PB upsert, 숨김곡
보정). 우리는 그 DB를 쓰지 않고 `songdb`/`records` 서비스가 같은 일을 하므로,
이 패치는 그 4곳(`_process_records`, `_get_hidden_personal_bests`,
`get_rating_breakdown`의 숨김곡 보정, ONGEKI 레이팅 분기)을 "DB 없이 원본
레코드를 그대로 반환"으로 바꾼다. 이 패치 덕분에 `chuni_penguin.database`,
`chuni_penguin.logging`, `chuni_penguin.config`, SQLAlchemy, structlog가
전부 필요 없어진다.

패치 적용 후 `self.database`를 읽는 코드는 생성자(`chuni_penguin/adapters/base.py`)
말고는 남지 않는다. 그래서 우리 쪽에서 `ChunithmNetAdapter(database=None, ...)`로
생성해도 된다 — `database`는 저장만 되고 아무도 읽지 않는다.
```

- [ ] **Step 3: `scripts/sync_upstream.py` 작성**

```python
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
from datetime import datetime, timezone
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
    run(["git", "clone", UPSTREAM_URL, str(tmp)])

    if ref is not None:
        run(["git", "checkout", ref], cwd=tmp)

    return run(["git", "rev-parse", "HEAD"], cwd=tmp).strip()


def apply_patches(tmp: Path) -> list[str]:
    applied = []

    for patch in sorted(PATCHES_DIR.glob("*.patch")):
        check = subprocess.run(
            ["git", "apply", "--check", str(patch)],
            cwd=tmp,
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

        run(["git", "apply", str(patch)], cwd=tmp)
        applied.append(patch.name)

    return applied


def discover_import_closure(tmp: Path) -> list[Path]:
    sys.path.insert(0, str(tmp))

    try:
        for module_name in ENTRYPOINT_MODULES:
            __import__(module_name)
    finally:
        sys.path.remove(str(tmp))

    files = []

    for name, module in sorted(sys.modules.items()):
        if not name.startswith("chuni_penguin"):
            continue

        file = getattr(module, "__file__", None)

        if file is None:
            continue

        path = Path(file)

        if tmp in path.parents or path == tmp:
            files.append(path.relative_to(tmp))

    return files


def vendor(tmp: Path, files: list[Path]) -> None:
    if THIRD_PARTY_DIR.exists():
        shutil.rmtree(THIRD_PARTY_DIR)

    for rel_path in [*files, *(Path(p) for p in EXTRA_DATA_FILES)]:
        src = tmp / rel_path
        dst = THIRD_PARTY_DIR.parent / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    shutil.copy2(tmp / "LICENSE", THIRD_PARTY_DIR / "UPSTREAM_LICENSE")


def write_upstream_file(commit: str, applied_patches: list[str]) -> None:
    lines = [
        f"source: {UPSTREAM_URL}",
        f"commit: {commit}",
        f"synced_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "patches:",
        *[f"  - third_party/patches/{name}" for name in applied_patches],
        "",
    ]
    (THIRD_PARTY_DIR / "UPSTREAM").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref",
        default=None,
        help="Upstream commit to pin to (default: latest default branch HEAD)",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        commit = clone_upstream(tmp, args.ref)
        applied_patches = apply_patches(tmp)
        files = discover_import_closure(tmp)
        vendor(tmp, files)
        write_upstream_file(commit, applied_patches)

    print(f"vendored {len(files)} files from commit {commit}")
    print(f"applied patches: {', '.join(applied_patches) or '(none)'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 처음 vendoring 실행 (검증에 쓴 커밋으로 고정)**

```bash
uv run python3 scripts/sync_upstream.py --ref 6c926755f296476850adbb59a98651156ba7c7f7
```

예상 출력: `vendored 37 files from commit 6c926755f296476850adbb59a98651156ba7c7f7` / `applied patches: 0001-chunithm-net-adapter-no-local-db.patch`. (이미 이 커밋으로 전체 과정을 한 번 검증했다.)

- [ ] **Step 5: import와 파일 개수 확인**

```bash
uv run python3 -c "
import sys
sys.path.insert(0, 'third_party')
from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
print('OK:', ChunithmNetAdapter)
"
find third_party/chuni_penguin -type f | wc -l   # 41이어야 함 (37 import closure + titles.json + songs.json + UPSTREAM + UPSTREAM_LICENSE)
```

- [ ] **Step 6: 멱등성 확인 — 두 번 연속 실행해도 결과가 같아야 함**

```bash
uv run python3 scripts/sync_upstream.py --ref 6c926755f296476850adbb59a98651156ba7c7f7
find third_party/chuni_penguin -type f | wc -l   # 여전히 41
```

- [ ] **Step 7: 패치가 깨지면 실패하는지 확인 (일회성 검증, 되돌림)**

```bash
cp third_party/patches/0001-chunithm-net-adapter-no-local-db.patch /tmp/patch-backup.patch
sed -i 's/async def _process_records/async def _process_records_BROKEN/' third_party/patches/0001-chunithm-net-adapter-no-local-db.patch
uv run python3 scripts/sync_upstream.py --ref 6c926755f296476850adbb59a98651156ba7c7f7; echo "exit code: $?"
```

0이 아닌 종료 코드와 "patch ... no longer applies cleanly" 에러가 나와야 한다. 확인 후 원복:

```bash
cp /tmp/patch-backup.patch third_party/patches/0001-chunithm-net-adapter-no-local-db.patch
rm /tmp/patch-backup.patch
uv run python3 scripts/sync_upstream.py --ref 6c926755f296476850adbb59a98651156ba7c7f7
```

- [ ] **Step 8: 커밋**

```bash
git add scripts/sync_upstream.py third_party/ pyproject.toml uv.lock .gitignore
git status   # third_party/chuni_penguin/** 와 patches/가 올라가는지, .env/data/는 안 올라가는지 확인
git commit -m "vendor chuni-penguin's CHUNITHM-NET adapter via sync_upstream.py"
```

---

## Task 3: vendored 코드 스모크 테스트

**Files:**
- Create: `docs/reference/chunithm-net/player_data.html` (chuni-penguin 테스트 저장본에서 가져온 샘플 HTML, 0BSD)
- Create: `tests/third_party/__init__.py`
- Create: `tests/third_party/test_chunithm_net_vendoring.py`

**Interfaces:**
- Consumes: Task 2의 vendored `third_party/chuni_penguin/`.
- Produces: `uv run pytest`로 상시 실행되는 회귀 테스트. 이후 단계(4단계 `chunithm_net` 서비스)가 이 테스트를 안전망으로 쓴다.

- [ ] **Step 1: 샘플 HTML 저장본 가져오기**

chuni-penguin 저장소의 `tests/chuni_penguin/adapters/chunithm_net/assets/player_data.html`은 자기들 파서 테스트용으로 이미 저장해 둔 샘플(0BSD)이다. 그대로 가져온다:

```bash
mkdir -p docs/reference/chunithm-net
curl -sL -o docs/reference/chunithm-net/player_data.html \
  https://raw.githubusercontent.com/beer-psi/chuni-penguin/6c926755f296476850adbb59a98651156ba7c7f7/tests/chuni_penguin/adapters/chunithm_net/assets/player_data.html
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/third_party/test_chunithm_net_vendoring.py`:

```python
import ast
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.chunithm_net.parser import parse_player_data

FIXTURE = Path("docs/reference/chunithm-net/player_data.html")
ADAPTER_SOURCE = Path("third_party/chuni_penguin/adapters/chunithm_net/adapter.py")


def test_parses_saved_player_data_page():
    html = FIXTURE.read_text(encoding="utf-8")
    profile = parse_player_data(LexborHTMLParser(html))

    assert profile.username
    assert profile.level > 0


def test_adapter_constructs_without_a_database():
    adapter = ChunithmNetAdapter(
        database=None,
        discord_id=0,
        lwp_cookie_jar="#LWP-Cookies-2.0\n",
    )
    assert adapter is not None


def test_patched_adapter_never_reads_self_database_outside_init():
    """Regression check for the sync_upstream.py patch: if a future upstream
    sync reintroduces a `self.database` read outside the constructor, this
    fails loudly instead of only blowing up later at runtime with database=None.
    """
    tree = ast.parse(ADAPTER_SOURCE.read_text(encoding="utf-8"))
    (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef)]

    offending: list[str] = []

    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "database"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            offending.append(f"line {node.lineno}")

    assert offending == [], f"self.database read outside __init__: {offending}"
```

- [ ] **Step 3: 테스트가 실패하는지 확인 (vendoring 전 상태를 가정한 확인이므로, 지금은 이미 Task 2가 끝났으니 바로 성공해도 된다 — 대신 일부러 깨뜨려서 테스트 자체가 유효한지 확인)**

```bash
uv run pytest tests/third_party/ -v
```

세 테스트 모두 통과해야 한다. 테스트가 실제로 뭔가 검증하는지 확인하려면, `return list(records)`를 임시로 `return await process_records(...)`로 되돌려 보고 세 번째 테스트가 실패하는지, `FIXTURE` 경로를 일부러 틀려서 첫 번째 테스트가 실패하는지 확인한 뒤 원복한다.

- [ ] **Step 4: 커밋**

```bash
git add docs/reference/chunithm-net/ tests/third_party/
git commit -m "add smoke tests for the vendored CHUNITHM-NET adapter"
```

---

## Task 4: Dockerfile과 docker-compose

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env` (로컬, 커밋 안 됨)

**Interfaces:**
- Consumes: Task 1~3의 전체 프로젝트.
- Produces: `docker compose up -d --build`로 뜨는 컨테이너. 2단계가 이 Dockerfile의 `CMD`를 실제 봇 엔트리포인트로 바꿔 끼운다.

- [ ] **Step 1: Dockerfile 작성**

```dockerfile
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY . .
RUN uv sync --locked --no-dev

ENV PYTHONPATH=/app/third_party

# 1단계 placeholder: 2단계에서 core/의 실제 엔트리포인트로 바꾼다.
CMD ["sleep", "infinity"]
```

- [ ] **Step 2: docker-compose.yml 작성**

```yaml
services:
  yuni:
    build: .
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/app/data
```

- [ ] **Step 3: 로컬 `.env`와 `data/` 준비 (커밋 안 됨)**

```bash
touch .env
mkdir -p data
```

- [ ] **Step 4: 빌드와 기동 확인**

```bash
docker compose up -d --build
docker compose ps        # yuni 컨테이너가 Up (혹은 Restarting이 아니어야 함 — placeholder CMD는 sleep이라 계속 떠 있어야 한다)
```

- [ ] **Step 5: 컨테이너 안에서 vendored 코드가 실제로 동작하는지 확인**

```bash
docker compose exec yuni uv run pytest tests/third_party/ -v
```

세 테스트 모두 통과해야 한다 — 이게 "third_party가 이미지 안에 제대로 들어갔고 import 경로(`PYTHONPATH`)도 맞다"는 확인이다.

- [ ] **Step 6: 정리**

```bash
docker compose down
```

- [ ] **Step 7: 커밋**

```bash
git add Dockerfile docker-compose.yml
git status   # .env, data/는 안 보여야 함
git commit -m "add Dockerfile and docker-compose for the mini PC deployment"
```

> **중요한 제약**: 이 Task는 x86-64 리눅스에서 Docker Compose로 빌드/기동이 되는 것까지 로컬로 검증한다. 실제 미니 PC(우분투, 라이젠 5)에 올라가서 뜨는 것은 아키텍처가 같은 x86-64라 같은 결과가 나올 것으로 보이지만, 그 기기 자체에서의 최종 확인은 사용자가 직접 해야 한다(에이전트가 그 기기에 접근할 수 없음).

---

## Task 5: 설계서 반영 + CLAUDE.md "명령" 절 갱신

**Files:**
- Modify: `docs/design/00-core.md` ("조사로 확인한 전제" 절, "미확인 사항과 리스크" 표)
- Modify: `CLAUDE.md` (third_party 규칙에 패치 메커니즘 한 줄 추가, "명령" 절)

**Interfaces:**
- Consumes: Task 1~4에서 실제로 확인된 사실과 명령.
- Produces: 다음 세션이 다시 조사하지 않아도 되는, 사실과 일치하는 설계서.

- [ ] **Step 1: `docs/design/00-core.md`의 "조사로 확인한 전제" 절 수정**

현재 문장(42~43번째 줄 근처):

```
- CHUNITHM-NET 접속과 파싱은 `chuni_penguin/adapters/`에 분리되어 있다. `NetworkAdapter` 추상 클래스가 프로필, 최근 기록, 베스트, 레이팅 내역, 코스 기록 등을 비동기 함수로 제공한다.
- 실제 구현인 `ChunithmNetAdapter`는 httpx와 selectolax만 쓰고 discord.py를 가져오지 않는다. 다만 같은 패키지의 `types`, `constants`, `utils`에는 의존한다. 원본은 Python 3.14 이상을 요구하고, 시드 데이터에 노트 수, 채보 제작자, sdvx.in 매핑(수록 중인 MAS·ULT 1,672채보 중 1,670개), 곡별 위키 문서명이 들어 있다. 한국어 별칭은 없다.
```

다음으로 교체:

```
- CHUNITHM-NET 접속과 파싱은 `chuni_penguin/adapters/`에 분리되어 있다. `NetworkAdapter` 추상 클래스가 프로필, 최근 기록, 베스트, 레이팅 내역, 코스 기록 등을 비동기 함수로 제공한다.
- 실제 구현인 `ChunithmNetAdapter`는 httpx와 selectolax로 HTTP와 파싱을 하지만, `discord.utils.MISSING` 같은 사소한 참조로 discord.py를 가져오긴 한다(우리도 쓰는 라이브러리라 문제는 아니다). 같은 패키지의 `types`, `constants`, `utils`, `calculation`에 의존한다. 점수를 돌려주는 메서드 대부분은 원래 chuni-penguin 자신의 SQLAlchemy DB(곡 메타데이터 보강, 레이팅 계산, PB upsert, 숨김곡 보정)에 묶여 있었는데, 그 보강은 우리의 `songdb`/`records` 서비스가 대신하기로 했으므로 1단계에서 그 결합 부분만 `third_party/patches/`의 작은 패치로 떼어냈다(상세는 `third_party/patches/README.md`). 패치 후에는 SQLAlchemy, structlog, chuni-penguin 자신의 설정 파일(`bot.ini`)이 전부 필요 없다. 원본은 Python 3.14 이상을 요구하고, 선택적 네이티브(Rust) 모듈(`penguin-native`)은 음성 기능에만 쓰여 필수가 아니다. 시드 데이터(`database/seeds/songs.json`)는 SQLAlchemy와 무관한 순수 JSON 리스트라 그대로 데이터로 쓸 수 있고, 노트 수, 채보 제작자, sdvx.in 매핑(수록 중인 MAS·ULT 1,672채보 중 1,670개), 곡별 위키 문서명이 들어 있다. 한국어 별칭은 없다.
```

- [ ] **Step 2: "미확인 사항과 리스크" 표 수정**

현재 두 행:

```
| 원본 `adapters`가 의존하는 파일 범위 | `types`, `constants`, `utils` 의존까지만 확인 | 1단계에서 전부 추적. 그 파일들이 다시 discord에 의존하면 그 부분만 우리 쪽에서 다시 쓴다. 선택적 네이티브 모듈(Rust)이 필수인지도 확인한다 |
```

```
| 원본 시드를 데이터로만 쓸 수 있는지 | 확인 못 함 | 4단계에서 시드 형식을 보고 읽기 코드를 새로 쓸지 정한다 |
```

다음으로 교체:

```
| 원본 `adapters`가 의존하는 파일 범위 | 확인됨 (1단계). `types`/`constants`/`utils`/`calculation`까지 필요. 점수 조회 메서드는 chuni-penguin 자신의 SQLAlchemy DB에 묶여 있어 `third_party/patches/`의 패치로 그 부분만 제거했다. 네이티브(Rust) 모듈은 음성 기능 전용이라 불필요 | 패치가 업스트림 변경으로 깨지면 `sync_upstream.py`가 실패하도록 했다(실제 검증함). 그때 사람이 다시 diff를 맞춘다 |
```

```
| 원본 시드를 데이터로만 쓸 수 있는지 | 확인됨 (1단계). `database/seeds/songs.json`은 SQLAlchemy와 무관한 순수 JSON 리스트라 그대로 데이터로 읽어 쓸 수 있다 | 4단계에서 이 형식으로 `songdb` 읽기 코드를 쓴다 |
```

- [ ] **Step 3: `CLAUDE.md`의 third_party 규칙에 패치 메커니즘 한 줄 추가**

현재:

```
- `third_party/`는 직접 고치지 않는다. 원본이 바뀌면 `scripts/sync_upstream.py`로 통째 교체하고, `UPSTREAM` 파일에 원본 커밋을 남긴다.
```

다음으로 교체:

```
- `third_party/`는 직접 고치지 않는다. 원본이 바뀌면 `scripts/sync_upstream.py`로 통째 교체하고, `UPSTREAM` 파일에 원본 커밋을 남긴다. 꼭 필요한 작은 동작 차이는 `third_party/patches/*.patch`로 보관해 `sync_upstream.py`가 교체 후 자동 적용하고, 패치가 더 안 맞으면 스크립트가 실패한다.
```

- [ ] **Step 4: `CLAUDE.md`의 "명령" 절을 실제로 확인한 명령으로 교체**

현재(예상이라고 적혀 있던 자리):

```
1단계에서 실제로 돌려 보고 이 절을 채운다. 예상:

\`\`\`bash
uv sync                                      # 의존성 설치
uv run pytest                                # 테스트
uv run ruff check . && uv run basedpyright   # 린트와 타입 검사
docker compose up -d --build                 # 미니 PC 배포 (x86-64 우분투)
\`\`\`
```

다음으로 교체(Task 1~4를 전부 실행한 뒤, 아래 명령이 실제로 통과하는 걸 확인하고 나서 적는다):

```bash
uv sync                                                    # 의존성 설치
uv run pytest                                               # 테스트
uv run ruff check .                                          # 린트
uv run basedpyright                                          # 타입 검사
uv run python3 scripts/sync_upstream.py [--ref <commit>]     # chuni-penguin 재동기화 (버전 고정 시 --ref)
docker compose up -d --build                                 # 기동 (x86-64 리눅스 — 미니 PC와 같은 아키텍처)
```

- [ ] **Step 5: 커밋**

```bash
git add docs/design/00-core.md CLAUDE.md
git commit -m "record stage 1 findings in the design docs and fill in CLAUDE.md's 명령 section"
```

---

## 최종 확인 (1단계 통과 기준)

- [ ] `third_party/chuni_penguin/`에 패치된 `ChunithmNetAdapter`와 그 의존 파일, 시드 데이터가 들어있다.
- [ ] `scripts/sync_upstream.py`가 존재하고, 정상 실행·멱등 재실행·패치 실패 시 비정상 종료 세 가지를 모두 검증했다.
- [ ] `third_party/chuni_penguin/UPSTREAM`에 원본 커밋이 기록되어 있다.
- [ ] `uv run pytest`가 통과한다(vendored 코드의 import + 파싱 + DB 비의존 스모크 테스트).
- [ ] `docker compose up -d --build`가 x86-64 리눅스에서 빌드/기동되고, 컨테이너 안에서도 같은 pytest가 통과한다. (미니 PC 자체에서의 최종 확인은 사용자 몫.)
- [ ] `docs/design/00-core.md`와 `CLAUDE.md`가 1단계에서 확인한 사실과 일치한다.
