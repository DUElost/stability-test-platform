"""G4's sole reader for published tool membership and issue-match JSON.

No signature normalization, similarity comparison, or branch inference lives here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import xlrd


def read_report(ref: dict) -> dict:
    result = {'status': 'INVALID_MAPPING', 'reason': 'mapping_mismatch', 'groups': [],
              'rows': [], 'meta': {}, 'events_unavailable': False, 'spill_unstaged': []}
    report = Path(ref['storage_uri'])
    if not report.name.endswith('_org.xls'):
        return {**result, 'status': 'AGGREGATE'}
    try:
        book = xlrd.open_workbook(str(report), on_demand=True)
        try:
            sheet = book.sheet_by_name('aeeexp')
            columns = {str(v).strip().lower(): i for i, v in enumerate(sheet.row_values(0))}
            paths = [str(sheet.cell_value(i, columns['path'])) for i in range(1, sheet.nrows)]
            serials = [str(sheet.cell_value(i, columns['deviceid'])) for i in range(1, sheet.nrows)]
        finally:
            book.release_resources()
        mapping = json.loads(Path(ref['mapping_uri']).read_text(encoding='utf-8'))
        rows = mapping['rows']
        result.update(events_unavailable=mapping.get('events_unavailable', False),
                      spill_unstaged=mapping.get('spill_unstaged', []))
        if len(rows) != len(paths) or any(
            r['sheet'] != 'aeeexp' or r['row'] != i + 2 or r['path'] != paths[i]
            for i, r in enumerate(rows)
        ):
            return result
        result['rows'] = [{**r, 'serial': serials[i]} for i, r in enumerate(rows)]
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, xlrd.XLRDError):
        return result
    result.update(status='DEGRADED', reason='sidecar_missing')
    if not ref.get('membership_uri') or not Path(ref['membership_uri']).is_file():
        return result
    result['reason'] = 'sidecar_unusable'
    try:
        data = json.loads(Path(ref['membership_uri']).read_text(encoding='utf-8'))
        result['meta'] = data.get('meta', {})
        if data['schema'] != 'stp.scan_membership' or str(data['schema_version']).split('.')[0] != '1':
            return result
        if data['meta']['reports']['org_xls']['sha256'] != hashlib.sha256(report.read_bytes()).hexdigest():
            return result
        members = data['rows']
        if len(members) != len(rows):
            return result
        by_number = {m['org_row']: m for m in members}
        if set(by_number) != set(range(1, len(rows) + 1)):
            return result
        for i, row in enumerate(rows, 1):
            m = by_number[i]
            if row['row'] != m['org_row'] + 1 or row['path'] != m['path']:
                return result
            if m['branch'] not in {'filtered', 'singleton', 'similarity'}:
                return result
        covered = set()
        groups = []
        for g in data['groups']:
            nums = g['member_rows']
            rep = g['representative_row']
            if not nums or rep not in nums or covered.intersection(nums):
                return result
            for n in nums:
                if by_number[n]['group'] != g['group'] or by_number[n]['branch'] == 'filtered':
                    return result
            covered.update(nums)
            groups.append({**g, 'signature': g['representative_signature'],
                           'org_row': rep, 'path': by_number[rep]['path'],
                           'branch': by_number[rep]['branch'],
                           'host_id': ref['host_id'], 'filename': report.name,
                           'ref': f"{ref['host_id']}:{report.name}:{rep}"})
        if covered != {n for n, m in by_number.items() if m['branch'] != 'filtered'}:
            return result
        return {**result, 'status': 'OK', 'reason': None, 'groups': groups,
                'members': by_number, 'meta': data['meta']}
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return result


def build_request(existing: list[dict], groups: list[dict]) -> dict:
    issues = []
    for issue in sorted(existing, key=lambda x: x['id']):
        raw = json.loads(issue['signature_raw'])
        issues.append({'issue_id': str(issue['id']), **{k: raw[k] for k in ('exp_class', 'package', 'caused_by')}})
    return {'schema': 'stp.issue_match_request', 'schema_version': '1.0',
            'existing': issues, 'rows': [
                {'ref': g['ref'], 'path': g['path'],
                 **{k: g['signature'][k] for k in ('exp_class', 'package', 'caused_by')}}
                for g in sorted(groups, key=lambda x: (x['host_id'], x['filename'], x['org_row']))]}


def match_issues(request: dict) -> dict:
    python = os.environ.get('STP_BACKEND_DEDUP_SCAN_PYTHON', '').strip()
    script = os.environ.get('STP_BACKEND_DEDUP_SCAN_SCRIPT', '').strip()
    if not python or not script:
        raise RuntimeError('published matching tool not configured')
    release = json.loads(Path(script).with_name('release.json').read_text(encoding='utf-8'))
    if not release.get('version') or not release.get('git_commit'):
        raise ValueError('matching tool has no release identity')
    with tempfile.TemporaryDirectory(prefix='stp-issue-match-') as work:
        inp, out = Path(work) / 'request.json', Path(work) / 'result.json'
        inp.write_text(json.dumps(request), encoding='utf-8')
        subprocess.run([python, script, '-match_issues', str(inp), '-o', str(out)],
                       cwd=work, capture_output=True, check=True, timeout=1800)
        return json.loads(out.read_text(encoding='utf-8'))


def validate_match(result: dict, request: dict) -> dict:
    if result.get('schema') != 'stp.issue_match_result' or str(result.get('schema_version')).split('.')[0] != '1':
        raise ValueError('unsupported issue match result')
    rows = result['results']
    by_ref = {r['ref']: r for r in rows}
    if len(by_ref) != len(rows) or set(by_ref) != {r['ref'] for r in request['rows']}:
        raise ValueError('issue match refs differ')
    allowed = {i['issue_id'] for i in request['existing']}
    for r in rows:
        if r.get('branch') not in {'similarity', 'singleton'}:
            raise ValueError('invalid issue match branch')
        hit, new = r.get('matched_issue_id'), r.get('new_group')
        if (hit is not None) == (new is not None) or (hit is not None and hit not in allowed):
            raise ValueError('invalid issue match identity')
        if new is not None and (not isinstance(new, str) or not new):
            raise ValueError('invalid issue match group')
    return by_ref
