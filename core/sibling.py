# core/sibling.py
"""같은 스킬 폴더의 보조 모듈(models.py, *_calc.py, render.py)을 파일 경로로 불러온다.

스킬 폴더 이름에 하이픈이 있어(`account-link`, `yuni-bond`) 보통의 패키지 import 경로로 쓸 수 없다.
스킬마다 같은 importlib 코드를 베끼지 않도록 여기 한 곳에 둔다."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_sibling(skill_file: str | Path, name: str, *, prefix: str) -> ModuleType:
    """`skill_file`(스킬의 `__file__`)과 같은 폴더의 `name`.py를 `_{prefix}_{name}` 모듈로 불러온다."""
    path = Path(skill_file).parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_{prefix}_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass(kw_only=True)가 `sys.modules[cls.__module__]`로 모듈 전역을 되찾으므로 exec 전에 등록해야 한다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
