"""阶段 B2-3 验收测试：PlanRun 表格真分页与并集筛选、独立 Minimap（含 1,000 Job ≤384 KiB 门禁）与单 Job 详情（#83 / #4117）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    DeviceStatus,
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
    PlanRunStatus,
)
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import plan_run_devices as prd_svc


def _attach_readonly_probe(db: Session):
    dml_stmts: list[str] = []
    commits: list[int] = []
    engine = db.get_bind()

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        upper = statement.lstrip().upper()
        if upper.startswith(("INSERT", "UPDATE", "DELETE")):
            dml_stmts.append(statement)

    def _before_commit(session):
        commits.append(1)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(Session, "before_commit", _before_commit)

    def _cleanup():
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)
        event.remove(Session, "before_commit", _before_commit)

    return dml_stmts, commits, _cleanup


def _seed_plan_and_run(db: Session, *, suffix: str) -> tuple[Plan, PlanRun]:
    plan = Plan(name=f"B23-Plan-{suffix}")
    db.add(plan)
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status=PlanRunStatus.RUNNING.value,
        run_type="MANUAL",
        triggered_by="pytest",
        plan_snapshot={
            "steps": [
                {
                    "stage": "init",
                    "step_key": "init_flash",
                    "script_name": "flash_device",
                    "script_version": "1.0.0",
                },
                {
                    "stage": "patrol",
                    "step_key": "patrol_monkey",
                    "script_name": "monkey_runner",
                    "script_version": "2.3.0",
                },
            ]
        },
    )
    db.add(run)
    db.flush()
    return plan, run


def _make_host(
    db: Session,
    *,
    host_id: str,
    name: str,
    ip: str,
    status: str = HostStatus.ONLINE.value,
    retired_at: datetime | None = None,
) -> Host:
    now = datetime.now(timezone.utc)
    host = Host(
        id=host_id,
        hostname=name,
        name=name,
        ip=ip,
        ip_address=ip,
        status=status,
        last_heartbeat=now,
        retired_at=retired_at,
    )
    db.add(host)
    db.flush()
    return host


class TestPlanRunMinimapAndPagination:
    """GET /devices/minimap 7 字段全集与 GET /devices 真分页 + 并集筛选。"""

    def test_minimap_7_fields_retired_host_map_stuck_ids_and_readonly(
        self, client, db_session, auth_headers
    ):
        suffix = uuid4().hex[:6]
        now = datetime.now(timezone.utc)
        plan, run = _seed_plan_and_run(db_session, suffix=suffix)

        h_online = _make_host(
            db_session,
            host_id=f"b23-h1-{suffix}",
            name=f"ActiveNode-{suffix}",
            ip=f"10.61.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status=HostStatus.ONLINE.value,
        )
        # 已退役主机仍需在 Minimap hosts 映射中保留显示名
        h_retired = _make_host(
            db_session,
            host_id=f"b23-h2-{suffix}",
            name=f"RetiredNode-{suffix}",
            ip=f"10.61.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status=HostStatus.OFFLINE.value,
            retired_at=now - timedelta(days=3),
        )

        d1 = Device(
            serial=f"SN-{suffix}-001",
            host_id=h_online.id,
            model="MLD_NX3",
            status=DeviceStatus.BUSY.value,
            adb_state="device",
            adb_connected=True,
        )
        d2 = Device(
            serial=f"SN-{suffix}-002",
            host_id=h_retired.id,
            model="MLD_NX3",
            status=DeviceStatus.OFFLINE.value,
            adb_state="offline",
            adb_connected=False,
        )
        d3 = Device(
            serial=f"SN-{suffix}-003",
            host_id=None,
            model="MLD_NX2",
            status=DeviceStatus.ONLINE.value,
            adb_state="device",
            adb_connected=True,
        )
        db_session.add_all([d1, d2, d3])
        db_session.flush()

        # j1: RUNNING 且心跳超期（stuck_job_ids 命中），带 4 条 watcher 异常和 disarm_results
        j1 = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=d1.id,
            host_id=h_online.id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {}},
            execution_state="EXECUTING_STEP",
            last_execution_heartbeat_at=now - timedelta(hours=2),
            started_at=now - timedelta(hours=2),
            log_signal_count=4,
            disarm_results={"monkey": {"result": "verified", "evidence": "X" * 2048}},
        )
        # j2: UNKNOWN on retired host（host_offline）
        j2 = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=d2.id,
            host_id=h_retired.id,
            status=JobStatus.UNKNOWN.value,
            pipeline_def={"lifecycle": {}},
            log_signal_count=0,
        )
        # j3: RUNNING + next_retry_at > now (backoff) + 无主机桶
        j3 = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=d3.id,
            host_id=None,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {}},
            next_retry_at=now + timedelta(minutes=10),
            last_execution_heartbeat_at=now,
            execution_state="EXECUTING_STEP",
            log_signal_count=2,
        )
        db_session.add_all([j1, j2, j3])
        db_session.commit()

        dml, commits, cleanup = _attach_readonly_probe(db_session)
        try:
            resp = client.get(
                f"/api/v1/plan-runs/{run.id}/devices/minimap",
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["plan_run_id"] == run.id
            assert data["facets"]["total"] == 3
            assert data["facets"]["by_status"]["running"] == 1
            assert data["facets"]["by_status"]["unknown"] == 1
            assert data["facets"]["by_status"]["backoff"] == 1
            assert data["facets"]["by_link_status"]["online"] == 2
            assert data["facets"]["by_link_status"]["host_offline"] == 1
            assert data["facets"]["by_host"][h_online.id] == 1
            assert data["facets"]["by_host"][h_retired.id] == 1
            assert data["facets"]["by_host"]["unassigned"] == 1

            # 主机显示名映射包含退役主机与无主机桶
            assert data["hosts"][h_online.id] == h_online.name
            assert data["hosts"][h_retired.id] == h_retired.name
            assert data["hosts"]["unassigned"] == "未分配"

            # 全局 stuck_job_ids
            assert data["stuck_job_ids"] == [j1.id]

            # 严格 7 字段，不含 ui_status 或 disarm_results
            expected_keys = {
                "job_id",
                "device_id",
                "device_serial",
                "host_id",
                "job_exec_status",
                "device_link_status",
                "log_signal_count",
            }
            assert len(data["devices"]) == 3
            for item in data["devices"]:
                assert set(item.keys()) == expected_keys
                assert isinstance(item["log_signal_count"], int)
                assert not isinstance(item["log_signal_count"], bool)
            by_jid = {item["job_id"]: item for item in data["devices"]}
            assert by_jid[j1.id]["log_signal_count"] == 4
            assert by_jid[j3.id]["job_exec_status"] == "backoff"

            assert dml == []
            assert commits == []
        finally:
            cleanup()

    def test_devices_table_true_pagination_union_filters_and_legacy_compat(
        self, client, db_session, auth_headers, monkeypatch
    ):
        suffix = uuid4().hex[:6]
        now = datetime.now(timezone.utc)
        plan, run = _seed_plan_and_run(db_session, suffix=suffix)

        h_on = _make_host(
            db_session,
            host_id=f"b23p-on-{suffix}",
            name=f"OnNode-{suffix}",
            ip=f"10.62.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status=HostStatus.ONLINE.value,
        )
        h_off = _make_host(
            db_session,
            host_id=f"b23p-off-{suffix}",
            name=f"OffNode-{suffix}",
            ip=f"10.62.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status=HostStatus.OFFLINE.value,
        )

        # 创建 5 台设备覆盖 running / unknown / backoff / completed / failed + 多种 link_status
        specs = [
            (f"SER-{suffix}-A1", h_on.id, DeviceStatus.BUSY.value, "device", True, JobStatus.RUNNING.value, None, 5),
            (f"SER-{suffix}-B2", h_off.id, DeviceStatus.OFFLINE.value, "offline", False, JobStatus.UNKNOWN.value, None, 1),
            (f"SER-{suffix}-C3", h_on.id, DeviceStatus.ONLINE.value, "unauthorized", False, JobStatus.RUNNING.value, now + timedelta(minutes=5), 9),
            (f"SER-{suffix}-D4", h_on.id, DeviceStatus.OFFLINE.value, "device", False, JobStatus.COMPLETED.value, None, 0),
            (f"SER-{suffix}-E5", h_on.id, DeviceStatus.ONLINE.value, "device", True, JobStatus.FAILED.value, None, 3),
        ]
        jobs = []
        for serial, hid, dst, adb_st, adb_conn, jst, next_retry, sig_cnt in specs:
            dev = Device(
                serial=serial,
                host_id=hid,
                model="MLD_NX3",
                status=dst,
                adb_state=adb_st,
                adb_connected=adb_conn,
            )
            db_session.add(dev)
            db_session.flush()
            job = JobInstance(
                plan_run_id=run.id,
                plan_id=plan.id,
                device_id=dev.id,
                host_id=hid,
                status=jst,
                pipeline_def={"lifecycle": {}},
                next_retry_at=next_retry,
                log_signal_count=sig_cnt,
            )
            db_session.add(job)
            db_session.flush()
            jobs.append(job)
        db_session.commit()

        # 1) 旧路径向后兼容：不传 skip/limit 返回全部 5 条，total=5, filtered_total=5
        r_legacy = client.get(f"/api/v1/plan-runs/{run.id}/devices", headers=auth_headers)
        assert r_legacy.status_code == 200
        leg = r_legacy.json()["data"]
        assert leg["total"] == 5
        assert leg["filtered_total"] == 5
        assert len(leg["devices"]) == 5

        # 2) 并集 KPI 筛选：lost_or_backoff == unknown OR backoff (j2 + j3)
        r_union_st = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"status": "lost_or_backoff"},
            headers=auth_headers,
        )
        assert r_union_st.status_code == 200
        u_st = r_union_st.json()["data"]
        assert u_st["total"] == 5  # total 保持 PlanRun 全集数，绝不被 filtered_total 覆盖
        assert u_st["filtered_total"] == 2
        assert {d["job_id"] for d in u_st["devices"]} == {jobs[1].id, jobs[2].id}

        # 逗号并集同样等价
        r_comma_st = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"status": "unknown,backoff"},
            headers=auth_headers,
        )
        assert {d["job_id"] for d in r_comma_st.json()["data"]["devices"]} == {jobs[1].id, jobs[2].id}

        # 3) 连接异常并集：link_status=abnormal == device_link_status != online (j2 host_offline, j3 adb_error, j4 offline)
        r_abn = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"link_status": "abnormal"},
            headers=auth_headers,
        )
        abn = r_abn.json()["data"]
        assert abn["total"] == 5
        assert abn["filtered_total"] == 3
        assert {d["job_id"] for d in abn["devices"]} == {jobs[1].id, jobs[2].id, jobs[3].id}

        # 4) 真分页 + 排序 + 只对当前页构造重 DeviceMatrixItem（监控 build_device_matrix_item 调用次数）
        hydrated_ids: list[int] = []
        orig_builder = prd_svc.build_device_matrix_item

        def _spy_builder(j, *args, **kwargs):
            hydrated_ids.append(int(j.id))
            return orig_builder(j, *args, **kwargs)

        monkeypatch.setattr(prd_svc, "build_device_matrix_item", _spy_builder)

        r_page = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"sort_by": "log_signal_count", "sort_dir": "desc", "skip": 0, "limit": 2},
            headers=auth_headers,
        )
        assert r_page.status_code == 200
        pg = r_page.json()["data"]
        assert pg["total"] == 5
        assert pg["filtered_total"] == 5
        assert [d["job_id"] for d in pg["devices"]] == [jobs[2].id, jobs[0].id]
        # 严格只对当页 2 条构造重对象，而非全集 5 条
        assert len(hydrated_ids) == 2

        # 5) q 串号过滤 + 非法 sort_by 返回 422
        r_q = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"q": f"{suffix}-c3"},
            headers=auth_headers,
        )
        assert [d["job_id"] for d in r_q.json()["data"]["devices"]] == [jobs[2].id]
        assert client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"sort_by": "invalid_col"},
            headers=auth_headers,
        ).status_code == 422

    def test_minimap_1000_jobs_fixture_under_384kib_raw_utf8(
        self, client, db_session, auth_headers
    ):
        """契约 §7.3 门禁：声明的 1,000 Job 夹具下 Minimap 原始 UTF-8 JSON ≤ 384 KiB（另记 gzip 字节）。"""
        suffix = uuid4().hex[:6]
        now = datetime.now(timezone.utc)
        plan, run = _seed_plan_and_run(db_session, suffix=suffix)

        # 40 台 Agent host × 25 台物理设备 = 1,000 Job
        hosts = []
        for h_idx in range(40):
            hid = f"h1k-{suffix}-{h_idx:02d}"
            hosts.append(
                Host(
                    id=hid,
                    hostname=f"rack-{h_idx // 8}-node-{h_idx:02d}-{suffix}",
                    name=f"rack-{h_idx // 8}-node-{h_idx:02d}-{suffix}",
                    ip=f"10.65.{h_idx // 10}.{10 + (h_idx % 10) * 10 + int(suffix[:2], 16) % 9}",
                    ip_address=f"10.65.{h_idx // 10}.{10 + (h_idx % 10) * 10 + int(suffix[:2], 16) % 9}",
                    status=HostStatus.ONLINE.value,
                    last_heartbeat=now,
                )
            )
        db_session.add_all(hosts)
        db_session.flush()

        devices = []
        for i in range(1000):
            host = hosts[i % 40]
            devices.append(
                Device(
                    serial=f"MTK{suffix.upper()}{i:06d}",
                    host_id=host.id,
                    model="MLD_NX3",
                    status=DeviceStatus.BUSY.value,
                    adb_state="device",
                    adb_connected=True,
                )
            )
        db_session.add_all(devices)
        db_session.flush()

        jobs = [
            JobInstance(
                plan_run_id=run.id,
                plan_id=plan.id,
                device_id=dev.id,
                host_id=dev.host_id,
                status=JobStatus.RUNNING.value if idx % 10 else JobStatus.COMPLETED.value,
                pipeline_def={"lifecycle": {}},
                execution_state="EXECUTING_STEP",
                last_execution_heartbeat_at=now,
                log_signal_count=idx % 7,
            )
            for idx, dev in enumerate(devices)
        ]
        db_session.add_all(jobs)
        db_session.commit()

        resp = client.get(
            f"/api/v1/plan-runs/{run.id}/devices/minimap",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        raw_bytes = len(resp.content)
        gzip_bytes = len(gzip.compress(resp.content))
        assert raw_bytes <= 384 * 1024, (
            f"1,000-job minimap raw UTF-8 JSON exceeded 384 KiB: "
            f"raw={raw_bytes} bytes, gzip={gzip_bytes} bytes"
        )
        payload = resp.json()["data"]
        assert payload["facets"]["total"] == 1000
        assert len(payload["devices"]) == 1000
        assert len(payload["hosts"]) == 40
        assert gzip_bytes > 0


class TestPlanRunSingleJobDetailAndLazySubqueries:
    """GET /jobs/{job_id} + /timeline + /watcher-signals 归属校验与有上限载荷。"""

    def test_single_job_detail_ownership_and_lazy_subqueries(
        self, client, db_session, auth_headers
    ):
        suffix = uuid4().hex[:6]
        now = datetime.now(timezone.utc)
        plan, run1 = _seed_plan_and_run(db_session, suffix=f"{suffix}-1")
        run2 = PlanRun(
            plan_id=plan.id,
            status=PlanRunStatus.RUNNING.value,
            run_type="MANUAL",
            triggered_by="pytest",
            plan_snapshot={},
        )
        db_session.add(run2)
        db_session.flush()

        host = _make_host(
            db_session,
            host_id=f"b23j-{suffix}",
            name=f"JobNode-{suffix}",
            ip=f"10.63.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        dev = Device(
            serial=f"SN-JOB-{suffix}",
            host_id=host.id,
            model="MLD_NX3",
            status=DeviceStatus.BUSY.value,
            adb_state="device",
            adb_connected=True,
        )
        db_session.add(dev)
        db_session.flush()

        job = JobInstance(
            plan_run_id=run1.id,
            plan_id=plan.id,
            device_id=dev.id,
            host_id=host.id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {}},
            current_patrol_step="patrol_monkey",
            patrol_cycle_count=3,
            patrol_success_cycle_count=2,
            patrol_failed_cycle_count=1,
            log_signal_count=2,
            execution_state="EXECUTING_STEP",
            last_execution_heartbeat_at=now,
            watcher_capability="inotifyd",
            disarm_results={"monkey": {"result": "verified"}},
        )
        db_session.add(job)
        db_session.flush()

        # 写入 2 条 StepTrace（带超长 output 与超长 error_message）
        db_session.add_all([
            StepTrace(
                job_id=job.id,
                step_id="init_flash",
                stage="init",
                status="COMPLETED",
                event_type="COMPLETED",
                output="FULL_LOG_LINE\n" * 500,
                error_message=None,
                exit_code=0,
                original_ts=now - timedelta(minutes=10),
            ),
            StepTrace(
                job_id=job.id,
                step_id="patrol_monkey",
                stage="patrol",
                status="FAILED",
                event_type="FAILED",
                output="ANOTHER_HUGE_LOG\n" * 500,
                error_message="E" * 1000,
                exit_code=1,
                original_ts=now - timedelta(minutes=2),
            ),
        ])
        # 写入 2 条 Watcher 异常信号（带大段 first_lines）与 1 条产物
        db_session.add_all([
            JobLogSignal(
                job_id=job.id,
                host_id=host.id,
                device_serial=dev.serial,
                seq_no=1,
                category="AEE",
                source="inotifyd",
                path_on_device="/data/aee_exp/db.00",
                artifact_uri="/nfs/db.00",
                size_bytes=12345,
                first_lines="RAW_STACK\n" * 200,
                detected_at=now - timedelta(minutes=5),
            ),
            JobLogSignal(
                job_id=job.id,
                host_id=host.id,
                device_serial=dev.serial,
                seq_no=2,
                category="ANR",
                source="polling",
                path_on_device="/data/anr/traces.txt",
                size_bytes=4096,
                first_lines="RAW_ANR\n" * 200,
                detected_at=now - timedelta(minutes=1),
            ),
            JobArtifact(
                job_id=job.id,
                storage_uri=f"/nfs/artifacts/{suffix}/db.00.tar.gz",
                artifact_type="aee_crash",
                size_bytes=9999,
            ),
        ])
        db_session.commit()

        # 1) (run_id, job_id) 错配必须返回 404（三条端点均校验）
        assert client.get(f"/api/v1/plan-runs/{run2.id}/jobs/{job.id}", headers=auth_headers).status_code == 404
        assert client.get(f"/api/v1/plan-runs/{run2.id}/jobs/{job.id}/timeline", headers=auth_headers).status_code == 404
        assert client.get(f"/api/v1/plan-runs/{run2.id}/jobs/{job.id}/watcher-signals", headers=auth_headers).status_code == 404

        # 2) 单 Job 详情完整返回且不含 traces/signals 全文
        r_det = client.get(f"/api/v1/plan-runs/{run1.id}/jobs/{job.id}", headers=auth_headers)
        assert r_det.status_code == 200
        det = r_det.json()["data"]
        assert det["plan_run_id"] == run1.id
        assert det["job_id"] == job.id
        assert det["device_serial"] == dev.serial
        assert det["host_name"] == host.name
        assert det["current_script_name"] == "monkey_runner"
        assert det["current_script_version"] == "2.3.0"
        assert det["disarm_results"] == {"monkey": {"result": "verified"}}
        assert det["capabilities"]["manual_retry"] is True
        assert det["step_trace_count"] == 2
        assert det["artifact_count"] == 1
        assert "output" not in det

        # 3) 阶段时间线有上限子查询：分页生效、截断长错误栈、不含 output
        r_tl = client.get(
            f"/api/v1/plan-runs/{run1.id}/jobs/{job.id}/timeline",
            params={"skip": 0, "limit": 2},
            headers=auth_headers,
        )
        assert r_tl.status_code == 200
        tl = r_tl.json()["data"]
        assert tl["total"] == 2
        assert len(tl["items"]) == 2
        assert tl["items"][0]["script_name"] == "flash_device"
        assert tl["items"][1]["script_name"] == "monkey_runner"
        assert "output" not in tl["items"][0]
        assert len(tl["items"][1]["error_message"]) <= 513

        # 4) Watcher 异常明细有上限子查询：支持 category 过滤、不含 first_lines
        r_ws = client.get(
            f"/api/v1/plan-runs/{run1.id}/jobs/{job.id}/watcher-signals",
            params={"category": "AEE"},
            headers=auth_headers,
        )
        assert r_ws.status_code == 200
        ws = r_ws.json()["data"]
        assert ws["total"] == 1
        assert len(ws["items"]) == 1
        assert ws["items"][0]["category"] == "AEE"
        assert ws["items"][0]["artifact_uri"] == "/nfs/db.00"
        assert "first_lines" not in ws["items"][0]


class TestPlanRunTimeoutParityAndDisarmGuard:
    """Q5 回归：带 disarm_guard + 有效租约的 RUNNING Job 不 500；长周期巡检超时判定三端一致。"""

    def test_disarm_guard_with_active_lease_returns_200_and_consistent_stuck_across_endpoints(
        self, client, db_session, auth_headers
    ):
        suffix = uuid4().hex[:6]
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        plan, run = _seed_plan_and_run(db_session, suffix=f"dg-{suffix}")

        boot_id = f"boot-{suffix}"
        agent_instance_id = f"agent-{suffix}"
        fencing_token = f"fence-{suffix}"
        host = Host(
            id=f"b23-dg-h-{suffix}",
            hostname=f"GuardNode-{suffix}",
            name=f"GuardNode-{suffix}",
            ip=f"10.66.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            ip_address=f"10.66.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status=HostStatus.ONLINE.value,
            last_heartbeat=now - timedelta(seconds=5),
            boot_id=boot_id,
            last_agent_instance_id=agent_instance_id,
        )
        db_session.add(host)
        db_session.flush()

        dev = Device(
            serial=f"SN-DG-{suffix}",
            host_id=host.id,
            model="MLD_NX3",
            status=DeviceStatus.BUSY.value,
            adb_state="device",
            adb_connected=True,
        )
        db_session.add(dev)
        db_session.flush()

        # 构造有效 disarm_guard（处于 execute 保护窗口内），同时执行心跳已很旧（若不走护栏会被判卡住）
        guard = {
            "protocol": 1,
            "t0": now_ts - 30.0,
            "job_deadline": now_ts + 600.0,
            "queue_remaining": 120.0,
            "step_budgets": {},
            "step_deadlines": {"teardown_disarm": now_ts + 300.0},
            "step_started_at": {"teardown_disarm": now_ts - 20.0},
            "first_server_at": now_ts - 30.0,
            "last_server_at": now_ts - 5.0,
            "seq": 2,
            "progress_seq": 2,
            "progress_seen_at": now_ts - 5.0,
            "coordinator_seen_at": now_ts - 5.0,
            "coordinator_seq": 2,
            "phase": "execute",
            "step_id": "teardown_disarm",
            "fencing_token": fencing_token,
            "agent_instance_id": agent_instance_id,
            "boot_id": boot_id,
        }
        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=dev.id,
            host_id=host.id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {}},
            execution_state="EXECUTING_DISARM",
            last_execution_heartbeat_at=now - timedelta(seconds=10),
            started_at=now - timedelta(hours=1),
            disarm_guard=guard,
        )
        db_session.add(job)
        db_session.flush()

        lease = DeviceLease(
            device_id=dev.id,
            job_id=job.id,
            host_id=host.id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=fencing_token,
            lease_generation=1,
            agent_instance_id=agent_instance_id,
            acquired_at=now - timedelta(hours=1),
            renewed_at=now - timedelta(seconds=5),
            expires_at=now + timedelta(minutes=10),
        )
        db_session.add(lease)
        db_session.commit()

        r_minimap = client.get(
            f"/api/v1/plan-runs/{run.id}/devices/minimap",
            headers=auth_headers,
        )
        r_page = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"skip": 0, "limit": 50},
            headers=auth_headers,
        )
        r_legacy = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            headers=auth_headers,
        )
        r_detail = client.get(
            f"/api/v1/plan-runs/{run.id}/jobs/{job.id}",
            headers=auth_headers,
        )

        assert r_minimap.status_code == 200
        assert r_page.status_code == 200
        assert r_legacy.status_code == 200
        assert r_detail.status_code == 200

        minimap_data = r_minimap.json()["data"]
        page_item = r_page.json()["data"]["devices"][0]
        legacy_item = r_legacy.json()["data"]["devices"][0]
        detail_item = r_detail.json()["data"]

        assert minimap_data["stuck_job_ids"] == []
        assert page_item["is_stuck"] is False
        assert legacy_item["is_stuck"] is False
        assert detail_item["is_stuck"] is False
        assert page_item["heartbeat_deadline_at"] == detail_item["heartbeat_deadline_at"]
        assert legacy_item["heartbeat_deadline_at"] == detail_item["heartbeat_deadline_at"]

    def test_long_patrol_interval_timeout_parity_across_minimap_table_and_detail(
        self, client, db_session, auth_headers
    ):
        """长周期巡检（interval=600s → 容忍 1800s，或含退避延迟）在 Minimap、分页表格和单 Job 详情判定一致。"""
        suffix = uuid4().hex[:6]
        now = datetime.now(timezone.utc)
        plan, run = _seed_plan_and_run(db_session, suffix=f"lp-{suffix}")
        host = _make_host(
            db_session,
            host_id=f"b23-lp-h-{suffix}",
            name=f"PatrolNode-{suffix}",
            ip=f"10.67.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )

        # 设备 1：interval_seconds=600（超时窗口 1800s），仅由 last_patrol_heartbeat_at / current_patrol_step
        # 标识进入 patrol，心跳距今 1100s（超过默认 900s，但在 1800s 内 → 不应误报超时）
        d_ok = Device(
            serial=f"SN-LP-OK-{suffix}",
            host_id=host.id,
            model="MLD_NX3",
            status=DeviceStatus.BUSY.value,
            adb_state="device",
            adb_connected=True,
        )
        # 设备 2：interval_seconds=600，心跳距今 2200s（超过 1800s → 三端一致判超时）
        d_stuck = Device(
            serial=f"SN-LP-STUCK-{suffix}",
            host_id=host.id,
            model="MLD_NX3",
            status=DeviceStatus.BUSY.value,
            adb_state="device",
            adb_connected=True,
        )
        db_session.add_all([d_ok, d_stuck])
        db_session.flush()

        long_patrol_pipeline = {
            "lifecycle": {
                "patrol": {
                    "interval_seconds": 600,
                    "steps": [{"action": "script:monkey_runner"}],
                }
            }
        }
        j_ok = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=d_ok.id,
            host_id=host.id,
            status=JobStatus.RUNNING.value,
            pipeline_def=long_patrol_pipeline,
            execution_state="EXECUTING_STEP",
            patrol_cycle_count=0,
            current_patrol_step="patrol_monkey",
            last_patrol_heartbeat_at=now - timedelta(seconds=1100),
            last_execution_heartbeat_at=now - timedelta(seconds=1100),
            started_at=now - timedelta(hours=2),
            updated_at=now - timedelta(seconds=1100),
        )
        j_stuck = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=d_stuck.id,
            host_id=host.id,
            status=JobStatus.RUNNING.value,
            pipeline_def=long_patrol_pipeline,
            execution_state="EXECUTING_STEP",
            patrol_cycle_count=3,
            current_patrol_step="patrol_monkey",
            last_patrol_heartbeat_at=now - timedelta(seconds=2200),
            last_execution_heartbeat_at=now - timedelta(seconds=2200),
            started_at=now - timedelta(hours=2),
            updated_at=now - timedelta(seconds=2200),
        )
        db_session.add_all([j_ok, j_stuck])
        db_session.commit()

        r_minimap = client.get(
            f"/api/v1/plan-runs/{run.id}/devices/minimap",
            headers=auth_headers,
        )
        r_page = client.get(
            f"/api/v1/plan-runs/{run.id}/devices",
            params={"skip": 0, "limit": 50},
            headers=auth_headers,
        )
        r_det_ok = client.get(
            f"/api/v1/plan-runs/{run.id}/jobs/{j_ok.id}",
            headers=auth_headers,
        )
        r_det_stuck = client.get(
            f"/api/v1/plan-runs/{run.id}/jobs/{j_stuck.id}",
            headers=auth_headers,
        )

        assert r_minimap.status_code == 200
        assert r_page.status_code == 200
        assert r_det_ok.status_code == 200
        assert r_det_stuck.status_code == 200

        assert r_minimap.json()["data"]["stuck_job_ids"] == [j_stuck.id]
        by_jid = {d["job_id"]: d for d in r_page.json()["data"]["devices"]}
        assert by_jid[j_ok.id]["is_stuck"] is False
        assert by_jid[j_stuck.id]["is_stuck"] is True
        assert r_det_ok.json()["data"]["is_stuck"] is False
        assert r_det_stuck.json()["data"]["is_stuck"] is True
