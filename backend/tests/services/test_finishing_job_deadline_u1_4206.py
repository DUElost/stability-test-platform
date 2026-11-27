"""B12 U1：job_guard_deadline 统一入口吸收 disarm_guard / finishing_guard（C2）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.agent.contracts.device_return import freeze_disarm_guard, receive_disarm_evidence
from backend.agent.contracts.finishing_guard import (
    FINISHING_KIND_ATOMIC_COMMIT,
    GUARD_STATE_GRANTED,
    wrap_disarm_as_finishing_guard,
)
from backend.services.device_return import job_guard_deadline


GPU_FINISH_STEP = {
    "step_id": "gpu_fin",
    "action": "script:gpu_finish",
    "version": "1.1.0",
    "timeout_seconds": 600,
    "package_sha256": "c" * 64,
}


def _fresh_gpu_disarm(*, t0: float):
    g = freeze_disarm_guard(
        [GPU_FINISH_STEP], now=t0, abort_at=t0,
        fencing_token="tok", agent_instance_id="inst",
    )
    g = receive_disarm_evidence(
        g,
        {
            "step_id": "gpu_fin", "seq": 1, "phase": "execute", "progress_seq": 1,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "c" * 64,
        },
        now=t0,
        via_coordinator=True,
    )
    return g


def test_job_guard_deadline_protects_legacy_disarm_past_300s(monkeypatch):
    t0 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(seconds=350)
    stamp0 = t0.timestamp()
    stamp_now = now.timestamp()
    wire = _fresh_gpu_disarm(t0=stamp0)
    # 刷新到 now，保持证据新鲜
    wire = receive_disarm_evidence(
        wire,
        {
            "step_id": "gpu_fin", "seq": 2, "phase": "execute", "progress_seq": 2,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "c" * 64,
        },
        now=stamp_now,
        via_coordinator=True,
    )
    job = SimpleNamespace(
        status="RUNNING",
        disarm_guard=wire,
        finishing_guard=None,
        last_execution_heartbeat_at=now,
        pipeline_def={},
        id=1,
        device_id=1,
        host_id="h1",
    )
    monkeypatch.setattr(
        "backend.services.device_return.guard_owner_valid",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.device_return.running_heartbeat_timeout_seconds",
        lambda job: 60,
    )
    deadline = job_guard_deadline(MagicMock(), job, now, read_only=True)
    assert deadline is not None
    assert deadline > now


def test_job_guard_deadline_uses_finishing_guard_column(monkeypatch):
    t0 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(seconds=20)
    stamp0 = t0.timestamp()
    wire = wrap_disarm_as_finishing_guard(_fresh_gpu_disarm(t0=stamp0))
    wire = receive_disarm_evidence(
        wire,
        {
            "step_id": "gpu_fin", "seq": 2, "phase": "execute", "progress_seq": 2,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "c" * 64,
        },
        now=now.timestamp(),
        via_coordinator=True,
    )
    job = SimpleNamespace(
        status="RUNNING",
        disarm_guard=None,
        finishing_guard=wire,
        last_execution_heartbeat_at=now,
        pipeline_def={},
        id=1,
        device_id=1,
        host_id="h1",
    )
    monkeypatch.setattr(
        "backend.services.device_return.guard_owner_valid",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.device_return.running_heartbeat_timeout_seconds",
        lambda job: 60,
    )
    assert job_guard_deadline(MagicMock(), job, now, read_only=True) is not None


def test_job_guard_deadline_atomic_commit_granted(monkeypatch):
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    ts = now.timestamp()
    guard = {
        "kind": FINISHING_KIND_ATOMIC_COMMIT,
        "guard_state": GUARD_STATE_GRANTED,
        "D_atomic": ts + 600,
        "last_liveness_receipt_at": ts,
        "last_progress_receipt_at": ts,
        "last_server_at": ts,
        "fencing_token": "t",
        "agent_instance_id": "i",
    }
    job = SimpleNamespace(
        status="RUNNING",
        disarm_guard=None,
        finishing_guard=guard,
        last_execution_heartbeat_at=now,
        pipeline_def={},
        id=1,
        device_id=1,
        host_id="h1",
    )
    monkeypatch.setattr(
        "backend.services.device_return.guard_owner_valid",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.device_return.running_heartbeat_timeout_seconds",
        lambda job: 60,
    )
    assert job_guard_deadline(MagicMock(), job, now, read_only=True) is not None
    # 失鲜
    later = now + timedelta(seconds=90)
    assert job_guard_deadline(MagicMock(), job, later, read_only=True) is None


def test_b1_dual_column_follows_live_disarm_seq(monkeypatch):
    """陈旧 finishing_guard 不得盖过 accept 路径持续写入的 B6 disarm_guard。"""
    t0 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(seconds=350)
    stamp0 = t0.timestamp()
    stale = wrap_disarm_as_finishing_guard(_fresh_gpu_disarm(t0=stamp0))
    live = receive_disarm_evidence(
        _fresh_gpu_disarm(t0=stamp0),
        {
            "step_id": "gpu_fin", "seq": 9, "phase": "execute", "progress_seq": 9,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "c" * 64,
        },
        now=now.timestamp(),
        via_coordinator=True,
    )
    job = SimpleNamespace(
        status="RUNNING",
        disarm_guard=live,
        finishing_guard=stale,
        last_execution_heartbeat_at=now,
        pipeline_def={},
        id=1, device_id=1, host_id="h1",
    )
    monkeypatch.setattr(
        "backend.services.device_return.guard_owner_valid", lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.device_return.running_heartbeat_timeout_seconds",
        lambda job: 60,
    )
    assert job_guard_deadline(MagicMock(), job, now, read_only=True) is not None


def test_b1_dual_column_absorbs_legacy_regression(monkeypatch):
    """旧列 reason=clock_regressed 时，即使新列仍新鲜也必须失效。"""
    t0 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(seconds=20)
    stamp0 = t0.timestamp()
    fresh = wrap_disarm_as_finishing_guard(_fresh_gpu_disarm(t0=stamp0))
    fresh = receive_disarm_evidence(
        fresh,
        {
            "step_id": "gpu_fin", "seq": 2, "phase": "execute", "progress_seq": 2,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "c" * 64,
        },
        now=now.timestamp(),
        via_coordinator=True,
    )
    legacy = {**_fresh_gpu_disarm(t0=stamp0), "reason": "clock_regressed"}
    job = SimpleNamespace(
        status="RUNNING",
        disarm_guard=legacy,
        finishing_guard=fresh,
        last_execution_heartbeat_at=now,
        pipeline_def={},
        id=1, device_id=1, host_id="h1",
    )
    monkeypatch.setattr(
        "backend.services.device_return.guard_owner_valid", lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.device_return.running_heartbeat_timeout_seconds",
        lambda job: 60,
    )
    assert job_guard_deadline(MagicMock(), job, now, read_only=True) is None
