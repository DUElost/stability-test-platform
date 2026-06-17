"""LocalDiskMonitor — Agent 本地盘水位监控 + 触发 LogArchiver 溢出（ADR-0025 Sprint 2 / D4）。

职责：
    - interval 后台线程读取 **BASE_DIR 所在盘**（非根分区 `/`）的使用率
    - 使用率 ≥ spill_threshold_pct → 反复调 LogArchiver.spill_oldest(最旧已完成 job)
      直至回落到 target_pct 或无更多可溢出的已完成 job
    - 永不溢出活跃 job（由 LogArchiver.spill_oldest 保证）

设计取舍：
    - 溢出仍走「归档 → 注册 → prune」完整链，绝不裸删本地日志
    - 无更多已完成 job 仍超阈 → 仅告警（活跃 job 占用不可强删），不死循环
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LocalDiskMonitor:
    """进程级单例；由 Agent main.py configure + start。"""

    _instance: Optional["LocalDiskMonitor"] = None
    _instance_lock = threading.Lock()

    # 单轮最多溢出多少个 job，避免一次扫描长时间占用 IO
    _MAX_SPILL_PER_CYCLE = 20

    def __init__(self) -> None:
        self._archiver = None
        self._base_dir: str = ""
        self._interval: float = 300.0
        self._threshold_pct: float = 80.0
        self._target_pct: float = 70.0
        self._disk_usage_fn = None  # 可注入（测试）
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured = False
        self._spill_cycles = 0
        self._last_usage_pct: float = 0.0
        self._metrics_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "LocalDiskMonitor":
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
        archiver,
        base_dir: str,
        interval_seconds: float = 300.0,
        spill_threshold_pct: float = 80.0,
        target_pct: float = 70.0,
        disk_usage_fn=None,
    ) -> "LocalDiskMonitor":
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("configure() after start() is not allowed")
        self._archiver = archiver
        self._base_dir = str(base_dir)
        self._interval = max(30.0, float(interval_seconds))
        self._threshold_pct = float(spill_threshold_pct)
        self._target_pct = min(float(target_pct), self._threshold_pct)
        # 默认用 system_monitor.get_disk_usage；可注入以便测试
        if disk_usage_fn is not None:
            self._disk_usage_fn = disk_usage_fn
        else:
            from .system_monitor import get_disk_usage
            self._disk_usage_fn = get_disk_usage
        self._configured = True
        logger.info(
            "local_disk_monitor_configured base_dir=%s interval=%.0fs threshold=%.0f%% target=%.0f%%",
            self._base_dir, self._interval, self._threshold_pct, self._target_pct,
        )
        return self

    def is_configured(self) -> bool:
        return self._configured

    def start(self) -> None:
        if not self._configured:
            raise RuntimeError("LocalDiskMonitor not configured — call configure(...) first")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="local-disk-monitor", daemon=True,
        )
        self._thread.start()
        logger.info("local_disk_monitor_started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("local_disk_monitor_stopped metrics=%s", self.snapshot_metrics())

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.check_once()
            except Exception:
                logger.exception("local_disk_monitor_check_unhandled")
            self._stop_evt.wait(self._interval)

    def check_once(self) -> int:
        """检查一次水位；超阈则溢出最旧已完成 job 直至回落。返回本次溢出的 job 数。"""
        if not self._configured or self._archiver is None:
            return 0
        usage_pct = self._read_usage_pct()
        if usage_pct is None:
            logger.warning(
                "local_disk_check_skipped_usage_unavailable base_dir=%s",
                self._base_dir,
            )
            return 0
        with self._metrics_lock:
            self._last_usage_pct = usage_pct
        if usage_pct < self._threshold_pct:
            return 0
        logger.warning(
            "local_disk_high_usage usage=%.1f%% threshold=%.1f%% → 触发溢出",
            usage_pct, self._threshold_pct,
        )
        spilled = 0
        for _ in range(self._MAX_SPILL_PER_CYCLE):
            n = 0
            try:
                n = self._archiver.spill_oldest(max_jobs=1)
            except Exception:
                logger.exception("local_disk_monitor_spill_failed")
                break
            if n <= 0:
                # 无更多可溢出的已完成 job（剩余被活跃 job 占用）→ 仅告警，不死循环
                still_high = self._read_usage_pct()
                still_high_display = (
                    f"{still_high:.1f}%" if still_high is not None else "unknown"
                )
                logger.warning(
                    "local_disk_still_high_no_spill_candidate usage=%s "
                    "(剩余空间被活跃 job 占用)", still_high_display,
                )
                break
            spilled += n
            current = self._read_usage_pct()
            if current is None or current <= self._target_pct:
                break
        if spilled:
            with self._metrics_lock:
                self._spill_cycles += 1
            logger.info("local_disk_spill_done spilled_jobs=%d", spilled)
        return spilled

    def _read_usage_pct(self) -> Optional[float]:
        """读取当前盘使用率；失败返回 None（调用方不得当作 0% 低水位）。"""
        try:
            info = self._disk_usage_fn(self._base_dir)
            return float(info.get("usage_percent", 0.0))
        except Exception:
            logger.exception("local_disk_usage_read_failed base_dir=%s", self._base_dir)
            return None

    def _current_usage_pct(self) -> float:
        """兼容旧调用：读数失败时返回上次已知值（默认 0）。"""
        usage = self._read_usage_pct()
        if usage is not None:
            with self._metrics_lock:
                self._last_usage_pct = usage
            return usage
        with self._metrics_lock:
            return self._last_usage_pct

    def snapshot_metrics(self) -> Dict[str, Any]:
        with self._metrics_lock:
            return {
                "local_disk_usage_pct": round(self._last_usage_pct, 1),
                "spill_cycles": self._spill_cycles,
                "spill_threshold_pct": self._threshold_pct,
            }


__all__ = ["LocalDiskMonitor"]
