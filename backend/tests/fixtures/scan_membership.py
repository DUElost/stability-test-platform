"""Synthetic xls/mapping/sidecar for G4 reader and ingestion tests."""
import hashlib
import json

import xlwt


def fixture_report(tmp_path):
    path = tmp_path / 'Result_test_org.xls'
    book = xlwt.Workbook()
    sheet = book.add_sheet('aeeexp')
    headers = ['Id', 'Path', 'Version', 'ExpClass', 'ExpType', 'Time', 'Package', 'Detail', 'CausedBy', 'X', 'Y', 'Count', 'Z', 'DeviceId', 'W']
    for i, v in enumerate(headers):
        sheet.write(0, i, v)
    for i, v in enumerate([0, '/event/db.dbg', '', 'JE', '', '', 'com.a', '', 'raw', '', '', 999, '', 'SN1', '']):
        sheet.write(1, i, v)
    book.save(str(path))
    mapping = {'rows': [{'sheet': 'aeeexp', 'row': 2, 'path': '/event/db.dbg', 'mapped': False}]}
    sidecar = {'schema': 'stp.scan_membership', 'schema_version': '1.0',
               'meta': {'reports': {'org_xls': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}},
               'rows': [{'org_row': 1, 'path': '/event/db.dbg', 'branch': 'similarity', 'group': 'g1'}],
               'groups': [{'group': 'g1', 'representative_row': 1, 'member_rows': [1], 'representative_signature': {'exp_class': 'JE', 'package': 'com.a', 'caused_by': 'raw', 'caused_by_norm': 'raw'}}]}
    mp, sp = tmp_path / 'mapping.json', tmp_path / 'members.json'
    mp.write_text(json.dumps(mapping))
    sp.write_text(json.dumps(sidecar))
    return {'storage_uri': str(path), 'mapping_uri': str(mp), 'membership_uri': str(sp), 'host_id': 'h'}, sidecar


