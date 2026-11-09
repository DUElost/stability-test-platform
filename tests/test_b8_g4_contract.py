"""Tool membership reader contract; synthetic rows never compare signatures here."""
import json

import pytest
from backend.tests.fixtures.scan_membership import fixture_report


@pytest.mark.parametrize('bad', ['missing', 'major', 'sha256', 'join'])
def test_unusable_membership_degrades_whole_report(tmp_path, bad):
    from backend.services.scan_membership import read_report
    ref, sidecar = fixture_report(tmp_path)
    if bad == 'missing':
        ref['membership_uri'] = None
    else:
        if bad == 'major':
            sidecar['schema_version'] = '2.0'
        elif bad == 'sha256':
            sidecar['meta']['reports']['org_xls']['sha256'] = 'wrong'
        else:
            sidecar['rows'][0]['path'] = 'different'
        from pathlib import Path
        Path(ref['membership_uri']).write_text(json.dumps(sidecar))
    result = read_report(ref)
    assert result['status'] == 'DEGRADED'
    assert not result['groups']
    assert result['reason'] == ('sidecar_missing' if bad == 'missing' else 'sidecar_unusable')


def test_match_input_order_and_raw_caused_by():
    from backend.services.scan_membership import build_request
    existing = [{'id': 9, 'signature_raw': json.dumps({'exp_class': 'JE', 'package': 'p', 'caused_by': 'RAW9', 'caused_by_norm': 'norm'})},
                {'id': 2, 'signature_raw': json.dumps({'exp_class': 'JE', 'package': 'p', 'caused_by': 'RAW2'})}]
    def group(host, filename, row):
        return {'host_id': host, 'filename': filename, 'org_row': row, 'ref': f'{host}:{filename}:{row}', 'path': '/db', 'signature': {'exp_class': 'JE', 'package': 'p', 'caused_by': 'raw'}}
    groups = [group('z', 'a', 1), group('a', 'b', 1), group('a', 'a', 2), group('a', 'a', 1)]
    req = build_request(existing, groups)
    assert [e['issue_id'] for e in req['existing']] == ['2', '9']
    assert req['existing'][1]['caused_by'] == 'RAW9'
    assert [r['ref'] for r in req['rows']] == ['a:a:1', 'a:a:2', 'a:b:1', 'z:a:1']
