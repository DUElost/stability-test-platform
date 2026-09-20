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

#2879 起，守卫对「不可判定」是 fail-closed 的：解析不了的文件、`getattr` 取用判据
函数、`**kwargs` 展开、非字面量 `resource_type` 一律按违规处理，而不是静默跳过——
否则守卫输出只说明「没看见」，不说明「看过且干净」。守据（改名导入）改为可识别；
已识别的调用点数量另设下限断言（`_CALL_SITES_BASELINE`），扫描面缩小同样变红。
"""

import ast
from pathlib import Path
from typing import Optional

from backend.core.audit import AUDIT_RESOURCE_TYPE_ALIASES, AUDIT_RESOURCE_TYPES

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {"tests", "alembic"}
_RECORD_FUNCS = {"record_audit", "record_audit_async"}

#: #2879：已识别调用点下限。扫描面缩小（改名导入、包装函数、动态取用……）必须让守卫
#: 自己变红，而不是悄悄少看几处。仓内确有增删时同步本常量（增不设上限）。
_CALL_SITES_BASELINE = 117

#: 通用动词：同一 action 在不同实体上出现是正常形态（create host / create script ...）。
_GENERIC_ACTIONS = frozenset({
    "create", "update", "delete", "deactivate", "export", "import",
})


def _callee_name(func: ast.expr) -> Optional[str]:
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _imported_record_aliases(tree: ast.AST) -> dict:
    """本模块 import 的 ``record_audit*`` 名 → 原名（#2879：``as`` 改名不再隐形）。"""
    aliases = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not (node.module or "").endswith("core.audit"):
            continue
        for alias in node.names:
            if alias.name in _RECORD_FUNCS:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _dynamic_record_refs(tree: ast.AST) -> list:
    """``getattr(mod, "record_audit")`` 形态：调用点对 AST 不可见（#2879）。"""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "getattr"):
            continue
        if len(node.args) < 2:
            continue
        attr = node.args[1]
        if isinstance(attr, ast.Constant) and attr.value in _RECORD_FUNCS:
            out.append((node.lineno, ast.unparse(node)[:80]))
    return out


def _scan_source(source: str) -> tuple:
    """扫描单份源码 → ``(calls, dynamic_refs)``；守卫与自检共用本实现。

    ``calls``：``[(行号, kwargs)]``，``kwargs`` 保留 ``**x`` 展开项（键 ``None``）。
    ``SyntaxError`` 由调用方按「不可判定」处理，不在此吞掉。
    """
    tree = ast.parse(source)
    names = _RECORD_FUNCS | set(_imported_record_aliases(tree))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _callee_name(node.func) not in names:
            continue
        calls.append((node.lineno, {kw.arg: kw.value for kw in node.keywords}))
    return calls, _dynamic_record_refs(tree)


