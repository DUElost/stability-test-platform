"""Finite safe U8 repair withdrawals. Run sequentially in its dedicated worktree.

Every selected test stubs process signalling. This never selects cancel tests or
#3739 mutations. Files are restored byte-for-byte in finally before a green rerun.
All pytest children use the project memory runner and a clean environment.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
AGENT = "backend/agent/tests/test_disarm_u8_3752.py"
API = "backend/tests/api/test_disarm_policy_u8_3752.py"
PHASE = "backend/tests/scheduler/test_disarm_phase_u8_3752.py"
CONTRACT = "backend/agent/contracts/device_return.py"
ENGINE = "backend/agent/pipeline_engine.py"
SERVICE = "backend/services/device_return.py"
RECYCLER = "backend/scheduler/recycler.py"


def replace(old, new, *, after=None):
    def edit(source):
        start = source.index(after) if after else 0
        assert old in source[start:], (after, old)
        return source[:start] + source[start:].replace(old, new, 1)
    return edit


def unguarded(source):
    start = source.index('    fresh = db.execute', source.index('def _final_disarm_grace_blocks_write'))
    end = source.index('\ndef _running_liveness_anchor', start)
    return source[:start] + '    return False\n\n' + source[end:]


CASES = [
    ("platform_priority", CONTRACT, replace('if started and exit_code in PLATFORM_INTERRUPTED_EXIT_CODES:', 'if False:', after='def map_step_outcome_to_disarm_result'), AGENT, 'platform_interruption'),
    ("exact_category_policy", CONTRACT, replace('match is None:', 'False:', after='def probe_result_policy'), AGENT, 'forged_formal or caller_facts'),
    ("raw_absence_mapping", CONTRACT, replace('verified and raw == "clear" and kind == "ok"', 'verified', after='if mode == "probe":'), AGENT, 'raw_absence'),
    ("registered_combinations", CONTRACT, replace('for permission in ("root", "non-root")', 'for permission in ()'), AGENT, 'registered_real or registry_contains'),
    ("agent_recompute", ENGINE, replace('value = policy["result"] if parsed.get("evidence") else (', 'value = structured if parsed.get("evidence") else ('), AGENT, 'agent_aggregation'),
    ("coordinator_30s", 'backend/agent/coordinator.py', lambda s: s.replace('min(self._interval, DISARM_COORDINATOR_MAX_SECONDS) if disarming else self._interval', 'self._interval'), AGENT, 'coordinator_60s'),
    ("localdb_persistence", 'backend/agent/registry/local_db.py', replace('state.update(fields)', 'state.update({})'), AGENT, 'localdb_preserves'),
    ("old_controller_mid_tail", ENGINE, replace('if getattr(coord, "disarm_protocol", None) == 0:', 'if False:', after='def _refresh_disarm_guard'), AGENT, 'discovered_during'),
    ("old_controller_known", ENGINE, lambda s: s.replace('getattr(coord, "disarm_protocol", None) == 0', 'False'), AGENT, 'old_controller_uses'),
    ("local_queue_budget", ENGINE, replace('min(time.monotonic() + tail["queue_remaining"], tail["deadline"])', 'time.monotonic() + wall'), AGENT, 'eleven_real_scheduler'),
    ("local_execution_budget", ENGINE, replace('remaining = min(deadlines[key], tail["deadline"]) - time.monotonic()', 'remaining = budget'), AGENT, 'exhausted_local'),
    ("server_frozen_step_deadline", CONTRACT, replace('deadlines.setdefault(key, min(now + step["seconds"], out["job_deadline"]))', 'deadlines[key] = min(now + step["seconds"], out["job_deadline"])'), AGENT, 'replays_retry'),
    ("server_sequence", CONTRACT, replace('type(seq) is not int or seq <= out["seq"]', 'type(seq) is not int'), AGENT, 'replays_retry'),
    ("coordinator_evidence_required", CONTRACT, replace('coord = guard.get("coordinator_seen_at")', 'coord = now'), AGENT, 'extend_only'),
    ("complete_trace_fallback", SERVICE, replace('if probe_entry_response(family, current, policy, entry_point="complete")["use_trace"]:', 'if False:'), API, 'complete_keeps_test_conclusion'),
    ("discharge_trust", SERVICE, replace('if policy["trusted"] and result == DISARM_RESULT_CONFIRMED:', 'if result in (DISARM_RESULT_CONFIRMED, DISARM_RESULT_EXECUTED_UNVERIFIED):', after='def apply_discharge_sync'), 'backend/tests/services/test_device_return_u3_3728.py', 'unverified_discharge_no_longer'),
    ("late_trust", SERVICE, replace('not decision["ignore"] and policy["trusted"] and policy["result"] == DISARM_RESULT_CONFIRMED', 'True', after='async def try_apply_late_confirmed'), API, 'late_confirmation_only'),
    ("audit_complete", SERVICE, replace('if not duplicate:', 'if False:', after='def audit_unverified_probe('), API, 'audit_has and complete'),
    ("audit_replay", SERVICE, replace('if not duplicate:', 'if True:', after='def audit_unverified_probe('), API, 'audit_has and discharge'),
    ("audit_late", SERVICE, replace('if not duplicate:', 'if False:', after='async def audit_unverified_probe_async'), API, 'audit_has and late'),
    ("init_policy", SERVICE, replace('if decision["status_code"] == 422:', 'if False:', after='def check_init_probe_result'), API, 'audit_has and init_check'),
    ("recycler_running_guard", RECYCLER, unguarded, PHASE, 'stale_candidate and running'),
    ("recycler_patrol_guard", RECYCLER, unguarded, PHASE, 'guard_overrides_late_patrol'),
    ("recycler_trace_guard", RECYCLER, unguarded, PHASE, 'stale_candidate and trace'),
    ("recycler_postlock_clock", RECYCLER, replace('now = max(now, datetime.now(timezone.utc))', 'now = now'), PHASE, 'committed_transition and execute and running'),
    ("reaper_postlock_clock", 'backend/scheduler/device_lease_reconciler.py', replace('now = max(now, datetime.now(timezone.utc))', 'now = now', after='async def _abort_reaper_recheck_job'), PHASE, 'reaper_uses_lock_time'),
    ("reaper_guard", 'backend/scheduler/device_lease_reconciler.py', replace('if guarded:', 'if False:', after='async def _abort_reaper_recheck_job'), PHASE, 'actual_queue_heartbeat'),
    ("ownership_reread", SERVICE, replace('owner_valid=guard_owner_valid(db, job, now, guard)', 'owner_valid=True'), PHASE, 'invalid_guard'),
    ("audit_report_identity", SERVICE, replace('report_identity=hashlib.sha256(fencing_token.encode()).hexdigest()', 'report_identity=None'), API, 'distinct_discharge'),
    ("restart_old_controller", ENGINE, replace('                self._refresh_disarm_guard()\n                if self._disarm_tail is None:\n                    return', '                pass'), AGENT, 'restarted_tail'),
    ("projection_owner_failure", 'backend/services/plan_run_devices.py', replace('return min(checked_at, datetime.fromtimestamp(disarm_guard_expiry(fresh.disarm_guard), timezone.utc))', 'return datetime.fromtimestamp(disarm_guard_expiry(fresh.disarm_guard), timezone.utc)'), PHASE, 'projection_does_not'),
    ("projection_reread", 'backend/services/plan_run_devices.py', replace('if db is not None:', 'if False:', after='def running_heartbeat_deadline'), PHASE, 'projection_rereads'),
    ("legacy_reserved_trace", 'backend/services/disarm_derive.py', replace('if code in PLATFORM_INTERRUPTED_EXIT_CODES:', 'if False:'), 'backend/tests/services/test_disarm_results_u2_3727.py', 'reserved'),
    ("started_incomplete", 'backend/services/disarm_derive.py', replace('terminal = {**event, "result": DISARM_RESULT_INCOMPLETE}', 'terminal = None'), 'backend/tests/services/test_disarm_results_u2_3727.py', 'only_started'),
]


def main():
    logdir = Path('/tmp/b6-u8-withdrawals')
    logdir.mkdir(exist_ok=True)
    summary = []
    env = {'PATH': os.pathsep.join(os.get_exec_path()), 'PYTHONPATH': '.', 'TESTING': '1', 'JWT_SECRET_KEY': 'b6-isolated-test-only'}
    start_at = sys.argv[1] if len(sys.argv) > 1 else None
    if start_at is not None and start_at not in {case[0] for case in CASES}:
        raise SystemExit("unknown withdrawal")
    active = start_at is None
    for name, filename, edit, test, selection in CASES:
        active = active or name == start_at
        if not active:
            continue
        path = ROOT / filename
        original = path.read_bytes()
        command = ['./scripts/project_python.sh', 'scripts/run_pytest.py', test, '-q', '-k', selection]
        try:
            path.write_text(edit(original.decode()))
            red = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (logdir / f'{name}-red.log').write_bytes(red.stdout)
        finally:
            path.write_bytes(original)
        green = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (logdir / f'{name}-green.log').write_bytes(green.stdout)
        item = {'fix': name, 'file': filename, 'command': command, 'red': red.returncode, 'restored': green.returncode,
                'restored_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        summary.append(item)
        (logdir / 'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(item), flush=True)
        if red.returncode != 1 or b'FAILED ' not in red.stdout or green.returncode != 0:
            raise SystemExit(f'{name}: withdrawal not proven; inspect logs')


if __name__ == '__main__':
    main()
