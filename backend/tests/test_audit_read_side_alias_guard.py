# -*- coding: utf-8 -*-
"""#2872：审计资源类型**读侧**别名归并的机械守卫。

背景：审计行 append-only（ADR-0015），历史字面量只在读取侧归并（#2778）——
`backend/core/audit.py::AUDIT_RESOURCE_TYPE_ALIASES` 是唯一词表，
`expand_resource_type_filter()` 是唯一展开入口。任何按规范值**精确匹配**的读侧
查询都会静默漏掉历史别名行（`job` 之于 `job_instance`、`script_catalog` 之于
`script`）；`plan_run_event_feed` 与 `ai_assistant` 都曾如此。

本文件把这条不变式钉成离线断言（纯 AST，不连库）：`AuditLog.resource_type` 出现在
**比较/筛选**位置时必须处于 `expand_resource_type_filter(...)` 的作用域内；把列
本身交给聚合/工具函数（如 facets 列原始值）不算筛选，不在此列。
"""

import ast
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {"tests", "alembic"}
_EXPAND_FUNC = "expand_resource_type_filter"
_MODEL_NAME = "AuditLog"
_COLUMN_NAME = "resource_type"
#: SQLAlchemy 列上的筛选方法：命中即视为「按值筛选」
_FILTER_METHODS = frozenset({"in_", "notin_", "any_", "all_", "like", "ilike"})


def _is_audit_resource_type_column(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == _COLUMN_NAME
        and isinstance(node.value, ast.Name)
        and node.value.id == _MODEL_NAME
    )


def _mentions_expand(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if getattr(func, "id", None) == _EXPAND_FUNC:
            return True
        if getattr(func, "attr", None) == _EXPAND_FUNC:
            return True
    return False


def _parents(tree: ast.AST) -> dict:
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def raw_alias_filter_violations(tree: ast.AST) -> list:
    """``[(行号, 源码片段)]``——按值筛选 `AuditLog.resource_type` 却未经 expand。"""
    violations = []
    parents = _parents(tree)
    for node in ast.walk(tree):
        if not _is_audit_resource_type_column(node):
            continue
        parent = parents.get(node)
        # 方法链：`AuditLog.resource_type.in_(...)` 的父节点是 `.in_` 那个 Attribute，
        # 再往上一层才是 Call——沿「父的 value 是本节点」爬链，直到非属性节点。
        while isinstance(parent, ast.Attribute) and parent.value is node:
            node, parent = parent, parents.get(parent)
        scope = None
        if isinstance(parent, ast.Compare):
            scope = parent
        elif (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Attribute)
            and parent.func.attr in _FILTER_METHODS
        ):
            scope = parent
        if scope is None:
            continue  # 非筛选用法：如把列交给 facet 聚合（列出原始值）
        if not _mentions_expand(scope):
            violations.append((node.lineno, ast.unparse(scope)[:140]))
    return violations


def _scan_backend() -> list:
    violations = []
    for path in sorted(_BACKEND_ROOT.rglob("*.py")):
        rel = path.relative_to(_BACKEND_ROOT)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel_repo = path.relative_to(_BACKEND_ROOT.parent)
        violations.extend(
            (str(rel_repo), lineno, expr)
            for lineno, expr in raw_alias_filter_violations(tree)
        )
    return violations


def test_read_side_resource_type_filters_expand_aliases():
    """#2872：读侧不得绕过词表——历史别名行会静默消失。"""
    violations = _scan_backend()
    assert not violations, (
        "读侧按规范值精确筛审计资源类型（#2872）：历史别名行会静默漏掉。\n"
        "改用 AuditLog.resource_type.in_(expand_resource_type_filter(<值>))：\n"
        + "\n".join(f"  {p}:{ln} {expr}" for p, ln, expr in violations)
    )


# ── 自检：守卫的牙齿本身要可证伪 ──────────────────────────────────────────
_RAW_EQ_SNIPPET = (
    "from backend.models.audit import AuditLog\n"
    "def f(db):\n"
    "    return db.query(AuditLog).filter(AuditLog.resource_type == 'job_instance')\n"
)
_RAW_IN_SNIPPET = (
    "from backend.models.audit import AuditLog\n"
    "def f(db, ids):\n"
    "    return db.query(AuditLog).filter(AuditLog.resource_type.in_(ids))\n"
)
_EXPANDED_SNIPPET = (
    "from backend.core.audit import expand_resource_type_filter\n"
    "from backend.models.audit import AuditLog\n"
    "def f(db):\n"
    "    return db.query(AuditLog).filter(\n"
    "        AuditLog.resource_type.in_(expand_resource_type_filter('job_instance')))\n"
)
_FACET_SNIPPET = (
    "from backend.models.audit import AuditLog\n"
    "def f(db):\n"
    "    return db.query(AuditLog.resource_type).distinct().all()\n"
)


def test_guard_self_test_detects_raw_filters():
    """自检：裸比较/裸 in_ 必红；走 expand 或「列交给聚合」不算违规。"""
    for snippet in (_RAW_EQ_SNIPPET, _RAW_IN_SNIPPET):
        assert raw_alias_filter_violations(ast.parse(snippet)), "裸筛选没被识别"
    assert raw_alias_filter_violations(ast.parse(_EXPANDED_SNIPPET)) == []
    assert raw_alias_filter_violations(ast.parse(_FACET_SNIPPET)) == []
