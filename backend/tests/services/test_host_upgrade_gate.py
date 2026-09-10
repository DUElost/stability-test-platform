"""统一升级门禁（#1249）：活跃 Job 拒绝 / abort 排空 / 维护窗口持有。

守的是 ADR-0021 D7/D8 的协议语义——UI 热更新、批量脚本、Ansible 三条入口
共用同一实现，因此这些断言失败即代表所有升级入口同时失去保护。
"""

from __future__ import annotations

import pytest

from backend.services.host_maintenance import (
    HostMaintenanceConflict,
    in_maintenance_window,
)
from backend.services.host_upgrade_gate import (
    HostAbortDrainTimeoutError,
    HostHasActiveJobsError,
    HostNotFoundError,
    begin_host_upgrade,
    end_host_upgrade,
)


def test_gate_acquires_and_releases_maintenance_window(db_session, sample_host):
    gate = begin_host_upgrade(db_session, sample_host.id, holder="test:acq")

    assert gate["holder"] == "test:acq"
    assert gate["active_jobs"] == []
    assert gate["expires_at"]
    db_session.refresh(sample_host)
    assert in_maintenance_window(sample_host.maintenance_until)

    end_host_upgrade(db_session, sample_host.id, "test:acq")
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)


def test_gate_rejects_active_jobs_without_abort(db_session, sample_host, sample_job_instance):
    with pytest.raises(HostHasActiveJobsError) as excinfo:
        begin_host_upgrade(db_session, sample_host.id, holder="test:reject")

    assert excinfo.value.code == "HOST_HAS_ACTIVE_JOBS"
    assert [j["id"] for j in excinfo.value.active_jobs] == [sample_job_instance.id]
    # 拒绝路径不得占用窗口——否则后续升级会被自己卡住
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)


def test_gate_conflicts_when_window_already_held(db_session, sample_host):
    begin_host_upgrade(db_session, sample_host.id, holder="test:first")

    with pytest.raises(HostMaintenanceConflict):
        begin_host_upgrade(db_session, sample_host.id, holder="test:second")

    end_host_upgrade(db_session, sample_host.id, "test:first")


def test_gate_aborts_pending_job_and_acquires_window(db_session, sample_host, sample_job_instance):
    gate = begin_host_upgrade(
        db_session,
        sample_host.id,
        holder="test:abort",
        abort_running_jobs=True,
        triggered_by="pytest",
        drain_timeout_seconds=5,
    )

    assert gate["aborted_summary"] is not None
    assert sample_job_instance.id in gate["aborted_summary"]["aborted_jobs"]
    db_session.refresh(sample_job_instance)
    assert sample_job_instance.status == "ABORTED"
    db_session.refresh(sample_host)
    assert in_maintenance_window(sample_host.maintenance_until)

    end_host_upgrade(db_session, sample_host.id, "test:abort")


def test_gate_drain_timeout_does_not_hold_window(db_session, sample_host, sample_running_job):
    with pytest.raises(HostAbortDrainTimeoutError) as excinfo:
        begin_host_upgrade(
            db_session,
            sample_host.id,
            holder="test:timeout",
            abort_running_jobs=True,
            drain_timeout_seconds=0.5,
        )

    assert sample_running_job.id in excinfo.value.lingering_jobs
    assert excinfo.value.abort_summary is not None
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)


def test_gate_unknown_host_raises_not_found(db_session):
    with pytest.raises(HostNotFoundError):
        begin_host_upgrade(db_session, "no-such-host", holder="test:missing")


def test_release_only_clears_matching_holder(db_session, sample_host):
    begin_host_upgrade(db_session, sample_host.id, holder="test:owner")

    end_host_upgrade(db_session, sample_host.id, "test:intruder")
    db_session.refresh(sample_host)
    assert in_maintenance_window(sample_host.maintenance_until)

    end_host_upgrade(db_session, sample_host.id, "test:owner")
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)
