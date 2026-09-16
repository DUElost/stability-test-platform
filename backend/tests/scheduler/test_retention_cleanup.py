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
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler import cron_scheduler


@pytest.fixture
def cleanup_env(db_session, monkeypatch, scheduler_env, tmp_path):
    from backend.realtime import log_writer

    scheduler_env("PLAN_RUN_RETENTION_DAYS", "0")
    monkeypatch.setattr(cron_scheduler, "SessionLocal", lambda: db_session)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path / "retention-storage"))
    monkeypatch.setattr(log_writer, "LOG_BASE_DIR", tmp_path / "console")
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


def test_batch_size_setting_bounds_one_tick(cleanup_env, scheduler_env):
    """#2105：单 tick 处理上限 = ``plan_run_retention_batch_size``。

    这条设置不是「性能选项」而是**持锁窗口的杠杆**：purge（NFS）与行删除同事务，
    窗口长度 ∝ 本 tick 的 run 数；窗口过长时的正确动作是调小它，而不是把 purge
    移出事务（那会造成「文件已删、行仍在」的不可自愈不一致）。
    """
    db, plan = cleanup_env
    for _ in range(3):
        _mk_run(db, plan, age_days=10)
    # 必须在调用前设置：settings 走 lru_cache，scheduler_env 会重置它。
    scheduler_env("PLAN_RUN_RETENTION_BATCH_SIZE", "2")

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).count() == 1, (
        "批大小=2 时单 tick 只应删 2 个 run，其余留下轮处理"
    )


def test_cleanup_reports_candidates_and_batch_size(cleanup_env, scheduler_env):
    """#2144：候选数与批大小成对上报——``candidates >= batch_size`` 即可判定积压。

    两个要点：① 批被填满时等于上限（饱和信号）；② 清空后回到 0——gauge 不许停在上一轮的
    非零值上，否则「已清空」会被显示成「仍在积压」（那会让这条观测面比没有更糟）。
    """
    from prometheus_client import REGISTRY

    db, plan = cleanup_env
    for _ in range(3):
        _mk_run(db, plan, age_days=10)
    # 必须在调用前设置：settings 走 lru_cache，scheduler_env 会重置它。
    scheduler_env("PLAN_RUN_RETENTION_BATCH_SIZE", "2")

    cron_scheduler.run_retention_cleanup()

    assert REGISTRY.get_sample_value("stability_retention_candidate_runs") == 2.0, (
        "本轮候选 3 个、批大小 2 → 上报值应是 2（= 上限，即饱和）"
    )
    assert REGISTRY.get_sample_value("stability_retention_batch_size") == 2.0

    cron_scheduler.run_retention_cleanup()  # 剩 1 个候选 → 不饱和
    assert REGISTRY.get_sample_value("stability_retention_candidate_runs") == 1.0

    cron_scheduler.run_retention_cleanup()  # 清空 → 必须归 0
    assert REGISTRY.get_sample_value("stability_retention_candidate_runs") == 0.0


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


def test_chain_larger_than_batch_makes_bounded_progress(cleanup_env, monkeypatch):
    db, plan = cleanup_env
    root = _mk_run(db, plan)
    parent = root
    for _ in range(100):
        parent = _mk_run(db, plan, parent=parent, root=root)
    purged_batches = []
    monkeypatch.setattr(
        cron_scheduler, "purge_run_storage_dirs",
        lambda run_ids, jobs_by_run=None: purged_batches.append(list(run_ids)) or set(),
    )

    cron_scheduler.run_retention_cleanup()
    assert db.query(PlanRun).count() == 1
    assert db.query(PlanRun.id).scalar() == root.id
    assert len(purged_batches[0]) == 100
    cron_scheduler.run_retention_cleanup()
    assert db.query(PlanRun).count() == 0
    assert purged_batches[1] == [root.id]


