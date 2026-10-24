"""B6-U4 init checks and host debt execution; U8 remains the result authority."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from .api_client import report_init_return_check, report_return_discharge
from .adb_wrapper import AdbWrapper
from .contracts.device_return import ONBOARDED, make_disarm_result
from .operation_scheduler import OperationScheduler, PermitDenied
from .script_packages import PackageUnavailable, resolve_script_path

logger = logging.getLogger(__name__)
INIT_CHECK_CAPABILITY_KEY = "return_init_check_available"
_RETRY_SECONDS = 30 * 60
_CONTINUE_RESULTS = {"confirmed", "executed_unverified"}
_RETRY_RESULTS = {"failed", "incomplete", "not_executed", "unreported"}


def registered_step(family: str, mode: str) -> dict:
    spec = ONBOARDED[family]
    return {"step_id": "return_check." + family, "action": "script:" + spec["disarm_script"],
            "version": spec["disarm_min_version"], "timeout_seconds": spec["disarm_budget_seconds"],
            "params": {"mode": mode}, "_return_check": True}


def execute_registered(engine: Any, family: str, mode: str, *, with_permit: bool = True) -> dict:
    """Run the frozen registration and reuse the real Engine's U8 aggregation.

    A separate accumulator prevents init facts from entering terminal results.
    The same path serves debt work after its caller has acquired the permit.
    """
    step = registered_step(family, mode)
    try:
        registration = engine._script_registry.resolve(step["action"].split(":", 1)[1], step["version"])
        resolve_script_path(registration)
    except Exception as exc:
        raise PackageUnavailable("package_unavailable") from exc
    engine._last_step_result = None
    if with_permit:
        engine._run_step_with_permit("init", step)
        outcome = engine._last_step_result
    else:
        outcome = engine._execute_step("init", step)
    if outcome is not None and "script package unavailable:" in (outcome.error_message or ""):
        raise PackageUnavailable("package_unavailable")
    if outcome is None:
        entry = make_disarm_result(result="not_executed", step_id=step["step_id"],
                                   reason=engine._last_step_error or "execution_not_started")
        entry["evidence"] = {"script_name": ONBOARDED[family]["disarm_script"],
            "script_version": step["version"], "package_sha256": registration.package_sha256}
        return entry
    output = outcome.output or ""
    # Failed script steps also retain stderr (including PROGRESS) after stdout.
    # Decode the first JSON value; all interpretation stays in U8 aggregation.
    try:
        decoded, _ = json.JSONDecoder().raw_decode(output.lstrip())
        if isinstance(decoded, dict):
            output = json.dumps(decoded)
    except (ValueError, TypeError):
        pass
    terminal_results = engine._disarm_results
    try:
        engine._disarm_results = {}
        engine._record_disarm_for_step(step, started=True, success=outcome.success,
            exit_code=outcome.exit_code, output=output, error_message=outcome.error_message)
        entry = dict(engine._disarm_results[family])
    finally:
        engine._disarm_results = terminal_results
    evidence = dict(entry.get("evidence") or {})
    evidence.update(script_name=ONBOARDED[family]["disarm_script"], script_version=step["version"],
                    package_sha256=registration.package_sha256)
    entry["evidence"] = evidence
    return entry


def run_init_checks(engine: Any, result_type: Callable):
    for family in ONBOARDED:
        step_id = "return_check." + family
        executions = []
        entry = None
        success = False
        error_message = None
        exit_code = None
        trace_output = None
        engine._report_step_trace_mq(step_id, "init", "STARTED", "RUNNING")
        try:
            try:
                entry = execute_registered(engine, family, "probe")
                executions.append({"mode": "probe", "entry": dict(entry)})
                if entry["result"] not in {"confirmed", "incomplete", "not_executed"}:
                    entry = execute_registered(engine, family, "disarm")
                    executions.append({"mode": "disarm", "entry": dict(entry)})
            except PackageUnavailable:
                error_message, exit_code = "package_unavailable", 2
                return result_type(success=False, exit_code=2, error_message="package_unavailable")
            entry["source"] = "init_check"
            local_db = getattr(engine, "_local_db", None)
            supported = local_db is not None and local_db.get_state(INIT_CHECK_CAPABILITY_KEY) == "1"
            if supported and engine._api_url:
                try:
                    try:
                        report_init_return_check(engine._api_url, engine._run_id, engine._fencing_token, {family: entry})
                    except requests.HTTPError as exc:
                        payload = exc.response.json() if exc.response is not None else {}
                        details = payload.get("detail", {}) if isinstance(payload, dict) else {}
                        violations = details.get("violations", []) if isinstance(details, dict) else []
                        rejected = exc.response is not None and exc.response.status_code == 422 and any(
                            v.get("family") == family for v in violations)
                        if entry["result"] != "confirmed" or not rejected:
                            raise
                        entry = {**entry, "result": "executed_unverified", "reason": "init_check_unverified_by_server"}
                        report_init_return_check(engine._api_url, engine._run_id, engine._fencing_token, {family: entry})
                except (requests.RequestException, ValueError) as exc:
                    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 409:
                        engine.mark_lease_lost_for_disarm()
                    error_message, exit_code = "init_check_unreported", 1
                    return result_type(success=False, exit_code=1, error_message="init_check_unreported")
            trace_output = json.dumps(entry)
            success = entry["result"] in _CONTINUE_RESULTS
            exit_code = 0 if success else entry.get("exit_code")
            if not success:
                error_message = "init_return_check:" + entry["result"]
                return result_type(success=False, exit_code=1, error_message=error_message)
        finally:
            terminal = "COMPLETED" if success else "FAILED"
            engine._report_step_trace_mq(step_id, "init", terminal, terminal,
                output=trace_output, error_message=error_message,
                exit_code=exit_code, metadata={"executions": executions})
    return result_type(success=True)


@dataclass
class _Debt:
    device_id: int
    serial: str
    token: str
    families: list[str]
    attempts: dict[str, tuple[float, str]] = field(default_factory=dict)
    connection_epoch: int = -1


class ReturnDebtConsumer:
    """Heartbeat-owned, serialized work queue; no work without a server debt."""

    def __init__(self, *, api_url: str, adb_path: str, script_registry: Any, local_db: Any,
                 get_scheduler: Callable, is_active: Callable, engine_factory: Callable,
                 clock: Callable = time.monotonic,
                 launch: Callable | None = None):
        self.api_url, self.adb_path = api_url, adb_path
        self.registry, self.local_db = script_registry, local_db
        self.get_scheduler, self.is_active, self.clock = get_scheduler, is_active, clock
        self.engine_factory = engine_factory
        self.launch = launch or (lambda fn: threading.Thread(target=fn, name="return-debts", daemon=True).start())
        self._lock = threading.Lock()
        self._debts: dict[int, _Debt] = {}
        self._online: set[str] = set()
        self._epochs: dict[str, int] = {}
        self._rejected: set[tuple[int, str]] = set()
        self._pending: list[tuple[_Debt, list[str]]] = []
        self._inflight: set[int] = set()
        self._running = False

    def receive(self, response: dict | None, devices: list[dict]) -> None:
        online = {d["serial"] for d in devices if d.get("adb_connected") is True}
        with self._lock:
            for serial in online - self._online:
                self._epochs[serial] = self._epochs.get(serial, 0) + 1
            self._online = online
            if not isinstance(response, dict):
                return
            self.local_db.set_state(INIT_CHECK_CAPABILITY_KEY, "1" if response.get("return_init_check_available") is True else "0")
            rows = response.get("return_debts")
            if not isinstance(rows, list):
                self._debts.clear()
                return
            current: dict[int, _Debt] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                did, serial, token = row.get("device_id"), row.get("serial"), row.get("fencing_token")
                if type(did) is not int or not isinstance(serial, str) or not isinstance(token, str) or not token:
                    continue
                if (did, self._token_digest(token)) in self._rejected:
                    continue
                raw_families = row.get("families")
                families = [f for f in raw_families if f in ONBOARDED] if isinstance(raw_families, list) else []
                prior = self._debts.get(did)
                debt = prior if prior and prior.token == token and prior.serial == serial else _Debt(did, serial, token, families)
                debt.families = families
                current[did] = debt
            self._debts = current
        self._schedule()

    def reconnected(self, serials: list[str]) -> None:
        self._schedule(reconnected=set(serials))

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _schedule(self, *, reconnected: set[str] | None = None) -> None:
        with self._lock:
            now = self.clock()
            for debt in self._debts.values():
                if reconnected is not None and debt.serial not in reconnected:
                    continue
                if debt.serial not in self._online or debt.device_id in self._inflight:
                    continue
                # Retain a reconnect trigger while a job/permit prevents work.
                # Repeated recovery callbacks in one connection epoch coalesce.
                reconnect = debt.connection_epoch != self._epochs.get(debt.serial, 0)
                due = [f for f in debt.families if reconnect or f not in debt.attempts or (
                    debt.attempts[f][1] in _RETRY_RESULTS and now - debt.attempts[f][0] >= _RETRY_SECONDS)]
                if due:
                    self._inflight.add(debt.device_id)
                    self._pending.append((debt, due))
            start = bool(self._pending) and not self._running
            if start:
                self._running = True
        if start:
            self.launch(self._drain)

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._running = False
                    return
                debt, families = self._pending.pop(0)
            try:
                self._execute(debt, families)
            except Exception:
                logger.exception("return_debt_execution_failed device_id=%s", debt.device_id)
            finally:
                with self._lock:
                    self._inflight.discard(debt.device_id)

    def _execute(self, debt: _Debt, families: list[str]) -> None:
        scheduler = self.get_scheduler()
        if not isinstance(scheduler, OperationScheduler) or self.is_active(debt.device_id):
            return
        try:
            permit = scheduler.acquire(debt.device_id, timeout=0)
        except PermitDenied:
            return
        with permit:
            if self.is_active(debt.device_id):
                return
            engine = self.engine_factory(AdbWrapper(self.adb_path), debt.serial, 0,
                script_registry=self.registry, nfs_root="", device_id=debt.device_id,
                operation_scheduler=scheduler)
            for family in families:
                if self.is_active(debt.device_id):
                    return
                with self._lock:
                    if self._debts.get(debt.device_id) is not debt:
                        return
                    debt.connection_epoch = self._epochs.get(debt.serial, 0)
                    debt.attempts[family] = (self.clock(), "unreported")
                try:
                    entry = execute_registered(engine, family, "disarm", with_permit=False)
                    report_return_discharge(self.api_url, debt.device_id, debt.token, {family: entry})
                    value = entry["result"]
                except requests.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    if status == 409:
                        with self._lock:
                            self._rejected.add((debt.device_id, self._token_digest(debt.token)))
                            if self._debts.get(debt.device_id) is debt:
                                del self._debts[debt.device_id]
                        return
                    value = "executed_unverified" if status == 422 else "unreported"
                except (requests.RequestException, PackageUnavailable):
                    value = "unreported"
                with self._lock:
                    debt.attempts[family] = (self.clock(), value)
