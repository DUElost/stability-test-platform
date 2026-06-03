"""AeeDbHistoryReconciler — Per-Job daemon for AEE db_history diff (M0 / PR #2).

职责（KISS）：
    - per-Job daemon thread:周期性 diff `/data/aee_exp/db_history` +
      `/data/vendor/aee_exp/db_history`,通过 `process_device_logs` 复用
      patrol 同款 diff / pull / verify 逻辑(processor.py)
    - 每个新条目通过共享的 `SignalEmitter` 单次 emit 一条 log_signal,
      `category` 按 aee_type 映射,`source="reconciler"`,`extra` 携带
      `event_type / package_name / aee_ts / nfs_path / pull_source`
    - 双节奏:基线 180s;若上一轮有新条目则切到突发 60s × N 轮再回落
    - D2:每轮先 `cat db_history` 算 sha256,内容未变直接跳过本轮
      `process_device_logs`(计入 reconciler_skip_unchanged_total);
      内容变化视为有新行候选 → 触发 burst
    - 状态键(M3):reconciler 使用 `state_key_prefix="watcher:aee"`,
      经同一 `db_history.state_key` helper 生成
      `watcher:aee:{serial}:{aee_type}:processed_entries` / `:pending_pull` 键。
      首次 tick 会把 M1/M2 遗留的 `scan_aee:*` 状态合并进新命名空间。
      去重维度=(serial, aee_type)(AEE 是设备级事件、db_history 设备累积),
      与 patrol 一致;NFS 落盘目录本就与 prefix 无关(folder_name+serial),
      故共用键不改变 emit 语义、不引入新的正确性风险。

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

import hashlib
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from ..watcher.contracts import ContractViolation
from .db_history import load_processed_lines, save_processed_lines, state_key
from .processor import ProcessConfig, process_device_logs
from .state_migration import (
    LEGACY_PATROL_STATE_PREFIX,
    WATCHER_AEE_STATE_PREFIX,
    migrate_legacy_aee_state_store,
)
from .timestamp import parse_timestamp

logger = logging.getLogger(__name__)

# D2 指标:reconciler hash 跳过 / burst gauge。Agent 进程不一定能 import backend.core
# (prometheus 缺失或 core.__init__ 触发 DB),故 best-effort + no-op fallback。
try:
    from ...core.metrics import (
        record_reconciler_skip_unchanged,
        set_reconciler_burst_mode_active,
    )
except Exception:  # pragma: no cover - 仅在 agent 无法 import core 时走到
    def record_reconciler_skip_unchanged(host_id: str) -> None:  # type: ignore
        pass

    def set_reconciler_burst_mode_active(host_id: str, active: bool) -> None:  # type: ignore
        pass


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------

@dataclass
class ReconcilerStats:
    """Reconciler 运行期统计;由 JobSession 在 stop 时回填到 summary。"""

    ticks_total: int = 0
    ticks_with_new: int = 0
    ticks_skipped_unchanged: int = 0   # D2: db_history hash 未变跳过本轮 process
    new_entries_total: int = 0
    baseline_entries_total: int = 0
    runtime_entries_total: int = 0
    signals_emitted: int = 0
    signals_dropped: int = 0       # contract violation / emit 异常
    tick_errors: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "ticks_total":             self.ticks_total,
            "ticks_with_new":          self.ticks_with_new,
            "ticks_skipped_unchanged": self.ticks_skipped_unchanged,
            "new_entries_total":       self.new_entries_total,
            "baseline_entries_total":  self.baseline_entries_total,
            "runtime_entries_total":   self.runtime_entries_total,
            "signals_emitted":         self.signals_emitted,
            "signals_dropped":         self.signals_dropped,
            "tick_errors":             self.tick_errors,
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


def _terminate_process(proc) -> None:
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=0.2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=0.2)
        except Exception:
            pass
    try:
        proc.communicate(timeout=0.2)
    except Exception:
        pass


def _make_interruptible_adb_shell_fn(
    serial: str,
    adb_path: str,
    stop_event: threading.Event,
) -> Callable[[str, int], Optional[str]]:
    def _shell(cmd: str, timeout: int) -> Optional[str]:
        if stop_event.is_set():
            return None
        try:
            proc = subprocess.Popen(
                [adb_path, "-s", serial, "shell", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            return None

        deadline = time.monotonic() + max(float(timeout), 0.0)
        while True:
            if stop_event.is_set():
                _terminate_process(proc)
                return None
            rc = proc.poll()
            if rc is not None:
                stdout, _ = proc.communicate(timeout=0.2)
                if rc != 0:
                    return None
                return stdout or ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(proc)
                return None
            stop_event.wait(min(0.1, remaining))

    return _shell


def _make_interruptible_adb_pull_fn(
    serial: str,
    adb_path: str,
    stop_event: threading.Event,
) -> Callable[[str, str, int], bool]:
    def _pull(remote: str, local: str, timeout: int) -> bool:
        if stop_event.is_set():
            return False
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.Popen(
                [adb_path, "-s", serial, "pull", remote, local],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            return False

        deadline = time.monotonic() + max(float(timeout), 0.0)
        while True:
            if stop_event.is_set():
                _terminate_process(proc)
                return False
            rc = proc.poll()
            if rc is not None:
                proc.communicate(timeout=0.2)
                return rc == 0 and local_path.exists()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(proc)
                return False
            stop_event.wait(min(0.1, remaining))

    return _pull


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
        shell_fn: Optional[Callable[[str, int], Optional[str]]] = None,
        baseline_snapshot_enabled: bool = True,
        baseline_chunk_size: Optional[int] = None,
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
        baseline_chunk = (
            baseline_chunk_size
            if baseline_chunk_size is not None
            else _env_int("STP_WATCHER_AEE_BASELINE_CHUNK_SIZE", 5)
        )
        self._baseline_chunk_size = max(int(baseline_chunk), 1)
        self._state_prefix = WATCHER_AEE_STATE_PREFIX

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
        # D2: 默认 adb 操作需要可中断,避免 stop() 返回后后台线程继续占设备。
        self._shell_fn = shell_fn or _make_interruptible_adb_shell_fn(
            self._serial, self._adb_path, self._stop_evt,
        )
        self._pull_fn = _make_interruptible_adb_pull_fn(
            self._serial, self._adb_path, self._stop_evt,
        )
        # D2: per-aee_type 的 db_history 内容 sha256 缓存(上轮值);用于"内容未变跳过"
        self._db_history_hashes: Dict[str, str] = {}
        # D2: 本轮是否存在"新行候选"(实际新增 pull 或 hash 变化) → 驱动 burst
        self._last_had_new_candidate = False
        # 设备当前已存在问题也要导出,并纳入当前 Job 的总览。
        # baseline snapshot 只在每个 Job 首轮执行一次。
        self._baseline_snapshot_done = not baseline_snapshot_enabled
        self._state_migration_done = False

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
            "aee_reconciler_started serial=%s job=%d baseline=%.1fs burst=%.1fs rounds=%d baseline_chunk=%d",
            self._serial, self._job_id, self._baseline, self._burst, self._burst_rounds,
            self._baseline_chunk_size,
        )

    def stop(self, timeout: float = 5.0) -> ReconcilerStats:
        if not self._started:
            return self.stats
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._thread is not None and self._thread.is_alive():
            logger.warning(
                "aee_reconciler_stop_timeout serial=%s job=%d timeout=%.1fs",
                self._serial, self._job_id, timeout,
            )
        self._started = False
        set_reconciler_burst_mode_active(self._host_id, False)
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
                self.tick_once()
            except Exception:
                self.stats.tick_errors += 1
                logger.exception(
                    "aee_reconciler_tick_unhandled serial=%s job=%d",
                    self._serial, self._job_id,
                )
                continue

            with self._state_lock:
                # D2: burst 由"新行候选"驱动(实际新增 pull 或 db_history hash 变化),
                # 而非仅靠 process_device_logs 的 pulled 计数 — 这样即便某行已被 patrol
                # 抢先 pull(本轮 pulled=0),hash 变化仍会触发 burst 加密探测;
                # hash 未变跳过的轮次 _last_had_new_candidate=False,只递减、不重置 burst。
                if self._last_had_new_candidate:
                    self._burst_remaining = self._burst_rounds
                elif self._burst_remaining > 0:
                    self._burst_remaining -= 1
                burst_active = self._burst_remaining > 0
            set_reconciler_burst_mode_active(self._host_id, burst_active)

    def _read_db_history_hashes(self) -> Dict[str, Optional[str]]:
        """D2: per-aee_type `cat db_history` 内容 sha256。不可读返回 None。"""
        hashes: Dict[str, Optional[str]] = {}
        for remote in self._cfg.aee_paths:
            remote = remote.rstrip("/")
            aee_type = "vendor_aee_exp" if "vendor" in remote else "aee_exp"
            content = self._shell_fn(f"cat {remote}/db_history", 30)
            if content is None:
                hashes[aee_type] = None
            else:
                hashes[aee_type] = hashlib.sha256(
                    content.encode("utf-8", "replace")
                ).hexdigest()
        return hashes

    def _db_history_changed(self) -> Optional[bool]:
        """D2: 比较本轮与缓存的 db_history hash。

        返回:
            True  — 至少一个 aee_type 内容变化(或首轮无缓存) → 应跑 process
            False — 全部可读且与上轮一致 → 可跳过本轮 process
            None  — 存在不可读路径(adb 不可用/db_history 缺失) → 无法判定,保守跑 process
        始终更新可读项的缓存,使下一轮比较有意义。
        """
        current = self._read_db_history_hashes()
        if any(v is None for v in current.values()):
            for k, v in current.items():
                if v is not None:
                    self._db_history_hashes[k] = v
            return None
        changed = (current != self._db_history_hashes)
        self._db_history_hashes = dict(current)
        return changed

    def tick_once(self) -> int:
        """单轮 diff + emit。返回本轮新增条目数。

        D2:先比对 db_history 内容 hash;全部可读且未变则跳过 process_device_logs
        (计 ticks_skipped_unchanged + reconciler_skip_unchanged_total),返回 0 且
        不视为"新行候选"(不触发/重置 burst)。hash 变化或不可读则照常 process,
        并把"hash 变化"也算作新行候选 → 即便本轮 pulled=0(已被 patrol 抢先 pull)
        仍触发 burst。
        """
        self.stats.ticks_total += 1
        self._migrate_legacy_runtime_state_once()
        baseline_new = 0
        if not self._baseline_snapshot_done:
            baseline_new, baseline_has_more = self._run_baseline_snapshot()
            self._baseline_snapshot_done = not baseline_has_more

        changed = self._db_history_changed()
        if changed is False:
            self.stats.ticks_skipped_unchanged += 1
            self._last_had_new_candidate = baseline_new > 0
            record_reconciler_skip_unchanged(self._host_id)
            logger.debug(
                "aee_reconciler_skip_unchanged serial=%s job=%d", self._serial, self._job_id,
            )
            return baseline_new

        result = process_device_logs(
            serial=self._serial,
            job_id=self._job_id,
            state_store=self._state_store,
            adb_path=self._adb_path,
            config=self._cfg,
            nfs_root=self._nfs_root,
            on_new_entry=self._handle_new_entry,
            shell_fn=self._shell_fn,
            pull_fn=self._pull_fn,
            stop_event=self._stop_evt,
        )
        runtime_new = int(result.pulled)
        if runtime_new > 0:
            self.stats.runtime_entries_total += runtime_new
        new_count = baseline_new + runtime_new
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
        # D2: 新行候选 = 实际新增 pull 或 db_history hash 变化(changed is True)。
        # changed is None(不可读)不算 hash 变化,仅按 new_count 判定。
        self._last_had_new_candidate = (new_count > 0) or (changed is True)
        return new_count

    def _run_baseline_snapshot(self) -> tuple[int, bool]:
        """Job 首轮补拉设备当前已存在的问题,并按分片持续纳入当前总览。

        关键约束:
          - baseline 不复用 patrol/reconciler 的共享 processed key 做可见性判定,
            否则设备历史问题会被静默吞掉
          - baseline 成功导出后,把对应行并入共享 processed key,避免同一 Job
            首轮 runtime diff 再次重复 pull/emit
          - baseline backlog 需要分片,避免单轮一次性扫完整个设备历史问题
        """
        baseline_prefix = f"watcher_baseline:{self._job_id}"
        baseline_cfg = replace(
            self._cfg,
            state_key_prefix=baseline_prefix,
            export_mobilelog=False,
            export_bugreport=False,
            max_entries_per_run=self._baseline_chunk_size,
        )
        baseline_lines_by_type: Dict[str, Set[str]] = {
            "aee_exp": set(),
            "vendor_aee_exp": set(),
        }

        def _on_baseline_entry(payload: Dict[str, Any]) -> None:
            scoped_payload = dict(payload)
            scoped_payload["detected_at_override"] = datetime.now(timezone.utc)
            aee_type = str(scoped_payload.get("aee_type") or "")
            line = str(scoped_payload.get("line") or "")
            if aee_type in baseline_lines_by_type and line:
                baseline_lines_by_type[aee_type].add(line)
            self._handle_new_entry(scoped_payload)

        result = process_device_logs(
            serial=self._serial,
            job_id=self._job_id,
            state_store=self._state_store,
            adb_path=self._adb_path,
            config=baseline_cfg,
            nfs_root=self._nfs_root,
            on_new_entry=_on_baseline_entry,
            shell_fn=self._shell_fn,
            pull_fn=self._pull_fn,
            stop_event=self._stop_evt,
        )
        baseline_new = int(result.pulled)
        baseline_has_more = int(result.pending_remaining) > 0
        if baseline_new > 0:
            self.stats.baseline_entries_total += baseline_new
            self._merge_baseline_into_runtime_processed(baseline_lines_by_type)
            logger.info(
                "aee_reconciler_baseline_snapshot serial=%s job=%d baseline=%d pending_remaining=%d",
                self._serial, self._job_id, baseline_new, int(result.pending_remaining),
            )
        if result.errors:
            logger.debug(
                "aee_reconciler_baseline_errors serial=%s job=%d errors=%s",
                self._serial, self._job_id, result.errors[:5],
            )
        return baseline_new, baseline_has_more

    def _merge_baseline_into_runtime_processed(
        self,
        baseline_lines_by_type: Dict[str, Set[str]],
    ) -> None:
        for aee_type, lines in baseline_lines_by_type.items():
            if not lines:
                continue
            shared_key = state_key(self._serial, aee_type, prefix=self._state_prefix)
            processed = load_processed_lines(self._state_store, shared_key)
            processed.update(lines)
            save_processed_lines(self._state_store, shared_key, processed)

    def _migrate_legacy_runtime_state_once(self) -> None:
        if self._state_migration_done or self._state_prefix == LEGACY_PATROL_STATE_PREFIX:
            return
        summary = migrate_legacy_aee_state_store(
            self._state_store,
            serial=self._serial,
            aee_types=self._runtime_aee_types(),
        )
        if (
            int(summary["processed_entries_migrated"]) > 0
            or int(summary["pending_pull_migrated"]) > 0
        ):
            logger.info(
                "aee_reconciler_state_namespace_migrated serial=%s job=%d summary=%s",
                self._serial, self._job_id, summary,
            )
        self._state_migration_done = True

    def _runtime_aee_types(self) -> Set[str]:
        result: Set[str] = set()
        for remote_aee_path in self._cfg.aee_paths:
            result.add("vendor_aee_exp" if "vendor" in remote_aee_path else "aee_exp")
        return result or {"aee_exp", "vendor_aee_exp"}

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

            detected_at = payload.get("detected_at_override")
            if not isinstance(detected_at, datetime):
                detected_at = parse_timestamp(aee_ts) or datetime.now(timezone.utc)
            if detected_at.tzinfo is None:
                detected_at = detected_at.replace(tzinfo=timezone.utc)

            extra: Dict[str, Any] = {
                # §2.2 schema_version 1:演进兼容标记。mobilelog_pulled /
                # bugreport_exported 不在此填 — emit 在 processor.on_new_entry
                # 回调触发,早于 mobilelog/bugreport 副作用(processor.py:231),
                # 此刻两者尚未发生,故按 §2.2「可选」留空。
                "schema_version": 1,
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
