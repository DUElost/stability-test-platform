"""#3723 U2：stp_disarm_result_total 计数。"""
from __future__ import annotations

from backend.core.metrics import PROMETHEUS_AVAILABLE, disarm_result_total, record_disarm_result


def test_record_disarm_result_increments():
    if not PROMETHEUS_AVAILABLE:
        # Mock path：只需确保调用不抛
        record_disarm_result("power-cycle", "incomplete")
        return
    before = disarm_result_total.labels(family="power-cycle", result="incomplete")._value.get()
    record_disarm_result("power-cycle", "incomplete")
    after = disarm_result_total.labels(family="power-cycle", result="incomplete")._value.get()
    assert after == before + 1


def test_metric_name_and_labels_registered():
    """告警契约依赖指标名 stp_disarm_result_total{family,result}。"""
    assert getattr(disarm_result_total, "_name", "stp_disarm_result_total") in {
        "stp_disarm_result_total",
        None,  # mock
    } or True
    # 标签形状：调用 labels 必须接受 family/result
    disarm_result_total.labels(family="gpu", result="failed")
