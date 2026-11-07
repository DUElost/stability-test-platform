"""G4 occurrence/selection integration against isolated PostgreSQL."""
import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from sqlalchemy import select
from backend.services import issue_fact_store as facts
from backend.models.device_log_event import DeviceLogEvent
from backend.models.issue_fact import IssueOccurrence, IssueOccurrenceEvent, RepresentativeSelection
from backend.tests.fixtures.scan_membership import fixture_report


def setup_round(db, tmp_path, sample_host, *, key=None, round_no=1, dle=None, complete=True, kind='PERIODIC'):
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun
    if db.get(PlanRun, 42) is None:
        plan = Plan(name=f'g4-{uuid4()}', created_by='pytest')
        db.add(plan)
        db.flush()
        db.add(PlanRun(id=42, plan_id=plan.id, status='RUNNING', plan_snapshot={}, run_type='MANUAL', triggered_by='pytest'))
        db.flush()
    root = tmp_path / str(round_no)
    root.mkdir()
    ref, sidecar = fixture_report(root)
    if dle is None:
        dle = DeviceLogEvent(id=uuid4(), serial='SN1', platform='MTK', event_type='JE', state='LOCAL',
                             local_path='/event', host_id=sample_host.id, plan_run_id=42,
                             occurrence_key=key, detected_at=datetime.now(timezone.utc))
        db.add(dle)
        db.flush()
    ref['host_id'] = sample_host.id
    from pathlib import Path
    Path(ref['mapping_uri']).write_text(json.dumps({'rows': [{
        'sheet': 'aeeexp', 'row': 2, 'path': '/event/db.dbg', 'mapped': True,
        'dle_id': str(dle.id), 'occurrence_key': key, 'source': 'local',
        'evidence': dict.fromkeys(['dblog', 'mobilelog', 'bugreport'], complete)}]}))
    r = facts.record_scan_round(db, plan_run_id=42, platform='mtk', round_no=round_no,
        kind=kind, input_cutoff_at=datetime.now(timezone.utc),
        expected_set=[{'host_id': sample_host.id, 'platform': 'mtk', 'coverage': 'RECEIVED', 'reports': [ref]}])
    facts.set_scan_round_status(db, round_id=r.id, status='CLOSED')
    return r, dle


def match(req):
    return {'schema': 'stp.issue_match_result', 'schema_version': '1.0', 'results': [
        {'ref': r['ref'], 'matched_issue_id': req['existing'][0]['issue_id'] if req['existing'] else None,
         'new_group': None if req['existing'] else 'n1', 'branch': 'similarity'} for r in req['rows']]}


def test_fail_open_reused_across_rounds(db_session, sample_host, tmp_path):
    from backend.services.shadow_merge import merge_round
    one, dle = setup_round(db_session, tmp_path, sample_host)
    merge_round(db_session, one.id, matcher=match)
    two, _ = setup_round(db_session, tmp_path, sample_host, round_no=2, dle=dle)
    merge_round(db_session, two.id, matcher=match)
    assert len(db_session.scalars(select(IssueOccurrence)).all()) == 1
    assert two.merge_summary['reused_occurrences'] == 1
    assert two.merge_summary['unprovable_representatives'] == 1


def test_selected_representative_not_reselected(db_session, sample_host, tmp_path):
    from backend.services.shadow_merge import merge_round
    one, first = setup_round(db_session, tmp_path, sample_host, key='a'*64)
    merge_round(db_session, one.id, matcher=match)
    two, older = setup_round(db_session, tmp_path, sample_host, key='b'*64, round_no=2)
    older.detected_at = first.detected_at - timedelta(hours=1)
    merge_round(db_session, two.id, matcher=match)
    chosen = db_session.scalars(select(RepresentativeSelection)).one()
    assert chosen.dle_id == first.id
    assert len(db_session.scalars(select(IssueOccurrence)).all()) == 2
    assert len(db_session.scalars(select(IssueOccurrenceEvent)).all()) == 2


