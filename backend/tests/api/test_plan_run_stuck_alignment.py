"""#993 — matrix stuck deadline aligned with recycler liveness判据.

`_running_heartbeat_deadline` must mirror `recycler._running_liveness_anchor`
(ADR-0026 §3): updated_at is NOT liveness (lease renewals cannot fake it,
#288); WAITING_* jobs stay non-stuck while their host's coordinator heartbeat
is fresh — only a dead coordinator ages them out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.api.routes.plan_runs import (
    _COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS,
    _running_heartbeat_deadline,
)
from backend.core.job_timeout_config import running_heartbeat_timeout_seconds
from backend.models.enums import JobStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job(**overrides) -> SimpleNamespace:
    base = dict(
        id=1,
        status=JobStatus.RUNNING.value,
        host_id="h1",
        execution_state=None,
        last_execution_heartbeat_at=None,
        last_patrol_heartbeat_at=None,
        started_at=_now() - timedelta(minutes=10),
        created_at=_now() - timedelta(minutes=10),
        updated_at=_now() - timedelta(seconds=30),  # 续租刚刷新——不得当存活
        pipeline_def=None,
        patrol_cycle_count=0,
        current_patrol_step=None,
        next_retry_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_non_running_job_has_no_deadline():
    assert _running_heartbeat_deadline(_job(status="COMPLETED")) is None


def test_executing_step_uses_execution_heartbeat():
    hb = _now() - timedelta(seconds=20)
    job = _job(execution_state="EXECUTING_STEP", last_execution_heartbeat_at=hb)
    deadline = _running_heartbeat_deadline(job, {"h1": _now()})
    assert deadline is not None
    assert deadline == hb + timedelta(seconds=running_heartbeat_timeout_seconds(job))
    assert deadline > _now()  # 心跳新鲜 → 不 stuck


def test_executing_step_without_heartbeat_anchors_at_dispatch_time():
    job = _job(execution_state="EXECUTING_STEP", last_execution_heartbeat_at=None)
    deadline = _running_heartbeat_deadline(job)
    assert deadline is not None
    assert deadline == job.started_at + timedelta(
        seconds=running_heartbeat_timeout_seconds(job),
    )


def test_waiting_slot_with_fresh_coordinator_is_not_stuck():
    """#993 核心回归：合法等待执行槽位 + coordinator 存活 → 不 stuck。

    旧实现按 updated_at（30s 前，续租刷新）判——不会误判；
    关键场景是 coordinator 心跳比 updated_at 旧而 job 在合法等待：
    这里把 updated_at 拨到 10 分钟前模拟「等待中无执行心跳」，coordinator
    30s 前仍新鲜 → 不得 stuck。
    """
    coord_hb = _now() - timedelta(seconds=30)
    job = _job(
        execution_state="WAITING_EXECUTION_SLOT",
        updated_at=_now() - timedelta(minutes=10),
    )
    deadline = _running_heartbeat_deadline(job, {"h1": coord_hb})
    assert deadline is not None
    assert deadline > _now()  # coordinator 新鲜 → deadline 在未来
    assert deadline == coord_hb + timedelta(
        seconds=_COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS,
    )


def test_waiting_state_with_dead_coordinator_ages_out():
    """coordinator 心跳超过 300s 超时 → stuck（recycler 同判）。"""
    stale_hb = _now() - timedelta(seconds=_COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS + 60)
    job = _job(execution_state="PATROL_SLEEP")
    deadline = _running_heartbeat_deadline(job, {"h1": stale_hb})
    assert deadline is not None
    assert deadline < _now()


def test_waiting_state_without_coordinator_row_anchors_at_dispatch():
    """coord 行缺失（含 map 未传）→ dispatch 锚 + coordinator 超时。"""
    job = _job(execution_state="WAITING_BARRIER")
    deadline = _running_heartbeat_deadline(job)  # map 缺省 None
    assert deadline is not None
    assert deadline == job.started_at + timedelta(
        seconds=_COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS,
    )


def test_unknown_execution_state_anchors_at_dispatch_with_graded_timeout():
    job = _job(execution_state=None)
    deadline = _running_heartbeat_deadline(job, {"h1": _now()})
    assert deadline is not None
    assert deadline == job.started_at + timedelta(
        seconds=running_heartbeat_timeout_seconds(job),
    )