def test_protected_prefix_cannot_starve_later_unreferenced_runs(cleanup_env):
    db, plan = cleanup_env
    protected_ids = set()
    for _ in range(101):
        parent = _mk_run(db, plan)
        active = _mk_run(db, plan, status="RUNNING", age_days=0, parent=parent, root=parent)
        protected_ids.update((parent.id, active.id))
    for _ in range(3):
        eligible = _mk_run(db, plan, age_days=1)
        eligible_id = eligible.id
        cron_scheduler.run_retention_cleanup()
        remaining = {run_id for (run_id,) in db.query(PlanRun.id).all()}
        assert eligible_id not in remaining
        assert remaining == protected_ids


def test_unreferenced_batch_is_capped_at_one_hundred(cleanup_env, monkeypatch):
    db, plan = cleanup_env
    for _ in range(105):
        _mk_run(db, plan)
    purged_batches = []
    monkeypatch.setattr(
        cron_scheduler, "purge_run_storage_dirs",
        lambda run_ids, jobs_by_run=None: purged_batches.append(list(run_ids)) or set(),
    )
    cron_scheduler.run_retention_cleanup()
    assert db.query(PlanRun).count() == 5
    assert len(purged_batches) == 1
    assert len(purged_batches[0]) == 100


def test_self_root_reference_does_not_block_expired_run(cleanup_env):
    db, plan = cleanup_env
    root = _mk_run(db, plan)
    root.root_plan_run_id = root.id
    db.commit()
    cron_scheduler.run_retention_cleanup()
    assert db.query(PlanRun).count() == 0


def test_locked_candidate_is_skipped_without_blocking_other_runs(cleanup_env):
    db, plan = cleanup_env
    locked = _mk_run(db, plan)
    _mk_run(db, plan)
    locked_id = locked.id
    with Session(db.get_bind()) as other:
        other.execute(select(PlanRun).where(PlanRun.id == locked_id).with_for_update())
        cron_scheduler.run_retention_cleanup()
        assert [run_id for (run_id,) in db.query(PlanRun.id).all()] == [locked_id]
    cron_scheduler.run_retention_cleanup()
    assert db.query(PlanRun).count() == 0


def test_failed_child_purge_preserves_ancestors_but_not_safe_siblings(cleanup_env, monkeypatch):
    db, plan = cleanup_env
    root = _mk_run(db, plan)
    parent = _mk_run(db, plan, parent=root)
    failed = _mk_run(db, plan, parent=parent)
    sibling_plan = Plan(name="retention-sibling")
    db.add(sibling_plan)
    db.flush()
    sibling = _mk_run(db, sibling_plan, parent=root, root=root)
    root_id, parent_id, failed_id, sibling_id = root.id, parent.id, failed.id, sibling.id
    monkeypatch.setattr(
        cron_scheduler, "purge_run_storage_dirs",
        lambda run_ids, jobs_by_run=None: {failed_id},
    )
    cron_scheduler.run_retention_cleanup()
    remaining = {run_id for (run_id,) in db.query(PlanRun.id).all()}
    assert remaining == {root_id, parent_id, failed_id}
    assert sibling_id not in remaining

    monkeypatch.setattr(
        cron_scheduler, "purge_run_storage_dirs",
        lambda run_ids, jobs_by_run=None: set(),
    )
    cron_scheduler.run_retention_cleanup()
    assert db.query(PlanRun).count() == 0


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
    (root / "jira" / str(run_id) / "extract").mkdir(parents=True)
    (root / "jira" / str(run_id) / "extract" / "bundle.zip").write_text("z")


def test_nfs_run_dirs_purged_with_db_row(cleanup_env, tmp_path, monkeypatch):
    """#1521/#1698: DB 行删除前清理 devices/dedup/jira/{id}/（NFS 轨 TTL）。"""
    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    _make_nfs_dirs(tmp_path, run.id)

    cron_scheduler.run_retention_cleanup()

    assert not (tmp_path / "devices" / str(run.id)).exists()
    assert not (tmp_path / "dedup" / str(run.id)).exists()
    assert not (tmp_path / "jira" / str(run.id)).exists()
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is None