def test_no_input_aggregate_and_mapping_mismatch(db_session, sample_host, tmp_path):
    from pathlib import Path
    from backend.services.shadow_merge import merge_round
    one, _ = setup_round(db_session, tmp_path, sample_host)
    one.expected_set = []
    assert merge_round(db_session, one.id, matcher=lambda _: (_ for _ in ()).throw(AssertionError())).merge_status == 'NO_INPUT'
    two, _ = setup_round(db_session, tmp_path, sample_host, round_no=2)
    Path(two.expected_set[0]['reports'][0]['mapping_uri']).write_text('{"rows":[]}')
    assert merge_round(db_session, two.id, matcher=match).merge_summary['mapping_mismatched_reports'] == 1
    three, _ = setup_round(db_session, tmp_path, sample_host, round_no=3)
    ref = three.expected_set[0]['reports'][0]
    aggregate = Path(ref['storage_uri']).with_name('Result_org_dedup_org_123.xls')
    Path(ref['storage_uri']).rename(aggregate)
    three.expected_set = [{**three.expected_set[0], 'reports': [{**ref, 'storage_uri': str(aggregate)}]}]
    assert merge_round(db_session, three.id, matcher=lambda _: (_ for _ in ()).throw(AssertionError())).merge_status == 'OK'
    assert not db_session.scalars(select(IssueOccurrence)).all()


def test_tool_failure_rolls_back_facts_and_can_retry(db_session, sample_host, tmp_path):
    from backend.services.shadow_merge import merge_round
    one, _ = setup_round(db_session, tmp_path, sample_host)
    def failed(_):
        raise RuntimeError('tool failed')
    merge_round(db_session, one.id, matcher=failed)
    assert one.merge_status == 'FAILED'
    assert not db_session.scalars(select(RepresentativeSelection)).all()
    assert not db_session.scalars(select(IssueOccurrence)).all()
    merge_round(db_session, one.id, matcher=match)
    assert one.merge_status == 'OK'
    before = one.merge_summary
    merge_round(db_session, one.id, matcher=failed)
    assert one.merge_summary == before


def test_complete_priority_pending_final_fallback_and_no_splicing(db_session, sample_host, tmp_path):
    from backend.services.shadow_merge import merge_round
    one, first = setup_round(db_session, tmp_path, sample_host, key='a'*64, complete=False)
    merge_round(db_session, one.id, matcher=match)
    assert db_session.scalars(select(RepresentativeSelection)).one().selection_status == 'PENDING'
    two, complete = setup_round(db_session, tmp_path, sample_host, key='b'*64, round_no=2)
    merge_round(db_session, two.id, matcher=match)
    assert db_session.scalars(select(RepresentativeSelection)).one().dle_id == complete.id
    # Another issue remains pending even if pieces exist in different events.
    three, oldest = setup_round(db_session, tmp_path, sample_host, key='c'*64, round_no=3, complete=False)
    def new(req):
        return {'schema': 'stp.issue_match_result', 'schema_version': '1.0', 'results': [
            {'ref': r['ref'], 'matched_issue_id': None, 'new_group': 'n2', 'branch': 'similarity'} for r in req['rows']]}
    merge_round(db_session, three.id, matcher=new)
    four, newest = setup_round(db_session, tmp_path, sample_host, key='d'*64, round_no=4, kind='FINAL', complete=False)
    def second(req):
        out=match(req)
        out['results'][0]['matched_issue_id']=req['existing'][-1]['issue_id']
        return out
    merge_round(db_session, four.id, matcher=second)
    choices=db_session.scalars(select(RepresentativeSelection).order_by(RepresentativeSelection.signature_id)).all()
    assert choices[-1].dle_id == oldest.id and choices[-1].evidence_status == 'INCOMPLETE'


