"""#8 — dispatcher device 派发校验测试.

校验项(短路返回第一个不通过原因):
  not_found / no_host / device_offline / host_offline / active_lease

- sync 入口:prepare_plan_run / complete_plan_run_dispatch (TOCTOU 兜底)
- async 入口:dispatch_plan
- 端点层:POST /api/v1/plans/{id}/run 返回结构化 400
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    DeviceStatus,
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
)
from backend.models.host import Device, Host
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.services.plan_dispatcher_core import PlanDispatchError
from backend.services.plan_dispatcher_sync import (
    AllocationError,
    _sync_allocate_devices,
    _sync_create_allocations,
    _validate_dispatch_devices_sync,
    complete_plan_run_dispatch,
    prepare_plan_run,
)
from backend.services.plan_run_abort import abort_plan_run


# ── Fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def dispatch_fixture(db_session):
    host = Host(
        id="h-disp-v",
        hostname="hdisp-v",
        status=HostStatus.ONLINE.value,
        ip="10.0.0.71",
        ssh_user="root",
        ssh_port=22,
        last_heartbeat=datetime.now(timezone.utc),
    )
    device = Device(
        serial="S-disp-v",
        host_id="h-disp-v",
        status=DeviceStatus.ONLINE.value,
    )
    script = Script(
        name="check_device",
        script_type="python",
        version="1.0.0",
        nfs_path="/s/check_device.py",
        content_sha256="abc",
        default_params={"timeout": 30},
    )
    plan = Plan(name="dispatch-validation")
    db_session.add_all([host, device, script, plan])
    db_session.commit()

    step = PlanStep(
        plan_id=plan.id,
        step_key="init_check",
        script_name="check_device",
        script_version="1.0.0",
        stage="init",
        sort_order=0,
        timeout_seconds=30,
        retry=0,
    )
    db_session.add(step)
    db_session.commit()
    return {"host": host, "device": device, "plan": plan}


def _attach_active_lease(
    db_session, device_id: int, host_id: str, *, job_id: int | None = None
) -> DeviceLease:
    now = datetime.now(timezone.utc)
    lease = DeviceLease(
        device_id=device_id,
        job_id=job_id,
        host_id=host_id,
        lease_type=LeaseType.JOB.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"tok-{device_id}",
        lease_generation=1,
        agent_instance_id="pytest-agent",
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(seconds=600),
    )
    db_session.add(lease)
    db_session.commit()
    return lease


# ── Pure validation function ───────────────────────────────────────────


class TestValidateDispatchDevicesSync:
    def test_passes_for_clean_online_device(self, db_session, dispatch_fixture):
        _validate_dispatch_devices_sync(
            db_session, [dispatch_fixture["device"].id]
        )

    def test_empty_device_ids_raises(self, db_session):
        with pytest.raises(PlanDispatchError, match="must not be empty"):
            _validate_dispatch_devices_sync(db_session, [])

    def test_unknown_device_id(self, db_session, dispatch_fixture):
        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(db_session, [99999])
        d = exc.value.detail()
        assert d["code"] == "DEVICES_UNAVAILABLE"
        assert d["unavailable_devices"] == [{"id": 99999, "reason": "not_found"}]

    def test_device_without_host(self, db_session, dispatch_fixture):
        dispatch_fixture["device"].host_id = None
        db_session.commit()

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(
                db_session, [dispatch_fixture["device"].id]
            )
        entries = exc.value.detail()["unavailable_devices"]
        assert len(entries) == 1
        assert entries[0]["reason"] == "no_host"

    def test_device_offline(self, db_session, dispatch_fixture):
        dispatch_fixture["device"].status = DeviceStatus.OFFLINE.value
        db_session.commit()

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(
                db_session, [dispatch_fixture["device"].id]
            )
        entries = exc.value.detail()["unavailable_devices"]
        assert entries[0]["reason"] == "device_offline"
        assert entries[0]["device_status"] == "OFFLINE"

    def test_device_error(self, db_session, dispatch_fixture):
        """issue #52: device.status=ERROR（如 adb unauthorized）应与 OFFLINE 一样被排除派发。"""
        dispatch_fixture["device"].status = DeviceStatus.ERROR.value
        db_session.commit()

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(
                db_session, [dispatch_fixture["device"].id]
            )
        entries = exc.value.detail()["unavailable_devices"]
        assert entries[0]["reason"] == "device_error"
        assert entries[0]["device_status"] == "ERROR"

    def test_host_offline(self, db_session, dispatch_fixture):
        dispatch_fixture["host"].status = HostStatus.OFFLINE.value
        db_session.commit()

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(
                db_session, [dispatch_fixture["device"].id]
            )
        entries = exc.value.detail()["unavailable_devices"]
        assert entries[0]["reason"] == "host_offline"
        assert entries[0]["host_status"] == "OFFLINE"

    def test_active_lease_rejects(self, db_session, dispatch_fixture):
        dev = dispatch_fixture["device"]
        _attach_active_lease(db_session, dev.id, dispatch_fixture["host"].id)

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(db_session, [dev.id])
        entries = exc.value.detail()["unavailable_devices"]
        assert entries[0]["reason"] == "active_lease"

    def test_busy_status_alone_does_not_reject(self, db_session, dispatch_fixture):
        """device.status=BUSY 是软指示;只要无 ACTIVE lease 就应放行 — lease 才是真值。"""
        dispatch_fixture["device"].status = DeviceStatus.BUSY.value
        db_session.commit()

        _validate_dispatch_devices_sync(
            db_session, [dispatch_fixture["device"].id]
        )

    def test_aggregates_multiple_unavailable_devices(
        self, db_session, dispatch_fixture
    ):
        """每台设备各自一份拒因,前端可逐项展示。"""
        ok_dev = dispatch_fixture["device"]
        # 第二台:OFFLINE 设备
        bad_dev = Device(
            serial="S-bad",
            host_id=dispatch_fixture["host"].id,
            status=DeviceStatus.OFFLINE.value,
        )
        db_session.add(bad_dev)
        db_session.commit()

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(
                db_session, [ok_dev.id, bad_dev.id, 99999]
            )
        entries = {e["id"]: e for e in exc.value.detail()["unavailable_devices"]}
        assert set(entries.keys()) == {bad_dev.id, 99999}
        assert entries[bad_dev.id]["reason"] == "device_offline"
        assert entries[99999]["reason"] == "not_found"

    def test_priority_not_found_before_other_reasons(
        self, db_session, dispatch_fixture
    ):
        """同一台设备只返回第一个匹配的拒因;not_found 最先。"""
        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(db_session, [99999])
        assert exc.value.detail()["unavailable_devices"][0]["reason"] == "not_found"

    def test_priority_device_offline_before_active_lease(
        self, db_session, dispatch_fixture
    ):
        """device_offline 优先于 active_lease — 离线优先报告,运维语义更强。"""
        dev = dispatch_fixture["device"]
        dev.status = DeviceStatus.OFFLINE.value
        db_session.commit()
        _attach_active_lease(db_session, dev.id, dispatch_fixture["host"].id)

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(db_session, [dev.id])
        assert exc.value.detail()["unavailable_devices"][0]["reason"] == "device_offline"

    def test_pending_job_without_lease_rejects_as_active_job(
        self, db_session, dispatch_fixture
    ):
        """B4:另一 PlanRun 的 PENDING job(尚无 lease)已占 uq_job_active_per_device。
        旧校验只看 lease → 放行 → 物化阶段撞唯一索引 → PlanRun 悬挂。
        新校验按索引口径拦截,返回 active_job。
        """
        from backend.models.job import JobInstance
        from backend.models.plan_run import PlanRun

        dev = dispatch_fixture["device"]
        other_run = PlanRun(
            plan_id=dispatch_fixture["plan"].id,
            status="RUNNING",
            failure_threshold=0.1,
            plan_snapshot={"plan_id": dispatch_fixture["plan"].id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(other_run)
        db_session.flush()
        db_session.add(JobInstance(
            plan_run_id=other_run.id,
            plan_id=dispatch_fixture["plan"].id,
            device_id=dev.id,
            host_id=dispatch_fixture["host"].id,
            status=JobStatus.PENDING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
        db_session.commit()

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(db_session, [dev.id])
        entry = exc.value.detail()["unavailable_devices"][0]
        assert entry["reason"] == "active_job"
        assert entry["job_status"] == JobStatus.PENDING.value

    def test_unknown_job_rejects_as_active_job(self, db_session, dispatch_fixture):
        """UNKNOWN job 也占唯一索引(设备隔离中),同样应拦截派发。"""
        from backend.models.job import JobInstance
        from backend.models.plan_run import PlanRun

        dev = dispatch_fixture["device"]
        other_run = PlanRun(
            plan_id=dispatch_fixture["plan"].id,
            status="RUNNING",
            failure_threshold=0.1,
            plan_snapshot={"plan_id": dispatch_fixture["plan"].id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(other_run)
        db_session.flush()
        db_session.add(JobInstance(
            plan_run_id=other_run.id,
            plan_id=dispatch_fixture["plan"].id,
            device_id=dev.id,
            host_id=dispatch_fixture["host"].id,
            status=JobStatus.UNKNOWN.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
        db_session.commit()

        with pytest.raises(PlanDispatchError) as exc:
            _validate_dispatch_devices_sync(db_session, [dev.id])
        assert exc.value.detail()["unavailable_devices"][0]["reason"] == "active_job"

    def test_terminal_job_does_not_reject(self, db_session, dispatch_fixture):
        """COMPLETED/FAILED job 不占唯一索引 → 同设备可重新派发(回归防护)。"""
        from backend.models.job import JobInstance
        from backend.models.plan_run import PlanRun

        dev = dispatch_fixture["device"]
        other_run = PlanRun(
            plan_id=dispatch_fixture["plan"].id,
            status="SUCCESS",
            failure_threshold=0.1,
            plan_snapshot={"plan_id": dispatch_fixture["plan"].id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(other_run)
        db_session.flush()
        db_session.add(JobInstance(
            plan_run_id=other_run.id,
            plan_id=dispatch_fixture["plan"].id,
            device_id=dev.id,
            host_id=dispatch_fixture["host"].id,
            status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
        db_session.commit()

        # Should not raise — device is free for a new dispatch.
        _validate_dispatch_devices_sync(db_session, [dev.id])


# ── prepare_plan_run / complete_plan_run_dispatch 集成 ──────────────────


class TestPrepareAndCompleteIntegration:
    def test_prepare_rejects_active_lease_before_creating_plan_run(
        self, db_session, dispatch_fixture
    ):
        """关键不变量:校验失败时 PlanRun 行不能创建,否则前端会看到鬼魂 PlanRun。"""
        from backend.models.plan_run import PlanRun
        dev = dispatch_fixture["device"]
        _attach_active_lease(db_session, dev.id, dispatch_fixture["host"].id)

        baseline = db_session.query(PlanRun).count()
        with pytest.raises(PlanDispatchError) as exc:
            prepare_plan_run(
                plan_id=dispatch_fixture["plan"].id,
                device_ids=[dev.id],
                triggered_by="pytest",
                db=db_session,
                run_type="MANUAL",
            )
        assert exc.value.detail()["code"] == "DEVICES_UNAVAILABLE"
        assert db_session.query(PlanRun).count() == baseline

    def test_complete_falls_to_failed_when_lease_arrives_during_window(
        self, db_session, dispatch_fixture
    ):
        """TOCTOU:prepare 通过、device 在 prepare→complete 窗口被占。complete 不能
        创建 Job(会卡死),应落 FAILED + 审计,与 ADR-0023 missing_scripts 同路径。
        """
        from backend.core.audit import AuditLog
        from backend.models.job import JobInstance
        from backend.models.plan_run import PlanRun

        dev = dispatch_fixture["device"]

        pr = prepare_plan_run(
            plan_id=dispatch_fixture["plan"].id,
            device_ids=[dev.id],
            triggered_by="pytest",
            db=db_session,
            run_type="MANUAL",
        )
        assert pr.status == "RUNNING"

        # 模拟 race:prepare 通过后,另一处给 device 上 ACTIVE lease
        _attach_active_lease(db_session, dev.id, dispatch_fixture["host"].id)

        complete_plan_run_dispatch(pr.id, db=db_session)
        db_session.refresh(pr)

        assert pr.status == "FAILED"
        assert pr.result_summary["dispatch_failed"] is True
        assert pr.result_summary["unavailable_devices"][0]["reason"] == "active_lease"
        # complete 不应创建任何 Job
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count() == 0
        # 审计行写入
        from sqlalchemy import select
        audit = db_session.execute(
            select(AuditLog)
            .where(AuditLog.action == "plan_dispatch_failed")
            .where(AuditLog.resource_id == str(pr.id))
        ).scalars().first()
        assert audit is not None
        assert audit.details["reason"] == "devices_unavailable_at_dispatch"

    def test_complete_succeeds_for_clean_device(self, db_session, dispatch_fixture):
        """Happy path 回归:无变动时 complete 正常创建 Job。"""
        from backend.models.job import JobInstance

        dev = dispatch_fixture["device"]
        pr = prepare_plan_run(
            plan_id=dispatch_fixture["plan"].id,
            device_ids=[dev.id],
            triggered_by="pytest",
            db=db_session,
            run_type="MANUAL",
        )
        complete_plan_run_dispatch(pr.id, db=db_session)
        db_session.refresh(pr)

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).all()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.PENDING.value
        assert jobs[0].device_id == dev.id
        assert pr.status == "RUNNING"

    @pytest.mark.parametrize(
        "terminal_status",
        ["SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED"],
    )
    def test_complete_does_not_materialize_jobs_for_terminal_plan_run(
        self, db_session, dispatch_fixture, terminal_status,
    ):
        """precheck 完成与 Job 物化之间若 PlanRun 已终态，Stage 2 必须只读跳过。"""
        from backend.models.job import JobInstance

        pr = prepare_plan_run(
            plan_id=dispatch_fixture["plan"].id,
            device_ids=[dispatch_fixture["device"].id],
            triggered_by="pytest",
            db=db_session,
            run_type="MANUAL",
        )
        pr.status = terminal_status
        db_session.commit()

        complete_plan_run_dispatch(pr.id, db=db_session)

        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count() == 0

    def test_complete_does_not_materialize_jobs_after_abort_requested(
        self, db_session, dispatch_fixture,
    ):
        """abort 与 dispatch gate 竞态时，abort_requested 必须阻止创建 PENDING Job。"""
        from backend.models.job import JobInstance

        pr = prepare_plan_run(
            plan_id=dispatch_fixture["plan"].id,
            device_ids=[dispatch_fixture["device"].id],
            triggered_by="pytest",
            db=db_session,
            run_type="MANUAL",
        )
        pr.run_context = {
            **(pr.run_context or {}),
            "abort_requested": {
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": "test_abort_race",
                "triggered_by": "pytest",
            },
        }
        db_session.commit()

        complete_plan_run_dispatch(pr.id, db=db_session)

        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count() == 0

    def test_postgresql_abort_and_dispatch_gate_leave_no_runnable_job(
        self, db_session, dispatch_fixture,
    ):
        from backend.models.job import JobInstance
        from backend.models.plan_run import PlanRun

        pr = prepare_plan_run(
            plan_id=dispatch_fixture["plan"].id,
            device_ids=[dispatch_fixture["device"].id],
            triggered_by="pytest",
            db=db_session,
            run_type="MANUAL",
        )
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def dispatch():
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                complete_plan_run_dispatch(pr.id, db=db)
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()

        def abort():
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                abort_plan_run(pr.id, db=db, reason="dispatch_race_abort")
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()

        with patch(
            "backend.services.plan_run_abort.should_trigger_dedup",
            return_value=False,
        ), patch(
            "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
        ), patch("backend.services.plan_run_abort.schedule_emit"):
            threads = [
                threading.Thread(target=dispatch),
                threading.Thread(target=abort),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        db_session.expire_all()
        persisted = db_session.get(PlanRun, pr.id)
        assert persisted.status == "FAILED"
        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id,
        ).all()
        assert len(jobs) <= 1
        assert all(job.status == JobStatus.ABORTED.value for job in jobs)

    def test_complete_fails_when_device_loses_host_at_complete(
        self, db_session, dispatch_fixture, monkeypatch,
    ):
        """Race: device host_id cleared after prepare → complete must FAILED, not warn-only."""
        from backend.models.job import JobInstance
        from unittest.mock import patch

        dev = dispatch_fixture["device"]
        pr = prepare_plan_run(
            plan_id=dispatch_fixture["plan"].id,
            device_ids=[dev.id],
            triggered_by="pytest",
            db=db_session,
            run_type="MANUAL",
        )
        # Simulate TOCTOU race: validate passed at prepare, host_id cleared before host map read.
        dev.host_id = None
        db_session.commit()
        with patch(
            "backend.services.plan_dispatcher_sync._validate_dispatch_devices_sync",
            lambda _db, _ids: None,
        ):
            complete_plan_run_dispatch(pr.id, db=db_session)
        db_session.refresh(pr)

        assert pr.status == "FAILED"
        assert pr.result_summary["dispatch_failed"] is True
        assert pr.result_summary["reason"] == "devices_without_host"
        assert dev.id in pr.result_summary["orphan_device_ids"]
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count() == 0

    def test_complete_fails_when_wifi_pool_unavailable(
        self, db_session, dispatch_fixture
    ):
        """connect_wifi plan must fail dispatch when no WiFi pool exists."""
        from backend.models.job import JobInstance
        from backend.models.plan_run import PlanRun

        wifi_script = Script(
            name="connect_wifi",
            script_type="shell",
            version="1.0.0",
            nfs_path="/s/connect_wifi.sh",
            content_sha256="wifi",
            default_params={"ssid": "", "password": ""},
        )
        db_session.add(wifi_script)
        db_session.add(
            PlanStep(
                plan_id=dispatch_fixture["plan"].id,
                step_key="wifi_connect",
                script_name="connect_wifi",
                script_version="1.0.0",
                stage="init",
                sort_order=1,
                timeout_seconds=60,
                retry=0,
            )
        )
        db_session.commit()

        dev = dispatch_fixture["device"]
        pr = prepare_plan_run(
            plan_id=dispatch_fixture["plan"].id,
            device_ids=[dev.id],
            triggered_by="pytest",
            db=db_session,
            run_type="MANUAL",
        )
        complete_plan_run_dispatch(pr.id, db=db_session)
        db_session.refresh(pr)

        assert pr.status == "FAILED"
        assert pr.result_summary["dispatch_failed"] is True
        assert pr.result_summary["reason"] == "wifi_allocation_failed"
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count() == 0

    def test_postgresql_wifi_capacity_one_serializes_allocations(
        self, db_session, dispatch_fixture,
    ):
        from backend.models.job import JobInstance
        from backend.models.plan_run import PlanRun
        from backend.models.resource_pool import (
            ResourceAllocation,
            ResourcePool,
        )

        second_device = Device(
            serial="S-disp-v-wifi-2",
            host_id=dispatch_fixture["host"].id,
            status=DeviceStatus.ONLINE.value,
        )
        pool = ResourcePool(
            name="single-slot",
            resource_type="wifi",
            config={"ssid": "lab", "password": "secret"},
            max_concurrent_devices=1,
            is_active=True,
        )
        db_session.add_all([second_device, pool])
        db_session.flush()

        jobs = []
        for device in (dispatch_fixture["device"], second_device):
            run = PlanRun(
                plan_id=dispatch_fixture["plan"].id,
                status="RUNNING",
                failure_threshold=0.1,
                plan_snapshot={"plan_id": dispatch_fixture["plan"].id},
                run_type="MANUAL",
                triggered_by="pytest",
            )
            db_session.add(run)
            db_session.flush()
            job = JobInstance(
                plan_run_id=run.id,
                plan_id=dispatch_fixture["plan"].id,
                device_id=device.id,
                host_id=dispatch_fixture["host"].id,
                status=JobStatus.PENDING.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
            db_session.add(job)
            db_session.flush()
            jobs.append(job)
        db_session.commit()

        barrier = threading.Barrier(2)
        results: list[str] = []
        errors: list[Exception] = []

        def allocate(job_id: int, device_id: int):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                assignments = _sync_allocate_devices(db, [device_id])
                _sync_create_allocations(
                    db, assignments, {job_id: device_id},
                )
                db.commit()
                results.append("allocated")
            except AllocationError:
                db.rollback()
                results.append("no_capacity")
            except Exception as exc:
                db.rollback()
                errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(
                target=allocate, args=(job.id, job.device_id),
            )
            for job in jobs
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(results) == ["allocated", "no_capacity"]
        db_session.expire_all()
        assert db_session.query(ResourceAllocation).filter(
            ResourceAllocation.resource_pool_id == pool.id,
        ).count() == 1


# ── PlanDispatchError.detail 结构 ──────────────────────────────────────


class TestPlanDispatchErrorUnavailableDevices:
    def test_detail_with_unavailable_devices(self):
        exc = PlanDispatchError(
            "rejected",
            unavailable_devices=[
                {"id": 1, "reason": "not_found"},
                {"id": 2, "reason": "device_offline", "device_status": "OFFLINE"},
            ],
        )
        d = exc.detail()
        assert d["code"] == "DEVICES_UNAVAILABLE"
        assert len(d["unavailable_devices"]) == 2
        assert d["unavailable_devices"][0]["reason"] == "not_found"

    def test_missing_scripts_takes_precedence_over_unavailable_devices(self):
        """两类失败同时存在时,missing_scripts 先报(脚本不齐则设备无意义)。"""
        exc = PlanDispatchError(
            "weird",
            missing_scripts=["a:1.0.0"],
            unavailable_devices=[{"id": 1, "reason": "not_found"}],
        )
        d = exc.detail()
        assert d["code"] == "INVALID_SCRIPT_REFS"


# ── 端点层 400 结构 ──────────────────────────────────────────────────


class TestRunPlanEndpointStructured400:
    def test_run_plan_returns_400_with_unavailable_devices_detail(
        self, client, auth_headers, db_session, dispatch_fixture
    ):
        dev = dispatch_fixture["device"]
        dev.status = DeviceStatus.OFFLINE.value
        db_session.commit()

        resp = client.post(
            f"/api/v1/plans/{dispatch_fixture['plan'].id}/run",
            json={"device_ids": [dev.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "DEVICES_UNAVAILABLE"
        assert detail["unavailable_devices"][0]["id"] == dev.id
        assert detail["unavailable_devices"][0]["reason"] == "device_offline"

    def test_run_plan_returns_400_for_unknown_device(
        self, client, auth_headers, db_session, dispatch_fixture
    ):
        resp = client.post(
            f"/api/v1/plans/{dispatch_fixture['plan'].id}/run",
            json={"device_ids": [9999999]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "DEVICES_UNAVAILABLE"
        assert detail["unavailable_devices"][0]["reason"] == "not_found"

    def test_run_plan_returns_400_with_active_lease(
        self, client, auth_headers, db_session, dispatch_fixture
    ):
        dev = dispatch_fixture["device"]
        _attach_active_lease(db_session, dev.id, dispatch_fixture["host"].id)

        resp = client.post(
            f"/api/v1/plans/{dispatch_fixture['plan'].id}/run",
            json={"device_ids": [dev.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "DEVICES_UNAVAILABLE"
        assert detail["unavailable_devices"][0]["reason"] == "active_lease"

    def test_preview_does_not_reject_offline_device(
        self, client, auth_headers, db_session, dispatch_fixture
    ):
        """preview 是扇出预演,不强校验设备 — 只验证 run 走严格校验。"""
        dev = dispatch_fixture["device"]
        dev.status = DeviceStatus.OFFLINE.value
        db_session.commit()

        resp = client.post(
            f"/api/v1/plans/{dispatch_fixture['plan'].id}/run/preview",
            json={"device_ids": [dev.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["device_count"] == 1
