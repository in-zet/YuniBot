# core/schema.py
from __future__ import annotations

import enum
import inspect
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from core.tool_types import STANDARD_REF_TYPES

_PRIMITIVES: frozenset[type] = frozenset({str, int, float, bool})


class SchemaBuildError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class ArgSpec:
    name: str
    py_type: type
    required: bool
    default: Any = None
    is_ref_type: bool = False


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


def build_arg_schema(fn: Callable[..., Any]) -> ArgSchema:
    """ctx를 제외한 함수 인자에서 JSON Schema에 준하는 ArgSchema를 만든다."""
    hints = get_type_hints(fn)
    signature = inspect.signature(fn)
    args: list[ArgSpec] = []

    for param_name, param in signature.parameters.items():
        if param_name == "ctx":
            continue

        py_type = hints.get(param_name, Any)
        py_type, is_optional = _unwrap_optional(py_type)

        is_ref_type = py_type in STANDARD_REF_TYPES
        is_enum = isinstance(py_type, type) and issubclass(py_type, enum.Enum)
        is_primitive = py_type in _PRIMITIVES

        if not (is_ref_type or is_enum or is_primitive):
            raise SchemaBuildError(
                f"'{fn.__name__}'의 인자 '{param_name}'({py_type})는 지원하지 않는 타입이다. "
                "표준 인자 타입, Enum, str/int/float/bool만 쓸 수 있다"
            )

        has_default = param.default is not inspect.Parameter.empty
        args.append(
            ArgSpec(
                name=param_name,
                py_type=py_type,
                required=not (has_default or is_optional),
                default=None if param.default is inspect.Parameter.empty else param.default,
                is_ref_type=is_ref_type,
            )
        )

    return ArgSchema(args=args)