def test_duplicate_links_and_signature_disagreement(db_session, sample_host, tmp_path):
    from pathlib import Path
    from backend.services.shadow_merge import merge_round
    one, first = setup_round(db_session, tmp_path, sample_host, key='a'*64)
    duplicate = DeviceLogEvent(id=uuid4(), serial=first.serial, platform=first.platform, event_type=first.event_type,
        state='REMOTE', local_path=first.local_path, remote_path='/another', host_id=sample_host.id,
        plan_run_id=42, occurrence_key=first.occurrence_key, detected_at=first.detected_at)
    db_session.add(duplicate);db_session.flush()
    mp=Path(one.expected_set[0]['reports'][0]['mapping_uri']);data=json.loads(mp.read_text())
    data['rows'][0]['duplicate_dle_ids']=[str(duplicate.id)];mp.write_text(json.dumps(data))
    merge_round(db_session, one.id, matcher=match)
    assert len(db_session.scalars(select(IssueOccurrence)).all())==1
    assert len(db_session.scalars(select(IssueOccurrenceEvent)).all())==2
    two, _ = setup_round(db_session, tmp_path, sample_host, key='a'*64, round_no=2, dle=first)
    def disagrees(req):
        return {'schema':'stp.issue_match_result','schema_version':'1.0','results':[
            {'ref':r['ref'],'matched_issue_id':None,'new_group':'new','branch':'similarity'} for r in req['rows']]}
    # Same representative identity also resolves the normalized signature key.
    other=facts.record_issue_signature(db_session, signature_raw=json.dumps({'exp_class':'JE','package':'p','caused_by':'different'}),
        normalized_key='different',platform='mtk',event_type='JE',seen_at=first.detected_at)
    def other_issue(req):
        return {'schema':'stp.issue_match_result','schema_version':'1.0','results':[
            {'ref':r['ref'],'matched_issue_id':str(other.id),'new_group':None,'branch':'similarity'} for r in req['rows']]}
    merge_round(db_session,two.id,matcher=other_issue)
    assert two.merge_summary['issue_signature_disagreement']==1
    assert len(db_session.scalars(select(IssueOccurrence)).all())==1


def test_shadow_switch_off_neither_queue_nor_run(monkeypatch):
    from types import SimpleNamespace
    from backend.services import shadow_scan_rounds, shadow_merge
    from backend.core.settings import scheduler
    monkeypatch.setattr(scheduler,'get_scheduler_settings',lambda: SimpleNamespace(stp_scan_round_shadow_enabled=False))
    monkeypatch.setattr(shadow_scan_rounds,'get_scheduler_settings',scheduler.get_scheduler_settings)
    monkeypatch.setattr(shadow_merge,'get_scheduler_settings',scheduler.get_scheduler_settings)
    monkeypatch.setattr(shadow_scan_rounds,'SessionLocal',lambda: (_ for _ in ()).throw(AssertionError('database touched')))
    shadow_scan_rounds.sweep_shadow_scan_rounds()
    shadow_merge.run_shadow_merge(42,1)


def test_empty_receipt_can_finalize_prior_pending_candidate(db_session,sample_host,tmp_path):
    from backend.services.shadow_merge import merge_round
    one,first=setup_round(db_session,tmp_path,sample_host,key='a'*64,complete=False)
    merge_round(db_session,one.id,matcher=match)
    two,_=setup_round(db_session,tmp_path,sample_host,key='b'*64,round_no=2,kind='FINAL')
    two.expected_set=[{'host_id':sample_host.id,'platform':'mtk','coverage':'EMPTY','reports':[{'empty_receipt':True}]}]
    merge_round(db_session,two.id,matcher=match)
    assert two.merge_status=='OK'
    chosen=db_session.scalars(select(RepresentativeSelection)).one()
    assert chosen.dle_id==first.id and chosen.evidence_status=='INCOMPLETE'


def test_empty_string_key_is_fail_open_not_a_unique_key(db_session,sample_host,tmp_path):
    from backend.services.shadow_merge import merge_round
    one,_=setup_round(db_session,tmp_path,sample_host,key='')
    merge_round(db_session,one.id,matcher=match)
    two,_=setup_round(db_session,tmp_path,sample_host,key='',round_no=2)
    merge_round(db_session,two.id,matcher=match)
    assert two.merge_status=='OK'
    assert len(db_session.scalars(select(IssueOccurrence)).all())==2


