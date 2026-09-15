"""#1686：链式衔接过滤离线设备（准入阻塞修复）。"""

from __future__ import annotations

import ast
from pathlib import Path


def _src() -> str:
    return (Path(__file__).resolve().parents[2] / "services/plan_chain_trigger.py").read_text(
        encoding="utf-8"
    )


def _func_ast(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(_src())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} 不在 plan_chain_trigger.py 中")


def _assert_chain_devices_result_feeds_dispatch(fn_name: str) -> None:
    """#2030：`_select_chain_devices` 的**结果**必须真的被使用并喂给派发。

    仅断言源码含 `_select_chain_devices(` 子串挡不住「调用了但丢弃返回值、
    继续用未过滤列表派发」——那正是 #1686 要防的「离线设备被触发」。此处做
    两段 AST 断言：① 调用出现在解包赋值的 RHS；② 解包出的设备 id 变量作为
    `prepare_plan_run(device_ids=...)` 的实参。
    """
    fn = _func_ast(fn_name)
    unpacked: str | None = None
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_select_chain_devices"
        ):
            assert len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple), (
                f"{fn_name}: _select_chain_devices 的返回未被解包赋值"
            )
            first = node.targets[0].elts[0]
            assert isinstance(first, ast.Name), (
                f"{fn_name}: 解包首项不是名字节点：{ast.dump(first)}"
            )
            unpacked = first.id
    assert unpacked is not None, (
        f"{fn_name} 未把 _select_chain_devices(rows) 的返回解包赋值——"
        "调用结果被丢弃时未过滤列表会照常派发（#1686 复发）"
    )

    for node in ast.walk(fn):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "prepare_plan_run"
        ):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "device_ids"
                and isinstance(kw.value, ast.Name)
                and kw.value.id == unpacked
            ):
                return
    raise AssertionError(
        f"{fn_name} 未把 {unpacked} 送入 prepare_plan_run(device_ids=...)——"
        "过滤结果没有喂给派发"
    )


def _has_online_comparison(node: ast.AST) -> bool:
    """函数体内存在与字符串常量 "ONLINE" 的比较（docstring/注释不算）。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Compare):
            for operand in (n.left, *n.comparators):
                if isinstance(operand, ast.Constant) and operand.value == "ONLINE":
                    return True
    return False


def test_chain_filters_offline_async_and_sync():
    """两处（async / sync）都走同一过滤实现，且过滤结果必须真的喂给派发。

    #1935 修正：原断言 ``src.count('if status == "ONLINE"') == 2`` 绑定字面量
    出现次数——#1822 把过滤抽成共享 helper ``_select_chain_devices``（判据也
    扩展为 ONLINE + 心跳窗口内瞬时 OFFLINE）后误红。改为结构断言。

    #2030 加固：原断言（对函数源码段做 ``"_select_chain_devices("`` 子串匹配）
    对「调用了但丢弃返回值」同样成立；改为 AST 级断言（解包赋值 + 结果送入
    ``prepare_plan_run``），ONLINE 判据断言同步改为比较节点级（原子串断言会被
    函数 docstring 里的字样满足）。
    """
    for fn_name in ("trigger_next_plan", "trigger_next_plan_sync"):
        _assert_chain_devices_result_feeds_dispatch(fn_name)
    assert _has_online_comparison(_func_ast("_select_chain_devices")), (
        "过滤实现丢失 ONLINE 判据"
    )


def test_chain_records_excluded_devices():
    """排除清单可观测（日志 + run_context）。"""
    src = _src()
    assert "plan_chain_trigger_excluded_offline" in src
    assert "chain_excluded_devices" in src


def test_chain_joins_device_for_status():
    """过滤依赖 Device.status（JOIN 查询）。"""
    src = _src()
    assert ".join(Device, Device.id == JobInstance.device_id)" in src
