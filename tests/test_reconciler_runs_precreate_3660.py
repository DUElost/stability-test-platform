"""#3660：terminal_job_active_lease 的 success/error 子序列必须在 import 时预置。

带标签 Counter 要到首次 ``.labels(...).inc()`` 才建子序列（#3500）。本单告警与
SOP G1 依赖 ``outcome="success"``；不预置时进程重启后「明确停转」只能落入
absent/无数据。此处只断言本单两条子序列，不覆盖 #3500 的其余 Counter。
"""

from __future__ import annotations

import os

# backend.core.database 在导入期解析 DATABASE_URL（root tests 无 conftest 注入）。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-reconciler-precreate-3660.db")


def test_terminal_job_active_lease_success_and_error_precreated():
    import backend.core.metrics  # noqa: F401
    from prometheus_client import REGISTRY

    collector = REGISTRY._names_to_collectors["stability_reconciler_runs_total"]
    assert ("terminal_job_active_lease", "success") in collector._metrics
    assert ("terminal_job_active_lease", "error") in collector._metrics
    assert (
        REGISTRY.get_sample_value(
            "stability_reconciler_runs_total",
            {"check": "terminal_job_active_lease", "outcome": "success"},
        )
        is not None
    )
    assert (
        REGISTRY.get_sample_value(
            "stability_reconciler_runs_total",
            {"check": "terminal_job_active_lease", "outcome": "error"},
        )
        is not None
    )