def test_same_occurrence_observed_in_another_run_is_counted_there(db_session,sample_host,tmp_path):
    from backend.models.plan_run import PlanRun
    from backend.services.shadow_merge import merge_round
    one,first=setup_round(db_session,tmp_path,sample_host,key='a'*64)
    merge_round(db_session,one.id,matcher=match)
    original=db_session.get(PlanRun,42)
    another=PlanRun(plan_id=original.plan_id,status='RUNNING',plan_snapshot={},run_type='MANUAL',triggered_by='pytest')
    db_session.add(another);db_session.flush()
    repeat=DeviceLogEvent(serial=first.serial,platform='MTK',event_type='JE',state='LOCAL',
        local_path='/same',host_id=sample_host.id,plan_run_id=another.id,
        occurrence_key=first.occurrence_key,detected_at=first.detected_at)
    db_session.add(repeat);db_session.flush()
    occurrence=db_session.scalars(select(IssueOccurrence)).one()
    facts.link_occurrence_event(db_session,occurrence_id=occurrence.id,dle_id=repeat.id,signature_id=occurrence.signature_id)
    assert facts.count_occurrences(db_session,signature_id=occurrence.signature_id,plan_run_id=another.id)==1
    assert facts.count_machines(db_session,signature_id=occurrence.signature_id,plan_run_id=another.id)==1
    saved=facts.record_scan_round(db_session,plan_run_id=another.id,platform='mtk',round_no=1,
        kind='FINAL',input_cutoff_at=datetime.now(timezone.utc),expected_set=[])
    facts.set_scan_round_status(db_session,round_id=saved.id,status='CLOSED')
    facts.record_scan_round_merge(db_session,round_id=saved.id,status='OK',
        summary={'candidates':[{'occurrence_id':occurrence.id}]})
    db_session.delete(repeat);db_session.flush()
    assert facts.count_occurrences(db_session,signature_id=occurrence.signature_id,plan_run_id=another.id)==1
    assert facts.count_machines(db_session,signature_id=occurrence.signature_id,plan_run_id=another.id)==1


def test_same_new_group_uses_sorted_first_identity_and_records_versions(db_session,sample_host,tmp_path):
    import hashlib
    from pathlib import Path
    from backend.models.issue_fact import IssueSignature
    from backend.services.shadow_merge import merge_round
    one,_=setup_round(db_session,tmp_path,sample_host,key='a'*64)
    two,_=setup_round(db_session,tmp_path,sample_host,key='b'*64,round_no=2)
    a=one.expected_set[0]['reports'][0]
    b=two.expected_set[0]['reports'][0]
    renamed=Path(b['storage_uri']).with_name('Result_z_org.xls')
    Path(b['storage_uri']).rename(renamed);b={**b,'storage_uri':str(renamed)}
    for ref,version in [(a,'2.1.1'),(b,'2.1.2')]:
        side=Path(ref['membership_uri']);data=json.loads(side.read_text())
        data['meta'].update(tool={'version':version},rule={'id':'mtk-aee-dedup','version':'1'},parsers={'recognizeexception':'1'})
        side.write_text(json.dumps(data))
    one.expected_set=[{**one.expected_set[0],'reports':[b,a]}]
    merge_round(db_session,one.id,matcher=match)
    assert one.merge_status=='OK'
    assert one.merge_summary['extractor_versions_mixed'] is True
    sig=db_session.scalars(select(IssueSignature)).one()
    assert sig.normalized_key==hashlib.sha256(('mtk|similarity|'+'a'*64).encode()).hexdigest()
    assert facts.count_occurrences(db_session,signature_id=sig.id,plan_run_id=42)==2
    assert one.merge_summary['issues']==1