def test_active_run_nfs_dirs_kept(cleanup_env, tmp_path, monkeypatch):
    """未到期 Run 的 NFS 目录不被清理（避免删在跑/未归档的 run）。"""
    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="RUNNING", age_days=0)
    _make_nfs_dirs(tmp_path, run.id)

    cron_scheduler.run_retention_cleanup()

    assert (tmp_path / "devices" / str(run.id)).exists()
    assert (tmp_path / "jira" / str(run.id)).exists()
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
    assert (tmp_path / "jira" / str(run.id)).exists()


# ── #2031：同根下的 jobs/{job_id}/（按 job 分桶）+ 共享根容器化校验 ─────────


def _mk_job(db, plan, run, device, host):
    from backend.models.job import JobInstance

    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id,
        device_id=device.id, host_id=host.id,
        status="COMPLETED",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db.add(job)
    db.commit()
    return job


def _make_nfs_job_dir(root, job_id):
    d = root / "jobs" / str(job_id) / "fleet"
    d.mkdir(parents=True)
    (d / "artifact.bin").write_text("x")


def test_nfs_job_dirs_purged_with_db_row(
    cleanup_env, sample_host, sample_device, tmp_path, monkeypatch,
):
    """#2031：`jobs/{job_id}/` 与 run 目录同根，但按 **job** 分桶——同样必须在删行前清理。

    反例（修复前）：该前缀不在清理集合里，而 StepTrace/JobArtifact 行随本批删除
    （保留期 3 天 << artifact 清理器 30 天）→ 目录索引消失，成为永不可回溯的孤儿。
    """
    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    job = _mk_job(db, plan, run, sample_device, sample_host)
    active_run = _mk_run(db, plan, status="RUNNING", age_days=0)
    active_job = _mk_job(db, plan, active_run, sample_device, sample_host)
    _make_nfs_job_dir(tmp_path, job.id)
    _make_nfs_job_dir(tmp_path, active_job.id)

    cron_scheduler.run_retention_cleanup()

    assert not (tmp_path / "jobs" / str(job.id)).exists()
    assert (tmp_path / "jobs" / str(active_job.id)).exists(), "未到期 run 的 job 目录不得清理"
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is None


def test_job_dir_purge_failure_defers_owning_run(
    cleanup_env, sample_host, sample_device, tmp_path, monkeypatch,
):
    """job 目录删除失败按**所属 run** 归因：该 run 整批推迟（行与目录都留下轮重试）。"""
    import shutil as _shutil

    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    job = _mk_job(db, plan, run, sample_device, sample_host)
    _make_nfs_job_dir(tmp_path, job.id)

    real_rmtree = _shutil.rmtree

    def _boom(path, *args, **kwargs):
        if "/jobs/" in str(path):
            raise OSError("read-only filesystem")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(_shutil, "rmtree", _boom)

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is not None
    assert (tmp_path / "jobs" / str(job.id)).exists()


def test_purge_refuses_target_outside_shared_root(cleanup_env, tmp_path, monkeypatch):
    """#2031：共享根下的符号链接目录指向根外时不得跟随删除（同 #1825 威胁模型）。

    越界一律拒绝并计入 failed → 该 run 推迟（行保留），等人工纠正共享根布局。
    """
    db, plan = cleanup_env
    nfs = tmp_path / "nfs"
    outside = tmp_path / "outside"
    nfs.mkdir()
    outside.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))

    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    keep = outside / str(run.id) / "sub"
    keep.mkdir(parents=True)
    (keep / "keep.log").write_text("keep")
    (nfs / "devices").symlink_to(outside)   # {根}/devices → 根外

    cron_scheduler.run_retention_cleanup()

    assert (keep / "keep.log").exists(), "越界目标被删除（容器化校验失效）"
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is not None


# ── #2262：devices/unassigned/{event_id}/（不随 run 分桶，关联不搬文件） ──────