def _scan_backend_audit_calls() -> tuple:
    """全量扫描 ``backend/`` 生产面 → ``(calls, report)``。

    ``calls``：``[(相对路径, 行号, kwargs)]``；``report`` 记解析失败与动态取用，
    由 :func:`test_audit_call_scan_surface_is_assertable` 断言为空。
    """
    calls = []
    report: dict = {"files_parsed": 0, "unparsable": [], "dynamic_refs": []}
    for path in sorted(_BACKEND_ROOT.rglob("*.py")):
        rel = path.relative_to(_BACKEND_ROOT)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        rel_repo = path.relative_to(_BACKEND_ROOT.parent)
        try:
            file_calls, dynamic_refs = _scan_source(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # #2879：不静默跳过——扫描面缺一块必须可见
            report["unparsable"].append((str(rel), getattr(exc, "lineno", None)))
            continue
        report["files_parsed"] += 1
        report["dynamic_refs"].extend((str(rel_repo), ln, expr) for ln, expr in dynamic_refs)
        calls.extend((rel_repo, ln, kwargs) for ln, kwargs in file_calls)
    return calls, report


def _literal_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _resource_type_offenders(calls: list) -> list:
    """``[(path, lineno, kwargs)]`` → 违规清单（#2879：不可判定即违规）。"""
    offenders = []
    for path, lineno, kwargs in calls:
        node = kwargs.get("resource_type")
        if node is None:
            if None in kwargs:
                offenders.append((path, lineno, "**kwargs", "kwargs 展开 ⇒ resource_type 不可判定"))
            continue
        value = _literal_str(node)
        if value is None:
            offenders.append((
                path, lineno, ast.unparse(node),
                "非字面量 resource_type ⇒ 守卫不可判定，按违规处理（#2879）",
            ))
            continue
        if value in AUDIT_RESOURCE_TYPE_ALIASES:
            offenders.append((path, lineno, value, "历史别名禁止写入（只读侧归并）"))
        elif value not in AUDIT_RESOURCE_TYPES:
            offenders.append((path, lineno, value, "未登记：先登记到 core/audit.py 词表"))
    return offenders


def test_audit_call_scan_surface_is_assertable():
    """#2879：扫描面自身先钉住——解析失败 / 动态取用 / 数量缩水都不得读成通过。"""
    calls, report = _scan_backend_audit_calls()

    assert report["unparsable"] == [], (
        "backend 生产面存在无法解析的文件，守卫对其免检（#2879）：\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in report["unparsable"])
    )
    assert report["dynamic_refs"] == [], (
        "getattr 取用 record_audit*：调用点对 AST 不可见，按违规处理（#2879）：\n"
        + "\n".join(f"  {p}:{ln} {e}" for p, ln, e in report["dynamic_refs"])
    )
    assert len(calls) >= _CALL_SITES_BASELINE, (
        f"已识别 record_audit* 调用点 {len(calls)} 处 < 下限 {_CALL_SITES_BASELINE}"
        "（#2879）：先确认不是守卫失明（改名导入 / 包装 / 动态取用），"
        "确属调用点真被删除再下调 _CALL_SITES_BASELINE。"
    )


def test_write_side_resource_types_are_registered_canonical_values():
    """写侧字面量必须 ∈ 规范词表；历史别名出现在写侧即为分裂复发。"""
    calls, _ = _scan_backend_audit_calls()
    offenders = _resource_type_offenders(calls)
    assert not offenders, (
        "写侧 resource_type 违规（#2778）：\n"
        + "\n".join(f"  {p}:{ln} {v!r}——{why}" for p, ln, v, why in offenders)
    )


def test_non_generic_actions_use_a_single_resource_type():
    """非通用动作的 (action → resource_type) 必须唯一——本单缺陷的形态本身。"""
    calls, _ = _scan_backend_audit_calls()
    seen: dict = {}
    for path, lineno, kwargs in calls:
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


# ── #2879 自检：守卫的牙齿本身要可证伪 ────────────────────────────────────
# 每条都是「同一逻辑换一种写法」的绕过形态；缺了任一命中，守卫的绿灯就不足以
# 作为合规证据。新增识别面时在这里补对应反例。
_CANONICAL_SNIPPET = (
    "from backend.core.audit import record_audit\n"
    "def f(db):\n"
    "    record_audit(db, action='x', resource_type='job_instance')\n"
)
_ALIASED_SNIPPET = (
    "from backend.core.audit import record_audit as ra\n"
    "def f(db):\n"
    "    ra(db, action='x', resource_type='job_instance')\n"
)
_NONLITERAL_SNIPPET = (
    "from backend.core.audit import record_audit\n"
    "def f(db, rt):\n"
    "    record_audit(db, action='x', resource_type=rt)\n"
)
_GETATTR_SNIPPET = (
    "from backend.core import audit\n"
    "def f(db):\n"
    "    getattr(audit, 'record_audit')(db, action='x', resource_type='job_instance')\n"
)
_KWARGS_SNIPPET = (
    "from backend.core.audit import record_audit\n"
    "def f(db, extra):\n"
    "    record_audit(db, action='x', **extra)\n"
)


def test_guard_self_test_bypass_forms():
    """#2879 自检：三类绕过形态必须被识别（否则守卫绿 = 没看见）。"""
    canonical_calls, _ = _scan_source(_CANONICAL_SNIPPET)
    assert len(canonical_calls) == 1
    assert _resource_type_offenders([("s", ln, kw) for ln, kw in canonical_calls]) == []

    # 改名导入：调用点仍必须被识别
    alias_calls, _ = _scan_source(_ALIASED_SNIPPET)
    assert len(alias_calls) == 1, "as 别名导入的调用点没被识别"
    assert _resource_type_offenders([("s", ln, kw) for ln, kw in alias_calls]) == []

    # 非字面量：按违规计，不再静默跳过
    nonliteral_calls, _ = _scan_source(_NONLITERAL_SNIPPET)
    assert len(nonliteral_calls) == 1
    nonliteral_offenders = _resource_type_offenders(
        [("s", ln, kw) for ln, kw in nonliteral_calls]
    )
    assert nonliteral_offenders and "非字面量" in nonliteral_offenders[0][3]

    # getattr 取用：调用点解析不到，必须由 dynamic_refs 兜住
    getattr_calls, getattr_dynamic = _scan_source(_GETATTR_SNIPPET)
    assert getattr_calls == []
    assert getattr_dynamic and getattr_dynamic[0][0] == 3

    # **kwargs 展开：按违规计
    kwargs_calls, _ = _scan_source(_KWARGS_SNIPPET)
    assert len(kwargs_calls) == 1
    kwargs_offenders = _resource_type_offenders([("s", ln, kw) for ln, kw in kwargs_calls])
    assert kwargs_offenders and "kwargs 展开" in kwargs_offenders[0][3]
