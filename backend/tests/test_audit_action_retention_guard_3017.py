# -*- coding: utf-8 -*-
"""#3017：审计 `action` 分层保留的登记制守卫（ADR-0049 D2）。

裁剪把 security/session 做成显式集、business 用 NOT IN 兜底——对新 action 封闭
是有意的；但安全类漏登会静默落到 90d（注释已自陈）。本守卫钉：

写侧 `record_audit*` 的可解析 `action=` 必须 ∈
``SESSION ∪ SECURITY ∪ BUSINESS_ACTIONS_ALLOWLIST``。
新 action 三选一：进 security / session / 点名进 business allowlist。

动态构造（f-string / 形参）无法静态展开时，调用点必须出现在
``_DYNAMIC_ACTION_SITES``（形态键 → 出现次数），且其**闭合后缀集**
已全部列入 allowlist——禁止「动态且未登记」。

登记键**刻意不含行号**：#3031 只是在 `plan_run_abort.py` 上方插入了 3 行，
两条合法登记就同时被判成「未登记动态调用点」，main 当场红，而报错给出的处置
（先去写 allowlist）与真实成因（行号漂移）完全不是一回事。锚点改钉在
「文件 + 所属函数 + `action=` 的源码形态」——改动它的任意一项都会改变语义，
因而必须重新点名；纯上下移动不再惊动守卫。
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Iterator, Optional

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

#: 动态 action= 调用点的登记表：**形态键 → 该形态在仓库里出现的次数**。
#: 新增动态构造必须在此点名，并把闭合 action 全集写入 BUSINESS_ACTIONS_ALLOWLIST。
#: 键的构成见 `_dynamic_action_key`（不含行号，故对上下移动免疫）。
_DYNAMIC_ACTION_SITES: dict[str, int] = {
    # f"ai_assistant_action_{verb}" — verb ∈ {approve, reject}
    'api/routes/ai_assistant.py::_decide_action::f"ai_assistant_action_{verb}"': 1,
    # f"ai_assistant_action_{event}" — event ∈ orchestrator 调用集
    'services/ai_assistant/orchestrator.py::_audit_action::f"ai_assistant_action_{event}"': 1,
    # audit_action 形参（默认 abort_plan_run；调用方另传 abort_jobs… / ai_assistant…）
    # 同一函数里两处同形态调用 ⇒ 以计数登记：出现第三处就必须显式改这里。
    "services/plan_run_abort.py::abort_plan_run::audit_action": 2,
}


#: `action=` 缺失时键里用的占位（缺 action 同样是「静态展开不出字面量」）。
_NO_ACTION_KWARG = "<no action= kwarg>"


def _iter_calls_with_scope(
    node: ast.AST, scope: tuple[str, ...] = ()
) -> Iterator[tuple[ast.Call, tuple[str, ...]]]:
    """产出 (Call 节点, 所属作用域链)。`ast.walk` 丢掉嵌套关系，这里自己带。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        scope = scope + (getattr(node, "name", ""),)
    if isinstance(node, ast.Call):
        yield node, scope
    for child in ast.iter_child_nodes(node):
        yield from _iter_calls_with_scope(child, scope)


def _dynamic_action_key(
    rel: str, scope: tuple[str, ...], text: str, node: ast.Call
) -> str:
    """动态 `action=` 调用点的**形态键**：`<相对 backend/ 的路径>::<函数链>::<action= 源码>`。

    不含行号（见模块 docstring）；但文件、所属函数、`action=` 表达式三者任意一项
    变了都会换键 ⇒ 登记表会红，逼人重新点名。改名/换构造是**真**语义变化，正是要
    拦的那一类。
    """
    kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    action = kwargs.get("action")
    src = ast.get_source_segment(text, action) if action is not None else None
    return f"{rel}::{'.'.join(scope) or '<module>'}::{src or _NO_ACTION_KWARG}"


def _dynamic_keys_from_source(text: str, rel: str = "fixture.py") -> list[tuple[int, str]]:
    """便利入口：给一段源文，返回其中动态调用点的 (lineno, 形态键)。

    只做「是不是 record_audit* 调用」的字面名判定（不解析 import 别名），
    专给下面的锚点用例用；生产扫描仍走 `_scan_backend`，两者共用
    `_dynamic_action_key`，锚点构造只有一份实现。
    """
    tree = ast.parse(text)
    out: list[tuple[int, str]] = []
    for node, scope in _iter_calls_with_scope(tree):
        if _callee_name(node.func) not in _RECORD_FUNCS:
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if _extract_action_literals(kwargs.get("action")) is not None:
            continue
        out.append((node.lineno, _dynamic_action_key(rel, scope, text, node)))
    return out


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
        "dynamic_keys": [],
        "unexpected_dynamic": [],
        "zombie_dynamic": [],
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
        for node, scope in _iter_calls_with_scope(tree):
            name = _callee_name(node.func)
            if name not in aliases and name not in _RECORD_FUNCS:
                continue
            if any(kw.arg is None for kw in node.keywords):
                report["kwargs_splat"].append((path, node.lineno))
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            actions = _extract_action_literals(kwargs.get("action"))
            if actions is None:
                report["unresolved"].append((path, node.lineno))
                report["dynamic_keys"].append(
                    (path, node.lineno, _dynamic_action_key(rel, scope, text, node))
                )
                continue
            resolved.append((path, node.lineno, actions))
    _apply_dynamic_registry(report)
    return resolved, report


