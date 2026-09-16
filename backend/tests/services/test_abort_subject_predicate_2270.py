"""ADR-0043 主体语义的**消费侧**判据（#2270）。

写侧：host 级 abort 不写 run 级时钟，只维护名单 + `abort_requested_hosts[host].at`。
消费侧此前一律按「`run_context` 里有 `abort_requested` 键」判定 → 一台主机的 host 级
abort 让**同 run 的旁主机**看起来也在 abort 中：热更新门禁永久 409（abort 收口中），
而 reaper 按主体语义永远不会回收那些 job。

本文件钉住共享判据（`plan_run_abort.run_abort_pending` / `abort_pending_job_ids`）与
门禁的 `_abort_pending_ids` 的主体语义。
"""

from __future__ import annotations

from backend.services.host_upgrade_gate import _abort_pending_ids
from backend.services.plan_run_abort import abort_pending_job_ids, run_abort_pending

_HOST_CLOCK = {"at": "2026-09-16T00:00:00+00:00", "reason": "host"}


def _ctx_host_level_abort(host_id: str, job_ids: list[int]) -> dict:
    """host 级 abort 的 run_context 形态：**没有** run 级 ``at``。"""
    return {
        "abort_requested": {
            "reason": "host upgrade",
            "requested_job_ids": job_ids,
        },
        "abort_requested_hosts": {host_id: dict(_HOST_CLOCK)},
    }


def test_host_level_abort_only_covers_its_own_host():
    """#2270 核心：旁主机（从未被请求中止）不得被判为待中止。"""
    ctx = _ctx_host_level_abort("host-a", [1])

    pending = abort_pending_job_ids(ctx, [(1, "host-a"), (2, "host-b")])

    assert pending == {1}


def test_run_level_abort_covers_all_jobs_without_a_list():
    ctx = {"abort_requested": {"at": "2026-09-16T00:00:00+00:00"}}

    assert abort_pending_job_ids(ctx, [(1, "host-a"), (2, "host-b")]) == {1, 2}
    assert run_abort_pending(ctx) is True


def test_run_level_abort_with_a_list_covers_only_listed_jobs():
    ctx = {
        "abort_requested": {
            "at": "2026-09-16T00:00:00+00:00",
            "requested_job_ids": [2],
        },
    }

    assert abort_pending_job_ids(ctx, [(1, "host-a"), (2, "host-b")]) == {2}


def test_key_without_any_clock_covers_nothing():
    """键在但两个时钟都没有（含名单）→ 不覆盖任何 job——旧判据的永久 409 成因。"""
    ctx = {"abort_requested": {"reason": "host upgrade", "requested_job_ids": [1]}}

    assert abort_pending_job_ids(ctx, [(1, "host-a"), (2, "host-b")]) == set()
    assert run_abort_pending(ctx) is False


def test_missing_or_foreign_run_context_is_not_pending():
    assert abort_pending_job_ids(None, [(1, "host-a")]) == set()
    assert abort_pending_job_ids("not-a-dict", [(1, "host-a")]) == set()
    assert run_abort_pending(None) is False


def test_gate_pending_ids_ignore_a_sibling_host_abort(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    """门禁的 `_abort_pending_ids` 同口径：旁主机的 host 级 abort 不算待中止。"""
    from backend.models.job import JobInstance

    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status="RUNNING",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    sample_plan_run.run_context = _ctx_host_level_abort("some-other-host", [job.id])
    db_session.commit()

    assert _abort_pending_ids(db_session, [job]) == set()


def test_gate_pending_ids_include_our_own_host_abort(
    db_session, sample_plan_run, sample_plan, sample_device, sample_host,
):
    from backend.models.job import JobInstance

    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status="RUNNING",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    sample_plan_run.run_context = _ctx_host_level_abort(sample_host.id, [job.id])
    db_session.commit()

    assert _abort_pending_ids(db_session, [job]) == {job.id}
