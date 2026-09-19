# -*- coding: utf-8 -*-
"""#2778：审计 `resource_type` 词表的机械守卫。

背景：`job_terminalized` 曾按收尾路径分裂成 `job` / `job_instance` 两个字面量
（Agent 完成侧一个、回收器侧一个），直到它们并排出现在 `/audit` 下拉里才被发现
——写入侧此前没有"取词必须来自登记词表"的守门员。本文件把两个不变式钉成**离线**
断言（纯 AST，不连库）：

1. **写侧只允许规范值**：`record_audit*` 的 `resource_type` 字面量必须 ∈
   `AUDIT_RESOURCE_TYPES`；历史别名出现在写侧即为分裂复发；
2. **非通用动作的资源类型唯一**：`create` / `update` / `delete` 这类通用动词天然
   跨实体复用，其余动作同一 action 出现两个资源类型即是新的分裂。

局限（有意为之）：只判定字面量；变量/拼接等动态取值不在此静态守卫射程内。
"""

import ast
from pathlib import Path

from backend.core.audit import AUDIT_RESOURCE_TYPE_ALIASES, AUDIT_RESOURCE_TYPES

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {"tests", "alembic"}
_RECORD_FUNCS = {"record_audit", "record_audit_async"}

#: 通用动词：同一 action 在不同实体上出现是正常形态（create host / create script ...）。
_GENERIC_ACTIONS = frozenset({
    "create", "update", "delete", "deactivate", "export", "import",
})


def _iter_audit_calls():
    """产出 (相对路径, 行号, kwargs) —— 全部 `record_audit*` 调用点。"""
    for path in _BACKEND_ROOT.rglob("*.py"):
        rel = path.relative_to(_BACKEND_ROOT)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 不因无法解析的旁支文件阻塞守卫
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in _RECORD_FUNCS:
                continue
            yield (
                path.relative_to(_BACKEND_ROOT.parent),
                node.lineno,
                {kw.arg: kw.value for kw in node.keywords if kw.arg},
            )


def _literal_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def test_write_side_resource_types_are_registered_canonical_values():
    """写侧字面量必须 ∈ 规范词表；历史别名出现在写侧即为分裂复发。"""
    offenders = []
    for path, lineno, kwargs in _iter_audit_calls():
        node = kwargs.get("resource_type")
        if node is None:
            continue
        value = _literal_str(node)
        if value is None:
            continue
        if value in AUDIT_RESOURCE_TYPE_ALIASES:
            offenders.append((path, lineno, value, "历史别名禁止写入（只读侧归并）"))
        elif value not in AUDIT_RESOURCE_TYPES:
            offenders.append((path, lineno, value, "未登记：先登记到 core/audit.py 词表"))
    assert not offenders, (
        "写侧 resource_type 违规（#2778）：\n"
        + "\n".join(f"  {p}:{ln} {v!r}——{why}" for p, ln, v, why in offenders)
    )


def test_non_generic_actions_use_a_single_resource_type():
    """非通用动作的 (action → resource_type) 必须唯一——本单缺陷的形态本身。"""
    seen: dict = {}
    for path, lineno, kwargs in _iter_audit_calls():
        action = _literal_str(kwargs.get("action"))
        resource_type = _literal_str(kwargs.get("resource_type"))
        if action is None or resource_type is None or action in _GENERIC_ACTIONS:
            continue
        seen.setdefault(action, {}).setdefault(resource_type, []).append(f"{path}:{lineno}")

    split = {a: rts for a, rts in seen.items() if len(rts) > 1}
    assert not split, (
        "同一 action 分裂出多个 resource_type（#2778）：\n"
        + "\n".join(
            f"  {a}: " + ", ".join(f"{rt} @ {locs}" for rt, locs in rts.items())
            for a, rts in split.items()
        )
    )


def test_alias_table_is_consistent():
    """别名表自身一致性：别名不得同时是规范值，目标必须是规范值。"""
    assert not (set(AUDIT_RESOURCE_TYPE_ALIASES) & AUDIT_RESOURCE_TYPES), (
        "别名与规范值集合重叠——归并会把规范值改写成别名"
    )
    assert set(AUDIT_RESOURCE_TYPE_ALIASES.values()) <= AUDIT_RESOURCE_TYPES, (
        "别名目标未登记为规范值"
    )
