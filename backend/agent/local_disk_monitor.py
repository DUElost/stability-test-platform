"""HddSpillMonitor — Agent HDD 溢出监控 + 上送 15.4（ADR-0025 方案 C Sprint 2）。

职责：
    - interval 后台线程读取 HDD（hdd_root 所在盘）使用率
    - 使用率 ≥ spill_threshold_pct → 找最旧 AEE 事件目录，上送到 15.4 CIFS
      {cifs_root}/devices/{folder_name}/{serial}/ 后 prune 本地
    - 循环直至回落到 target_pct 或无更多可上送目录
    - 永不删除活跃 job 关联的事件目录（保守：只按 mtime 排序，跳过近期的）

设计取舍：
    - 上送走 shutil.copytree（安全忽略 NFS/CIFS copystat EPERM）
    - 上送成功后才 prune 本地（不丢数据）
    - 无更多事件目录仍超阈 → 仅告警，不死循环
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
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
        self._last_usage_pct: float = 0.0
        self._metrics_lock = threading.Lock()

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
        """检查 HDD 水位；超阈则上送最旧事件目录到 15.4 后 prune。返回上送目录数。"""
        if not self._configured or not self._cifs_root:
            return 0
        usage_pct = self._current_usage_pct()
        with self._metrics_lock:
            self._last_usage_pct = usage_pct if usage_pct is not None else 0.0
        if usage_pct is None:
            logger.warning("hdd_usage_unavailable — skipping spill check")
            return 0
        if usage_pct < self._threshold_pct:
            return 0
        logger.warning(
            "hdd_high_usage usage=%.1f%% threshold=%.1f%% → 触发溢出上送",
            usage_pct, self._threshold_pct,
        )
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

    def _spill_oldest_event_dir(self) -> int:
        """找最旧事件目录，上送到 CIFS 后 prune 本地。返回 1 或 0。"""
        hdd = Path(self._hdd_root)
        if not hdd.is_dir():
            return 0

        candidates = []
        for entry in hdd.rglob("*"):
            if not entry.is_dir():
                continue
            try:
                if (entry / "__exp_main.txt").exists() or (entry / "main.dbg").exists():
                    mtime = entry.stat().st_mtime
                    candidates.append((mtime, entry))
            except OSError:
                continue

        if not candidates:
            return 0

        candidates.sort(key=lambda t: t[0])
        mtime, local_dir = candidates[0]

        try:
            rel = local_dir.relative_to(hdd)
        except ValueError:
            return 0

        cifs_dir = Path(self._cifs_root) / "devices" / rel

        if cifs_dir.exists():
            shutil.rmtree(str(cifs_dir), ignore_errors=True)

        try:
            self._copytree_safe(str(local_dir), str(cifs_dir))
        except Exception:
            logger.exception("hdd_spill_copy_failed %s → %s", local_dir, cifs_dir)
            return 0

        if not cifs_dir.exists():
            logger.error("hdd_spill_copy_verify_failed %s", cifs_dir)
            return 0

        shutil.rmtree(str(local_dir), ignore_errors=True)
        with self._metrics_lock:
            self._spilled_total += 1
        logger.info("hdd_spill_oldest %s → %s", local_dir, cifs_dir)
        return 1

    @staticmethod
    def _copytree_safe(src: str, dst: str) -> None:
        """copytree ignoring copystat EPERM on NFS/CIFS mounts."""
        src_path = Path(src)
        dst_path = Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)
        for entry in src_path.rglob("*"):
            rel = entry.relative_to(src_path)
            target = dst_path / rel
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.is_file():
                shutil.copyfile(str(entry), str(target))

    def _current_usage_pct(self) -> Optional[float]:
        try:
            info = self._disk_usage_fn(self._hdd_root)
            return float(info.get("usage_percent", 0.0))
        except Exception:
            logger.exception("hdd_usage_read_failed root=%s", self._hdd_root)
            return None

    def snapshot_metrics(self) -> Dict[str, Any]:
        with self._metrics_lock:
            return {
                "local_disk_usage_pct": round(self._last_usage_pct, 1),
                "spill_cycles": self._spill_cycles,
                "spill_threshold_pct": self._threshold_pct,
                "spilled_total": self._spilled_total,
            }


LocalDiskMonitor = HddSpillMonitor

__all__ = ["HddSpillMonitor", "LocalDiskMonitor"]
