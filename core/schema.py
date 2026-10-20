# core/schema.py
from __future__ import annotations

import enum
import inspect
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, get_args, get_origin, get_type_hints, is_typeddict

from core.decorators import DRY_RUN_PARAM
from core.tables import TableNeeds
from core.tool_types import STANDARD_REF_TYPES, TableRef

_PRIMITIVES: frozenset[type] = frozenset({str, int, float, bool})

# 실행기가 직접 넣는 인자 — 모델/슬래시가 고르는 값이 아니라 스키마에 싣지 않는다.
# dry_run: confirm="bulk" 도구에 실행기가 먼저 dry_run=True로 불러 바뀔 건수(data["count"])를
# 받아 본다(core/executor.py). 모델/슬래시가 같은 이름을 넘겨도 스키마에 없으니 무시된다.
CORE_INJECTED_PARAMS: frozenset[str] = frozenset({"ctx", DRY_RUN_PARAM})


class SchemaBuildError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class ArgSpec:
    name: str
    py_type: type
    required: bool
    default: Any = None
    is_ref_type: bool = False
    # TableRef 인자가 Annotated로 선언한 필요한 것. 선언이 없으면 None.
    table_needs: TableNeeds | None = None


@dataclass(slots=True, kw_only=True)
class ArgSchema:
    args: list[ArgSpec] = field(default_factory=list)


def _unwrap_optional(py_type: Any) -> tuple[Any, bool]:
    if (
        isinstance(py_type, types.UnionType)
        or getattr(py_type, "__origin__", None) is not None
    ):
        args = getattr(py_type, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            return non_none[0], True
    return py_type, False


def is_list_type(py_type: Any) -> bool:
    """`list[기본 타입]` 또는 `list[TypedDict]`. 모델이 번호 목록이나 조건 목록을 넘기는 데 쓴다."""
    if get_origin(py_type) is not list:
        return False
    (item,) = get_args(py_type) or (None,)
    return item in _PRIMITIVES or is_typeddict(item)


def _strip_annotated(py_type: Any) -> tuple[Any, list[Any]]:
    """Annotated[X, ...]에서 X와 메타데이터를 꺼낸다. Annotated가 아니면 그대로."""
    if get_origin(py_type) is Annotated:
        base, *extras = get_args(py_type)
        return base, list(extras)
    return py_type, []


def build_arg_schema(fn: Callable[..., Any]) -> ArgSchema:
    """ctx(와 실행기가 넣는 dry_run)를 제외한 함수 인자에서 JSON Schema에 준하는 ArgSchema를 만든다."""
    hints = get_type_hints(fn, include_extras=True)
    signature = inspect.signature(fn)
    args: list[ArgSpec] = []

    for param_name, param in signature.parameters.items():
        if param_name in CORE_INJECTED_PARAMS:
            continue

        py_type = hints.get(param_name, Any)
        py_type, extras = _strip_annotated(py_type)
        py_type, is_optional = _unwrap_optional(py_type)
        py_type, more = _strip_annotated(py_type)
        extras += more
        table_needs = next((e for e in extras if isinstance(e, TableNeeds)), None)
        if table_needs is not None and py_type is not TableRef:
            raise SchemaBuildError(f"'{fn.__name__}'의 인자 '{param_name}'는 TableRef가 아닌데 TableNeeds를 선언했다")

        is_ref_type = py_type in STANDARD_REF_TYPES
        is_enum = isinstance(py_type, type) and issubclass(py_type, enum.Enum)
        is_primitive = py_type in _PRIMITIVES
        is_list = is_list_type(py_type)

        if not (is_ref_type or is_enum or is_primitive or is_list):
            raise SchemaBuildError(
                f"'{fn.__name__}'의 인자 '{param_name}'({py_type})는 지원하지 않는 타입이다. "
                "표준 인자 타입, Enum, str/int/float/bool, 그 목록(list[int] 등)과 TypedDict 목록만 쓸 수 있다"
            )

        has_default = param.default is not inspect.Parameter.empty
        args.append(
            ArgSpec(
                name=param_name,
                py_type=py_type,
                required=not (has_default or is_optional),
                default=None if param.default is inspect.Parameter.empty else param.default,
                is_ref_type=is_ref_type,
                table_needs=table_needs,
            )
        )

    return ArgSchema(args=args)
