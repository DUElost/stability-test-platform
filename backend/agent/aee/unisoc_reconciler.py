"""UnisocUniviewReconciler — per-Job uniview watcher (ADR-0032 D8 w1)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

from ..watcher.contracts import ContractViolation
from .paths import get_aee_local_root
from .reconciler import (
    ReconcilerStats,
    _env_float,
    _env_int,
    _make_interruptible_adb_shell_fn,
    is_reconciler_enabled,
)

logger = logging.getLogger(__name__)

_UNISOC_STATE_PREFIX = "watcher:unisoc"
_PROCESSED_SUFFIX = "processed_event_dirs"


def _unisoc_watcher_root(local_root: Path, run_date_stamp: Optional[str], serial: str) -> Path:
    stamp = run_date_stamp or "unknown"
    return local_root / "uniview_watcher" / stamp / serial


class UnisocUniviewReconciler:
    """Poll local uniview watcher tree and emit UNIVIEW reconciler signals."""

    def __init__(
        self,
        *,
        signal_emitter,
        state_store: Any,
        serial: str,
        job_id: int,
        host_id: str,
        adb_path: str = "adb",
        local_root: Optional[Path] = None,
        run_date_stamp: Optional[str] = None,
        baseline_interval_seconds: Optional[float] = None,
        plan_run_id: Optional[int] = None,
        platform: str = "UNISOC",
        device_log_client: Any = None,
        platform_collector: Any = None,
        shell_fn: Optional[Callable[[str, int], Optional[str]]] = None,
        **_: Any,
    ) -> None:
        self._emitter = signal_emitter
        self._state_store = state_store
        self._serial = str(serial)
        self._job_id = int(job_id)
        self._host_id = str(host_id)
        self._adb_path = str(adb_path)
        self._local_root = Path(local_root) if local_root else get_aee_local_root()
        self._run_date_stamp = run_date_stamp
        self._plan_run_id = int(plan_run_id) if plan_run_id is not None else None
        self._platform = str(platform or "UNISOC")
        self._device_log_client = device_log_client
        self._platform_collector = platform_collector
        self._baseline = (
            baseline_interval_seconds
            if baseline_interval_seconds is not None
            else _env_float("STP_WATCHER_UNISOC_RECONCILE_INTERVAL_SECONDS", 180.0)
        )
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self.stats = ReconcilerStats()
        self._shell_fn = shell_fn or _make_interruptible_adb_shell_fn(
            self._serial, self._adb_path, self._stop_evt,
        )
        self._processed: Set[str] = set()
        self._state_lock = threading.Lock()
        self._max_consecutive_tick_errors = _env_int(
            "STP_WATCHER_UNISOC_RECONCILE_MAX_TICK_ERRORS", 5,
        )
        self._consecutive_tick_errors = 0

    def start(self) -> bool:
        if self._started:
            return True
        self._load_processed_state()
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"unisoc-reconciler-{self._serial}-{self._job_id}",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info(
            "unisoc_reconciler_started serial=%s job=%d interval=%.1fs",
            self._serial, self._job_id, self._baseline,
        )
        return True

    def stop(self, timeout: float = 5.0) -> ReconcilerStats:
        if not self._started:
            return self.stats
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._started = False
        logger.info(
            "unisoc_reconciler_stopped serial=%s job=%d stats=%s",
            self._serial, self._job_id, self.stats.to_dict(),
        )
        return self.stats

    def _state_key(self) -> str:
        return f"{_UNISOC_STATE_PREFIX}:{self._serial}:{_PROCESSED_SUFFIX}"

    def _load_processed_state(self) -> None:
        if self._state_store is None:
            return
        try:
            raw = self._state_store.get(self._state_key())
            if raw:
                self._processed = set(json.loads(raw))
        except Exception:
            logger.debug("unisoc_reconciler_state_load_failed", exc_info=True)

    def _save_processed_state(self) -> None:
        if self._state_store is None:
            return
        try:
            self._state_store.set(self._state_key(), json.dumps(sorted(self._processed)))
        except Exception:
            logger.debug("unisoc_reconciler_state_save_failed", exc_info=True)

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.tick_once()
                self._consecutive_tick_errors = 0
            except Exception:
                self.stats.tick_errors += 1
                self._consecutive_tick_errors += 1
                logger.exception(
                    "unisoc_reconciler_tick_error serial=%s job=%d",
                    self._serial, self._job_id,
                )
                if self._consecutive_tick_errors >= self._max_consecutive_tick_errors:
                    break
            if self._stop_evt.wait(self._baseline):
                break

    def tick_once(self) -> int:
        self.stats.ticks_total += 1
        root = _unisoc_watcher_root(self._local_root, self._run_date_stamp, self._serial)
        root.mkdir(parents=True, exist_ok=True)
        emitted = 0
        for event_dir in sorted(root.iterdir()):
            if not event_dir.is_dir():
                continue
            key = event_dir.name
            with self._state_lock:
                if key in self._processed:
                    continue
            if not (event_dir / "unievent_info.json").is_file():
                continue
            if self._emit_event(event_dir):
                with self._state_lock:
                    self._processed.add(key)
                emitted += 1
        if emitted:
            self.stats.ticks_with_new += 1
            self.stats.new_entries_total += emitted
            self._save_processed_state()
        return emitted

    def _emit_event(self, event_dir: Path) -> bool:
        detected_at = datetime.now(timezone.utc)
        if self._platform_collector is None:
            return False
        try:
            meta = self._platform_collector.parse_metadata(event_dir)
        except Exception:
            logger.debug("unisoc_reconciler_metadata_failed dir=%s", event_dir, exc_info=True)
            return False

        extra: Dict[str, Any] = {
            "schema_version": 2,
            "event_type": "UNIVIEW",
            "event_subtype": meta.event_subtype,
            "package_name": meta.package_name,
            "aee_ts": meta.event_subtype,
            "aee_ts_utc": meta.device_timestamp.isoformat() if meta.device_timestamp else None,
            "nfs_path": str(event_dir),
            "pull_source": "reconciler",
            "entry_origin": "runtime",
        }
        try:
            seq_no = self._emitter.emit(
                category="UNIVIEW",
                source="reconciler",
                path_on_device=str(event_dir.name),
                detected_at=detected_at,
                artifact_uri=str(event_dir),
                extra=extra,
            )
            self.stats.signals_emitted += 1
            if self._device_log_client is not None:
                self._device_log_client.create_local_event(
                    serial=self._serial,
                    platform=self._platform,
                    event_type="UNIVIEW",
                    event_subtype=meta.event_subtype,
                    detected_at=detected_at,
                    device_timestamp=meta.device_timestamp,
                    local_path=event_dir,
                    plan_run_id=self._plan_run_id,
                    job_id=self._job_id,
                    link_signal_seq_no=seq_no,
                    size_bytes=self._device_log_client.dir_size_bytes(event_dir),
                )
            return True
        except ContractViolation as exc:
            self.stats.signals_dropped += 1
            logger.warning(
                "unisoc_reconciler_contract_violation serial=%s job=%d err=%s",
                self._serial, self._job_id, exc,
            )
            return False
        except Exception:
            self.stats.signals_dropped += 1
            logger.exception("unisoc_reconciler_emit_failed serial=%s job=%d", self._serial, self._job_id)
            return False


def resolve_unisoc_reconciler_enabled(host_id: Optional[str]) -> bool:
    return is_reconciler_enabled(host_id)