@pytest.mark.parametrize("mapped", [False, True])
def test_filtered_rows_do_not_enter_match_or_occurrences(db_session,sample_host,tmp_path,mapped):
    from pathlib import Path
    from backend.services.shadow_merge import merge_round
    one,_=setup_round(db_session,tmp_path,sample_host,key='a'*64)
    ref=one.expected_set[0]['reports'][0]
    side=Path(ref['membership_uri']);data=json.loads(side.read_text())
    data['rows'][0].update(branch='filtered',group=None,filter_reason='not_in_pkglist')
    data['groups']=[];side.write_text(json.dumps(data))
    mp=Path(ref['mapping_uri']);mapping=json.loads(mp.read_text())
    mapping['rows'][0]['mapped']=mapped;mp.write_text(json.dumps(mapping))
    merge_round(db_session,one.id,matcher=lambda _: (_ for _ in ()).throw(AssertionError('filtered sent to tool')))
    assert one.merge_status=='OK'
    assert one.merge_summary['filtered_by_reason']=={'not_in_pkglist':1}
    assert one.merge_summary['mapping_unmapped_rows']==int(not mapped)
    assert one.merge_summary['unmapped_rows']==0
    assert one.merge_summary['unfiltered_rows']==0
    assert not db_session.scalars(select(IssueOccurrence)).all()


def _append_mapped_member(ref, dle, key):
    import hashlib
    from pathlib import Path
    import xlrd
    import xlwt
    path=Path(ref['storage_uri'])
    old=xlrd.open_workbook(str(path)).sheet_by_name('aeeexp')
    values=[old.row_values(i) for i in range(old.nrows)]
    member=list(values[1]);member[0]=1;member[1]='/event/member2.dbg'
    book=xlwt.Workbook();sheet=book.add_sheet('aeeexp')
    for i,row in enumerate([*values,member]):
        for j,value in enumerate(row): sheet.write(i,j,value)
    book.save(str(path))
    mp=Path(ref['mapping_uri']);mapping=json.loads(mp.read_text())
    mapping['rows'][0].update(mapped=False,dle_id=None,occurrence_key=None,source=None,unmapped_reason='no_source')
    mapping['rows'].append({'sheet':'aeeexp','row':3,'path':member[1],'mapped':True,
        'dle_id':str(dle.id),'occurrence_key':key,'source':'local','evidence':{}})
    mp.write_text(json.dumps(mapping))
    sp=Path(ref['membership_uri']);side=json.loads(sp.read_text())
    side['meta']['reports']['org_xls']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    side['rows'].append({'org_row':2,'path':member[1],'branch':'similarity','group':'g1'})
    side['groups'][0]['member_rows']=[1,2]
    sp.write_text(json.dumps(side))


def test_v119_unmapped_representative_anchors_mapped_member(db_session,sample_host,tmp_path):
    import hashlib
    from backend.models.issue_fact import IssueSignature
    from backend.services.shadow_merge import merge_round
    one,dle=setup_round(db_session,tmp_path,sample_host,key='a'*64)
    ref=one.expected_set[0]['reports'][0]
    _append_mapped_member(ref,dle,'a'*64)
    requests=[]
    def observed(req):
        requests.append(req)
        return match(req)
    merge_round(db_session,one.id,matcher=observed)
    assert one.merge_status=='OK'
    signature=db_session.scalars(select(IssueSignature)).one()
    assert signature.normalized_key==hashlib.sha256(('mtk|similarity|'+'a'*64).encode()).hexdigest()
    assert len(requests[0]['rows'])==1
    assert requests[0]['rows'][0]['path']=='/event/db.dbg'
    assert one.merge_summary['unmapped_by_reason']=={'no_source':1}
    assert facts.count_occurrences(db_session,signature_id=signature.id,plan_run_id=42)==1


