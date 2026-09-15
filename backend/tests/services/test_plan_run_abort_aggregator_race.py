"""abort vs aggregator 竞态回归 — PlanRunStateMachine 引入后第二个写入方不得炸。

场景:两个写入方几乎同时从 RUNNING 落终态。生产路径依赖:
  1. apply_plan_run_aggregation 顶部的 _TERMINAL_PLAN_RUN_STATUSES 守卫
  2. abort_plan_run 开头的终态检查(抛 PlanRunAbortError,非 InvalidTransitionError)
  3. SELECT ... FOR UPDATE 串行化 read-modify-write

本测试用 DB fixture 验证「先落终态 → 第二方重入」不抛 InvalidTransitionError。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from sqlalchemy import update

from backend.core.database import SessionLocal
from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.services.plan_run_abort import PlanRunAbortError, abort_plan_run
from backend.services.plan_run_aggregation import apply_plan_run_aggregation
from backend.services.state_machine import InvalidTransitionError


def _terminal_jobs(db_session, sample_plan_run, sample_plan, sample_device, sample_host, status: JobStatus):
  """Attach two terminal jobs so aggregation can close the run."""
  second_device = Device(
    serial=f"race-{sample_plan_run.id}-2",
    host_id=sample_host.id,
    status="ONLINE",
  )
  db_session.add(second_device)
  db_session.flush()
  devices = [sample_device, second_device]
  jobs = []
  for device in devices:
    job = JobInstance(
      plan_run_id=sample_plan_run.id,
      plan_id=sample_plan.id,
      device_id=device.id,
      host_id=sample_host.id,
      status=status.value,
      pipeline_def={"lifecycle": {"init": [], "teardown": []}},
      ended_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    jobs.append(job)
  db_session.commit()
  for job in jobs:
    db_session.refresh(job)
  return jobs


def test_aggregator_reentry_after_terminal_does_not_raise(db_session, sample_plan_run, sample_plan, sample_device, sample_host):
  """aggregator 先落 SUCCESS → 二次 apply_plan_run_aggregation 返回 False,不炸。"""
  jobs = _terminal_jobs(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host, JobStatus.COMPLETED,
  )

  applied_first = apply_plan_run_aggregation(sample_plan_run, jobs)
  assert applied_first is True
  assert sample_plan_run.status == PlanRunStatus.SUCCESS.value

  applied_second = apply_plan_run_aggregation(sample_plan_run, jobs)
  assert applied_second is False
  assert sample_plan_run.status == PlanRunStatus.SUCCESS.value


def test_unknown_job_keeps_plan_run_running_and_never_degraded(
  db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
  """PostgreSQL 持久化回归：UNKNOWN 未完成 recovery/grace 前不得终态化 PlanRun。"""
  unknown_device = Device(
    serial=f"race-{sample_plan_run.id}-unknown",
    host_id=sample_host.id,
    status="ONLINE",
  )
  db_session.add(unknown_device)
  db_session.flush()
  completed = JobInstance(
    plan_run_id=sample_plan_run.id,
    plan_id=sample_plan.id,
    device_id=sample_device.id,
    host_id=sample_host.id,
    status=JobStatus.COMPLETED.value,
    pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    ended_at=datetime.now(timezone.utc),
  )
  unknown = JobInstance(
    plan_run_id=sample_plan_run.id,
    plan_id=sample_plan.id,
    device_id=unknown_device.id,
    host_id=sample_host.id,
    status=JobStatus.UNKNOWN.value,
    pipeline_def={"lifecycle": {"init": [], "teardown": []}},
  )
  db_session.add_all([completed, unknown])
  db_session.commit()

  applied = apply_plan_run_aggregation(sample_plan_run, [completed, unknown])
  db_session.commit()
  db_session.expire_all()

  persisted = db_session.get(type(sample_plan_run), sample_plan_run.id)
  assert applied is False
  assert persisted.status == PlanRunStatus.RUNNING.value
  assert persisted.ended_at is None
  assert persisted.result_summary is None


def test_abort_after_aggregator_terminal_raises_abort_error_not_invalid_transition(
  db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
  """aggregator 先落 SUCCESS → abort 抛 PlanRunAbortError,非 InvalidTransitionError。"""
  jobs = _terminal_jobs(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host, JobStatus.COMPLETED,
  )
  apply_plan_run_aggregation(sample_plan_run, jobs)
  db_session.commit()
  db_session.refresh(sample_plan_run)
  assert sample_plan_run.status == PlanRunStatus.SUCCESS.value

  with pytest.raises(PlanRunAbortError, match="already terminal"):
    abort_plan_run(sample_plan_run.id, db=db_session, reason="aborted_by_user")


def test_aggregator_after_abort_terminal_does_not_raise(
  db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
  """abort 先落 FAILED(全 COMPLETED + abort_requested override) → aggregator 重入不炸。"""
  jobs = _terminal_jobs(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host, JobStatus.COMPLETED,
  )
  sample_plan_run.run_context = {
    "abort_requested": {
      "at": datetime.now(timezone.utc).isoformat(),
      "reason": "aborted_by_user",
      "triggered_by": "test",
    },
  }
  db_session.commit()

  with patch("backend.services.plan_run_abort.should_trigger_dedup", return_value=False), patch(
    "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
  ), patch("backend.services.plan_run_abort.schedule_emit"):
    abort_plan_run(sample_plan_run.id, db=db_session, reason="aborted_by_user")

  db_session.refresh(sample_plan_run)
  assert sample_plan_run.status == PlanRunStatus.FAILED.value

  applied = apply_plan_run_aggregation(sample_plan_run, jobs)
  assert applied is False
  assert sample_plan_run.status == PlanRunStatus.FAILED.value


def test_abort_then_aggregator_both_paths_never_raise_invalid_transition(
  db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
  """abort 先落 FAILED(全 ABORTED) → aggregator 重入不炸;显式排除 InvalidTransitionError。"""
  jobs = _terminal_jobs(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host, JobStatus.ABORTED,
  )

  with patch("backend.services.plan_run_abort.should_trigger_dedup", return_value=False), patch(
    "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
  ), patch("backend.services.plan_run_abort.schedule_emit"):
    abort_plan_run(sample_plan_run.id, db=db_session, reason="aborted_by_user")

  db_session.refresh(sample_plan_run)
  assert sample_plan_run.status == PlanRunStatus.FAILED.value

  try:
    applied = apply_plan_run_aggregation(sample_plan_run, jobs)
  except InvalidTransitionError:
    pytest.fail("aggregator re-entry after abort terminal must not raise InvalidTransitionError")
  assert applied is False


def test_postgresql_abort_and_aggregator_are_serialized_by_plan_run_lock(
  db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
  jobs = _terminal_jobs(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
    JobStatus.COMPLETED,
  )
  barrier = threading.Barrier(2)
  errors: list[Exception] = []

  def aggregate():
    from backend.services.aggregator_sync import plan_aggregator_sync

    db = SessionLocal()
    try:
      job = db.get(JobInstance, jobs[0].id)
      barrier.wait(timeout=5)
      plan_aggregator_sync(job, db)
      db.commit()
    except Exception as exc:
      errors.append(exc)
    finally:
      db.close()

  def abort():
    db = SessionLocal()
    try:
      barrier.wait(timeout=5)
      abort_plan_run(
        sample_plan_run.id, db=db, reason="concurrent_abort",
      )
    except PlanRunAbortError:
      pass
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
      threading.Thread(target=aggregate),
      threading.Thread(target=abort),
    ]
    for thread in threads:
      thread.start()
    for thread in threads:
      thread.join(timeout=10)

  assert all(not thread.is_alive() for thread in threads)
  assert errors == []
  db_session.expire_all()
  persisted = db_session.get(type(sample_plan_run), sample_plan_run.id)
  assert persisted.status in {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.FAILED.value,
  }


def test_abort_batch_pending_updates_counters_and_audit_once(
        db_session, sample_plan_run, sample_plan, sample_device, sample_host):
    """#492：PENDING 批量 abort → 状态批量置位、计数器聚合。"""
    from backend.models.plan_run import PlanRun
    from backend.models.job import JobInstance
    from backend.models.enums import JobStatus
    from backend.models.host import Device
    from backend.services.plan_run_abort import abort_plan_run

    run = db_session.get(PlanRun, sample_plan_run.id)
    db_session.add_all([
        Device(serial=f"bulk-{i}", host_id=sample_host.id, status="ONLINE")
        for i in range(15)
    ])
    db_session.flush()
    devs = db_session.query(Device).filter(
        Device.serial.like("bulk-%")).all()
    job_ids = []
    for dev in devs:
        job = JobInstance(
            plan_run_id=run.id, plan_id=sample_plan.id,
            device_id=dev.id, host_id=sample_host.id,
            status=JobStatus.PENDING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.flush()
        job_ids.append(job.id)
    run.total_job_count = len(job_ids)
    db_session.commit()

    result = abort_plan_run(run.id, db=db_session, reason="bulk_test")

    db_session.expire_all()
    run = db_session.get(PlanRun, run.id)
    remaining = db_session.query(JobInstance).filter(
        JobInstance.plan_run_id == run.id,
        JobInstance.status == JobStatus.PENDING.value,
    ).count()
    assert remaining == 0
    aborted = db_session.query(JobInstance).filter(
        JobInstance.plan_run_id == run.id,
        JobInstance.status == JobStatus.ABORTED.value,
    ).count()
    assert aborted == 15
    assert run.aborted_job_count == 15
    assert run.terminal_job_count == 15
    assert len(result.get("aborted_jobs") or []) == 15


def test_abort_bulk_emits_collapsed_job_status(
    db_session, sample_plan_run, sample_plan, sample_host,
):
    """#703：abort 推送 O(1)——一条汇总 JOB_STATUS + 一条 PLAN_RUN_STATUS，非逐 job。"""
    from unittest.mock import patch

    from backend.models.plan_run import PlanRun
    from backend.models.job import JobInstance
    from backend.models.enums import JobStatus
    from backend.models.host import Device
    from backend.services.plan_run_abort import abort_plan_run

    run = db_session.get(PlanRun, sample_plan_run.id)
    db_session.add_all([
        Device(serial=f"emit-{i}", host_id=sample_host.id, status="ONLINE")
        for i in range(12)
    ])
    db_session.flush()
    devs = db_session.query(Device).filter(Device.serial.like("emit-%")).all()
    for dev in devs:
        db_session.add(
            JobInstance(
                plan_run_id=run.id,
                plan_id=sample_plan.id,
                device_id=dev.id,
                host_id=sample_host.id,
                status=JobStatus.PENDING.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
        )
    run.total_job_count = len(devs)
    db_session.commit()

    with patch(
        "backend.services.plan_run_abort.should_trigger_dedup", return_value=False,
    ), patch(
        "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
    ), patch("backend.services.plan_run_abort.schedule_emit") as emit:
        abort_plan_run(run.id, db=db_session, reason="emit_collapse")

    job_status_emits = []
    plan_status_emits = []
    for c in emit.call_args_list:
        event = c.args[0] if c.args else None
        if event == "job_status":
            job_status_emits.append(c.args[1])
        elif event == "plan_run_status":
            plan_status_emits.append(c.args[1])

    assert len(job_status_emits) == 1, f"expected 1 bulk job_status, got {len(job_status_emits)}"
    assert len(plan_status_emits) == 1
    payload = job_status_emits[0]["payload"]
    assert payload.get("abort_bulk") is True
    assert payload.get("aborted_count") == 12
    assert "job_id" not in payload


def test_abort_pending_count_uses_returning_after_concurrent_claim(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    """#988：预读 PENDING 后并发 claim→RUNNING 时，计数用实际 UPDATE 行，并纳入停止协议。

    #2012 起 abort 在 commit 后先重发 phase-2 预锁再碰 plan_run，claim 可赢的窗口
    收窄为「#703 commit 与 phase-2 预锁之间」——注入点随竞窗迁移（见
    `inject_claim_in_phase2_window`），断言语义不变。
    """
    from backend.models.host import Device
    from backend.models.plan_run import PlanRun

    run = db_session.get(PlanRun, sample_plan_run.id)
    second = Device(
        serial=f"race-claim-{sample_plan_run.id}",
        host_id=sample_host.id,
        status="ONLINE",
    )
    db_session.add(second)
    db_session.flush()

    jobs = []
    for device in (sample_device, second):
        job = JobInstance(
            plan_run_id=run.id,
            plan_id=sample_plan.id,
            device_id=device.id,
            host_id=sample_host.id,
            status=JobStatus.PENDING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.flush()
        jobs.append(job)

    claimed_id = jobs[0].id
    kept_pending_id = jobs[1].id
    run.total_job_count = 2
    # Host 计数器行：批量路径会对命中 host 做 +cnt
    from backend.models.plan_run import PlanRunHost

    db_session.add(
        PlanRunHost(
            plan_run_id=run.id,
            host_id=sample_host.id,
            total_job_count=2,
        )
    )
    db_session.commit()

    claim_injected = {"done": False}

    def inject_claim_in_phase2_window(seconds, phase):
        """#2012 后竞窗迁移：claim 注入点从「第一条 UPDATE job_instance」挪到
        「#703 commit 之后、phase-2 重发预锁之前」。

        旧编排里 abort 发出批量 UPDATE 时已不持 job 行锁（commit 已释放预锁），
        并发 claim 可以赢。#2012 让 abort 在 commit 后**先重发同序预锁再碰
        plan_run**——批量 UPDATE 执行时本会话又持有全部 pending 行锁，若仍在
        UPDATE 的注入点开同进程第二会话做 claim UPDATE，它会与本会话的自持锁
        互等（测试编排自锁，非生产形态）。新窗口是 claim 唯一还能赢的位置：
        commit 与 phase-2 预锁之间；此后 claim 的 UPDATE 只能排队并被
        `WHERE status='PENDING'` 拒绝。
        """
        if phase == "abort_requested" and not claim_injected["done"]:
            claim_injected["done"] = True
            other = SessionLocal()
            try:
                other.execute(
                    update(JobInstance)
                    .where(
                        JobInstance.id == claimed_id,
                        JobInstance.status == JobStatus.PENDING.value,
                    )
                    .values(
                        status=JobStatus.RUNNING.value,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                other.commit()
            finally:
                other.close()

    with patch(
        "backend.services.plan_run_abort.record_plan_run_abort_lock_seconds",
        inject_claim_in_phase2_window,
    ), patch(
        "backend.services.plan_run_abort.should_trigger_dedup",
        return_value=False,
    ), patch(
        "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
    ), patch("backend.services.plan_run_abort.schedule_emit"), patch(
        "backend.services.plan_run_abort.schedule_agent_control_fanout",
    ) as fanout:
        result = abort_plan_run(run.id, db=db_session, reason="race_claim")

    assert claim_injected["done"] is True
    assert result["aborted_jobs"] == [kept_pending_id]
    assert result["abort_requested_jobs"] == [claimed_id]

    db_session.expire_all()
    run = db_session.get(PlanRun, run.id)
    claimed = db_session.get(JobInstance, claimed_id)
    kept = db_session.get(JobInstance, kept_pending_id)
    host_row = db_session.query(PlanRunHost).filter_by(
        plan_run_id=run.id, host_id=sample_host.id,
    ).one()

    assert claimed.status == JobStatus.RUNNING.value
    assert kept.status == JobStatus.ABORTED.value
    assert run.aborted_job_count == 1
    assert run.terminal_job_count == 1
    assert host_row.aborted_job_count == 1
    assert host_row.terminal_job_count == 1
    # 仍有 RUNNING 待 ACK → PlanRun 不得因虚高 aborted 计数提前 FAILED
    assert run.status == PlanRunStatus.RUNNING.value
    assert run.run_context["abort_requested"]["requested_job_ids"] == [claimed_id]

    assert fanout.called, "claimed RUNNING job must receive abort control"
    items = fanout.call_args.args[0]
    assert len(items) == 1
    assert items[0][0] == sample_host.id
    assert items[0][1]["payload"]["job_ids"] == [claimed_id]


def test_abort_run_context_patch_preserves_concurrent_writer_keys(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    """#793：abort 的 run_context 更新必须分段（jsonb_set）——并发写者（如归档
    的 record_scan_archive_state）在 abort 读快照之后写入的键不得被整段写回抹掉。

    注入点：批量 UPDATE job_instance 前（abort 已写完 abort_requested 首段），
    用独立会话写入 `{archive}`；旧整段实现会在后续整写时把它覆盖掉。
    """
    import json as _json

    from sqlalchemy import text as _text
    from sqlalchemy.orm import Session

    from backend.models.plan_run import PlanRun

    run = db_session.get(PlanRun, sample_plan_run.id)
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.PENDING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    run.total_job_count = 1
    db_session.commit()

    injected = {"done": False}
    orig_execute = Session.execute

    def _is_plan_run_read(statement) -> bool:
        # abort 开场的 `select(PlanRun)...with_for_update` —— 其后 run_ctx 快照
        # 才被复制；注入点必须在快照读取之后、首个 run_context 写之前。
        try:
            # 渲染 SQL 含换行 → 归一空白后再做子串匹配
            sql = " ".join(str(statement).split())
        except Exception:
            return False
        return "SELECT" in sql.upper() and "FROM plan_run WHERE" in sql

    def execute_with_foreign_archive(self, statement, *args, **kwargs):
        result = orig_execute(self, statement, *args, **kwargs)
        if self is db_session and not injected["done"] and _is_plan_run_read(statement):
            injected["done"] = True
            # 同一事务内模拟「外部写者（归档）在 abort 读快照之后写入 {archive}」：
            # 旧整段实现随后会用 stale 快照覆盖该键；分段 jsonb_set 只改自己的键。
            # （跨会话 UPDATE 会与 abort 的行锁互等——同事务注入才可控可测。）
            orig_execute(
                self,
                _text(
                    "UPDATE plan_run SET run_context = jsonb_set("
                    "  COALESCE(NULLIF(run_context, 'null'::jsonb), '{}'::jsonb), "
                    "  '{archive}', CAST(:value AS jsonb), true"
                    ") WHERE id = :run_id"
                ),
                {
                    "run_id": run.id,
                    "value": _json.dumps({"hosts_triggered": ["h-race"]}),
                },
            )
        return result

    with patch.object(Session, "execute", execute_with_foreign_archive), patch(
        "backend.services.plan_run_abort.should_trigger_dedup",
        return_value=False,
    ), patch(
        "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
    ), patch("backend.services.plan_run_abort.schedule_emit"):
        abort_plan_run(run.id, db=db_session, reason="race_archive")

    assert injected["done"] is True
    db_session.expire_all()
    fresh = db_session.get(PlanRun, run.id)
    ctx = fresh.run_context or {}
    assert ctx.get("archive") == {"hosts_triggered": ["h-race"]}, (
        "并发写者（归档）的键不得被 abort 抹掉"
    )
    assert ctx["abort_requested"]["reason"] == "race_archive"
    assert ctx["abort_requested"]["requested_job_ids"] == []


def test_abort_all_terminal_running_run_is_not_finalized_as_success(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host, monkeypatch,
):
    """#1552：卡在 RUNNING、job 已全终态时 abort，必须落 FAILED 而非 SUCCESS。

    `_patch_run_context` 走原生 text() UPDATE，SQLAlchemy 不会同步 identity map；
    而调用方手上的 `run_ctx` 只是 `dict(pr.run_context)` 的副本，写回它并不会改到
    ORM 属性。若 expire 排在聚合读取（`_abort_requested`）之后，同 session 的聚合
    读到的仍是**写入前**的 run_context → abort 覆盖不生效 →
    `aborted=0, failed_only=0` 收敛成 SUCCESS，并发出成功通知、
    `result_summary.abort_requested=False`。

    这条路径正是 counter_reconciler（#789）存在的原因：run 卡在 RUNNING 而 job
    已全部终态（计数器漂移 / 聚合副作用失败），运维此时点 abort。
    """
    from backend.models.plan_run import PlanRun

    _terminal_jobs(
        db_session, sample_plan_run, sample_plan, sample_device, sample_host,
        JobStatus.COMPLETED,
    )
    assert sample_plan_run.status == PlanRunStatus.RUNNING.value

    monkeypatch.setattr(
        "backend.services.plan_run_aggregation._notify_plan_run_terminal", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "backend.services.plan_run_abort.should_trigger_dedup", lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "backend.services.plan_run_abort.enqueue_dedup_terminal_sync", lambda *a, **k: None,
    )
    monkeypatch.setattr("backend.services.plan_run_abort.schedule_emit", lambda *a, **k: None)

    abort_plan_run(
        sample_plan_run.id, db=db_session, reason="aborted_by_user", triggered_by="tester",
    )

    db_session.expire_all()
    fresh = db_session.get(PlanRun, sample_plan_run.id)
    assert fresh.status == PlanRunStatus.FAILED.value, (
        f"abort 后应落 FAILED，实际 {fresh.status}——abort_requested 覆盖未生效"
    )
    assert (fresh.result_summary or {}).get("abort_requested") is True


def test_abort_running_heavy_does_not_load_full_job_orm_rows(
    db_session, sample_plan_run, sample_plan, sample_host,
):
    """#703：RUNNING 为主时只 SELECT id/host/status，不 ``query(JobInstance).all()``。"""
    from unittest.mock import patch

    from backend.models.host import Device
    from backend.models.plan_run import PlanRun
    from backend.services.plan_run_abort import abort_plan_run

    run = db_session.get(PlanRun, sample_plan_run.id)
    db_session.add_all([
        Device(serial=f"run-heavy-{i}", host_id=sample_host.id, status="ONLINE")
        for i in range(20)
    ])
    db_session.flush()
    # 既有终态 job + 活跃 RUNNING：若误装全表会把 COMPLETED 也拉进 session。
    for i, serial in enumerate([f"run-heavy-{i}" for i in range(20)]):
        dev = db_session.query(Device).filter(Device.serial == serial).one()
        status = JobStatus.COMPLETED.value if i < 5 else JobStatus.RUNNING.value
        db_session.add(
            JobInstance(
                plan_run_id=run.id,
                plan_id=sample_plan.id,
                device_id=dev.id,
                host_id=sample_host.id,
                status=status,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
        )
    run.total_job_count = 20
    run.completed_job_count = 5
    run.terminal_job_count = 5
    db_session.commit()

    loaded_full = {"count": 0}
    real_query = db_session.query

    def _spy_query(*entities, **kwargs):
        q = real_query(*entities, **kwargs)
        if entities == (JobInstance,) or (
            len(entities) == 1 and entities[0] is JobInstance
        ):
            real_all = q.all

            def _all():
                rows = real_all()
                loaded_full["count"] += 1
                return rows

            q.all = _all  # type: ignore[method-assign]
        return q

    with patch.object(db_session, "query", side_effect=_spy_query), patch(
        "backend.services.plan_run_abort.should_trigger_dedup", return_value=False,
    ), patch(
        "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
    ), patch("backend.services.plan_run_abort.schedule_emit"), patch(
        "backend.services.plan_run_abort.schedule_agent_control_fanout",
    ) as fanout:
        result = abort_plan_run(run.id, db=db_session, reason="narrow_select")

    assert loaded_full["count"] == 0, "RUNNING 路径不得全量 JobInstance.all()"
    assert len(result.get("abort_requested_jobs") or []) == 15
    assert result.get("aborted_jobs") == []
    assert fanout.call_count == 1
    items = fanout.call_args.args[0]
    assert len(items) == 1
    assert len(items[0][1]["payload"]["job_ids"]) == 15


def test_abort_pending_commits_abort_requested_before_batch(
    db_session, sample_plan_run, sample_plan, sample_host,
):
    """#703：有 PENDING 时 abort_requested 先 commit，再开第二段事务做批量终态。"""
    from unittest.mock import patch

    from backend.models.host import Device
    from backend.models.plan_run import PlanRun
    from backend.services.plan_run_abort import abort_plan_run

    run = db_session.get(PlanRun, sample_plan_run.id)
    db_session.add_all([
        Device(serial=f"early-c-{i}", host_id=sample_host.id, status="ONLINE")
        for i in range(8)
    ])
    db_session.flush()
    for serial in [f"early-c-{i}" for i in range(8)]:
        dev = db_session.query(Device).filter(Device.serial == serial).one()
        db_session.add(
            JobInstance(
                plan_run_id=run.id,
                plan_id=sample_plan.id,
                device_id=dev.id,
                host_id=sample_host.id,
                status=JobStatus.PENDING.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
        )
    run.total_job_count = 8
    db_session.commit()

    commits: list[str] = []
    real_commit = db_session.commit

    def _commit():
        # 第一次 abort 内 commit：abort_requested 已落库、PENDING 尚未终态
        ctx = (db_session.get(PlanRun, run.id).run_context or {})
        ar = ctx.get("abort_requested")
        pending_left = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == run.id,
            JobInstance.status == JobStatus.PENDING.value,
        ).count()
        if ar and pending_left == 8:
            commits.append("early")
        elif pending_left == 0:
            commits.append("final")
        else:
            commits.append(f"other:pending={pending_left}")
        return real_commit()

    with patch.object(db_session, "commit", side_effect=_commit), patch(
        "backend.services.plan_run_abort.should_trigger_dedup", return_value=False,
    ), patch(
        "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
    ), patch("backend.services.plan_run_abort.schedule_emit"):
        result = abort_plan_run(run.id, db=db_session, reason="early_commit")

    assert "early" in commits, f"expected early commit releasing lock, got {commits}"
    assert "final" in commits
    assert len(result.get("aborted_jobs") or []) == 8


def test_abort_multi_host_control_uses_single_fanout(
    db_session, sample_plan_run, sample_plan, sample_host,
):
    """#703：多 host RUNNING abort 只调用一次 schedule_agent_control_fanout。"""
    from datetime import datetime, timezone
    from unittest.mock import patch

    from backend.models.enums import JobStatus
    from backend.models.host import Device, Host
    from backend.models.job import JobInstance
    from backend.models.plan_run import PlanRun
    from backend.services.plan_run_abort import abort_plan_run

    run = db_session.get(PlanRun, sample_plan_run.id)
    hosts = [sample_host]
    for i in range(1, 12):
        h = Host(
            id=f"fanout-h-{i}",
            hostname=f"fanout-host-{i}",
            name=f"fanout-host-{i}",
            ip=f"10.9.0.{i}",
            ip_address=f"10.9.0.{i}",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
        )
        db_session.add(h)
        hosts.append(h)
    db_session.flush()
    for i, h in enumerate(hosts):
        dev = Device(serial=f"fanout-d-{i}", host_id=h.id, status="BUSY")
        db_session.add(dev)
        db_session.flush()
        db_session.add(
            JobInstance(
                plan_run_id=run.id,
                plan_id=sample_plan.id,
                device_id=dev.id,
                host_id=h.id,
                status=JobStatus.RUNNING.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
        )
    run.total_job_count = len(hosts)
    db_session.commit()

    with patch(
        "backend.services.plan_run_abort.should_trigger_dedup", return_value=False,
    ), patch(
        "backend.services.plan_run_abort.enqueue_dedup_terminal_sync",
    ), patch("backend.services.plan_run_abort.schedule_emit"), patch(
        "backend.services.plan_run_abort.schedule_agent_control_fanout",
    ) as fanout:
        abort_plan_run(run.id, db=db_session, reason="multi_host_fanout")

    assert fanout.call_count == 1
    items = fanout.call_args.args[0]
    assert len(items) == 12
    host_ids = {item[0] for item in items}
    assert host_ids == {h.id for h in hosts}
    for _hid, data in items:
        assert data["command"] == "abort"
        assert len(data["payload"]["job_ids"]) == 1