def _mk_unassigned_event(db, run, device, host, event_id, nfs_root, *, state="ARCHIVED"):
    """建一条 remote_path 指向 ``{nfs}/devices/unassigned/{event_id}/{basename}/`` 的 DLE 行。

    **必须用内层形态**：Agent 记录的 remote_path 是 dst（``{event_id}/{basename}``），
    生产实测 7 段路径即此形态；测试若直接用事件目录本身（早期写法）会掩盖
    「事件目录 = remote_path.parent」这一层判定。
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from backend.models.device_log_event import DeviceLogEvent

    db.add(DeviceLogEvent(
        id=event_id,
        serial=device.serial,
        platform="UNISOC",
        event_type="UNIVIEW",
        event_subtype="KE",
        detected_at=datetime.now(timezone.utc),
        state=state,
        local_path="/local/uniview/2262",
        remote_path=str(
            Path(nfs_root) / "devices" / "unassigned" / str(event_id) / "2026_0812_spill_db.99.ANR"
        ),
        host_id=str(host.id),
        job_id=None,
        plan_run_id=run.id,
        signal_seq_no=None,
    ))
    db.commit()


def _make_unassigned_dir(root, event_id):
    d = root / "devices" / "unassigned" / str(event_id) / "artifacts"
    d.mkdir(parents=True)
    (d / "evt.bin").write_text("x")
    return d.parent


def test_unassigned_event_dir_purged_with_row(
    cleanup_env, sample_host, sample_device, tmp_path, monkeypatch,
):
    """#2262：行被 retention 删除时，同批清掉它引用的 devices/unassigned/{event_id}/。

    反例（修复前）：该目录不随 run 分桶、关联也不搬文件 → run 级 purge 命中不到，
    行删后目录成为永不可回溯的孤儿；行还活着时**不得**删（remote_path 仍是回溯依据）。
    """
    from uuid import uuid4

    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    active_run = _mk_run(db, plan, status="RUNNING", age_days=0)
    expiring_event, live_event = uuid4(), uuid4()
    _mk_unassigned_event(db, run, sample_device, sample_host, expiring_event, tmp_path)
    _mk_unassigned_event(db, active_run, sample_device, sample_host, live_event, tmp_path)
    doomed = _make_unassigned_dir(tmp_path, expiring_event)
    kept = _make_unassigned_dir(tmp_path, live_event)

    cron_scheduler.run_retention_cleanup()

    assert not doomed.exists(), "到期行的 unassigned 目录未被清理"
    assert kept.exists(), "未到期 run 的 unassigned 目录不得清理"
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is None


def test_unassigned_dir_purge_failure_defers_run(
    cleanup_env, sample_host, sample_device, tmp_path, monkeypatch,
):
    """目录清不掉 → 该 run 的行保留（先文件后行，下轮重试）。"""
    import shutil as _shutil
    from uuid import uuid4

    db, plan = cleanup_env
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)
    event_id = uuid4()
    _mk_unassigned_event(db, run, sample_device, sample_host, event_id, tmp_path)
    target = _make_unassigned_dir(tmp_path, event_id)

    real_rmtree = _shutil.rmtree

    def _boom(path, *args, **kwargs):
        if "/unassigned/" in str(path):
            raise OSError("read-only filesystem")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(_shutil, "rmtree", _boom)

    cron_scheduler.run_retention_cleanup()

    assert target.exists()
    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is not None


def test_unassigned_purge_rejects_non_child_path(
    cleanup_env, sample_host, sample_device, tmp_path, monkeypatch,
):
    """remote_path 形态不符（借 `unassigned/..` 指向别处）→ 不删该目标，**且不阻塞本批**。

    fail-safe：宁可留孤儿，也不让被污染的 remote_path 把 rmtree 引到别处；
    但也不得把它算成「删除失败」——那会让整个 run 的 retention 每轮推迟（初版就是
    把形态不符计入 failed，被真实形态证伪后改判为跳过 + 告警）。
    """
    from uuid import uuid4

    from backend.models.device_log_event import DeviceLogEvent

    db, plan = cleanup_env
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    run = _mk_run(db, plan, status="SUCCESS", age_days=10)

    # 越界形态：借 unassigned 前缀 + '..' 指到 jira 桶
    outside = nfs / "jira" / "999" / "bundle"
    outside.mkdir(parents=True)
    (outside / "keep.zip").write_text("keep")
    event_id = uuid4()
    db.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="UNISOC",
        event_type="UNIVIEW",
        event_subtype="KE",
        detected_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        state="ARCHIVED",
        local_path="/local/uniview/2262",
        remote_path=str(nfs / "devices" / "unassigned" / ".." / ".." / "jira" / "999" / "bundle"),
        host_id=str(sample_host.id),
        plan_run_id=run.id,
    ))
    db.commit()

    cron_scheduler.run_retention_cleanup()

    assert (outside / "keep.zip").exists(), "越界目标被删除"
    # 形态不符是数据问题，不是删除失败：不得把 run 拖成每轮推迟
    from backend.models.plan_run import PlanRun

    assert db.query(PlanRun).filter(PlanRun.id == run.id).first() is None


# ── #2278：取锁之后的早退也必须上报持锁窗口 ─────────────────────────────────


def _window_recorder(monkeypatch) -> list:
    windows: list = []
    monkeypatch.setattr(cron_scheduler, "record_retention_txn", windows.append)
    return windows


def test_window_reported_when_lock_recheck_empties_batch(cleanup_env, monkeypatch):
    """路径 ①：`_retention_lock_runs` 锁内复核清空 → 早退，但取锁（含等待）已发生。

    #2104 的指标就是为了观测「锁序统一后 死锁→等待」的代价，而这一类 tick 恰恰
    「什么都没删成」——不在上报点里，代价就永远看不见（三个上报点全在这两个 return
    之后，等于把最该覆盖的样本丢掉）。
    """
    db, plan = cleanup_env
    windows = _window_recorder(monkeypatch)
    monkeypatch.setattr(cron_scheduler, "_retention_lock_runs", lambda *_a, **_k: [])
    _mk_run(db, plan, age_days=10)

    cron_scheduler.run_retention_cleanup()

    assert len(windows) == 1, "锁内复核清空后的早退必须上报一次窗口"
    assert windows[0] >= 0.0


def test_window_reported_when_all_candidates_kept_by_refs(cleanup_env, monkeypatch):
    """路径 ②：`_retention_safe_ids` 清空安全集 → 该出口同样必须上报窗口。

    该出口在今天的代码里**只能由并发触发**：`_retention_candidate_ids` 已在 SQL 层
    用 `~referenced` 排掉被引用者，候选非空而安全集为空只剩「读取与加锁之间新插入
    了引用」这一种形态——也正因为它罕见，漏报才会长期无人发现。链引用计算本身由
    上面的 #936 用例覆盖，这里钉的是**该出口的窗口接线**。
    """
    db, plan = cleanup_env
    windows = _window_recorder(monkeypatch)
    kept = _mk_run(db, plan, age_days=10)
    monkeypatch.setattr(
        cron_scheduler, "_retention_safe_ids", lambda _db, ids: ([], set(ids)),
    )

    cron_scheduler.run_retention_cleanup()

    assert len(windows) == 1, "安全集为空的早退必须上报一次窗口"
    assert windows[0] >= 0.0
    db.expire_all()
    assert db.query(PlanRun).filter_by(id=kept.id).one() is not None, "未上报≠已删除"


def test_no_window_reported_before_any_lock_is_taken(cleanup_env, monkeypatch):
    """反向边界：**未取锁**（无候选）时不得上报——否则窗口指标会被空 tick 稀释。"""
    db, plan = cleanup_env
    windows = _window_recorder(monkeypatch)
    _mk_run(db, plan, status="RUNNING", age_days=0)  # 运行中 → 不是候选

    cron_scheduler.run_retention_cleanup()

    assert windows == [], "候选为空（未进入取锁阶段）不应产生窗口观测"