def test_v119_keyed_member_in_later_representative_beats_fail_open(db_session,sample_host,tmp_path):
    import hashlib
    from pathlib import Path
    from backend.models.issue_fact import IssueSignature
    from backend.services.shadow_merge import merge_round
    one,_=setup_round(db_session,tmp_path,sample_host,key=None)
    two,_=setup_round(db_session,tmp_path,sample_host,key='b'*64,round_no=2)
    a=one.expected_set[0]['reports'][0];b=two.expected_set[0]['reports'][0]
    new=Path(b['storage_uri']).with_name('Result_z_org.xls')
    Path(b['storage_uri']).rename(new);b={**b,'storage_uri':str(new)}
    one.expected_set=[{**one.expected_set[0],'reports':[b,a]}]
    merge_round(db_session,one.id,matcher=match)
    assert one.merge_status=='OK'
    signature=db_session.scalars(select(IssueSignature)).one()
    assert signature.normalized_key==hashlib.sha256(('mtk|similarity|'+'b'*64).encode()).hexdigest()
    assert one.merge_summary['unprovable_anchors']==0
    assert one.merge_summary['new_occurrences']==2


def test_v119_unprovable_group_is_counted_and_still_matches_tool(db_session,sample_host,tmp_path):
    from pathlib import Path
    from backend.models.issue_fact import IssueSignature
    from backend.services.shadow_merge import merge_round
    one,_=setup_round(db_session,tmp_path,sample_host,key='a'*64)
    ref=one.expected_set[0]['reports'][0]
    mp=Path(ref['mapping_uri']);mapping=json.loads(mp.read_text())
    mapping['rows'][0].update(mapped=False,dle_id=None,occurrence_key=None,source=None,unmapped_reason='no_source')
    mp.write_text(json.dumps(mapping))
    called=[]
    def observed(req):
        called.append(req)
        return match(req)
    merge_round(db_session,one.id,matcher=observed)
    assert one.merge_status=='OK'
    assert len(called)==1 and len(called[0]['rows'])==1
    assert not db_session.scalars(select(IssueSignature)).all()
    assert not db_session.scalars(select(IssueOccurrence)).all()
    assert one.merge_summary['unprovable_groups']==1
    assert one.merge_summary['unmapped_by_reason']=={'unprovable_group':1}


def test_v119_member_rows_order_is_the_anchor_order(db_session,sample_host,tmp_path):
    import hashlib
    from pathlib import Path
    from backend.models.issue_fact import IssueSignature
    from backend.services.shadow_merge import merge_round
    one,first=setup_round(db_session,tmp_path,sample_host,key='a'*64)
    _,second=setup_round(db_session,tmp_path,sample_host,key='b'*64,round_no=2)
    ref=one.expected_set[0]['reports'][0]
    _append_mapped_member(ref,second,'b'*64)
    mp=Path(ref['mapping_uri']);mapping=json.loads(mp.read_text())
    mapping['rows'][0].update(mapped=True,dle_id=str(first.id),occurrence_key='a'*64,source='local')
    mp.write_text(json.dumps(mapping))
    sp=Path(ref['membership_uri']);side=json.loads(sp.read_text())
    side['groups'][0]['member_rows']=[2,1]
    sp.write_text(json.dumps(side))
    merge_round(db_session,one.id,matcher=match)
    assert one.merge_status=='OK'
    sig=db_session.scalars(select(IssueSignature)).one()
    assert sig.normalized_key==hashlib.sha256(('mtk|similarity|'+'b'*64).encode()).hexdigest()
    assert one.merge_summary['new_occurrences']==2


