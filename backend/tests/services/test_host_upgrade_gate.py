"""统一升级门禁（#1249）：活跃 Job 拒绝 / abort 排空 / 维护窗口持有。

守的是 ADR-0021 D7/D8 的协议语义——UI 热更新、批量脚本、Ansible 三条入口
共用同一实现，因此这些断言失败即代表所有升级入口同时失去保护。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.models.host import Host
from backend.services import host_upgrade_gate as gate_service

from backend.services.host_maintenance import (
    HostMaintenanceConflict,
    in_maintenance_window,
)
from backend.services.host_upgrade_gate import (
    HostAbortDrainTimeoutError,
    HostHasActiveJobsError,
    HostNotFoundError,
    HostRetiredError,
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


def test_window_is_committed_before_first_job_check(db_session, sample_host, monkeypatch):
    def check_inside_window(session, host_id):
        with Session(session.get_bind()) as observer:
            persisted = observer.get(Host, host_id)
            assert in_maintenance_window(persisted.maintenance_until)
            assert persisted.maintenance_holder == "test:ordered"
        return []

    monkeypatch.setattr(gate_service, "active_jobs_for_host", check_inside_window)
    gate = begin_host_upgrade(db_session, sample_host.id, holder="test:ordered")
    end_host_upgrade(db_session, sample_host.id, gate["holder"])


def test_conflict_does_not_check_or_abort_jobs(db_session, sample_host, monkeypatch):
    begin_host_upgrade(db_session, sample_host.id, holder="test:owner")
    monkeypatch.setattr(gate_service, "active_jobs_for_host", lambda *args: pytest.fail("checked before lock"))
    monkeypatch.setattr(gate_service, "abort_jobs_for_host", lambda *args, **kwargs: pytest.fail("aborted"))
    with pytest.raises(HostMaintenanceConflict):
        begin_host_upgrade(db_session, sample_host.id, holder="test:other", abort_running_jobs=True)
    db_session.refresh(sample_host)
    assert sample_host.maintenance_holder == "test:owner"
    end_host_upgrade(db_session, sample_host.id, "test:owner")


@pytest.mark.parametrize("failure_point", ["active_jobs_for_host", "abort_jobs_for_host", "wait_until_no_active_jobs"])
def test_errors_during_check_abort_or_drain_release_window(
    db_session, sample_host, sample_job_instance, monkeypatch, failure_point,
):
    def fail_while_held(*args, **kwargs):
        with Session(db_session.get_bind()) as observer:
            persisted = observer.get(Host, sample_host.id)
            assert persisted.maintenance_holder == "test:error"
            assert in_maintenance_window(persisted.maintenance_until)
        raise RuntimeError("injected gate failure")

    monkeypatch.setattr(gate_service, failure_point, fail_while_held)
    with pytest.raises(RuntimeError, match="injected gate failure"):
        begin_host_upgrade(db_session, sample_host.id, holder="test:error", abort_running_jobs=True)
    db_session.refresh(sample_host)
    assert sample_host.maintenance_until is None
    assert sample_host.maintenance_holder == ""


def test_failed_transaction_is_rolled_back_before_release(db_session, sample_host, monkeypatch):
    def broken_query(session, host_id):
        session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(gate_service, "active_jobs_for_host", broken_query)
    with pytest.raises(DBAPIError):
        begin_host_upgrade(db_session, sample_host.id, holder="test:database-error")
    db_session.refresh(sample_host)
    assert sample_host.maintenance_until is None


def test_failure_cleanup_does_not_release_new_holder(db_session, sample_host, monkeypatch):
    def take_over_then_fail(session, host_id):
        with Session(session.get_bind()) as other:
            other.execute(update(Host).where(Host.id == host_id).values(maintenance_holder="test:new"))
            other.commit()
        raise RuntimeError("ownership changed")

    monkeypatch.setattr(gate_service, "active_jobs_for_host", take_over_then_fail)
    with pytest.raises(RuntimeError, match="ownership changed"):
        begin_host_upgrade(db_session, sample_host.id, holder="test:old")
    db_session.refresh(sample_host)
    assert sample_host.maintenance_holder == "test:new"
    assert in_maintenance_window(sample_host.maintenance_until)
    end_host_upgrade(db_session, sample_host.id, "test:new")


def test_gate_rejects_retired_host_without_taking_window(db_session, sample_host):
    """ADR-0038 D5：退役主机拒绝执行/配置类动作（热更新/升级门禁/批量），
    且拒绝路径不得占用维护窗口（与活跃 Job 拒绝同一约定）。"""
    from datetime import datetime, timezone

    sample_host.retired_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(HostRetiredError) as excinfo:
        begin_host_upgrade(db_session, sample_host.id, holder="test:retired")

    assert excinfo.value.code == "HOST_RETIRED"
    db_session.refresh(sample_host)
    assert not in_maintenance_window(sample_host.maintenance_until)
