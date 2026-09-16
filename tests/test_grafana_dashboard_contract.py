"""Grafana 仪表板指标契约（#1258 / R14-F12）。

两层校验：

1. **幽灵序列**：仪表板引用的每个 ``stability_*`` 名必须存在于指标注册表；
2. **无生产者面板**：「有定义、无生产者」的指标不得出现在仪表板——修复方式为补真实
   生产者或撤下面板。

第 2 条的判定自 #2286 起**从生产者分析器派生**（``tests/test_alert_metric_producers.py``，
#2237），不再是本文件手维护的清单：原清单在中间件落地后就没同步过，5 条豁免全部过期；
而「面板引用 ∩ 手维护豁免」这种交集只要面板不引用就恒不触发——既挡不住新债，也留不下
「债已清」的证据。派生之后清单只保留**人工兜底**用途（强判据误认写出者时强制记债）。
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.metrics_registry import metric_registry_index
from tests.test_alert_metric_producers import _base_name, unproduced_definitions

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "docs" / "grafana" / "stability-platform-dashboard.json"

_METRIC_RE = re.compile(r"stability_[a-z0-9_]+")

# 人工登记的「有定义、暂无生产者」指标（强判据误认写出者、或需临时记债时的兜底）。
# **#2286 收口后为空**：原来 5 条 API 请求相关豁免的前提（「生产者尚未落地」）已经不
# 成立——中间件挂在 ``backend/main.py``，写入点在 ``backend/core/request_metrics.py``。
#
# 这里为空不代表断言变弱：无生产者集合现在由 ``unproduced_definitions()`` 派生，新面板
# 引用真无生产者的指标会直接红，不依赖有人记得往本清单加条目。加条目的正确时机只有一个：
# 强判据漏认了真实写入点、又来不及补锚点——必须同时写原因与终态出口（同 #1258 口径）。
UNPRODUCED_METRICS: set[str] = set()


def _dashboard_metrics() -> set[str]:
    refs = set(_METRIC_RE.findall(DASHBOARD.read_text(encoding="utf-8")))
    assert refs, "仪表板未解析出任何 stability_* 指标引用"
    return refs


def test_dashboard_has_no_ghost_series():
    index, non_queryable = metric_registry_index()
    ghosts = sorted(
        m for m in _dashboard_metrics()
        if m not in index and m not in non_queryable
    )
    assert not ghosts, "仪表板引用了注册表不存在的指标：\n" + "\n".join(ghosts)


def test_dashboard_does_not_use_unproduced_metrics():
    """面板引用的指标必须有生产者证据（判据派生，不靠本文件手维护清单）。"""
    derived = unproduced_definitions()
    referenced = {_base_name(name) for name in _dashboard_metrics()}
    overlap = sorted(referenced & (set(derived) | UNPRODUCED_METRICS))
    assert not overlap, (
        "以下指标查不到生产者却被仪表板引用（补埋点或撤下面板；确需人工记债才往 "
        "UNPRODUCED_METRICS 加条目并写明原因）：\n"
        + "\n".join(f"  {name}: {derived.get(name, '人工登记')}" for name in overlap)
    )
    stale = sorted(name for name in UNPRODUCED_METRICS if name not in derived)
    assert not stale, f"人工豁免已失效（指标恢复生产者或定义已删），请删除：{stale}"
