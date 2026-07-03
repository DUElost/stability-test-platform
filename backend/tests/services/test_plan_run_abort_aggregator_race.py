"""abort vs aggregator 竞态回归 — PlanRunStateMachine 引入后第二个写入方不得炸。

场景:两个写入方几乎同时从 RUNNING 落终态。生产路径依赖:
  1. apply_plan_run_aggregation 顶部的 _TERMINAL_PLAN_RUN_STATUSES 守卫
  2. abort_plan_run 开头的终态检查(抛 PlanRunAbortError,非 InvalidTransitionError)
  3. SELECT ... FOR UPDATE 串行化 read-modify-write

本测试用 DB fixture 验证「先落终态 → 第二方重入」不抛 InvalidTransitionError。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.job import JobInstance
from backend.services.plan_run_abort import PlanRunAbortError, abort_plan_run
from backend.services.plan_run_aggregation import apply_plan_run_aggregation
from backend.services.state_machine import InvalidTransitionError


def _terminal_jobs(db_session, sample_plan_run, sample_plan, sample_device, sample_host, status: JobStatus):
  """Attach two terminal jobs so aggregation can close the run."""
  jobs = []
  for i in range(2):
    job = JobInstance(
      plan_run_id=sample_plan_run.id,
      plan_id=sample_plan.id,
      device_id=sample_device.id,
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