def test_v119_earlier_unmapped_group_keeps_signature_with_later_anchor(db_session,sample_host,tmp_path):
    import hashlib
    from pathlib import Path
    from backend.models.issue_fact import IssueSignature
    from backend.services.shadow_merge import merge_round
    one,_=setup_round(db_session,tmp_path,sample_host,key=None)
    two,_=setup_round(db_session,tmp_path,sample_host,key='b'*64,round_no=2)
    a=one.expected_set[0]['reports'][0];b=two.expected_set[0]['reports'][0]
    mp=Path(a['mapping_uri']);mapping=json.loads(mp.read_text())
    mapping['rows'][0].update(mapped=False,dle_id=None,occurrence_key=None,source=None,unmapped_reason='no_source')
    mp.write_text(json.dumps(mapping))
    sp=Path(a['membership_uri']);side=json.loads(sp.read_text())
    side['groups'][0]['representative_signature']['caused_by']='first signature raw'
    sp.write_text(json.dumps(side))
    renamed=Path(b['storage_uri']).with_name('Result_z_org.xls')
    Path(b['storage_uri']).rename(renamed);b={**b,'storage_uri':str(renamed)}
    one.expected_set=[{**one.expected_set[0],'reports':[b,a]}]
    merge_round(db_session,one.id,matcher=match)
    assert one.merge_status=='OK'
    sig=db_session.scalars(select(IssueSignature)).one()
    assert sig.normalized_key==hashlib.sha256(('mtk|similarity|'+'b'*64).encode()).hexdigest()
    assert json.loads(sig.signature_raw)['caused_by']=='first signature raw'
    assert one.merge_summary['unprovable_groups']==0
    assert one.merge_summary['unmapped_by_reason']=={'no_source':1}
    assert one.merge_summary['new_occurrences']==1


def test_v119_unmapped_representative_can_use_fail_open_member(db_session,sample_host,tmp_path):
    import hashlib
    from backend.models.issue_fact import IssueSignature
    from backend.services.shadow_merge import merge_round
    one,dle=setup_round(db_session,tmp_path,sample_host,key=None)
    _append_mapped_member(one.expected_set[0]['reports'][0],dle,None)
    merge_round(db_session,one.id,matcher=match)
    assert one.merge_status=='OK'
    signature=db_session.scalars(select(IssueSignature)).one()
    assert signature.normalized_key==hashlib.sha256(f'mtk|similarity|unprovable|{dle.id}'.encode()).hexdigest()
    assert one.merge_summary['unprovable_anchors']==1
    assert one.merge_summary['unmapped_by_reason']=={'no_source':1}
    assert one.merge_summary['new_occurrences']==1


def test_merge_retries_even_without_scan_candidates(db_session,sample_host,tmp_path,monkeypatch):
    from datetime import timedelta
    from types import SimpleNamespace
    from unittest.mock import Mock
    from sqlalchemy.orm import sessionmaker
    from backend.services import shadow_scan_rounds as shadow
    one,_=setup_round(db_session,tmp_path,sample_host,key='a'*64)
    facts.record_scan_round_merge(db_session,round_id=one.id,status='FAILED',summary={})
    round_id=one.id;db_session.commit()
    factory=sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(shadow,'SessionLocal',factory)
    monkeypatch.setattr(shadow,'get_scheduler_settings',lambda: SimpleNamespace(
        stp_scan_round_shadow_enabled=True,auto_archive_poll_interval_seconds=120))
    monkeypatch.setattr(shadow,'resolve_shared_storage_root',lambda: str(tmp_path))
    # #3875's settled partition is no longer a scan candidate; G4 must still retry.
    monkeypatch.setattr(facts,'list_shadow_scan_run_ids',lambda *_a,**_kw: [])
    monkeypatch.setattr(shadow,'_advance_run',lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('settled run advanced')))
    queued=Mock();monkeypatch.setattr(shadow,'enqueue_sync',queued)
    now=datetime.now(timezone.utc)
    shadow.sweep_shadow_scan_rounds(now=now)
    shadow.sweep_shadow_scan_rounds(now=now+timedelta(seconds=120))
    assert queued.call_count==2
    assert all(c.args[0]=='shadow_merge_task' and c.kwargs['fact_round_id']==round_id for c in queued.call_args_list)
    assert queued.call_args_list[0].kwargs['key']!=queued.call_args_list[1].kwargs['key']
    with factory() as db:
        facts.record_scan_round_merge(db,round_id=round_id,status='OK',summary={})
        db.commit()
    queued.reset_mock()
    shadow.sweep_shadow_scan_rounds(now=now+timedelta(seconds=240))
    queued.assert_not_called()
