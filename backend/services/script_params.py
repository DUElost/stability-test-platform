"""脚本参数的有效值语义（#977 / R05-F14）。

与前端 ``PlanStepInspector.ParamFormCard`` 的取值优先级严格同源：

    step.params  >  default_params  >  schema.default

派发（``plan_dispatcher_core``）、快照重放与保存期校验（``plans.py``）共用本
模块，避免「展示默认」与「执行默认」再次漂移。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional

# scripts.py 的 _validate_param_schema 保证 type 只可能是这四种
_VALID_TYPES = ("string", "integer", "boolean", "number")


def merge_effective_params(
    param_schema: Optional[Mapping[str, Any]],
    default_params: Optional[Mapping[str, Any]],
    step_params: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """合并生效参数：``schema.default`` 为底，``default_params`` 覆盖，
    ``step.params`` 最高（与前端展示同优先级）。"""
    merged: Dict[str, Any] = {
        key: field["default"]
        for key, field in (param_schema or {}).items()
        if isinstance(field, dict) and "default" in field
    }
    if default_params:
        merged.update(deepcopy(dict(default_params)))
    if step_params:
        merged.update(deepcopy(dict(step_params)))
    return merged


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def validate_params_against_schema(
    params: Optional[Mapping[str, Any]],
    param_schema: Optional[Mapping[str, Any]],
) -> List[str]:
    """校验已提供参数的类型/枚举，返回问题列表（空 = 通过）。

    只校验 schema 声明的键；未声明键属已接受的自由键模式。``required`` 不在此
    强制——wifi/suite 注入在派发期补键（ADR-0020 的两处豁免），保存期强制会
    误伤这些路径。
    """
    problems: List[str] = []
    if not params or not param_schema:
        return problems
    for key, value in params.items():
        field = param_schema.get(key)
        if not isinstance(field, dict):
            continue
        expected = field.get("type")
        if expected in _VALID_TYPES and not _type_matches(value, expected):
            problems.append(
                f"params[{key!r}]: expected {expected}, got {type(value).__name__}"
            )
            continue
        enum_values = field.get("enum")
        if isinstance(enum_values, list) and value not in enum_values:
            problems.append(
                f"params[{key!r}]: must be one of {enum_values}, got {value!r}"
            )
    return problems