def _apply_dynamic_registry(report: dict) -> None:
    """把实见的动态形态键与登记表**双向**对齐（#3017）。

    - 出现未登记的形态，或某形态实见数**超过**登记数 → `unexpected_dynamic`；
    - 登记数**高于**实见数 → `zombie_dynamic`：调用点被改成字面量、被改名、
      或换了构造。原先靠 lineno 配对时，这两种情况混在一起且互相误判。
    """
    seen: Counter[str] = Counter(key for _, _, key in report["dynamic_keys"])
    for path, lineno, key in report["dynamic_keys"]:
        if seen[key] > _DYNAMIC_ACTION_SITES.get(key, 0):
            report["unexpected_dynamic"].append((path, lineno, key))
    report["zombie_dynamic"] = [
        (key, expected, seen.get(key, 0))
        for key, expected in sorted(_DYNAMIC_ACTION_SITES.items())
        if seen.get(key, 0) < expected
    ]


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
        "动态 action= 调用点未登记（或某形态超出登记条数）（#3017）：\n"
        "  先把闭合 action 全集写入 BUSINESS_ACTIONS_ALLOWLIST，再把下面每条"
        "的键加进 _DYNAMIC_ACTION_SITES（同形态多处 ⇒ 把次数 +1）。\n"
        + "\n".join(
            f"  {path}:{ln}  {key!r}"
            for path, ln, key in report["unexpected_dynamic"]
        )
    )
    # 登记表不得留僵尸条目：登记与实见必须**双向**闭合。
    assert not report["zombie_dynamic"], (
        "_DYNAMIC_ACTION_SITES 有僵尸登记（#3017）——调用点被改成字面量、换了所属"
        "函数、或换了 action= 构造（纯上下移动不会走到这里，键里不含行号）：\n"
        + "\n".join(
            f"  登记 {expected} 处、实见 {actual} 处：{key}"
            for key, expected, actual in report["zombie_dynamic"]
        )
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


# ── 锚点自身要有红绿两侧（#3017 的键从 lineno 改成形态键）────────────

_FIXTURE_SRC = (
    "from backend.core.audit import record_audit\n"
    "def emit(db, tag):\n"
    "    record_audit(db, action=tag, resource_type='plan_run')\n"
)


def test_dynamic_anchor_survives_line_drift() -> None:
    """整段下移 N 行 ⇒ 键不变，但行号确实变了（否则这条夹具是空转）。"""
    base = _dynamic_keys_from_source(_FIXTURE_SRC)
    padded = _dynamic_keys_from_source("# pad\n" * 7 + _FIXTURE_SRC)
    assert base and padded, "夹具里没找到动态调用点——用例恒真"
    assert [key for _, key in base] == [key for _, key in padded]
    assert base[0][0] != padded[0][0], (
        f"两个行号相同（{base[0][0]}）：夹具没有真的制造漂移，这条判据是空转"
    )


def test_dynamic_anchor_breaks_on_shape_change() -> None:
    """改名 / 换 action 构造 ⇒ 键必须变，登记表才会逼人重新点名。"""
    original = _dynamic_keys_from_source(_FIXTURE_SRC)[0][1]
    renamed = _dynamic_keys_from_source(
        _FIXTURE_SRC.replace("def emit(", "def emit_renamed(")
    )[0][1]
    other_expr = _dynamic_keys_from_source(
        _FIXTURE_SRC.replace("action=tag", "action=tag.suffix")
    )[0][1]
    assert original != renamed, "所属函数改名不换键 ⇒ 登记制形同虚设"
    assert original != other_expr, "action= 构造变了不换键 ⇒ 登记制形同虚设"


def test_registered_dynamic_shapes_are_all_live() -> None:
    """登记表每条都能在当前源码里取到（防「登记与实现脱节」回到隐形状态）。"""
    _, report = _scan_backend()
    seen: Counter[str] = Counter(key for _, _, key in report["dynamic_keys"])
    assert len(seen) > 0, "全仓没有任何动态 action= 调用点——这条判据不会失败，是空转"
    for key, expected in _DYNAMIC_ACTION_SITES.items():
        assert seen.get(key, 0) == expected, (
            f"登记 {expected} 处、实见 {seen.get(key, 0)} 处：{key}"
        )
