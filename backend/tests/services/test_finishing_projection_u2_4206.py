"""B12 U2：finishing 只读投影谓词与 C3 反例（文件名避开 guard 子串）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.agent.contracts.device_return import freeze_disarm_guard, receive_disarm_evidence
from backend.agent.contracts.finishing_guard import (
    FINISHING_KIND_ATOMIC_COMMIT,
    FINISHING_KIND_TEARDOWN_DISARM,
    GUARD_STATE_GRANTED,
    wrap_disarm_as_finishing_guard,
)
from backend.api.schemas.plan_run import PlanRunMinimapDeviceOut
from backend.core.job_timeout_config import EXECUTING_DISARM
from backend.models.enums import JobStatus
from backend.services.plan_run_devices import (
    critical_section_protects_adb_disconnect,
    job_exec_status_for_job,
    project_job_finishing,
    ui_status_for_job,
)


def _job(**kwargs):
    defaults = dict(
        id=101,
        status=JobStatus.RUNNING.value,
        manual_action=None,
        next_retry_at=None,
        execution_state=None,
        disarm_guard=None,
        finishing_guard=None,
        pipeline_def={},
        host_id="h1",
        lifecycle_stage=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _collection_pipeline():
    return {
        "lifecycle": {
            "teardown": [
                {
                    "step_id": "collect_logs",
                    "action": "script:collect_device_logs",
                    "enabled": True,
                    "timeout_seconds": 60,
                }
            ]
        }
    }


def test_c3_pure_collection_teardown_deadline_is_null():
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    job = _job(
        execution_state="EXECUTING_STEP",
        pipeline_def=_collection_pipeline(),
        lifecycle_stage="teardown",
    )
    run_context = {
        "abort_requested": {
            "at": now.isoformat(),
            "requested_job_ids": [101],
        }
    }
    proj = project_job_finishing(job, now=now, db=None, run_context=run_context)
    assert proj is not None
    assert proj.kind == FINISHING_KIND_TEARDOWN_DISARM
    assert proj.deadline_at is None
    assert job_exec_status_for_job(job, now, run_context=run_context) == "finishing"


def test_c3_granted_atomic_commit_adb_disconnect_stays_finishing(monkeypatch):
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    stamp = now.timestamp()
    finishing_guard = {
        "kind": FINISHING_KIND_ATOMIC_COMMIT,
        "guard_state": GUARD_STATE_GRANTED,
        "trigger": "manual_abort",
        "D_atomic": stamp + 1800,
        "last_liveness_receipt_at": stamp,
        "last_progress_receipt_at": stamp,
        "last_server_at": stamp,
    }
    job = _job(finishing_guard=finishing_guard, id=202)
    run_context = {
        "abort_requested": {
            "at": now.isoformat(),
            "requested_job_ids": [202],
        }
    }
    device = SimpleNamespace(
        adb_connected=False,
        adb_state="offline",
        status="OFFLINE",
    )
    monkeypatch.setattr(
        "backend.services.plan_run_devices.job_guard_deadline",
        lambda *a, **k: now + timedelta(seconds=600),
    )
    ui = ui_status_for_job(
        job,
        now,
        device,
        "ONLINE",
        run_context=run_context,
        host_last_heartbeat=now,
        db=MagicMock(),
    )
    assert ui == "finishing"
    assert ui != "unknown"


def test_c3_minimap_dto_exactly_seven_fields_and_vocab():
    fields = set(PlanRunMinimapDeviceOut.model_fields.keys())
    assert fields == {
        "job_id",
        "device_id",
        "device_serial",
        "host_id",
        "job_exec_status",
        "device_link_status",
        "log_signal_count",
    }
    assert "finishing_kind" not in fields
    assert "finishing_trigger" not in fields
    assert "finishing_deadline_at" not in fields


def test_executing_disarm_projects_finishing_with_deadline(monkeypatch):
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    t0 = now.timestamp()
    wire = freeze_disarm_guard(
        [{
            "step_id": "gpu_fin",
            "action": "script:gpu_finish",
            "version": "1.1.0",
            "timeout_seconds": 600,
            "package_sha256": "c" * 64,
        }],
        now=t0,
        abort_at=t0,
        fencing_token="tok",
        agent_instance_id="inst",
    )
    wire = receive_disarm_evidence(
        wire,
        {
            "step_id": "gpu_fin",
            "seq": 1,
            "phase": "execute",
            "progress_seq": 1,
            "script_name": "gpu_finish",
            "script_version": "1.1.0",
            "package_sha256": "c" * 64,
        },
        now=t0,
        via_coordinator=True,
    )
    job = _job(
        execution_state=EXECUTING_DISARM,
        disarm_guard=wire,
        finishing_guard=wrap_disarm_as_finishing_guard(wire, trigger="manual_abort"),
    )
    deadline = now + timedelta(seconds=500)
    monkeypatch.setattr(
        "backend.services.plan_run_devices.job_guard_deadline",
        lambda *a, **k: deadline,
    )
    proj = project_job_finishing(job, now=now, db=MagicMock(), run_context=None)
    assert proj is not None
    assert proj.kind == FINISHING_KIND_TEARDOWN_DISARM
    assert proj.deadline_at == deadline


def test_granted_without_abort_stays_running_but_protects_unknown():
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    stamp = now.timestamp()
    job = _job(
        finishing_guard={
            "kind": FINISHING_KIND_ATOMIC_COMMIT,
            "guard_state": GUARD_STATE_GRANTED,
            "D_atomic": stamp + 1800,
            "last_liveness_receipt_at": stamp,
            "last_progress_receipt_at": stamp,
            "last_server_at": stamp,
        }
    )
    assert project_job_finishing(job, now=now, run_context={}) is None
    assert critical_section_protects_adb_disconnect(
        job, now=now, host_last_heartbeat=now,
    )
    device = SimpleNamespace(adb_connected=False, adb_state="offline", status="OFFLINE")
    assert ui_status_for_job(
        job, now, device, "ONLINE", host_last_heartbeat=now, run_context={},
    ) == "running"


def test_pending_and_unknown_never_finishing():
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    assert project_job_finishing(
        _job(status=JobStatus.PENDING.value, execution_state=EXECUTING_DISARM),
        now=now,
    ) is None
    assert project_job_finishing(
        _job(status=JobStatus.UNKNOWN.value, execution_state=EXECUTING_DISARM),
        now=now,
    ) is None
