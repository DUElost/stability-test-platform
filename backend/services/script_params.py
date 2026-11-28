"""脚本参数的有效值语义（#977 / R05-F14 / ADR-0067 D2）。

与前端 ``draftParameterMerge.mergeDraftParameters`` 的取值优先级严格同源（静态 4 层）：

    step.params (第 4 层)
      > project_preset (第 3 层，仅非 null 键且仅作用于 log_prepare)
      > default_params (第 2 层)
      > schema.default (第 1 层)

第 5 层（``dispatch_injection``：WiFi/网络供给池与托管套件填空）在物化期由
``plan_dispatcher_core`` 执行。派发、快照重放、投影与保存期校验共用本模块，
避免「展示默认」与「执行默认」漂移。
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Dict, List, Mapping, Optional

# scripts.py 的 _validate_param_schema 保证 type 仅取这些受控类型
_VALID_TYPES = ("string", "integer", "boolean", "number", "array")


def merge_effective_params(
    param_schema: Optional[Mapping[str, Any]],
    default_params: Optional[Mapping[str, Any]],
    step_params: Optional[Mapping[str, Any]] = None,
    *,
    project_preset: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """合并生效参数：``schema.default`` < ``default_params`` < ``project_preset`` < ``step.params``。

    - 第 3 层 ``project_preset`` 仅注入非 ``None`` 键（ADR-0067 D2.3），避免用
      ``null`` 抹除第 1–2 层脚本默认；
    - 第 4 层 ``step.params`` 保留显式 ``None`` 键（ADR-0067 D2.2），用于覆盖
      可空项目预设（如 ``aee_mode: null``）回退设备当前值。
    """
    merged: Dict[str, Any] = {
        key: deepcopy(field["default"])
        for key, field in (param_schema or {}).items()
        if isinstance(field, dict) and "default" in field
    }
    if default_params:
        merged.update(deepcopy(dict(default_params)))
    if project_preset:
        for key, value in project_preset.items():
            if value is not None:
                merged[key] = deepcopy(value)
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
    if expected == "array":
        return isinstance(value, list)
    return True


def _validate_array_field(
    key: str, value: list[Any], field: Mapping[str, Any],
) -> List[str]:
    problems: List[str] = []
    min_items = field.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        problems.append(
            f"params[{key!r}]: must contain at least {min_items} item(s), got {len(value)}"
        )
    item_schema = field.get("items")
    if isinstance(item_schema, dict):
        item_type = item_schema.get("type")
        item_enum = item_schema.get("enum")
        for idx, item in enumerate(value):
            if item_type in _VALID_TYPES and not _type_matches(item, item_type):
                problems.append(
                    f"params[{key!r}][{idx}]: expected {item_type}, got {type(item).__name__}"
                )
                continue
            if isinstance(item_enum, list) and item not in item_enum:
                problems.append(
                    f"params[{key!r}][{idx}]: must be one of {item_enum}, got {item!r}"
                )
    required_items = field.get("required_items")
    if isinstance(required_items, list):
        missing = [req for req in required_items if req not in value]
        if missing:
            problems.append(
                f"params[{key!r}]: must include required item(s) {missing}"
            )
    return problems


def validate_params_against_schema(
    params: Optional[Mapping[str, Any]],
    param_schema: Optional[Mapping[str, Any]],
) -> List[str]:
    """校验已提供参数的类型、可空性、枚举、数值边界、正则与数组约束，返回问题列表（空 = 通过）。

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
        if value is None and field.get("nullable") is True:
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
            continue
        if expected in ("integer", "number"):
            minimum = field.get("minimum")
            maximum = field.get("maximum")
            if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
                problems.append(
                    f"params[{key!r}]: must be >= {minimum}, got {value!r}"
                )
            if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
                problems.append(
                    f"params[{key!r}]: must be <= {maximum}, got {value!r}"
                )
        elif expected == "string":
            pattern = field.get("pattern")
            if isinstance(pattern, str) and not re.fullmatch(pattern, value):
                problems.append(
                    f"params[{key!r}]: must match pattern {pattern!r}, got {value!r}"
                )
        elif expected == "array":
            problems.extend(_validate_array_field(key, value, field))
    return problems
