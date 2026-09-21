# -*- coding: utf-8 -*-
"""#3017：审计 `action` 分层保留的登记制守卫（ADR-0049 D2）。

裁剪把 security/session 做成显式集、business 用 NOT IN 兜底——对新 action 封闭
是有意的；但安全类漏登会静默落到 90d（注释已自陈）。本守卫钉：

写侧 `record_audit*` 的可解析 `action=` 必须 ∈
``SESSION ∪ SECURITY ∪ BUSINESS_ACTIONS_ALLOWLIST``。
新 action 三选一：进 security / session / 点名进 business allowlist。

动态构造（f-string / 形参）无法静态展开时，调用点必须出现在
``_DYNAMIC_ACTION_SITES``（相对 backend/ 的路径 + 行号），且其**闭合后缀集**
已全部列入 allowlist——禁止「动态且未登记」。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from backend.scheduler.audit_log_cleanup import (
    BUSINESS_ACTIONS_ALLOWLIST,
    SECURITY_ACTIONS,
    SESSION_ACTIONS,
    SUMMARY_ACTION,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {"tests", "alembic"}
_RECORD_FUNCS = {"record_audit", "record_audit_async"}
_CALL_SITES_BASELINE = 117

_REGISTERED: frozenset[str] = (
    SESSION_ACTIONS | SECURITY_ACTIONS | BUSINESS_ACTIONS_ALLOWLIST | {SUMMARY_ACTION}
)

#: 无法静态展开的 action= 调用点（relpath from backend/, lineno）。
#: 新增动态构造必须在此登记，并把闭合 action 全集写入 BUSINESS_ACTIONS_ALLOWLIST。
_DYNAMIC_ACTION_SITES: frozenset[tuple[str, int]] = frozenset({
    # f"ai_assistant_action_{verb}" — verb ∈ {approve, reject}
    ("api/routes/ai_assistant.py", 624),
    # f"ai_assistant_action_{event}" — event ∈ orchestrator 调用集
    ("services/ai_assistant/orchestrator.py", 321),
    # audit_action 形参（默认 abort_plan_run；调用方另传 abort_jobs… / ai_assistant…）
    ("services/plan_run_abort.py", 455),
    ("services/plan_run_abort.py", 778),
})


def _callee_name(func: ast.expr) -> Optional[str]:
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _imported_record_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not (node.module or "").endswith("core.audit"):
            continue
        for alias in node.names:
            if alias.name in _RECORD_FUNCS:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _dynamic_record_refs(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Call):
            continue
        inner = node.func
        if not isinstance(inner.func, ast.Name) or inner.func.id != "getattr":
            continue
        if len(inner.args) >= 2 and isinstance(inner.args[1], ast.Constant):
            if inner.args[1].value in _RECORD_FUNCS:
                out.append((node.lineno, f"getattr(..., {inner.args[1].value!r})"))
    return out


def _extract_action_literals(node: ast.AST | None) -> list[str] | None:
    """可静态展开 → 字面量列表；SUMMARY_ACTION 名 → 常量值；否则 None。"""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and node.id == "SUMMARY_ACTION":
        return [SUMMARY_ACTION]
    if isinstance(node, ast.IfExp):
        left = _extract_action_literals(node.body)
        right = _extract_action_literals(node.orelse)
        if left is not None and right is not None:
            return left + right
    return None


def _scan_backend() -> tuple[list[tuple[Path, int, list[str]]], dict]:
    resolved: list[tuple[Path, int, list[str]]] = []
    report: dict = {
        "unparsable": [],
        "dynamic_refs": [],
        "kwargs_splat": [],
        "unresolved": [],
        "unexpected_dynamic": [],
    }
    for path in sorted(_BACKEND_ROOT.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError) as exc:
            report["unparsable"].append((path, getattr(exc, "lineno", 0)))
            continue
        aliases = _imported_record_aliases(tree)
        for lineno, msg in _dynamic_record_refs(tree):
            report["dynamic_refs"].append((path, lineno, msg))
        rel = str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee_name(node.func)
            if name not in aliases and name not in _RECORD_FUNCS:
                continue
            if any(kw.arg is None for kw in node.keywords):
                report["kwargs_splat"].append((path, node.lineno))
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            actions = _extract_action_literals(kwargs.get("action"))
            if actions is None:
                key = (rel, node.lineno)
                if key in _DYNAMIC_ACTION_SITES:
                    report["unresolved"].append((path, node.lineno))
                else:
                    report["unexpected_dynamic"].append((path, node.lineno))
                continue
            resolved.append((path, node.lineno, actions))
    return resolved, report


def test_audit_action_scan_surface_is_assertable():
    resolved, report = _scan_backend()
    assert report["unparsable"] == [], (
        "backend 生产面无法解析（#3017）：\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in report["unparsable"])
    )
    assert report["dynamic_refs"] == [], (
        "getattr 取用 record_audit*（#3017）：\n"
        + "\n".join(f"  {p}:{ln} {e}" for p, ln, e in report["dynamic_refs"])
    )
    assert report["kwargs_splat"] == [], (
        "record_audit* 使用 **kwargs（#3017）：\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in report["kwargs_splat"])
    )
    assert report["unexpected_dynamic"] == [], (
        "动态 action= 未登记到 _DYNAMIC_ACTION_SITES（#3017）：\n"
        "  先把闭合 action 全集写入 BUSINESS_ACTIONS_ALLOWLIST，再登记本调用点。\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in report["unexpected_dynamic"])
    )
    # 登记表不得留僵尸行
    seen_dyn = {
        (str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/"), ln)
        for p, ln in report["unresolved"]
    }
    stale = sorted(_DYNAMIC_ACTION_SITES - seen_dyn)
    assert not stale, (
        "_DYNAMIC_ACTION_SITES 有僵尸行（调用点已改字面量或行号漂移）（#3017）：\n"
        + "\n".join(f"  {rel}:{ln}" for rel, ln in stale)
    )
    n_sites = len(resolved) + len(report["unresolved"])
    assert n_sites >= _CALL_SITES_BASELINE, (
        f"已识别调用点 {n_sites} < 下限 {_CALL_SITES_BASELINE}（#3017）"
    )


def test_write_side_actions_are_retention_registered():
    resolved, _ = _scan_backend()
    offenders: list[str] = []
    for path, lineno, actions in resolved:
        for action in actions:
            if action not in _REGISTERED:
                offenders.append(f"{path}:{lineno} action={action!r}")
    assert not offenders, (
        "审计 action 未登记分层（#3017 / ADR-0049 D2）。三选一：\n"
        "  1) 安全事件 → SECURITY_ACTIONS\n"
        "  2) 会话心跳 → SESSION_ACTIONS\n"
        "  3) 故意留 business 90d → BUSINESS_ACTIONS_ALLOWLIST\n"
        "未登记项：\n  " + "\n  ".join(offenders)
    )


def test_allowlist_does_not_overlap_explicit_layers():
    overlap = BUSINESS_ACTIONS_ALLOWLIST & (SESSION_ACTIONS | SECURITY_ACTIONS)
    assert not overlap, f"BUSINESS_ACTIONS_ALLOWLIST 与显式层重叠：{sorted(overlap)}"


def test_guard_rejects_unregistered_literal_and_ifexp():
    """变异：未登记字面量与 IfExp 双分支都能被抓住。"""
    src = (
        "from backend.core.audit import record_audit\n"
        "def f(db, flag):\n"
        "    record_audit(db, action='brand_new_security_evt', resource_type='user')\n"
        "    record_audit(db, action='scan' if flag else 'also_unregistered', "
        "resource_type='script')\n"
    )
    tree = ast.parse(src)
    aliases = _imported_record_aliases(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node.func)
        if name not in aliases:
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        actions = _extract_action_literals(kwargs.get("action"))
        assert actions is not None
        found.extend(actions)
    assert "brand_new_security_evt" in found
    assert "also_unregistered" in found
    assert "brand_new_security_evt" not in _REGISTERED
    assert "also_unregistered" not in _REGISTERED
