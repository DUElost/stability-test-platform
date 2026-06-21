"""LogArchiver — Agent 侧 SSD 运行日志 prune 调度器（ADR-0025 方案 C）。

职责：周期扫描 SSD run_log_dir，prune 已过 grace 的非活跃 Job 目录。
不再搬运运行日志到 15.4，不再做 cycle 快照。
HDD 溢出上送由 HddSpillManager 负责。
"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LogArchiver:
    """进程级单例；由 Agent main.py configure + start。"""

    _instance: Optional["LogArchiver"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._db = None
        self._run_log_dir: Optional[Path] = None
        self._interval: float = 3600.0
        self._grace_seconds: float = 1800.0
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured = False
        self._pruned_total = 0
        self._metrics_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "LogArchiver":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            try:
                inst.stop(timeout=0.5)
            except Exception:
                pass

    def configure(
        self,
        *,
        local_db,
        run_log_dir: str,
        interval_seconds: float = 3600.0,
        grace_seconds: float = 1800.0,
    ) -> "LogArchiver":
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("configure() after start() is not allowed")
        self._db = local_db
        self._run_log_dir = Path(run_log_dir)
        self._interval = max(60.0, float(interval_seconds))
        self._grace_seconds = max(0.0, float(grace_seconds))
        self._configured = True
        logger.info(
            "log_archiver_configured run_log_dir=%s interval=%.0fs grace=%.0fs",
            self._run_log_dir, self._interval, self._grace_seconds,
        )
        return self

    def is_configured(self) -> bool:
        return self._configured

    def start(self) -> None:
        if not self._configured:
            raise RuntimeError("LogArchiver not configured — call configure(...) first")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="log-archiver", daemon=True,
        )
        self._thread.start()
        logger.info("log_archiver_started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("log_archiver_stopped metrics=%s", self.snapshot_metrics())

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception("log_archiver_scan_unhandled")
            self._stop_evt.wait(self._interval)

    def scan_once(self, *, grace_seconds: float | None = None) -> int:
        """扫描并 prune 所有已完成且过 grace 的非活跃 Job 目录。返回 prune 数。"""
        if not self._configured or self._db is None:
            return 0
        effective_grace = self._grace_seconds if grace_seconds is None else grace_seconds
        pruned = 0
        now = self._now()
        active_ids = self._active_job_ids()
        for job_dir, job_id in self._iter_job_dirs():
            if job_id in active_ids:
                continue
            if not self._is_aged(job_dir, now, effective_grace):
                continue
            self._prune_local(job_dir, job_id)
            pruned += 1
        return pruned

    def _iter_job_dirs(self):
        if self._run_log_dir is None or not self._run_log_dir.exists():
            return
        for entry in self._run_log_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                job_id = int(entry.name)
            except ValueError:
                continue
            yield entry, job_id

    def _active_job_ids(self) -> set:
        try:
            return {int(j["job_id"]) for j in self._db.get_active_jobs()}
        except Exception:
            logger.exception("log_archiver_active_jobs_failed")
            raise

    @staticmethod
    def _is_aged(job_dir: Path, now: datetime, grace_seconds: float) -> bool:
        try:
            mtime = job_dir.stat().st_mtime
        except OSError:
            return False
        return (now.timestamp() - mtime) >= grace_seconds

    def _prune_local(self, job_dir: Path, job_id: int) -> None:
        shutil.rmtree(str(job_dir), ignore_errors=True)
        with self._metrics_lock:
            self._pruned_total += 1

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def snapshot_metrics(self) -> Dict[str, Any]:
        with self._metrics_lock:
            return {"pruned_total": self._pruned_total}


def collect_archive_heartbeat_metrics() -> Optional[Dict[str, Any]]:
    archiver = LogArchiver.instance()
    if not archiver.is_configured():
        return None
    metrics: Dict[str, Any] = dict(archiver.snapshot_metrics())
    try:
        from .local_disk_monitor import HddSpillMonitor
        monitor = HddSpillMonitor.instance()
        if monitor.is_configured():
            metrics.update(monitor.snapshot_metrics())
    except Exception:
        pass
    return metrics


__all__ = [
    "LogArchiver",
    "collect_archive_heartbeat_metrics",
]
