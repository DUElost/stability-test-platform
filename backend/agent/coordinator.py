"""ADR-0026 Step 5b — HostRunCoordinator (per-host singleton).

One Coordinator per Agent process manages every PlanRunHost on this host.
It shares the host-global OperationScheduler and periodically reports:
- coordinator_heartbeat_at + coordinator_epoch for each active PlanRunHost
- per-job execution_state and last_progress_at snapshots

Epoch fencing: coordinator_epoch monotonic per PlanRunHost, incremented on
Agent restart. The control plane rejects reports carrying a lower epoch
than the stored value — a previous process instance cannot overwrite state.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class PlanRunHostView:
    """Mutable projection of one PlanRunHost row (synced to the control plane
    periodically). Thread-safe for the coordinator thread + worker threads."""

    def __init__(self, host_row_id: int, plan_run_id: int, host_id: str) -> None:
        self.id = host_row_id
        self.plan_run_id = plan_run_id
        self.host_id = host_id
        self._lock = threading.Lock()
        self.phase: str = "INIT"   # INIT → PATROL → TEARDOWN
        self._epoch: int = 1       # incremented on Agent restart
        # Barrier: how many jobs on this PlanRunHost and how many have
        # reached the current phase boundary. Reset on phase advance.
        self.barrier_total: int = 0
        self.barrier_arrived: int = 0
        self._barrier_event = threading.Event()

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def bump_epoch(self) -> int:
        with self._lock:
            self._epoch += 1
            return self._epoch

    def to_payload(self) -> dict:
        return {"id": self.id, "plan_run_id": self.plan_run_id, "host_id": self.host_id}

    def set_barrier_total(self, total: int) -> None:
        with self._lock:
            self.barrier_total = total
            self.barrier_arrived = 0
            self._barrier_event.clear()

    def arrive_at_barrier(self) -> bool:
        """Atomically increment arrived count. Returns True if this is the
        LAST arrival (phase can advance), False otherwise."""
        with self._lock:
            self.barrier_arrived += 1
            if self.barrier_arrived >= self.barrier_total:
                self._barrier_event.set()
                return True
            return False

    def wait_barrier(self, timeout: float | None = None) -> bool:
        """Block until all jobs have arrived. Returns True on phase advance,
        False on timeout."""
        return self._barrier_event.wait(timeout=timeout)

    def advance_phase(self, next_phase: str) -> None:
        with self._lock:
            self.phase = next_phase
            self.barrier_arrived = 0
            self._barrier_event.clear()


class JobExecutionView:
    """Thread-safe snapshot of one job's current execution state (read by
    the coordinator for heartbeat, written by worker threads as steps
    progress)."""

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self._lock = threading.Lock()
        self.execution_state: Optional[str] = None
        self.last_progress_at: Optional[str] = None  # ISO8601 UTC

    def update(
        self, state: Optional[str] = None, progress_ts: Optional[str] = None
    ) -> None:
        with self._lock:
            if state is not None:
                self.execution_state = state
            if progress_ts is not None:
                self.last_progress_at = progress_ts

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "execution_state": self.execution_state,
                "last_progress_at": self.last_progress_at,
            }


class HostRunCoordinator:
    """Per-host singleton — started once in the Agent main loop, shares
    the OperationScheduler. Periodically POSTs /coordinator-heartbeat."""

    def __init__(
        self,
        api_url: str,
        host_id: str,
        agent_instance_id: str,
        agent_secret: str = "",
        local_db: Any = None,
    ) -> None:
        self._api_url = api_url
        self._host_id = host_id
        self._agent_instance_id = agent_instance_id
        self._agent_secret = agent_secret
        self._local_db = local_db
        self._interval = float(os.getenv("COORDINATOR_HEARTBEAT_INTERVAL", "30"))
        self._lock = threading.Lock()
        self._plan_run_hosts: Dict[int, PlanRunHostView] = {}  # keyed by host_row_id
        self._job_views: Dict[int, JobExecutionView] = {}  # keyed by job_id
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        # Restore persisted epochs and bump for this process instance.
        self._restore_epochs()
        with self._lock:
            for v in self._plan_run_hosts.values():
                v.bump_epoch()
        self._persist_epochs()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="coordinator")
        self._thread.start()
        logger.info("coordinator_started host=%s", self._host_id)

    def _epoch_key(self, prh_id: int) -> str:
        return f"coord_epoch:{self._host_id}:{prh_id}"

    def _restore_epochs(self) -> None:
        if self._local_db is None:
            return
        for prh_id, view in list(self._plan_run_hosts.items()):
            try:
                raw = self._local_db.get_state(self._epoch_key(prh_id), "1")
                epoch = int(raw)
            except (ValueError, TypeError):
                epoch = 1
            with view._lock:
                view._epoch = epoch

    def _persist_epochs(self) -> None:
        if self._local_db is None:
            return
        for prh_id, view in self._plan_run_hosts.items():
            try:
                self._local_db.set_state(
                    self._epoch_key(prh_id), str(view.epoch)
                )
            except Exception:
                logger.debug("coord_epoch_persist_failed prh=%d", prh_id)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    # ── registration (called by the claim loop) ────────────────────────────

    def register_plan_run_host(self, host_row_id: int, plan_run_id: int) -> PlanRunHostView:
        with self._lock:
            if host_row_id not in self._plan_run_hosts:
                self._plan_run_hosts[host_row_id] = PlanRunHostView(
                    host_row_id, plan_run_id, self._host_id,
                )
            return self._plan_run_hosts[host_row_id]

    def deregister_plan_run_host(self, host_row_id: int) -> None:
        with self._lock:
            self._plan_run_hosts.pop(host_row_id, None)

    def set_barrier_total(self, host_row_id: int, total: int) -> None:
        """Called when all jobs for a PlanRunHost are admitted — sets the
        barrier count for phase advancement."""
        with self._lock:
            v = self._plan_run_hosts.get(host_row_id)
        if v is not None:
            v.set_barrier_total(total)

    def arrive_at_barrier(self, host_row_id: int) -> bool:
        """One job has finished the current phase. Returns True if this is
        the LAST job (caller should advance phase)."""
        with self._lock:
            v = self._plan_run_hosts.get(host_row_id)
        if v is None:
            return False
        return v.arrive_at_barrier()

    def wait_barrier(self, host_row_id: int, timeout: float | None = None) -> bool:
        """Block until all jobs on this PlanRunHost have arrived at the
        barrier (phase can advance)."""
        with self._lock:
            v = self._plan_run_hosts.get(host_row_id)
        if v is None:
            return True  # no host to wait on — proceed
        return v.wait_barrier(timeout=timeout)

    def advance_phase(self, host_row_id: int, next_phase: str) -> None:
        with self._lock:
            v = self._plan_run_hosts.get(host_row_id)
        if v is None:
            return
        v.advance_phase(next_phase)
        with self._lock:
            if job_id not in self._job_views:
                self._job_views[job_id] = JobExecutionView(job_id)
            return self._job_views[job_id]

    def deregister_job(self, job_id: int) -> None:
        with self._lock:
            self._job_views.pop(job_id, None)

    # ── heartbeat loop ─────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self._tick()
            except Exception:
                logger.debug("coordinator_tick_error", exc_info=True)

    def _tick(self) -> None:
        headers = {"X-Agent-Secret": self._agent_secret} if self._agent_secret else {}
        epoch: int
        host_entries: list[dict]
        job_entries: list[dict]

        with self._lock:
            epoch = next(iter(self._plan_run_hosts.values())).epoch if self._plan_run_hosts else 1
            host_entries = [v.to_payload() for v in self._plan_run_hosts.values()]
            job_entries = [
                {
                    "job_id": jv.job_id,
                    **jv.snapshot(),
                }
                for jv in self._job_views.values()
            ]

        try:
            resp = requests.post(
                f"{self._api_url}/api/v1/agent/coordinator-heartbeat",
                json={
                    "host_id": self._host_id,
                    "agent_instance_id": self._agent_instance_id,
                    "coordinator_epoch": epoch,
                    "plan_run_hosts": host_entries,
                    "jobs": job_entries,
                },
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            stale = data.get("stale_plan_run_host_ids") or []
            if stale:
                logger.warning(
                    "coordinator_stale_epoch host=%s stale_hosts=%s",
                    self._host_id, stale,
                )
        except Exception as exc:
            logger.debug("coordinator_heartbeat_failed host=%s: %s", self._host_id, exc)
