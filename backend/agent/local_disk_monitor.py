"""HddSpillMonitor — Agent HDD 溢出监控（ADR-0025 / ADR-0028）。

职责：
    - interval 后台线程读取 HDD（hdd_root 所在盘）使用率
    - 使用率 ≥ spill_threshold_pct → 查 DB ``state=LOCAL`` 最旧事件，
      经 EventUploader enqueue（与常规连续上送同一 queue）
    - EventUploader 落盘路径为 ``devices/{plan_run_id}/`` 或
      ``unassigned/{event_id}/``（不再 rglob + copytree 到 legacy 相对路径）
    - 循环直至回落到 target_pct 或无可 enqueue 的 LOCAL 事件
    - SSD fallback root 下禁用 spill（``is_ssd_fallback_root``）
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


class HddSpillMonitor:
    """进程级单例；由 Agent main.py configure + start。"""

    _instance: Optional["HddSpillMonitor"] = None
    _instance_lock = threading.Lock()

    _MAX_SPILL_PER_CYCLE = 20

    def __init__(self) -> None:
        self._hdd_root: str = ""
        self._cifs_root: str = ""
        self._interval: float = 300.0
        self._threshold_pct: float = 95.0
        self._target_pct: float = 70.0
        self._disk_usage_fn = None
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured = False
        self._spill_cycles = 0
        self._spilled_total = 0
        self._last_usage_pct: Optional[float] = None
        self._metrics_lock = threading.Lock()
        self._api_url = ""
        self._agent_secret = ""
        self._host_id = ""
        self._spill_enqueued_ids: set[str] = set()

    @classmethod
    def instance(cls) -> "HddSpillMonitor":
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
        hdd_root: str,
        cifs_root: str,
        interval_seconds: float = 300.0,
        spill_threshold_pct: float = 95.0,
        target_pct: float = 70.0,
        disk_usage_fn=None,
        api_url: str = "",
        agent_secret: str = "",
        host_id: str = "",
    ) -> "HddSpillMonitor":
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("configure() after start() is not allowed")
        self._hdd_root = str(hdd_root)
        self._cifs_root = str(cifs_root)
        self._interval = max(30.0, float(interval_seconds))
        self._threshold_pct = float(spill_threshold_pct)
        self._target_pct = min(float(target_pct), self._threshold_pct)
        if disk_usage_fn is not None:
            self._disk_usage_fn = disk_usage_fn
        else:
            from .system_monitor import get_disk_usage
            self._disk_usage_fn = get_disk_usage
        self._configured = True
        self._api_url = api_url
        self._agent_secret = agent_secret
        self._host_id = host_id
        logger.info(
            "hdd_spill_monitor_configured hdd=%s cifs=%s interval=%.0fs threshold=%.0f%% target=%.0f%%",
            self._hdd_root, self._cifs_root, self._interval,
            self._threshold_pct, self._target_pct,
        )
        return self

    def is_configured(self) -> bool:
        return self._configured

    def start(self) -> None:
        if not self._configured:
            raise RuntimeError("HddSpillMonitor not configured")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="hdd-spill-monitor", daemon=True,
        )
        self._thread.start()
        logger.info("hdd_spill_monitor_started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("hdd_spill_monitor_stopped metrics=%s", self.snapshot_metrics())

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.check_once()
            except Exception:
                logger.exception("hdd_spill_monitor_check_unhandled")
            self._stop_evt.wait(self._interval)

    def check_once(self) -> int:
        """检查 HDD 水位；超阈则经 EventUploader enqueue 最旧 LOCAL 事件。返回 enqueue 数。"""
        if not self._configured or not self._cifs_root:
            return 0
        if self._ssd_spill_disabled():
            logger.debug("hdd_spill_skipped_ssd_mode hdd=%s", self._hdd_root)
            return 0
        usage_pct = self._current_usage_pct()
        with self._metrics_lock:
            if usage_pct is not None:
                self._last_usage_pct = usage_pct
        if usage_pct is None:
            logger.warning("hdd_usage_unavailable — skipping spill check")
            return 0
        if usage_pct < self._threshold_pct:
            return 0
        logger.warning(
            "hdd_high_usage usage=%.1f%% threshold=%.1f%% → 触发溢出上送",
            usage_pct, self._threshold_pct,
        )
        self._spill_enqueued_ids.clear()
        spilled = 0
        for _ in range(self._MAX_SPILL_PER_CYCLE):
            n = self._spill_oldest_event_dir()
            if n == 0:
                post_usage = self._current_usage_pct()
                logger.warning(
                    "hdd_still_high_no_spill_candidate usage=%s",
                    f"{post_usage:.1f}%" if post_usage is not None else "N/A",
                )
                break
            spilled += n
            post_usage = self._current_usage_pct()
            if post_usage is None or post_usage <= self._target_pct:
                break
        if spilled:
            with self._metrics_lock:
                self._spill_cycles += 1
            logger.info("hdd_spill_done dirs=%d", spilled)
        return spilled

    def _ssd_spill_disabled(self) -> bool:
        try:
            from .aee.paths import is_ssd_fallback_root
        except ImportError:
            from agent.aee.paths import is_ssd_fallback_root
        return is_ssd_fallback_root(self._hdd_root)

    def _spill_via_event_uploader(self) -> int:
        """Enqueue one LOCAL DeviceLogEvent via EventUploader (#213 C1)."""
        try:
            from .event_uploader import EventUploader
            from .aee.device_log_event_client import DeviceLogEventClient
        except ImportError:
            from agent.event_uploader import EventUploader
            from agent.aee.device_log_event_client import DeviceLogEventClient

        if not EventUploader.is_enabled():
            logger.warning("hdd_spill_skipped_uploader_disabled")
            return 0
        client = DeviceLogEventClient.from_env(
            api_url=self._api_url,
            agent_secret=self._agent_secret,
            host_id=self._host_id,
        )
        if client is None:
            logger.warning("hdd_spill_skipped_dle_client_unavailable")
            return 0
        events = client.list_events(state="LOCAL", limit=50)
        if not events:
            return 0
        for candidate in events:
            event_id = str(candidate.get("id") or "")
            if event_id and event_id in self._spill_enqueued_ids:
                continue
            if EventUploader.instance().enqueue_local_event(event=candidate, force=True):
                if event_id:
                    self._spill_enqueued_ids.add(event_id)
                logger.info(
                    "hdd_spill_enqueue_event_uploader event_id=%s path=%s",
                    candidate.get("id"), candidate.get("local_path"),
                )
                return 1
        return 0

    def _spill_oldest_event_dir(self) -> int:
        """Enqueue one LOCAL event via EventUploader. Returns 1 or 0 (#213 C1)."""
        if not self._spill_via_event_uploader():
            return 0
        with self._metrics_lock:
            self._spilled_total += 1
        return 1

    def _current_usage_pct(self) -> Optional[float]:
        try:
            info = self._disk_usage_fn(self._hdd_root)
        except Exception:
            logger.exception("hdd_usage_read_failed root=%s", self._hdd_root)
            return None
        if not isinstance(info, dict):
            logger.warning(
                "hdd_usage_invalid_type root=%s type=%s",
                self._hdd_root, type(info).__name__,
            )
            return None
        raw = info.get("usage_percent")
        if raw is None:
            logger.warning("hdd_usage_percent_missing root=%s", self._hdd_root)
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "hdd_usage_percent_invalid root=%s value=%r",
                self._hdd_root, raw,
            )
            return None
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            logger.warning(
                "hdd_usage_percent_out_of_range root=%s value=%r",
                self._hdd_root, raw,
            )
            return None
        return value

    def snapshot_metrics(self) -> Dict[str, Any]:
        with self._metrics_lock:
            last = self._last_usage_pct
            return {
                "local_disk_usage_pct": round(last, 1) if last is not None else None,
                "spill_cycles": self._spill_cycles,
                "spill_threshold_pct": self._threshold_pct,
                "spilled_total": self._spilled_total,
            }


LocalDiskMonitor = HddSpillMonitor

__all__ = ["HddSpillMonitor", "LocalDiskMonitor"]
