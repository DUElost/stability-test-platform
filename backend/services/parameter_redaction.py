"""结构化参数掩码。

输出只保留「已设置 / 未设置」：敏感位置的值固定为 ``None``，不暴露字符数、
部分字符，也不使用随长度变化的星号。函数不修改传入对象。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence

from backend.core.redaction import sensitive_query_keys

_SCHEMA_SECRET_SLOTS = ("default", "enum", "const", "examples")

# 注入路径与登记表路径之外，键名精确命中（大小写不敏感）即掩码。
def sensitive_name(key: Any) -> bool:
    return isinstance(key, str) and key.lower() in sensitive_query_keys()


def path_is_sensitive(path: Sequence[Any], extra: Iterable[tuple] = ()) -> bool:
    tup = tuple(path)
    if tup in set(extra):
        return True
    return bool(tup) and sensitive_name(tup[-1])


def redact_params(value: Any, extra_paths: Iterable[tuple] = ()) -> Any:
    """深拷贝后按路径与敏感键名把命中位置替换为 ``None``。"""
    paths = {tuple(p) for p in extra_paths}
    return _redact_node(deepcopy(value), (), paths)


def _redact_node(node: Any, path: tuple, paths: set[tuple]) -> Any:
    if path and (path in paths or sensitive_name(path[-1])):
        return None
    if isinstance(node, dict):
        return {
            key: _redact_node(child, path + (key,), paths)
            for key, child in node.items()
        }
    if isinstance(node, list):
        return [
            _redact_node(child, path + (index,), paths)
            for index, child in enumerate(node)
        ]
    return node


def redact_schema(schema: Any, extra_paths: Iterable[tuple] = ()) -> Any:
    """按参数路径清除 schema 的 default/enum/const/examples。"""
    if not isinstance(schema, dict):
        return deepcopy(schema)
    paths = {tuple(p) for p in extra_paths}
    return {
        key: _redact_field(field, (key,), paths)
        for key, field in schema.items()
    }


def _redact_field(field: Any, path: tuple, paths: set[tuple]) -> Any:
    if not isinstance(field, dict):
        return None if path_is_sensitive(path, paths) else deepcopy(field)
    sensitive = path_is_sensitive(path, paths)
    relative = _relative_paths(paths, path)
    cleaned: dict[str, Any] = {}
    for key, value in field.items():
        if key in _SCHEMA_SECRET_SLOTS and sensitive:
            cleaned[key] = None
        elif key in _SCHEMA_SECRET_SLOTS:
            # 当前节点本身不敏感时，default/enum/const/examples 仍可能嵌着子路径明文。
            cleaned[key] = _redact_schema_slot(
                value,
                relative,
                unwrap_candidates=key in ("enum", "examples"),
            )
        elif key == "properties" and isinstance(value, dict):
            cleaned[key] = {
                name: _redact_field(child, path + (name,), paths)
                for name, child in value.items()
            }
        else:
            cleaned[key] = deepcopy(value)
    return cleaned


def _redact_schema_slot(
    value: Any,
    relative: set[tuple],
    *,
    unwrap_candidates: bool = False,
) -> Any:
    """按参数路径清除槽位值。

    ``enum`` / ``examples`` 最外层是同一字段的候选值，这一层下标不是参数路径。
    候选值内部以及 ``default`` / ``const`` 的列表下标属于参数路径
    （例如 ``("servers", 0, "psk")``），交给 ``redact_params`` 保留。
    """
    if unwrap_candidates and isinstance(value, list):
        return [
            _redact_schema_slot(item, relative, unwrap_candidates=False)
            for item in value
        ]
    return redact_params(value, relative)


def _relative_paths(paths: set[tuple], prefix: tuple) -> set[tuple]:
    rel: set[tuple] = set()
    for path in paths:
        if path[: len(prefix)] == prefix:
            rel.add(path[len(prefix) :])
    return rel


def builtin_sensitive_paths(script_name: str | None) -> set[tuple]:
    """内置注入路径。字面键 ``wifi.password`` 与嵌套 ``['wifi','password']`` 不相等。"""
    if script_name == "connect_wifi":
        return {("password",)}
    if script_name == "monkey_setup":
        return {("wifi", "password")}
    return set()


def union_sensitive_paths(
    script_name: str | None,
    registry_paths: Iterable[tuple],
) -> set[tuple]:
    """登记表敏感路径取该路径全部条目的并集，版本不匹配也不能取消掩码。"""
    paths = builtin_sensitive_paths(script_name)
    paths.update(tuple(path) for path in registry_paths)
    return paths


def is_set_value(value: Any) -> bool:
    """``None`` 与缺失为未设置；``False`` / ``0`` / ``""`` 算已设置。"""
    return value is not None


def masked_value(value: Any, path: Sequence[Any], extra_paths: Iterable[tuple]) -> Any:
    if path_is_sensitive(path, extra_paths) or _tree_has_sensitive(value, tuple(path), set(extra_paths)):
        if path_is_sensitive(path, extra_paths):
            return None
        return redact_params(value, _relative_paths(set(map(tuple, extra_paths)), tuple(path)))
    if isinstance(value, (dict, list)):
        return redact_params(value, _relative_paths(set(map(tuple, extra_paths)), tuple(path)))
    return deepcopy(value)


def _tree_has_sensitive(value: Any, path: tuple, paths: set[tuple]) -> bool:
    if path_is_sensitive(path, paths):
        return True
    if isinstance(value, dict):
        return any(
            _tree_has_sensitive(child, path + (key,), paths) for key, child in value.items()
        )
    if isinstance(value, list):
        return any(
            _tree_has_sensitive(child, path + (index,), paths)
            for index, child in enumerate(value)
        )
    return False


def contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, Mapping):
        return any(contains_text(k, needle) or contains_text(v, needle) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_text(item, needle) for item in value)
    return False
