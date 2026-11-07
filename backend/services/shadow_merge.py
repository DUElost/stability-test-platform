"""B8 G4: consume tool decisions into G1 facts; shadow-only, no upload/merge xls."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import logging
from uuid import UUID

from backend.agent.contracts.evidence import evidence_complete
from backend.core.database import SessionLocal
from backend.core.settings.scheduler import get_scheduler_settings
from backend.services import issue_fact_store as facts
from backend.services.scan_membership import build_request, match_issues, read_report, validate_match

logger = logging.getLogger(__name__)


def _summary():
    return dict(issues=0, new_occurrences=0, reused_occurrences=0, unmapped_rows=0,
                unmapped_by_reason={}, mapping_mismatched_reports=0,
                degraded_coverage=0, degraded_by_reason={}, filtered_by_reason={},
                issue_signature_disagreement=0, unprovable_representatives=0,
                extractor_versions_mixed=False, reports=[], candidates=[])


def _select(db, row, candidates):
    by_issue = {}
    for candidate in candidates:
        by_issue.setdefault(candidate['signature_id'], []).append(candidate)
    for signature_id, choices in by_issue.items():
        existing = facts.get_representative_selection(db, plan_run_id=row.plan_run_id,
                                                     platform=row.platform, signature_id=signature_id)
        if existing is not None and existing.selection_status == facts.SELECTION_SELECTED:
            continue
        choices.sort(key=lambda c: (datetime.fromisoformat(c['observed_at']), c['dle_id']))
        complete = [c for c in choices if evidence_complete(c['evidence'])]
        chosen = complete[0] if complete else (choices[0] if row.kind != facts.SCAN_ROUND_KIND_PERIODIC else None)
        facts.record_representative_selection(db, plan_run_id=row.plan_run_id, platform=row.platform,
            signature_id=signature_id, selection_status=facts.SELECTION_SELECTED if chosen else facts.SELECTION_PENDING,
            dle_id=UUID(chosen['dle_id']) if chosen else None,
            evidence_status=(facts.EVIDENCE_COMPLETE if complete else facts.EVIDENCE_INCOMPLETE) if chosen else None)


def _ingest(db, row, matcher):
    summary = _summary()
    degraded, filtered, unmapped = Counter(), Counter(), Counter()
    groups, report_by_ref = [], {}
    new_occurrences, reused_occurrences = set(), set()
    versions = set()
    refs = [r for u in row.expected_set for r in u.get('reports', []) if not r.get('empty_receipt')]
    seen = set()
    for ref in refs:
        if ref['storage_uri'] in seen:
            continue
        seen.add(ref['storage_uri'])
        report = read_report(ref)
        if report['status'] == 'AGGREGATE':
            continue
        if report['events_unavailable'] or report['spill_unstaged'] or report['status'] == 'DEGRADED':
            summary['degraded_coverage'] += 1
        if report['events_unavailable']:
            degraded['events_unavailable'] += 1
        if report['spill_unstaged']:
            degraded['spill_unstaged'] += len(report['spill_unstaged'])
        meta = report['meta']
        version = {k: meta.get(k) for k in ('tool', 'rule', 'parsers')}
        summary['reports'].append({'storage_uri': ref['storage_uri'], **version})
        if meta:
            versions.add(json.dumps({'tool': meta.get('tool', {}).get('version'),
                'rule': {k: meta.get('rule', {}).get(k) for k in ('id', 'version')},
                'parsers': meta.get('parsers')}, sort_keys=True))
        if report['status'] == 'INVALID_MAPPING':
            summary['mapping_mismatched_reports'] += 1
            continue
        if report['status'] == 'DEGRADED':
            degraded[report['reason']] += 1
            continue
        for n, mapping in enumerate(report['rows'], 1):
            if report['members'][n]['branch'] == 'filtered':
                filtered[report['members'][n]['filter_reason']] += 1
            if not mapping.get('mapped'):
                unmapped[mapping.get('unmapped_reason', 'no_source')] += 1
        for g in report['groups']:
            g['rule'] = meta.get('rule', {})
            g['mapping'] = [report['rows'][n - 1] for n in g['member_rows']]
            g['representative_mapping'] = report['rows'][g['org_row'] - 1]
            # A group with no mapped observations contributes coverage, not facts.
            if any(m.get('mapped') for m in g['mapping']):
                groups.append(g)
                report_by_ref[g['ref']] = g
    summary.update(degraded_by_reason=dict(degraded),
                   filtered_by_reason=dict(filtered), unmapped_by_reason=dict(unmapped),
                   unmapped_rows=sum(unmapped.values()), extractor_versions_mixed=len(versions) > 1)
    if not refs and not any(r.get('empty_receipt') for u in row.expected_set for r in u.get('reports', [])):
        return 'NO_INPUT', summary
    existing = facts.list_issue_signatures(db, platform=row.platform)
    request = build_request(existing, groups)
    matches = validate_match(matcher(request), request) if groups else {}
    new_signatures, issues = {}, set()
    for r in request['rows']:
        group = report_by_ref[r['ref']]
        decision = matches[r['ref']]
        representative = group['representative_mapping']
        if not representative.get('occurrence_key'):
            summary['unprovable_representatives'] += 1
        signature_id = decision['matched_issue_id']
        if signature_id is not None:
            signature_id = int(signature_id)
        else:
            new_group = decision['new_group']
            if new_group not in new_signatures:
                if not representative.get('mapped'):
                    raise ValueError('new group representative is unmapped; occurrence identity unprovable')
                identity = representative.get('occurrence_key') or f"unprovable|{representative['dle_id']}"
                key = hashlib.sha256(f"{row.platform}|{group['branch']}|{identity}".encode()).hexdigest()
                sig = facts.record_issue_signature(db, platform=row.platform,
                    event_type=group['signature']['exp_class'], normalized_key=key,
                    signature_raw=json.dumps({**group['signature'], 'rule': group['rule']}, ensure_ascii=False),
                    seen_at=row.closed_at)
                new_signatures[new_group] = sig.id
            signature_id = new_signatures[new_group]
        for mapping in group['mapping']:
            if not mapping.get('mapped'):
                continue
            dle_id = UUID(mapping['dle_id'])
            dle = facts.get_partition_event(db, dle_id=dle_id, plan_run_id=row.plan_run_id,
                                            platform=row.platform, host_id=group['host_id'])
            if dle is None or (dle.occurrence_key or None) != (mapping.get('occurrence_key') or None):
                raise ValueError('mapping identity differs from partition DLE')
            key = mapping.get('occurrence_key') or None
            occurrence = (facts.find_occurrence_by_key(db, occurrence_key=key) if key else
                          facts.find_occurrence_by_dle(db, dle_id=dle_id))
            if occurrence is None:
                occurrence = facts.record_issue_occurrence(db, signature_id=signature_id, occurrence_key=key,
                    serial=mapping['serial'], observed_at=dle.detected_at, first_seen_source=mapping['source'],
                    first_seen_plan_run_id=row.plan_run_id, first_seen_round_id=row.id)
                new_occurrences.add(occurrence.id)
            else:
                if occurrence.id not in new_occurrences:
                    reused_occurrences.add(occurrence.id)
                if occurrence.signature_id != signature_id:
                    summary['issue_signature_disagreement'] += 1
            for event_id in [str(dle_id), *mapping.get('duplicate_dle_ids', [])]:
                event_uuid = UUID(event_id)
                event = facts.get_partition_event(db, dle_id=event_uuid, plan_run_id=row.plan_run_id,
                                                  platform=row.platform, host_id=group['host_id'])
                if event is None or (event.occurrence_key or None) != key:
                    raise ValueError('duplicate DLE identity differs')
                facts.link_occurrence_event(db, occurrence_id=occurrence.id, dle_id=event_uuid,
                                            signature_id=occurrence.signature_id)
            issues.add(occurrence.signature_id)
            summary['candidates'].append({'signature_id': occurrence.signature_id, 'occurrence_id': occurrence.id,
                'dle_id': str(dle_id), 'observed_at': occurrence.observed_at.isoformat(),
                'evidence': mapping.get('evidence', {})})
    summary['new_occurrences'] = len(new_occurrences)
    summary['reused_occurrences'] = len(reused_occurrences)
    summary['issues'] = len(issues)
    summary['counts'] = {str(sid): {
        'occurrences': facts.count_occurrences(db, signature_id=sid, plan_run_id=row.plan_run_id),
        'machines': facts.count_machines(db, signature_id=sid, plan_run_id=row.plan_run_id),
    } for sid in sorted(issues)}
    candidates = facts.list_partition_merge_candidates(db, plan_run_id=row.plan_run_id, platform=row.platform)
    _select(db, row, [*candidates, *summary['candidates']])
    return 'OK', summary


def merge_round(db, round_id: int, *, matcher=match_issues):
    row = facts.get_scan_round(db, round_id=round_id)
    if row is None or row.platform != 'mtk' or row.status != facts.SCAN_ROUND_STATUS_CLOSED:
        return None
    if row.merge_status in {'OK', 'NO_INPUT'}:
        return row
    try:
        with db.begin_nested():
            status, summary = _ingest(db, row, matcher)
    except Exception as exc:
        logger.exception('shadow_merge_failed round=%d', round_id)
        status, summary = 'FAILED', {**_summary(), 'error_type': type(exc).__name__, 'error': str(exc)[:500]}
    return facts.record_scan_round_merge(db, round_id=round_id, status=status, summary=summary)


def run_shadow_merge(plan_run_id: int, fact_round_id: int):
    if not get_scheduler_settings().stp_scan_round_shadow_enabled:
        return
    with SessionLocal() as db:
        if facts.lock_shadow_merge(db, plan_run_id=plan_run_id) is None:
            return
        row = facts.get_scan_round(db, round_id=fact_round_id)
        if row is not None and row.plan_run_id == plan_run_id:
            merge_round(db, fact_round_id)
            db.commit()
