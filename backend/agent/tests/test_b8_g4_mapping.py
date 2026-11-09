"""G4 duplicate identity, same-event evidence, shadow sidecar transport."""
import json
from datetime import datetime, timezone

import pytest
import xlwt
from backend.agent.aee.scan_scope import report_mapping
from backend.agent.contracts.evidence import event_evidence
from backend.agent.upload_manager import UploadManager
from backend.agent.contracts.round_report_layout import round_manifest_path


def write_report(path, raw):
    book = xlwt.Workbook()
    sheet = book.add_sheet('aeeexp')
    sheet.write(0, 0, 'Path')
    sheet.write(1, 0, raw)
    book.save(str(path))


@pytest.mark.parametrize('keys,expected', [(['k','k'], True), (['k','x'], False), (['k',None], False)])
@pytest.mark.parametrize('fact_round_id', [None, 7])
def test_duplicate_dle_mapping(tmp_path, keys, expected, fact_round_id):
    root = tmp_path/'scan'
    event = root/'V1'/'SER1'/'aee_exp'/'db'
    event.mkdir(parents=True)
    (event/'raw.dbg').write_bytes(b'raw')
    report = tmp_path/'Result_org.xls'
    write_report(report, str(event/'raw.dbg'))
    sources = [dict(id=i, serial='SER1', source='local', scan_path=str(event), local_path=str(event),
                    occurrence_key=k, detected_at=datetime(2026,1,1,tzinfo=timezone.utc).isoformat())
               for i,k in zip(['b','a'],keys,strict=True)]
    row = report_mapping(str(report), serials=['SER1'], sources=sources, scan_root=str(root),
                         plan_run_id=1, host_id='h', platform='mtk', fact_round_id=fact_round_id)['rows'][0]
    assert row['mapped'] is (expected and fact_round_id is not None)
    if fact_round_id is None:
        assert 'unmapped_reason' not in row
    elif expected:
        assert row['dle_id']=='a' and row['duplicate_dle_ids']==['b']
        assert row['evidence']=={'dblog':True,'mobilelog':False,'bugreport':False}
    else:
        assert row['unmapped_reason']=='ambiguous_duplicate'


def test_evidence_never_combines_events_or_follows_symlinks(tmp_path):
    a,b=tmp_path/'a',tmp_path/'b'
    a.mkdir();b.mkdir()
    (a/'x.dbg').write_bytes(b'db')
    (b/'mobilelog').mkdir();(b/'mobilelog'/'x').write_bytes(b'log')
    (b/'bugreport').mkdir();(b/'bugreport'/'ts_bugreport.zip').write_bytes(b'zip')
    (a/'mobilelog').symlink_to(b/'mobilelog')
    assert event_evidence(a)=={'dblog':True,'mobilelog':False,'bugreport':False}
    assert event_evidence(b)=={'dblog':False,'mobilelog':True,'bugreport':True}


@pytest.mark.parametrize('round_id', [None,7])
@pytest.mark.parametrize('present',[True,False])
def test_sidecar_only_uploaded_for_shadow(tmp_path, round_id, present):
    src=tmp_path/'Result_org.xls';src.write_bytes(b'report')
    side=tmp_path/'Result_members.json';side.write_bytes(b'{"schema":"stp.scan_membership"}')
    if not present: side.unlink()
    manager=UploadManager();manager.configure(nfs_root=str(tmp_path/'nfs'))
    dest=manager.upload_scan_report(42,'h',str(src),platform_subdir='mtk',fact_round_id=round_id)
    assert dest
    if round_id is None:
        shard=tmp_path/'nfs/_meta/42/h.json'
    else:
        shard=round_manifest_path(tmp_path/'nfs',42,round_id,'h')
    entries=json.loads(shard.read_text())['artifacts']
    entry=next(e for e in entries if e['registerable'])
    assert ('membership_file_key' in entry) is (round_id is not None)
    assert len([e for e in entries if e.get('artifact_type')=='scan_membership'])==int(present and round_id is not None)
