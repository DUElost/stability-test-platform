"""Finite U4 withdrawals. Opt-in only; every pytest uses the protected runner.

Only pure policy, new init wiring and new debt wiring are withdrawn. No cancel,
process-termination or U8 guard mutation. All selected device paths use signal
stubs and frozen-script device-I/O substitutes. Production sources are restored
byte-for-byte in finally, including when a subprocess fails.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[2]
ENGINE = "backend/agent/pipeline_engine.py"
AGENT = "backend/agent/device_return.py"
SERVICE = "backend/services/device_return.py"
INIT = "backend/agent/tests/test_return_init_u4_3729.py"
DEBT = "backend/agent/tests/test_return_debts_u4_3729.py"
API = "backend/tests/api/test_init_return_u4_3729.py"
LOCK = "backend/tests/api/test_init_return_i5_u4_3729.py"
POLICY = "backend/agent/contracts/device_return.py"
ROUTE = "backend/api/routes/agent_device_return.py"
APP = "def apply_init_check_sync("
BASELINE = "d4008e01373a2989d8d2d8b5a881393f5ee640c8"


@dataclass
class Case:
    name: str
    edits: list[tuple[str, str, str, str | None]]
    target: str
    selection: str


def change(path, old, new, after=None):
    return path, old, new, after


CASES = [
    Case("init_hook", [change(ENGINE, "init_result = self._check_init_returns()", "init_result = StepResult(success=True)")], INIT, "frozen_init_paths"),
    Case("init_disarm_fallback", [change(AGENT, '{"confirmed", "incomplete", "not_executed"}', '{"confirmed", "incomplete", "not_executed", "failed"}')], INIT, "frozen_init_paths"),
    Case("init_failed_result", [change(AGENT, '_CONTINUE_RESULTS = {"confirmed", "executed_unverified"}', '_CONTINUE_RESULTS = {"confirmed", "executed_unverified", "failed"}')], INIT, "frozen_init_paths"),
    Case("init_package", [change(AGENT, 'result_type(success=False, exit_code=2, error_message="package_unavailable")', 'result_type(success=True, exit_code=0, error_message="package_unavailable")')], INIT, "package_unavailable"),
    Case("init_capability", [change(AGENT, 'supported = local_db is not None and local_db.get_state(INIT_CHECK_CAPABILITY_KEY) == "1"', 'supported = True')], INIT, "old_control_plane"),
    Case("init_report_fail_closed", [change(AGENT, 'result_type(success=False, exit_code=1, error_message="init_check_unreported")', 'result_type(success=True, exit_code=0, error_message="init_check_unreported")')], INIT, "report_outage"),
    Case("init_422_downgrade", [change(AGENT, 'entry = {**entry, "result": "executed_unverified", "reason": "init_check_unverified_by_server"}', 'entry = {**entry, "result": "confirmed", "reason": "init_check_unverified_by_server"}')], INIT, "422_is_downgraded"),
    Case("init_lease", [change(ENGINE, 'if phase == "init" and step.get("_return_check") and self._teardown_lease_lost():', 'if False:')], INIT, "lease_lost_after_permit"),
    Case("precheck_no_new_teardown", [change(ENGINE, 'if not return_checks_passed:', 'if False:')], INIT, "precheck_failure_does_not_start"),
    Case("init_terminal_isolation", [
        change(AGENT, 'engine._disarm_results = terminal_results', 'engine._disarm_results.update(terminal_results)'),
        change(ENGINE, 'disarm_results = {family: entry for family, entry in (getattr(self, "_disarm_results", {}) or {}).items()\n                          if family in self._footprint_families}', 'disarm_results = dict(getattr(self, "_disarm_results", {}) or {})'),
    ], API, "frozen_engine_init_api_and_complete"),
    Case("init_accumulator_isolation_single", [
        change(AGENT, 'engine._disarm_results = {}', 'pass'),
    ], INIT, "footprinted_job_skipped_own_disarm"),
    Case("checkpoint_teardown_single", [
        change(ENGINE, 'return_checks_passed = skip_init', 'return_checks_passed = False'),
    ], INIT, "checkpoint_resume_skips_precheck_but_keeps_teardown"),
    Case("init_partial_commit_single", [change(ROUTE,
        'db.commit()\n    if result.get("rejected_families"):',
        'if result.get("rejected_families"):\n        db.rollback()\n    else:\n        db.commit()\n    if result.get("rejected_families"):',
        'def agent_init_return_check(')], API, "partial_init_rejection"),
    Case("init_trace_aggregation_single", [
        change(ENGINE, 'trace_step = not (phase == "init" and step.get("_return_check"))', 'trace_step = True'),
    ], API, "init_trace_aggregation"),
    Case("init_policy_nonprivileged", [change(POLICY, 'or not privileged:\n', 'or (not privileged and entry_point != "init_check"):\n')], API, "untrusted_nonprivileged"),
    Case("init_token", [change(SERVICE, 'lease.fencing_token != fencing_token', 'False', APP)], API, "token_rejection"),
    Case("init_shared_policy", [change(SERVICE, 'decision = check_init_probe_result(db, device, family, entry, token_valid=True, report_identity=identity)', 'decision = {"status_code": 200}', APP)], API, "untrusted_privileged"),
    Case("init_closed_replay", [change(SERVICE, 'if replayed_debt is not None:', 'if False:', APP)], API, "closed_init_failure_replay"),
    Case("init_audit_identity", [change(SERVICE, 'token_valid=True, report_identity=identity)', 'token_valid=True)', APP)], API, "distinct_init_lease"),
    Case("init_close_all_debts", [change(SERVICE, 'if result == DISARM_RESULT_CONFIRMED:', 'if False:', APP)], API, "confirmed_closes_all"),
    Case("init_failure_debt", [change(SERVICE, 'elif state == FAMILY_RETURN_DEBT:', 'elif False:', APP)], API, "untrusted_nonprivileged"),
    Case("init_i5", [
        change(SERVICE, 'device = _lock_device_sync(db, job.device_id)', 'device = db.get(Device, job.device_id)', APP),
        change(SERVICE, 'now = _now()\n', 'device = _lock_device_sync(db, job.device_id)\n    now = _now()\n', APP),
    ], LOCK, "third_session_nowait"),
    Case("heartbeat_capability", [change("backend/api/routes/heartbeat.py", '"return_init_check_available": True', '"return_init_check_available": False')], API, "heartbeat_declares"),
    Case("heartbeat_consume", [change("backend/agent/heartbeat_thread.py", 'self._on_return_state(response, devices_list)', 'None')], DEBT, "real_heartbeat_tick"),
    Case("heartbeat_scheduler_binding", [change("backend/agent/heartbeat_bindings.py", 'return getattr(getattr(heartbeat, "_get_operation_stats", None), "__self__", None)', 'return None')], DEBT, "real_heartbeat_bindings"),
    Case("debt_reconnect", [change(AGENT, 'self._epochs[serial] = self._epochs.get(serial, 0) + 1', 'self._epochs[serial] = self._epochs.get(serial, 0)')], DEBT, "physical_reconnect"),
    Case("debt_retry_interval", [change(AGENT, '_RETRY_SECONDS = 30 * 60', '_RETRY_SECONDS = 0')], DEBT, "failed_discharge_waits"),
    Case("debt_unverified_no_retry", [change(AGENT, '_RETRY_RESULTS = {"failed", "incomplete", "not_executed", "unreported"}', '_RETRY_RESULTS = {"failed", "incomplete", "not_executed", "unreported", "executed_unverified"}')], DEBT, "unverified_does_not_retry_periodically"),
    Case("debt_active_job", [
        change(AGENT, 'or self.is_active(debt.device_id):', 'or False:'),
        change(AGENT, 'if self.is_active(debt.device_id):', 'if False:'),
        change(AGENT, 'if self.is_active(debt.device_id):', 'if False:'),
    ], DEBT, "active_job_blocks"),
    Case("debt_permit", [
        change(AGENT, 'import hashlib\n', 'import hashlib\nfrom contextlib import nullcontext\n'),
        change(AGENT, 'permit = scheduler.acquire(debt.device_id, timeout=0)', 'permit = nullcontext()'),
    ], DEBT, "no_permit_prevents"),
    Case("debt_409_token", [change(AGENT, 'self._rejected.add((debt.device_id, self._token_digest(debt.token)))', 'pass')], DEBT, "definitive_rejection"),
    Case("debt_422_untrusted", [change(AGENT, 'value = "executed_unverified" if status == 422 else "unreported"', 'value = "unreported"')], DEBT, "definitive_rejection"),
    Case("debt_missing_field", [change(AGENT, 'self._debts.clear()', 'pass')], DEBT, "missing_field_clears"),
    Case("debt_report_token", [change(AGENT, 'report_return_discharge(self.api_url, debt.device_id, debt.token, {family: entry})', 'report_return_discharge(self.api_url, debt.device_id, "wrong-test-token", {family: entry})')], DEBT, "physical_reconnect"),
]


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    paths = {item[0] for case in CASES for item in case.edits}
    originals = {name: (ROOT / name).read_bytes() for name in paths}
    baseline = subprocess.check_output(["git", "show", BASELINE + ":" + ENGINE], cwd=ROOT)
    def bodies(source):
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef)}
    old_bodies, new_bodies = bodies(baseline), bodies(originals[ENGINE])
    for name in ("cancel", "_terminate_process_tree", "_pump_process", "_begin_disarm_tail",
                 "_refresh_disarm_guard", "_publish_disarm_phase", "_current_disarm_deadline"):
        assert old_bodies[name] == new_bodies[name], "baseline may not withdraw signal safety or U8 guard: " + name
    cases = [Case("baseline_init", [change(ENGINE, originals[ENGINE].decode(), baseline.decode())],
                  INIT, "frozen_init_paths"), *CASES]
    results = []
    env = {"PATH": os.pathsep.join(os.get_exec_path()), "PYTHONPATH": ".", "TESTING": "1", "JWT_SECRET_KEY": "b6-isolated-test-only"}
    try:
        for case in cases:
            print("START " + case.name, flush=True)
            try:
                for name, old, new, anchor in case.edits:
                    path = ROOT / name
                    source = path.read_text()
                    before, found, tail = source.partition(anchor) if anchor else ("", "", source)
                    assert (not anchor or found) and old in tail, (case.name, name, old)
                    path.write_text(before + found + tail.replace(old, new, 1))
                command = [str(ROOT / "scripts/project_python.sh"), "scripts/run_pytest.py", case.target,
                           "-q", "-k", case.selection]
                log_path = output / (case.name + ".log")
                with log_path.open("w") as log:
                    result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                content = log_path.read_text()
                red = result.returncode == 1 and "FAILED " in content and not any(
                    label in content for label in ("ERROR collecting", "ERROR at setup", "ERROR at teardown"))
                row = {"name": case.name, "pytest_exit": result.returncode, "red": red,
                       "command": command, "log": str(log_path),
                       "baseline_sha": BASELINE if case.name == "baseline_init" else None}
                results.append(row)
                print(json.dumps({"name": case.name, "pytest_exit": result.returncode, "red": red}), flush=True)
            finally:
                for name, source in originals.items():
                    (ROOT / name).write_bytes(source)
            (output / "status.json").write_text(json.dumps(results, indent=2) + "\n")
    finally:
        for name, source in originals.items():
            (ROOT / name).write_bytes(source)
        restored = all((ROOT / name).read_bytes() == source for name, source in originals.items())
        (output / "restored.json").write_text(json.dumps({"restored": restored, "sha256": {
            name: hashlib.sha256(source).hexdigest() for name, source in originals.items()}}, indent=2) + "\n")
        print("RESTORED=" + str(restored), flush=True)
    return 0 if len(results) == len(cases) and all(row["red"] for row in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="explicit opt-in to reversible withdrawals")
    parser.add_argument("--output-dir", type=Path)
    options = parser.parse_args()
    if options.run:
        if options.output_dir is None:
            parser.error("--run requires --output-dir")
        raise SystemExit(run(options.output_dir))
    print("baseline_init\n" + "\n".join(case.name for case in CASES))
