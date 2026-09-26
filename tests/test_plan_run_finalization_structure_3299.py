# -*- coding: utf-8 -*-
"""#3299 — 结构守卫：终态编排者归属 + 五模块环不得回潮。

验收判据本体：C5 基线中 ``plan_run_aggregation -> post_completion`` 已删且
五模块无环（由 ``.importlinter`` 的 acyclic_siblings 合约把守，
``unmatched_ignore_imports_alerting = error`` 保证基线行不会变成永久豁免）。
本文件在其上钉**归属边**——比「无环」更强的约束：某些边即使不成环也不许存在，
因为它们的语义就是「副作用编排权」的错位：

- ``plan_run_aggregation`` 不得 import 任何副作用模块（含 finalization 本身）：
  聚合器=纯计算+落库；依赖方向是编排者→聚合器，反向即环。
- ``post_completion`` 不得直接 import ``plan_chain_trigger`` / ``plan_run_aggregation``：
  链式修复与 RISK_HIGH 是 Run 级副作用，必须经 finalization 的入口。
- ``plan_dispatcher_sync`` 不得 import ``plan_run_abort``：判据已下沉
  ``plan_run_context``；恢复这条边就恢复了 ``abort → finalization → chain_trigger →
  dispatcher → abort`` 四环。

判据全部走 AST（含函数体内 import，与 grimp/import-linter 同口径）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tools.dev.source_anchor import SourceGuard

SERVICES = Path(__file__).resolve().parents[1] / "backend" / "services"


def _imported_modules(module_file: str) -> set[str]:
    tree = ast.parse((SERVICES / module_file).read_text(encoding="utf-8"))
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                seen.add(node.module)
        elif isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
    return seen


def test_importlinter_c5_baseline_edge_removed():
    """验收字面判据：基线行不得复活（回归 = 有人重新引入了闭合边）。"""
    SourceGuard.of_repo_path(".importlinter").anchored(
        "[importlinter:contract:c5-services-acyclic]"
    ).assert_absent(
        "backend.services.plan_run_aggregation -> backend.services.post_completion",
        why="#3299 C5：plan_run_aggregation -> post_completion 基线边不得回潮",
    )


def test_aggregation_imports_no_side_effect_modules():
    seen = _imported_modules("plan_run_aggregation.py")
    banned = {
        "backend.services.post_completion",
        "backend.services.plan_run_finalization",
        "backend.services.plan_chain_trigger",
        "backend.services.dedup_scan",
        "backend.services.notification_service",
    }
    assert not seen & banned, f"聚合器出现副作用 import：{seen & banned}"


def test_post_completion_routes_side_effects_through_finalization():
    seen = _imported_modules("post_completion.py")
    assert "backend.services.plan_chain_trigger" not in seen
    assert "backend.services.plan_run_aggregation" not in seen


def test_dispatcher_does_not_depend_on_abort():
    seen = _imported_modules("plan_dispatcher_sync.py")
    assert "backend.services.plan_run_abort" not in seen


def test_side_effect_entries_only_live_in_finalization():
    """迁移符号的旧家不得留转发壳（单一所有权，防双主漂移）。"""
    import importlib

    aggregation = importlib.import_module("backend.services.plan_run_aggregation")
    post_completion = importlib.import_module("backend.services.post_completion")
    job_term = importlib.import_module("backend.services.job_terminalization")
    fin = importlib.import_module("backend.services.plan_run_finalization")

    for name in ("notify_plan_run_terminal", "maybe_notify_risk_high"):
        assert not hasattr(aggregation, name), f"aggregation 仍持有 {name}"
        assert callable(getattr(fin, name))
    for name in ("refresh_report_cache_for_plan_run", "schedule_report_cache_refresh"):
        assert not hasattr(post_completion, name), f"post_completion 仍持有 {name}"
        assert callable(getattr(fin, name))
    for name in ("_post_aggregation_side_effects_async", "_post_aggregation_side_effects_sync"):
        assert not hasattr(job_term, name), f"job_terminalization 仍持有 {name}"
    for name in ("finalize_parent_run_async", "finalize_parent_run_sync", "recover_chain_trigger"):
        assert callable(getattr(fin, name))


def test_recount_counters_has_a_single_home():
    """#3376 项 3：`recount_plan_run_counters` 的唯一家在聚合器；终态编排者不得留再导出壳。

    该符号随 ADR-0052 #3244 迁入 ``plan_run_aggregation``，但 ``job_terminalization``
    末尾留了「兼容再导入」壳（注释自称旧导入路径）。壳会让调用方与 patch 目标分叉
    （#3292 / #3307 / ADR-0054 D5 同一纪律），故删除并在此钉住：旧家不得再持有，
    调用方（finalization / counter_reconciler / 测试）一律直接 import 新家。
    """
    import importlib

    aggregation = importlib.import_module("backend.services.plan_run_aggregation")
    job_term = importlib.import_module("backend.services.job_terminalization")

    assert callable(aggregation.recount_plan_run_counters)
    assert not hasattr(job_term, "recount_plan_run_counters"), (
        "job_terminalization 又长出了 recount_plan_run_counters 再导出壳（单一所有权，见 #3376）"
    )
