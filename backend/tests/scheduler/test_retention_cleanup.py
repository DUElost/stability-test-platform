"""#936 — run_retention_cleanup 链式引用安全。

parent/root 自引用 FK 无删除级联：父到期而子未到期/仍运行时，按批次直删
违反 FK、整批回滚（且每轮必重选同批 → 僵尸积压）。修复语义 = 删除前计算
引用闭包保留集（外部未删 Run 引用的批内 id 沿祖先链传播），只删安全叶子。

cron_scheduler.run_retention_cleanup 使用模块级 ``SessionLocal`` 与
``PLAN_RUN_RETENTION_DAYS``——测试经 monkeypatch 指向 fixture session 与 0 天
保留（started_at < now 即入选）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler import cron_scheduler


@pytest.fixture
def cleanup_env(db_session, monkeypatch):
    monkeypatch.setattr(cron_scheduler, "PLAN_RUN_RETENTION_DAYS", 0)
    monkeypatch.setattr(cron_scheduler, "SessionLocal", lambda: db_session)
    plan = Plan(name="retention-chain")
    db_session.add(plan)
    db_session.flush()
    return db_session, plan


def _mk_run(db, plan, *, status="SUCCESS", age_days=10, parent=None, root=None):
    run = PlanRun(
        plan_id=plan.id,
        status=status,
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        started_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        parent_plan_run_id=parent.id if parent else None,
        root_plan_run_id=root.id if root else None,
    )
    db.add(run)
    # cleanup 退出 with SessionLocal 时会 close（回滚未提交事务）——fixture
    # 数据必须先落库，否则被 cleanup 的 close 一并丢弃。
    db.commit()
    return run


def test_parent_kept_when_child_still_running(cleanup_env):
    """#936 验收主场景：父到期、子仍运行 → 父保留、不整批回滚。"""
    db, plan = cleanup_env
    parent = _mk_run(db, plan, age_days=10)
    _mk_run(db, plan, status="RUNNING", age_days=0, parent=parent)  # 子运行中

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).filter_by(id=parent.id).one() is not None


def test_chain_all_expired_deleted(cleanup_env):
    """全链到期：祖→父→子全部删除（无引用阻碍）。"""
    db, plan = cleanup_env
    root = _mk_run(db, plan, age_days=10)
    mid = _mk_run(db, plan, age_days=9, parent=root, root=root)
    leaf = _mk_run(db, plan, age_days=8, parent=mid, root=root)

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).count() == 0
    assert {root.id, mid.id, leaf.id} and not db.query(PlanRun).filter(
        PlanRun.id.in_([root.id, mid.id, leaf.id])
    ).first()


def test_grandparent_kept_via_ancestry_propagation(cleanup_env):
    """孙运行中：父与祖父经 parent/root 传播全部保留。"""
    db, plan = cleanup_env
    root = _mk_run(db, plan, age_days=10)
    mid = _mk_run(db, plan, age_days=9, parent=root, root=root)
    leaf = _mk_run(db, plan, status="RUNNING", age_days=0, parent=mid, root=root)  # 孙运行中

    cron_scheduler.run_retention_cleanup()

    remaining = {r.id for r in db.query(PlanRun).all()}
    assert remaining == {root.id, mid.id, leaf.id}  # 祖先链保留 + 运行中孙本身


def test_root_referenced_by_active_run_kept(cleanup_env):
    """链根被运行中 Run 的 root 引用 → 保留。"""
    db, plan = cleanup_env
    root = _mk_run(db, plan, age_days=10)
    _mk_run(db, plan, status="RUNNING", age_days=0, root=root)  # 仅 root 引用

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).filter_by(id=root.id).one() is not None


def test_unreferenced_runs_deleted_normally(cleanup_env):
    """无链引用 → 正常删除（既有行为回归）。"""
    db, plan = cleanup_env
    a = _mk_run(db, plan, age_days=10)
    b = _mk_run(db, plan, age_days=10)

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).count() == 0
    assert a.id != b.id


