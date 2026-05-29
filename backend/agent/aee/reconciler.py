"""AeeDbHistoryReconciler — Per-Job daemon for AEE db_history diff (M0 / PR #2).

职责（KISS）：
    - per-Job daemon thread:周期性 diff `/data/aee_exp/db_history` +
      `/data/vendor/aee_exp/db_history`,通过 `process_device_logs` 复用
      patrol 同款 diff / pull / verify 逻辑(processor.py)
    - 每个新条目通过共享的 `SignalEmitter` 单次 emit 一条 log_signal,
      `category` 按 aee_type 映射,`source="reconciler"`,`extra` 携带
      `event_type / package_name / aee_ts / nfs_path / pull_source`
    - 双节奏:基线 180s;若上一轮有新条目则切到突发 60s × N 轮再回落
    - 状态键命名空间 `aee:reconciler:{job_id}` 与 patrol `scan_aee:{serial}`
      隔离,避免跨 Job 污染

不在本类职责（YAGNI）：
    - 不做 inotifyd 事件接收(那是 DeviceLogWatcher 职责)
    - 不做 capability 探测(由 JobSession 在调用方按 WatcherHandle.capability 把关)
    - 不创建 SignalEmitter(由 JobSession 透传 watcher 内部的同一实例,保证 seq_no 单调)

线程模型：
    - 单后台 daemon 线程;stop() 通过 threading.Event 通知退出 + join(timeout)
    - tick_once() 暴露同步入口便于单元测试

环境变量（与 §5 计划对齐）：
    STP_WATCHER_AEE_RECONCILE_ENABLED          1/true 开启;默认 false
    STP_WATCHER_AEE_RECONCILE_INTERVAL_SECONDS 基线节奏,默认 180
    STP_WATCHER_AEE_RECONCILE_BURST_INTERVAL_SECONDS 突发节奏,默认 60
    STP_WATCHER_AEE_RECONCILE_BURST_ROUNDS     突发轮数,默认 5
    STP_WATCHER_AEE_RECONCILE_HOSTS            灰度 host 白名单,逗号分隔
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..watcher.contracts import ContractViolation
from .processor import ProcessConfig, process_device_logs
from .timestamp import parse_timestamp

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------

@dataclass
class ReconcilerStats:
    """Reconciler 运行期统计;由 JobSession 在 stop 时回填到 summary。"""

    ticks_total: int = 0
    ticks_with_new: int = 0
    new_entries_total: int = 0
    signals_emitted: int = 0
    signals_dropped: int = 0       # contract violation / emit 异常
    tick_errors: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "ticks_total":       self.ticks_total,
            "ticks_with_new":    self.ticks_with_new,
            "new_entries_total": self.new_entries_total,
            "signals_emitted":   self.signals_emitted,
            "signals_dropped":   self.signals_dropped,
            "tick_errors":       self.tick_errors,
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_AEE_TYPE_TO_CATEGORY = {
    "aee_exp":        "AEE",
    "vendor_aee_exp": "VENDOR_AEE",
}


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
        return v if v > 0 else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v >= 0 else default
    except ValueError:
        return default


def is_reconciler_enabled(host_id: Optional[str] = None) -> bool:
    """统一开关判定:总开关 + 可选灰度 host 白名单。

    若 STP_WATCHER_AEE_RECONCILE_HOSTS 不为空,只放行命中其中的 host_id;
    否则按 STP_WATCHER_AEE_RECONCILE_ENABLED 判定。
    """
    if not _env_truthy("STP_WATCHER_AEE_RECONCILE_ENABLED", default=False):
        return False
    hosts_raw = (os.environ.get("STP_WATCHER_AEE_RECONCILE_HOSTS", "") or "").strip()
    if not hosts_raw:
        return True
    if host_id is None:
        return False
    allow = {h.strip() for h in hosts_raw.split(",") if h.strip()}
    return host_id in allow


# ----------------------------------------------------------------------
# AeeDbHistoryReconciler
# ----------------------------------------------------------------------

class AeeDbHistoryReconciler:
    """Per-Job AEE db_history 增量轮询器。

    用法（典型）::
        reconciler = AeeDbHistoryReconciler(
            signal_emitter=watcher.emitter,
            state_store=local_db,
            serial="SX",
            job_id=123,
            host_id="HOST",
            adb_path="adb",
            nfs_root=Path("/mnt/nfs/stability"),
        )
        reconciler.start()
        ...
        reconciler.stop(timeout=5.0)

    stop(timeout) 同步 join 后台线程;timeout 内未退出则放弃 join,daemon
    线程随进程退出。stop 后 stats 可读。
    """

    def __init__(
        self,
        *,
        signal_emitter,
        state_store: Any,
        serial: str,
        job_id: int,
        host_id: str,
        adb_path: str = "adb",
        nfs_root: Optional[Path] = None,
        baseline_interval_seconds: Optional[float] = None,
        burst_interval_seconds: Optional[float] = None,
        burst_rounds: Optional[int] = None,
        aee_paths: Optional[List[str]] = None,
        export_mobilelog: bool = True,
        export_bugreport: bool = True,
    ) -> None:
        self._emitter = signal_emitter
        self._state_store = state_store
        self._serial = str(serial)
        self._job_id = int(job_id)
        self._host_id = str(host_id)
        self._adb_path = str(adb_path)
        self._nfs_root = Path(nfs_root) if nfs_root else None

        self._baseline = (
            baseline_interval_seconds
            if baseline_interval_seconds is not None
            else _env_float("STP_WATCHER_AEE_RECONCILE_INTERVAL_SECONDS", 180.0)
        )
        self._burst = (
            burst_interval_seconds
            if burst_interval_seconds is not None
            else _env_float("STP_WATCHER_AEE_RECONCILE_BURST_INTERVAL_SECONDS", 60.0)
        )
        self._burst_rounds = (
            burst_rounds
            if burst_rounds is not None
            else _env_int("STP_WATCHER_AEE_RECONCILE_BURST_ROUNDS", 5)
        )
        # state_key_prefix 按 Job 隔离;与 patrol scan_aee:{serial} 命名空间不冲突
        self._state_prefix = f"aee:reconciler:{self._job_id}"

        self._cfg = ProcessConfig(
            aee_paths=aee_paths or ["/data/aee_exp", "/data/vendor/aee_exp"],
            export_mobilelog=export_mobilelog,
            export_bugreport=export_bugreport,
            state_key_prefix=self._state_prefix,
        )

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self.stats = ReconcilerStats()
        self._burst_remaining = 0
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"aee-reconciler-{self._serial}-{self._job_id}",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info(
            "aee_reconciler_started serial=%s job=%d baseline=%.1fs burst=%.1fs rounds=%d",
            self._serial, self._job_id, self._baseline, self._burst, self._burst_rounds,
        )

    def stop(self, timeout: float = 5.0) -> ReconcilerStats:
        if not self._started:
            return self.stats
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._started = False
        logger.info(
            "aee_reconciler_stopped serial=%s job=%d stats=%s",
            self._serial, self._job_id, self.stats.to_dict(),
        )
        return self.stats

    # ------------------------------------------------------------------
    # 主循环 / 单次 tick(测试可直接调用)
    # ------------------------------------------------------------------

    def _run(self) -> None:
        # 第一轮立即跑(避免冷启动 180s 真空期)
        first_run = True
        while not self._stop_evt.is_set():
            if not first_run:
                # 根据 burst 状态决定本轮等待时长
                with self._state_lock:
                    use_burst = self._burst_remaining > 0
                wait = self._burst if use_burst else self._baseline
                if self._stop_evt.wait(wait):
                    break
            first_run = False

            try:
                new_count = self.tick_once()
            except Exception:
                self.stats.tick_errors += 1
                logger.exception(
                    "aee_reconciler_tick_unhandled serial=%s job=%d",
                    self._serial, self._job_id,
                )
                continue

            with self._state_lock:
                if new_count > 0:
                    # 命中突发窗:重置剩余突发轮数
                    self._burst_remaining = self._burst_rounds
                elif self._burst_remaining > 0:
                    self._burst_remaining -= 1

    def tick_once(self) -> int:
        """单轮 diff + emit。返回本轮新增条目数。"""
        self.stats.ticks_total += 1
        result = process_device_logs(
            serial=self._serial,
            job_id=self._job_id,
            state_store=self._state_store,
            adb_path=self._adb_path,
            config=self._cfg,
            nfs_root=self._nfs_root,
            on_new_entry=self._handle_new_entry,
        )
        new_count = int(result.pulled)
        if new_count > 0:
            self.stats.ticks_with_new += 1
            self.stats.new_entries_total += new_count
            # M1/T1-2: 双写灰度期对账日志 — 仅在本轮有新行时 INFO,避免 180s 节奏空轮刷屏;
            # 包含累计 stats 快照,运维可滚动对比 reconciler emit 数与 patrol step_trace.metrics。
            logger.info(
                "aee_reconciler_round serial=%s job=%d new=%d "
                "ticks_total=%d new_entries_total=%d signals_emitted=%d "
                "signals_dropped=%d",
                self._serial, self._job_id, new_count,
                self.stats.ticks_total, self.stats.new_entries_total,
                self.stats.signals_emitted, self.stats.signals_dropped,
            )
        if result.errors:
            logger.debug(
                "aee_reconciler_tick_errors serial=%s job=%d errors=%s",
                self._serial, self._job_id, result.errors[:5],
            )
        return new_count

    # ------------------------------------------------------------------
    # 新条目回调 → emit log_signal
    # ------------------------------------------------------------------

    def _handle_new_entry(self, payload: Dict[str, Any]) -> None:
        """processor.on_new_entry 回调:把新落盘的 AEE 条目 emit 成 log_signal。

        payload shape 见 processor.process_device_logs docstring。
        """
        try:
            aee_type = str(payload.get("aee_type") or "")
            category = _AEE_TYPE_TO_CATEGORY.get(aee_type)
            if not category:
                logger.warning(
                    "aee_reconciler_unknown_aee_type serial=%s job=%d aee_type=%r",
                    self._serial, self._job_id, aee_type,
                )
                return

            parsed: Dict[str, Any] = dict(payload.get("parsed") or {})
            db_path: str = str(parsed.get("db_path") or "")
            aee_ts: str = str(parsed.get("timestamp") or "")
            pkg_name: str = str(parsed.get("pkg_name") or "") or "unknown"
            event_type: str = str(parsed.get("event_type") or "") or "UNKNOWN"
            output_subdir = payload.get("output_subdir")

            detected_at = parse_timestamp(aee_ts) or datetime.now(timezone.utc)
            if detected_at.tzinfo is None:
                detected_at = detected_at.replace(tzinfo=timezone.utc)

            extra: Dict[str, Any] = {
                "event_type":   event_type,
                "package_name": pkg_name,
                "aee_ts":       aee_ts,
                "nfs_path":     str(output_subdir) if output_subdir else None,
                "pull_source":  "reconciler",
            }

            self._emitter.emit(
                category=category,
                source="reconciler",
                path_on_device=db_path,
                detected_at=detected_at,
                artifact_uri=str(output_subdir) if output_subdir else None,
                extra=extra,
            )
            self.stats.signals_emitted += 1
        except ContractViolation as exc:
            self.stats.signals_dropped += 1
            logger.warning(
                "aee_reconciler_contract_violation serial=%s job=%d err=%s",
                self._serial, self._job_id, exc,
            )
        except Exception:
            self.stats.signals_dropped += 1
            logger.exception(
                "aee_reconciler_emit_failed serial=%s job=%d payload=%s",
                self._serial, self._job_id, payload,
            )


__all__ = [
    "AeeDbHistoryReconciler",
    "ReconcilerStats",
    "is_reconciler_enabled",
]
