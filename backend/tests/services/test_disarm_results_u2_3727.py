"""#3723 U2 / ADR-0061 D4：disarm_results 接受、推导与指标。

反例（修复前红）：
- 带 disarm_results 的 complete 载荷被忽略
- 缺失时不按 §3.0 第 6 条推导
- 指标不计
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.core import metrics as metrics_mod
from backend.services.agent_completion import (
    _RunCompleteIn,
    apply_disarm_results,
    derive_disarm_results_from_traces,
    normalize_disarm_results,
)
from backend.services.disarm_derive import _map_trace_to_disarm_result


PIPELINE = {
    "lifecycle": {
        "init": [
            {
                "step_id": "pc_setup",
                "action": "script:powercycle_setup",
                "version": "1.0.0",
            }
        ],
        "teardown": [
            {
                "step_id": "pc_finish",
                "action": "script:powercycle_finish",
                "version": "1.0.4",
                "timeout_seconds": 600,
            }
        ],
    }
}


def test_run_complete_in_accepts_disarm_results():
    payload = _RunCompleteIn(
        update={"status": "FINISHED"},
        fencing_token="tok",
        disarm_results={
            "power-cycle": {
                "result": "confirmed",
                "step_id": "pc_finish",
                "exit_code": 0,
                "readback_summary": "clear",
            }
        },
    )
    assert payload.disarm_results["power-cycle"]["result"] == "confirmed"


def test_normalize_rejects_empty_and_keeps_evidence():
    assert normalize_disarm_results(None) is None
    assert normalize_disarm_results({}) is None
    got = normalize_disarm_results({"power-cycle": "executed_unverified"})
    assert got["power-cycle"]["result"] == "executed_unverified"


@pytest.mark.parametrize(
    "status,exit_code,expected",
    [
        ("COMPLETED", 0, "executed_unverified"),
        ("FINISHED", 0, "executed_unverified"),
        ("FAILED", 143, "incomplete"),
        ("FAILED", -15, "incomplete"),
        ("FAILED", 137, "incomplete"),
        ("FAILED", -9, "incomplete"),
        ("FAILED", 1, "failed"),
        ("RUNNING", None, "not_executed"),
    ],
)
def test_map_trace_table(status, exit_code, expected):
    assert _map_trace_to_disarm_result(status, exit_code) == expected


def test_derive_from_teardown_completed():
    traces = [
        SimpleNamespace(stage="init", step_id="pc_setup", status="COMPLETED", exit_code=0),
        SimpleNamespace(stage="teardown", step_id="pc_finish", status="COMPLETED", exit_code=0),
    ]
    got = derive_disarm_results_from_traces(PIPELINE, traces)
    assert got["power-cycle"]["result"] == "executed_unverified"
    assert got["power-cycle"]["step_id"] == "pc_finish"


def test_derive_signal_exit_incomplete():
    traces = [
        SimpleNamespace(stage="init", step_id="pc_setup", status="COMPLETED", exit_code=0),
        SimpleNamespace(stage="teardown", step_id="pc_finish", status="FAILED", exit_code=143),
    ]
    got = derive_disarm_results_from_traces(PIPELINE, traces)
    assert got["power-cycle"]["result"] == "incomplete"


def test_derive_other_failed():
    traces = [
        SimpleNamespace(stage="init", step_id="pc_setup", status="COMPLETED", exit_code=0),
        SimpleNamespace(stage="teardown", step_id="pc_finish", status="FAILED", exit_code=1),
    ]
    got = derive_disarm_results_from_traces(PIPELINE, traces)
    assert got["power-cycle"]["result"] == "failed"


def test_derive_no_teardown_trace_not_executed():
    traces = [
        SimpleNamespace(stage="init", step_id="pc_setup", status="COMPLETED", exit_code=0),
    ]
    got = derive_disarm_results_from_traces(PIPELINE, traces)
    assert got["power-cycle"]["result"] == "not_executed"


def test_apply_disarm_results_sets_column_and_counts(monkeypatch):
    seen: list[tuple[str, str]] = []

    def _capture(family: str, result: str) -> None:
        seen.append((family, result))

    monkeypatch.setattr(metrics_mod, "record_disarm_result", _capture)
    # apply_disarm_results imports record_disarm_result at module level — patch there
    import backend.services.agent_completion as ac

    monkeypatch.setattr(ac, "record_disarm_result", _capture)

    job = SimpleNamespace(disarm_results=None, status_reason="keep-me")
    apply_disarm_results(
        job,
        {"power-cycle": {"result": "failed", "step_id": "pc_finish", "exit_code": 1, "readback_summary": ""}},
    )
    assert job.disarm_results["power-cycle"]["result"] == "failed"
    assert job.status_reason == "keep-me"
    assert ("power-cycle", "failed") in seen


def test_late_started_does_not_override_completed():
    """§3.3.2 B：更早发生但迟到入库的 STARTED 不得覆盖其后 COMPLETED。"""
    now = datetime.now(timezone.utc)
    traces = [
        SimpleNamespace(
            stage="init", step_id="pc_setup", status="COMPLETED", exit_code=0,
            original_ts=now - timedelta(seconds=30),
        ),
        SimpleNamespace(
            stage="teardown", step_id="pc_finish", status="COMPLETED", exit_code=0,
            original_ts=now,
        ),
        SimpleNamespace(
            stage="teardown", step_id="pc_finish", status="RUNNING", exit_code=None,
            original_ts=now - timedelta(seconds=10),
        ),
    ]
    # Delivery order puts STARTED last; occurrence order must still prefer COMPLETED.
    got = derive_disarm_results_from_traces(PIPELINE, traces)
    assert got["power-cycle"]["result"] == "executed_unverified", got


def test_later_failed_overrides_earlier_completed():
    now = datetime.now(timezone.utc)
    traces = [
        SimpleNamespace(
            stage="teardown", step_id="pc_finish", status="COMPLETED", exit_code=0,
            original_ts=now - timedelta(seconds=5),
        ),
        SimpleNamespace(
            stage="teardown", step_id="pc_finish", status="FAILED", exit_code=1,
            original_ts=now,
        ),
    ]
    got = derive_disarm_results_from_traces(PIPELINE, list(reversed(traces)))
    assert got["power-cycle"]["result"] == "failed", got


def test_only_started_is_incomplete():
    now = datetime.now(timezone.utc)
    traces = [
        SimpleNamespace(
            stage="teardown", step_id="pc_finish", status="RUNNING", exit_code=None,
            original_ts=now,
        ),
    ]
    got = derive_disarm_results_from_traces(PIPELINE, traces)
    assert got["power-cycle"]["result"] == "incomplete", got