def test_console_log_files_purged_with_run(
    cleanup_env, sample_host, sample_device, tmp_path, monkeypatch,
):
    """#798: retention 删除 Run 后对应 console.log 目录与内存锁条目被清理。"""
    import backend.realtime.log_writer as lw
    from backend.models.job import JobInstance

    db, plan = cleanup_env
    monkeypatch.setattr(lw, "LOG_BASE_DIR", tmp_path)

    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id,
        device_id=sample_device.id, host_id=sample_host.id,
        status="COMPLETED",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db.add(job)
    db.commit()

    log_dir = tmp_path / "jobs" / str(job.id)
    log_dir.mkdir(parents=True)
    (log_dir / "console.log").write_text("x\n", encoding="utf-8")
    lw._get_lock(job.id)  # 模拟运行期建立的内存锁条目

    cron_scheduler.run_retention_cleanup()

    assert not log_dir.exists(), "console.log 目录未被 retention 清理"
    assert job.id not in lw._locks, "内存锁条目未清理"


def test_job_log_signal_and_dle_deleted_with_run(
    cleanup_env, sample_host, sample_device,
):
    """#781: retention 删 Job/PlanRun 前显式清 job_log_signal 与 device_log_event。

    二者 FK 为 SET NULL——若只删 Job，signal/event 变孤儿并单调堆积。
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    from backend.models.device_log_event import DeviceLogEvent
    from backend.models.job import JobInstance, JobLogSignal

    db, plan = cleanup_env
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id,
        device_id=sample_device.id, host_id=sample_host.id,
        status="COMPLETED",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db.add(job)
    db.flush()

    now = datetime.now(timezone.utc)
    event_id = uuid4()
    db.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="AEE",
        event_subtype="KE",
        detected_at=now,
        state="REMOTE",
        local_path="/local/aee/781",
        remote_path="/nfs/devices/781/aee/1",
        host_id=str(sample_host.id),
        job_id=job.id,
        plan_run_id=run.id,
        signal_seq_no=1,
    ))
    db.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_host.id),
        device_log_event_id=event_id,
        device_serial=sample_device.serial,
        seq_no=1,
        category="AEE",
        source="polling",
        path_on_device="/sdcard/aee/781",
        detected_at=now,
        received_at=now,
    ))
    db.commit()

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).filter_by(id=run.id).first() is None
    assert db.query(JobInstance).filter_by(id=job.id).first() is None
    assert db.query(JobLogSignal).count() == 0
    assert db.query(DeviceLogEvent).filter_by(id=event_id).first() is None


def _make_nfs_dirs(root, run_id):
    (root / "devices" / str(run_id) / "172-21-1-1").mkdir(parents=True)
    (root / "devices" / str(run_id) / "172-21-1-1" / "evt.log").write_text("x")
    (root / "dedup" / str(run_id) / "mtk").mkdir(parents=True)
    (root / "dedup" / str(run_id) / "mtk" / "result.xls").write_text("y")


def test_nfs_run_dirs_purged_with_db_row(cleanup_env, tmp_path, monkeypatch):
    """#1521: DB 行删除前清理 devices/{id}/ 与 dedup/{id}/（NFS 轨 TTL）。"""
    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    _make_nfs_dirs(tmp_path, run.id)

    cron_scheduler.run_retention_cleanup()

    assert not (tmp_path / "devices" / str(run.id)).exists()
    assert not (tmp_path / "dedup" / str(run.id)).exists()
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is None


def test_active_run_nfs_dirs_kept(cleanup_env, tmp_path, monkeypatch):
    """未到期 Run 的 NFS 目录不被清理（避免删在跑/未归档的 run）。"""
    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="RUNNING", age_days=0)
    _make_nfs_dirs(tmp_path, run.id)

    cron_scheduler.run_retention_cleanup()

    assert (tmp_path / "devices" / str(run.id)).exists()
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is not None


def test_purge_failure_defers_db_row_for_retry(cleanup_env, tmp_path, monkeypatch):
    """文件清理失败 → DB 行保留（先文件后行，下轮重试可自愈）。"""
    import shutil as _shutil

    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    _make_nfs_dirs(tmp_path, run.id)

    def _boom(path, *_a, **_k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(_shutil, "rmtree", _boom)

    cron_scheduler.run_retention_cleanup()

    # DB 行仍在（下轮重试文件清理），目录仍在
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is not None
    assert (tmp_path / "devices" / str(run.id)).exists()
