"""B12 U1 / B3：候选预筛与最终行锁复读共用统一护盾入口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.agent.contracts.device_return import freeze_disarm_guard, receive_disarm_evidence
from backend.agent.contracts.finishing_guard import wrap_disarm_as_finishing_guard
from backend.services.device_return import job_guard_deadline


GPU_FINISH_STEP = {
    "step_id": "gpu_fin",
    "action": "script:gpu_finish",
    "version": "1.1.0",
    "timeout_seconds": 600,
    "package_sha256": "d" * 64,
}


def _fresh(*, t0: float, seq: int = 1, at: float | None = None):
    now = at if at is not None else t0
    g = freeze_disarm_guard(
        [GPU_FINISH_STEP], now=t0, abort_at=t0,
        fencing_token="tok", agent_instance_id="inst",
    )
    return receive_disarm_evidence(
        g,
        {
            "step_id": "gpu_fin", "seq": seq, "phase": "execute", "progress_seq": seq,
            "script_name": "gpu_finish", "script_version": "1.1.0",
            "package_sha256": "d" * 64,
        },
        now=now,
        via_coordinator=True,
    )


def test_candidate_prefilter_and_final_agree_on_fresh_guard(monkeypatch):
    t0 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(seconds=350)
    wire = _fresh(t0=t0.timestamp(), seq=2, at=now.timestamp())
    job = SimpleNamespace(
        status="RUNNING",
        disarm_guard=wire,
        finishing_guard=wrap_disarm_as_finishing_guard(wire),
        last_execution_heartbeat_at=now,
        pipeline_def={},
        id=7, device_id=1, host_id="h1",
    )
    monkeypatch.setattr(
        "backend.services.device_return.guard_owner_valid", lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.device_return.running_heartbeat_timeout_seconds",
        lambda job: 60,
    )
    # 候选预筛（read_only）与最终复读均应判定仍受保护
    assert job_guard_deadline(MagicMock(), job, now, read_only=True) is not None
    assert job_guard_deadline(MagicMock(), job, now, read_only=False) is not None


def test_candidate_prefilter_releases_when_guard_stale(monkeypatch):
    t0 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    # 证据停在 t0，查询时刻超 60s 新鲜窗
    now = t0 + timedelta(seconds=90)
    wire = _fresh(t0=t0.timestamp(), seq=1, at=t0.timestamp())
    job = SimpleNamespace(
        status="RUNNING",
        disarm_guard=wire,
        finishing_guard=None,
        last_execution_heartbeat_at=now,
        pipeline_def={},
        id=8, device_id=1, host_id="h1",
    )
    monkeypatch.setattr(
        "backend.services.device_return.guard_owner_valid", lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.device_return.running_heartbeat_timeout_seconds",
        lambda job: 60,
    )
    assert job_guard_deadline(MagicMock(), job, now, read_only=True) is None
