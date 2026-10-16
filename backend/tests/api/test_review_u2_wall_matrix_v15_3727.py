"""#3723 v1.5 §3.3.2 C：三入口墙钟错误契约矩阵。"""
import pytest
from backend.tests.api.test_onboarded_wall_clock_api_u2_3727 import (  # noqa: F401
    _create_ok_plan,
    _init_step,
    _seed_plan_defaults,
    _teardown_step,
    _uniq,
    onboarded_registry,
    pc_script,
)

# Cross-module: autouse from source module does not apply here; request it.
@pytest.fixture(autouse=True)
def _wall_matrix_seed(_seed_plan_defaults):
    return _seed_plan_defaults

from backend.models.plan import Plan,PlanStep
from backend.services.plan_dispatcher_core import build_lifecycle_from_snapshot,build_plan_snapshot

@pytest.mark.parametrize('entry',['create','update','append'])
@pytest.mark.parametrize('timeout',['omit',None,0,-1,659])
def test_all_save_entries_return_structured_rejection(entry,timeout,client,auth_headers,sample_script,pc_script,onboarded_registry,db_session):
    head=None
    if entry!='create':head=_create_ok_plan(client,auth_headers)
    before=(db_session.query(Plan).count(),db_session.query(PlanStep).count())
    td=_teardown_step()
    if timeout!='omit':td['timeout_seconds']=timeout
    data={'name':_uniq('review_reject'),'steps':[_init_step(),td],'project_key':'GENERIC','specialty_key':'ops'}
    if entry=='create':r=client.post('/api/v1/plans',json=data,headers=auth_headers)
    else:
        data['expected_updated_at']=head['updated_at']
        if entry=='update':r=client.put(f"/api/v1/plans/{head['id']}",json=data,headers=auth_headers)
        else:r=client.post(f"/api/v1/plans/{head['id']}/append-chain-tail",json=data,headers=auth_headers)
    assert r.status_code==422,r.text
    detail=r.json()['detail']
    assert isinstance(detail,dict) and detail.get('code')=='WALL_CLOCK_BELOW_ONBOARDED_BUDGET',r.text
    assert detail['violations'][0]['timeout_seconds']==(None if timeout=='omit' else timeout)
    assert before==(db_session.query(Plan).count(),db_session.query(PlanStep).count())

@pytest.mark.parametrize('timeout',[660,700])
@pytest.mark.parametrize('agent_env',[None,'1','43200'])
def test_saved_explicit_timeout_survives_snapshot_and_agent_env(timeout,agent_env,client,auth_headers,sample_script,pc_script,onboarded_registry,db_session,monkeypatch):
    data=_create_ok_plan(client,auth_headers,timeout)
    plan=db_session.get(Plan,data['id']);steps=db_session.query(PlanStep).filter(PlanStep.plan_id==plan.id).all()
    metadata={('check_device','1.0.0'):{'default_params':{},'param_schema':{}},('powercycle_finish','1.0.4'):{'default_params':dict(pc_script.default_params),'param_schema':{}}}
    snapshot=build_plan_snapshot(plan,steps,metadata)
    lifecycle=build_lifecycle_from_snapshot(snapshot)
    from backend.agent.pipeline_engine import _resolve_step_wall_clock
    if agent_env is None:monkeypatch.delenv('STP_STEP_WALL_CLOCK_SECONDS',raising=False)
    else:monkeypatch.setenv('STP_STEP_WALL_CLOCK_SECONDS',agent_env)
    assert lifecycle['teardown'][0]['timeout_seconds']==timeout
    assert _resolve_step_wall_clock(lifecycle['teardown'][0])==float(timeout)

@pytest.mark.parametrize('params,timeout,ok,minimum',[(None,300,False,660),(None,660,True,660),({'wait_device_online_seconds':100},159,False,160),({'wait_device_online_seconds':100},160,True,160)])
def test_real_api_uses_script_defaults_and_step_overrides(params,timeout,ok,minimum,client,auth_headers,sample_script,pc_script,onboarded_registry,db_session):
    step=_teardown_step(params=params,timeout_seconds=timeout)
    response=client.post('/api/v1/plans',json={'name':_uniq('review_defaults'),'steps':[_init_step(),step],'project_key':'GENERIC','specialty_key':'ops'},headers=auth_headers)
    if ok:
        assert response.status_code==201,response.text
        plan=db_session.get(Plan,response.json()['data']['id']);steps=db_session.query(PlanStep).filter(PlanStep.plan_id==plan.id).all()
        meta={('check_device','1.0.0'):{'default_params':{},'param_schema':{}},('powercycle_finish','1.0.4'):{'default_params':dict(pc_script.default_params),'param_schema':{}}}
        td=build_lifecycle_from_snapshot(build_plan_snapshot(plan,steps,meta))['teardown'][0]
        assert td['timeout_seconds']==timeout
        assert td['params']['wait_device_online_seconds']==minimum-60
    else:
        assert response.status_code==422,response.text
        assert response.json()['detail']['violations'][0]['min_wall_clock_seconds']==minimum
