"""Grafana 仪表板指标契约（#1258 / R14-F12）。

两层校验：

1. **幽灵序列**：仪表板引用的每个 ``stability_*`` 名必须存在于指标注册表；
2. **无生产者面板**：已知「有定义、无生产者」的指标不得出现在仪表板——
   修复方式为补真实生产者或撤下面板（未完成项见 Agent Note ``Revisit``）。
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.metrics_registry import metric_registry_index

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "docs" / "grafana" / "stability-platform-dashboard.json"

_METRIC_RE = re.compile(r"stability_[a-z0-9_]+")

# 有定义但暂无生产者、已从仪表板撤下的指标（#1258 deferred：API 请求中间件。
# 补上真实生产者或删除定义后，从本清单移除对应条目）。
UNPRODUCED_METRICS = {
    "stability_api_requests_total",
    "stability_api_request_duration_seconds",
    "stability_api_request_duration_seconds_bucket",
    "stability_api_request_duration_seconds_count",
    "stability_api_request_duration_seconds_sum",
}


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
    overlap = sorted(_dashboard_metrics() & UNPRODUCED_METRICS)
    assert not overlap, (
        "以下指标尚无生产者却出现在仪表板（补埋点或撤下面板后更新本测试）：\n"
        + "\n".join(overlap)
    )
