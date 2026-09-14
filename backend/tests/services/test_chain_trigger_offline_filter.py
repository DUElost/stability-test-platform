"""#1686：链式衔接过滤离线设备（准入阻塞修复）。"""

from __future__ import annotations

import ast
from pathlib import Path


def _src() -> str:
    return (Path(__file__).resolve().parents[2] / "services/plan_chain_trigger.py").read_text(
        encoding="utf-8"
    )


def _func_source(name: str) -> str:
    """按 AST 取函数源码段（抗缩进/顺序变化，优于整文件字面量计数）。"""
    src = _src()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} 不在 plan_chain_trigger.py 中")


def test_chain_filters_offline_async_and_sync():
    """两处（async / sync）都走同一过滤实现，且实现保留 ONLINE 判据。

    #1935 修正：原断言 ``src.count('if status == "ONLINE"') == 2`` 绑定字面量
    出现次数——#1822 把过滤抽成共享 helper ``_select_chain_devices``（判据也
    扩展为 ONLINE + 心跳窗口内瞬时 OFFLINE）后误红。改为结构断言：两个触发
    路径都必须调用该 helper，helper 内保留 ONLINE 判据（意图不变、抗重构）。
    """
    for fn in ("trigger_next_plan", "trigger_next_plan_sync"):
        assert "_select_chain_devices(" in _func_source(fn), f"{fn} 未走共享离线过滤"
    assert '"ONLINE"' in _func_source("_select_chain_devices"), "过滤实现丢失 ONLINE 判据"


def test_chain_records_excluded_devices():
    """排除清单可观测（日志 + run_context）。"""
    src = _src()
    assert "plan_chain_trigger_excluded_offline" in src
    assert "chain_excluded_devices" in src


def test_chain_joins_device_for_status():
    """过滤依赖 Device.status（JOIN 查询）。"""
    src = _src()
    assert ".join(Device, Device.id == JobInstance.device_id)" in src
