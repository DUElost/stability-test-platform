"""#3723 v1.5 §3.3.2：通用 recycler 解除保护、乱序推导与真实 complete 回归。"""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import os
import pytest
from backend.core.database import SessionLocal
from backend.models.job import JobInstance,StepTrace
from backend.models.device_lease import DeviceLease
from backend.models.plan_run import PlanRun
from backend.scheduler import recycler
from backend.scheduler.device_lease_reconciler import _disarm_grace_defers_reap
from backend.services.agent_completion import derive_disarm_results_from_traces
from backend.tests.scheduler.test_recycler import _seed_running_job,_cleanup_seed,_seed_fresh_coordinator_heartbeat

@pytest.fixture(autouse=True)
def _safety_fuse(monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Reviewer U2 probe must never send host signals')
    monkeypatch.setattr(os,'kill',forbidden);monkeypatch.setattr(os,'killpg',forbidden)

PIPE={'lifecycle':{'init':[{'step_id':'setup','action':'script:powercycle_setup'}],'teardown':[{'step_id':'finish','action':'script:powercycle_finish','timeout_seconds':600}]}}

def test_fresh_disarm_survives_actual_generic_recycler(db_session,monkeypatch):
    now=datetime.now(timezone.utc)
    seed=_seed_running_job(started_at=now-timedelta(hours=2),updated_at=now,pipeline_def=PIPE,execution_state='EXECUTING_DISARM',last_execution_heartbeat_at=now)
    with SessionLocal() as db:
        job=db.get(JobInstance,seed['job_id']);job.last_progress_at=now
        run=db.get(PlanRun,seed['plan_run_id']);run.run_context={'abort_requested':{'at':(now-timedelta(seconds=90)).isoformat()}}
        db.add(DeviceLease(device_id=seed['device_id'],job_id=seed['job_id'],host_id=seed['host_id'],lease_type='JOB',status='ACTIVE',fencing_token='review-active-token',lease_generation=1,agent_instance_id='review-isolated',expires_at=now+timedelta(hours=1)))
        db.commit()
        assert _disarm_grace_defers_reap(job,now-timedelta(seconds=90),now)
    _seed_fresh_coordinator_heartbeat(seed,at=now)
    monkeypatch.setattr(recycler,'_fill_deferred_post_completions',lambda *_a:0)
    monkeypatch.setattr(recycler,'_prune_steptrace_artifacts',lambda *_a:None)
    monkeypatch.setattr(recycler,'schedule_emit',lambda *_a,**_k:None)
    try:
        recycler.recycle_once()
        with SessionLocal() as db:
            row=db.get(JobInstance,seed['job_id'])
            assert row.status=='RUNNING',{'status':row.status,'reason':row.status_reason,'state':row.execution_state}
    finally:_cleanup_seed(seed)

def test_disabled_long_teardown_does_not_extend_active_step_ceiling(db_session):
    now=datetime.now(timezone.utc)
    job=SimpleNamespace(execution_state='EXECUTING_DISARM',last_progress_at=now,pipeline_def={'lifecycle':{'teardown':[{'step_id':'active','action':'script:powercycle_finish','timeout_seconds':600},{'step_id':'disabled','action':'script:sleep_finish','timeout_seconds':3600,'enabled':False}]}})
    assert not _disarm_grace_defers_reap(job,now-timedelta(seconds=700),now)

def test_trace_delivery_order_must_not_replace_latest_terminal(db_session):
    now=datetime.now(timezone.utc)
    traces=[SimpleNamespace(stage='init',step_id='setup',event_type='STARTED',status='RUNNING',exit_code=None,original_ts=now-timedelta(seconds=30)),SimpleNamespace(stage='teardown',step_id='finish',event_type='COMPLETED',status='COMPLETED',exit_code=0,original_ts=now),SimpleNamespace(stage='teardown',step_id='finish',event_type='STARTED',status='RUNNING',exit_code=None,original_ts=now-timedelta(seconds=10))]
    got=derive_disarm_results_from_traces(PIPE,traces)
    assert got['power-cycle']['result']=='executed_unverified',got


@pytest.mark.parametrize('reported',[True,False,'late_started'])
def test_actual_complete_persists_results_and_replay_counts_once(client,db_session,monkeypatch,reported):
    from backend.services import agent_completion as ac
    from backend.core import task_queue
    now=datetime.now(timezone.utc)
    seed=_seed_running_job(started_at=now-timedelta(minutes=1),updated_at=now,pipeline_def=PIPE,execution_state='EXECUTING_STEP',last_execution_heartbeat_at=now)
    seen=[]
    monkeypatch.setattr(ac,'record_disarm_result',lambda f,r:seen.append((f,r)))
    monkeypatch.setattr(ac.PlanAggregator,'on_job_terminal',AsyncMock())
    monkeypatch.setattr(ac,'broadcast_run_job_update',AsyncMock())
    monkeypatch.setattr(task_queue,'get_queue',lambda:SimpleNamespace(enqueue=AsyncMock()))
    try:
        with SessionLocal() as db:
            db.add(DeviceLease(device_id=seed['device_id'],job_id=seed['job_id'],host_id=seed['host_id'],lease_type='JOB',status='ACTIVE',fencing_token='review-u2-test-token',lease_generation=1,agent_instance_id='review-u2-isolated',expires_at=now+timedelta(hours=1)))
            for sid,stage,status,event in [('setup','init','COMPLETED','COMPLETED'),('finish','teardown','COMPLETED','COMPLETED')]:
                db.add(StepTrace(job_id=seed['job_id'],step_id=sid,stage=stage,status=status,event_type=event,exit_code=0,original_ts=now))
            db.commit()
        if reported == 'late_started':
            with SessionLocal() as db:
                db.add(StepTrace(job_id=seed['job_id'],step_id='finish',stage='teardown',status='RUNNING',event_type='STARTED',exit_code=None,original_ts=now-timedelta(seconds=10)))
                db.commit()
        renewal=client.post('/api/v1/agent/leases/extend-batch',json={'host_id':seed['host_id'],'agent_instance_id':'review-u2-isolated','leases':[{'job_id':seed['job_id'],'fencing_token':'review-u2-test-token','execution_state':'EXECUTING_DISARM','progress_marker':{'last_progress_at':now.isoformat()}}]})
        assert renewal.status_code==200,renewal.text
        with SessionLocal() as db:
            row=db.get(JobInstance,seed['job_id']);assert row.execution_state=='EXECUTING_DISARM'
            assert row.last_progress_at is not None
        payload={'update':{'status':'FINISHED'},'fencing_token':'review-u2-test-token'}
        expected='failed' if reported is True else 'executed_unverified'
        if reported is True:payload['disarm_results']={'power-cycle':{'result':'failed','step_id':'finish','exit_code':1,'readback_summary':'fixture failure'}}
        response=client.post(f"/api/v1/agent/jobs/{seed['job_id']}/complete",json=payload)
        assert response.status_code==200,response.text
        response2=client.post(f"/api/v1/agent/jobs/{seed['job_id']}/complete",json=payload)
        assert response2.status_code==200,response2.text
        with SessionLocal() as db:
            row=db.get(JobInstance,seed['job_id']);assert row.status=='COMPLETED'
            assert row.disarm_results['power-cycle']['result']==expected
            assert not row.status_reason
            from backend.services.plan_run_catalog import _job_out
            assert _job_out(row,[]).disarm_results['power-cycle']['result']==expected
        assert seen==[('power-cycle',expected)],seen
    finally:_cleanup_seed(seed)


def test_fresh_progress_protects_even_with_stale_execution_heartbeat(db_session, monkeypatch):
    """§3.3.2 E：基础执行心跳较旧但进度新鲜，仍在解除宽限内 → 不被 recycle_once 回收。"""
    now = datetime.now(timezone.utc)
    seed = _seed_running_job(
        started_at=now - timedelta(hours=2),
        updated_at=now,
        pipeline_def=PIPE,
        execution_state="EXECUTING_DISARM",
        last_execution_heartbeat_at=now - timedelta(hours=2),
    )
    with SessionLocal() as db:
        job = db.get(JobInstance, seed["job_id"])
        job.last_progress_at = now
        run = db.get(PlanRun, seed["plan_run_id"])
        run.run_context = {
            "abort_requested": {"at": (now - timedelta(seconds=90)).isoformat()}
        }
        db.add(
            DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=seed["host_id"],
                lease_type="JOB",
                status="ACTIVE",
                fencing_token="review-stale-exec-token",
                lease_generation=1,
                agent_instance_id="review-isolated",
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()
        assert _disarm_grace_defers_reap(job, now - timedelta(seconds=90), now)
    _seed_fresh_coordinator_heartbeat(seed, at=now)
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda *_a: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda *_a: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *_a, **_k: None)
    try:
        recycler.recycle_once()
        with SessionLocal() as db:
            row = db.get(JobInstance, seed["job_id"])
            assert row.status == "RUNNING", {
                "status": row.status,
                "reason": row.status_reason,
                "state": row.execution_state,
            }
            assert row.execution_state == "EXECUTING_DISARM"
    finally:
        _cleanup_seed(seed)


def test_stale_progress_allows_generic_recycler(db_session, monkeypatch):
    """保护失效（进度超过两周期）时，通用 recycler 仍可回收。"""
    now = datetime.now(timezone.utc)
    seed = _seed_running_job(
        started_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
        pipeline_def=PIPE,
        execution_state="EXECUTING_DISARM",
        last_execution_heartbeat_at=now - timedelta(hours=2),
    )
    with SessionLocal() as db:
        job = db.get(JobInstance, seed["job_id"])
        job.last_progress_at = now - timedelta(seconds=120)  # > 2*30s
        run = db.get(PlanRun, seed["plan_run_id"])
        run.run_context = {
            "abort_requested": {"at": (now - timedelta(seconds=90)).isoformat()}
        }
        db.add(
            DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=seed["host_id"],
                lease_type="JOB",
                status="ACTIVE",
                fencing_token="review-stale-progress-token",
                lease_generation=1,
                agent_instance_id="review-isolated",
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()
        assert not _disarm_grace_defers_reap(
            job, now - timedelta(seconds=90), now,
        )
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda *_a: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda *_a: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *_a, **_k: None)
    try:
        recycler.recycle_once()
        with SessionLocal() as db:
            row = db.get(JobInstance, seed["job_id"])
            assert row.status == "UNKNOWN", {
                "status": row.status,
                "reason": row.status_reason,
                "state": row.execution_state,
            }
    finally:
        _cleanup_seed(seed)


def test_later_failed_beats_earlier_completed_by_original_ts():
    now = datetime.now(timezone.utc)
    traces = [
        SimpleNamespace(
            stage="init", step_id="setup", event_type="COMPLETED",
            status="COMPLETED", exit_code=0, original_ts=now - timedelta(seconds=30),
        ),
        SimpleNamespace(
            stage="teardown", step_id="finish", event_type="COMPLETED",
            status="COMPLETED", exit_code=0, original_ts=now - timedelta(seconds=10),
        ),
        SimpleNamespace(
            stage="teardown", step_id="finish", event_type="FAILED",
            status="FAILED", exit_code=1, original_ts=now,
        ),
    ]
    got = derive_disarm_results_from_traces(PIPE, list(reversed(traces)))
    assert got["power-cycle"]["result"] == "failed", got


def test_running_heartbeat_deadline_uses_execution_hb_for_disarm():
    from backend.services.plan_run_devices import running_heartbeat_deadline
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        status="RUNNING",
        execution_state="EXECUTING_DISARM",
        last_execution_heartbeat_at=now,
        started_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=2),
        pipeline_def=PIPE,
        patrol_cycle_count=0,
        last_patrol_heartbeat_at=None,
        current_patrol_step=None,
        updated_at=now,
        next_retry_at=None,
    )
    deadline = running_heartbeat_deadline(job)
    assert deadline is not None
    # Must not anchor at 2h-old started_at
    assert deadline > now
