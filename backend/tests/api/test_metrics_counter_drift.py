"""#77：``stability_plan_run_counter_drift_total`` 经 ``/metrics`` 端点暴露。

sweep → 埋点的路径由 ``test_counter_reconciler_aggregation.py`` 的单测覆盖；
本用例验证暴露面（与 #1258 的 fleet gauge 用例同范式）。
#1927：label 只保留 mode（白名单有界值域）——plan_run_id 单调无界，
不得进入 label 值域（run 维度走日志）。
"""
from __future__ import annotations

from backend.core.metrics import record_plan_run_counter_drift


def test_metrics_exposes_plan_run_counter_drift(client, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    # 模拟 counter_reconciler 修复了 terminal/completed 两列漂移
    record_plan_run_counter_drift(777001, ["terminal", "completed"])

    body = client.get("/metrics").text

    assert 'mode="terminal"' in body
    assert 'stability_plan_run_counter_drift_total{' in body
    # #1927：plan_run_id 不再是 label——无界值域不得进入指标面
    assert 'plan_run_id=' not in body.split("stability_plan_run_counter_drift_total")[1].split("\n")[0]
    # 非白名单列名不得进入值域
    record_plan_run_counter_drift(777002, ["unknown_column"])
    body2 = client.get("/metrics").text
    assert 'mode="unknown_column"' not in body2
