"""`stability_reconciler_dirs_oversized_skipped_total` 的口径对拍（#2640）。

HELP 原文写着「cumulative unique dirs」，而实现是**每个 job 结束时把该 job 的唯一目录数
累加一次**（`UnisocReconciler._oversized_seen` 随实例新建，实例每 job 一份）：同一目录在
N 个 job 里被降级就计 N 次。按字面读它做看板/告警会**高估**被降级目录数。

本文件不重跑聚合逻辑（那需要起 Agent），而是把「HELP 声明的口径」与「实现实际的聚合面」
**静态对拍**——三处必须互相自洽：

| 判据 | 位置 | 钉住的事实 |
|---|---|---|
| HELP 声明 per-job、不再声称跨 job 唯一 | `backend/core/metrics.py` | 读者拿到的口径 |
| 唯一性只在 job 内 | `backend/agent/aee/unisoc_reconciler.py` | 集合随实例新建 + `len(self._oversized_seen)` |
| 每完成一个 job 累加一次 | `backend/services/agent_completion.py` | 桥接 `amount=oversized` |

三处都用 `tools.dev.source_anchor.SourceGuard`（#2639）：相关代码搬走时报「用例已过期」，
而不是把「口径又失真」和「代码搬家」混成同一种红。
"""

from __future__ import annotations

import ast

from tools.dev.source_anchor import REPO_ROOT, SourceGuard

METRICS_REL = "backend/core/metrics.py"
RECONCILER_REL = "backend/agent/aee/unisoc_reconciler.py"
BRIDGE_REL = "backend/services/agent_completion.py"

METRIC_VAR = "reconciler_dirs_oversized_skipped_total"
METRIC_NAME = "stability_reconciler_dirs_oversized_skipped_total"
#: #2640 修掉的错误声明——它承诺的是「跨 job 唯一目录数」。
FALSE_CLAIM = "cumulative unique dirs"


def _help_text_of(metric_var: str) -> str:
    """从 `backend/core/metrics.py` 取该指标的 HELP 字面量（AST，不 import 产品代码）。

    为什么不 import `backend.core.metrics`：根 `tests/` 走 required check 的离线子集
    （#1707 的判据），而该模块在导入期就会解析配置并注册指标。
    """
    tree = ast.parse((REPO_ROOT / METRICS_REL).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == metric_var for t in node.targets):
            continue
        call = node.value
        while isinstance(call, ast.IfExp):  # … if PROMETHEUS_AVAILABLE else _MockMetric()
            call = call.body
        if not (isinstance(call, ast.Call) and len(call.args) >= 2):
            continue
        exported, help_text = call.args[0], call.args[1]
        # 变量名与**导出指标名**必须仍是同一条：改名会让下面的对拍查错对象，
        # 而静默查到别的指标的 HELP 比直接红更糟。
        if not (isinstance(exported, ast.Constant) and exported.value == METRIC_NAME):
            raise AssertionError(
                f"{METRICS_REL} 里 {metric_var} 导出的指标名是 "
                f"{getattr(exported, 'value', exported)!r}，不是 {METRIC_NAME!r}——"
                "指标改名后请把本文件的两个常量一起改，不要只改一处"
            )
        if isinstance(help_text, ast.Constant) and isinstance(help_text.value, str):
            return help_text.value
    raise AssertionError(f"{METRICS_REL} 里找不到 {metric_var} 的 Counter 定义——判据已过期")


def test_help_declares_per_job_aggregation() -> None:
    help_text = _help_text_of(METRIC_VAR)
    guard = SourceGuard(help_text, origin=f"{METRIC_VAR}.HELP").anchored("#2252")
    guard.assert_present("per-job", why="唯一性按 job 成立")
    guard.assert_absent(FALSE_CLAIM, why="#2640：跨 job 不唯一，这么写会让人按目录数读 Counter 而高估")


def test_producer_side_dedups_within_job_only() -> None:
    prod = (
        SourceGuard.of_repo_path(RECONCILER_REL)
        .anchored("self.stats.dirs_oversized_skipped = len(self._oversized_seen)")
    )
    # 集合实例在 __init__ 里新建 ⇒ 每 job 一份；跨 job 唯一性不成立（这正是 HELP 的口径来源）
    prod.assert_present("self._oversized_seen: Set[str] = set()", why="per-job 去重集合的出生地")


def test_bridge_increments_once_per_completed_job() -> None:
    bridge = (
        SourceGuard.of_repo_path(BRIDGE_REL)
        .anchored('oversized = stats.get("dirs_oversized_skipped")')
    )
    bridge.assert_present(
        "record_reconciler_dirs_oversized_skipped(host, amount=oversized)",
        why="每个完成 job 累加一次 = HELP 声明的 per-job 求和口径",
    )


def test_record_helper_documentation_states_the_aggregation() -> None:
    """记录函数的 docstring 必须与 HELP 同一口径（读代码的人先看 docstring）。"""
    doc = SourceGuard.of_repo_path(METRICS_REL).anchored(
        "def record_reconciler_dirs_oversized_skipped(host_id: str, amount: int):"
    )
    doc.assert_present("唯一性只在 job 内", why="#2640 的口径结论要留在读码路径上")
