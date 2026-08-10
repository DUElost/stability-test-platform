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
      M3 后 legacy `scan_aee:*` 状态在 agent 启动期一次性迁移,运行期不再改写旧键。
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

初筛选主路径（与 InotifydSource 兜底互补）：
    - JobSession 在 Watcher 启动后并行挂载本 Reconciler;成功则共享同一 SignalEmitter。
    - 监测目录仅 /data/aee_exp 与 /data/vendor/aee_exp（MTK 平台 ANR 含于 aee_exp,不监测 /data/anr）。
    - AWS-203:adb pull / db_history 处理在 processor 链路;与 inotifyd 实时监听分离。

环境变量（与 §5 计划对齐）：
    STP_WATCHER_AEE_RECONCILE_ENABLED          1/true 开启;默认 true(ADR-0018 2026-06-18 改)
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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from ..watcher.contracts import ContractViolation
from .db_history import load_processed_lines, save_processed_lines, state_key
from .paths import get_aee_local_root
from .processor import ProcessConfig, process_device_logs
from .state_migration import WATCHER_AEE_STATE_PREFIX
from .timestamp import parse_timestamp, to_utc

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
    if not _env_truthy("STP_WATCHER_AEE_RECONCILE_ENABLED", default=True):
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
            local_root=Path("/mnt/nfs/stability"),
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
        local_root: Optional[Path] = None,
        run_date_stamp: Optional[str] = None,
        baseline_interval_seconds: Optional[float] = None,
        burst_interval_seconds: Optional[float] = None,
        burst_rounds: Optional[int] = None,
        aee_paths: Optional[List[str]] = None,
        export_mobilelog: bool = True,
        export_bugreport: bool = True,
        shell_fn: Optional[Callable[[str, int], Optional[str]]] = None,
        baseline_snapshot_enabled: bool = True,
        baseline_chunk_size: Optional[int] = None,
        plan_run_id: Optional[int] = None,
        platform: str = "MTK",
        device_log_client: Any = None,
        platform_collector: Any = None,
    ) -> None:
        self._emitter = signal_emitter
        self._state_store = state_store
        self._serial = str(serial)
        self._job_id = int(job_id)
        self._host_id = str(host_id)
        self._adb_path = str(adb_path)
        self._local_root = Path(local_root) if local_root else None
        self._run_date_stamp = run_date_stamp
        self._plan_run_id = int(plan_run_id) if plan_run_id is not None else None
        self._platform = str(platform or "MTK")
        self._device_log_client = device_log_client
        self._platform_collector = platform_collector

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
        # 连续 tick 失败上限:超过即自我关闭 + emit rollback signal。
        # 防 #72 现场:STP_AEE_LOCAL_ROOT 不可写时 process_device_logs.mkdir
        # 每 180s 抛 PermissionError → _run() try/except 死循环吞异常累计
        # 上万条 ERROR,无上限时无声吞噬 1GB+ 日志空间且无明确自愈路径。
        self._max_consecutive_tick_errors = _env_int(
            "STP_WATCHER_AEE_RECONCILE_MAX_TICK_ERRORS", 10,
        )
        self._consecutive_tick_errors = 0
    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """启动后台 reconciler 线程。返回 True 表示已启动。

        #78 子任务 2:preflight 仅 INFO log 不可写 local_root,不阻止启动
        (避免 #72 现场 race/挂载抖动误判、避免破坏默认 fallback 路径上的
        现有测试用例)。真正的硬保护由 _run() 的连续 tick 错误上限自我关闭
        机制提供:连续 N 次 tick_once 抛异常即 self_stop + emit rollback signal,
        不再死循环吞异常到 11M 行日志。
        """
        if self._started:
            return True
        # preflight(软性 hint,不阻塞):local_root 不可写时 INFO log 警告。
        # 让调用方/运维感知,但仍启动 reconciler 让 self-stop 机制做最终判定。
        root_for_preflight = self._local_root or get_aee_local_root()
        if not self._is_local_root_writable(root_for_preflight):
            logger.info(
                "aee_reconciler_local_root_not_writable serial=%s job=%d root=%s "
                "— starting anyway; self-stop will trigger if tick keeps failing",
                self._serial, self._job_id, root_for_preflight,
            )
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
        return True

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

    @staticmethod
    def _is_local_root_writable(root: Path) -> bool:
        """local_root 可写性 preflight:能 mkdir 父链 + touch 测试文件即视为可写。

        避免单纯 `os.access(root, W_OK)` 对不存在的目录(父链也不存在)误判
        (POSIX access 对 ENOENT 返回 False,虽 parent 可建但 root 自身仍待创建)。
        """
        try:
            root.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return False
        except OSError:
            return False
        # 再用写测试 token 验证 root 自身可写(防挂载点 ro mount 等场景)
        try:
            probe = root / f".reconciler_probe_{os.getpid()}"
            probe.mkdir(exist_ok=True)
            probe.rmdir()
            return True
        except OSError:
            return False

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
                self._consecutive_tick_errors += 1
                logger.exception(
                    "aee_reconciler_tick_unhandled serial=%s job=%d",
                    self._serial, self._job_id,
                )
                # #78 子任务 2:连续 tick 错误超阈值 → 自我关闭 + emit rollback。
                # 不再死循环 180s tick 百万次(参见 #72 现场:tick_unhandled
                # 累计逾万、agent_error.log 1.1GB、production 长期 0 emit)。
                # 自我关闭后 JobSession 的 inotifyd 兜底路径(若 active)独立工作。
                if (
                    self._max_consecutive_tick_errors > 0
                    and self._consecutive_tick_errors >= self._max_consecutive_tick_errors
                ):
                    logger.error(
                        "aee_reconciler_emit_rollback serial=%s job=%d "
                        "consecutive_errors=%d threshold=%d — self-stopping, "
                        "inotifyd fallback path (if any) continues",
                        self._serial, self._job_id,
                        self._consecutive_tick_errors,
                        self._max_consecutive_tick_errors,
                    )
                    self._emit_rollback_signal()
                    self._stop_evt.set()
                continue

            # tick 成功 → 重置连续错误计数
            self._consecutive_tick_errors = 0

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

        def _on_runtime_entry(payload: Dict[str, Any]) -> None:
            scoped_payload = dict(payload)
            scoped_payload["entry_origin"] = "runtime"
            self._handle_new_entry(scoped_payload)

        result = process_device_logs(
            serial=self._serial,
            job_id=self._job_id,
            state_store=self._state_store,
            adb_path=self._adb_path,
            config=self._cfg,
            local_root=self._local_root,
            run_date_stamp=self._run_date_stamp,
            on_new_entry=_on_runtime_entry,
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
            max_entries_per_run=self._baseline_chunk_size,
        )
        baseline_lines_by_type: Dict[str, Set[str]] = {
            "aee_exp": set(),
            "vendor_aee_exp": set(),
        }

        def _on_baseline_entry(payload: Dict[str, Any]) -> None:
            scoped_payload = dict(payload)
            scoped_payload["detected_at_override"] = datetime.now(timezone.utc)
            scoped_payload["entry_origin"] = "baseline"
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
            local_root=self._local_root,
            run_date_stamp=self._run_date_stamp,
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
            raw_event_type: str = str(parsed.get("raw_event_type") or "")
            event_subtype: str = str(parsed.get("event_subtype") or "") or "其他"
            entry_origin: str = str(payload.get("entry_origin") or "") or "runtime"
            output_subdir = payload.get("output_subdir")

            # #88:detected_at 是「控制面观测到该信号的时刻」,必须来自服务端
            # 时钟 —— 设备时钟不可信(实测生产机漂移 3~9 天),且 db_history 的
            # 时区缩写解析后仍可能与真实 UTC 有出入。
            #
            # 原实现只有 baseline 路径传 detected_at_override,runtime 路径退回
            # 设备时钟,导致 runtime 信号的 detected_at 落到 PlanRun 时间窗口
            # 之外,被 watcher-summary 的 `detected_at BETWEEN ...` 静默过滤 →
            # 运行期间新产生的崩溃在仪表盘上完全不可见。
            #
            # 设备侧原始时间仍完整保留在 extra.aee_ts / aee_ts_utc,不丢信息。
            detected_at = payload.get("detected_at_override")
            if not isinstance(detected_at, datetime):
                detected_at = datetime.now(timezone.utc)
            if detected_at.tzinfo is None:
                detected_at = detected_at.replace(tzinfo=timezone.utc)

            # 设备自报时间换算成真实 UTC 后另存,便于排查设备时钟漂移。
            # 无时区信息时 to_utc 返回 None(不猜),见 timestamp.to_utc 文档。
            aee_ts_utc = to_utc(parse_timestamp(aee_ts))

            extra: Dict[str, Any] = {
                # §2.2 schema_version 2:演进兼容标记。mobilelog_pulled /
                # bugreport_exported 不在此填 — emit 在 processor.on_new_entry
                # 回调触发,早于 mobilelog/bugreport 副作用(processor.py:231),
                # 此刻两者尚未发生,故按 §2.2「可选」留空。
                "schema_version": 2,
                "event_type": event_type,
                "event_subtype": event_subtype,
                "raw_event_type": raw_event_type,
                "package_name": pkg_name,
                "aee_ts": aee_ts,
                # #88:设备自报时间换算出的**真实** UTC;设备没给时区(或时区
                # 缩写不认识)时为 None —— 此时无从换算,不做 UTC 假设。
                # 非 None 时,与 detected_at 的差值即设备时钟漂移,可用于排查。
                "aee_ts_utc": aee_ts_utc.isoformat() if aee_ts_utc else None,
                "nfs_path": str(output_subdir) if output_subdir else None,
                "pull_source": "reconciler",
                "entry_origin": entry_origin,
            }

            seq_no = self._emitter.emit(
                category=category,
                source="reconciler",
                path_on_device=db_path,
                detected_at=detected_at,
                artifact_uri=str(output_subdir) if output_subdir else None,
                extra=extra,
            )
            self.stats.signals_emitted += 1
            self._register_device_log_event(
                payload,
                seq_no=seq_no,
                detected_at=detected_at,
                event_type=event_type,
                event_subtype=event_subtype,
                aee_ts_utc=aee_ts_utc,
            )
            logger.debug(
                "aee_reconciler_emit serial=%s job=%d cat=%s pkg=%s subtype=%s",
                self._serial, self._job_id, category,
                extra.get("package_name", "-"),
                extra.get("event_subtype", "-"),
            )
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

    def _register_device_log_event(
        self,
        payload: Dict[str, Any],
        *,
        seq_no: int,
        detected_at: datetime,
        event_type: str,
        event_subtype: str,
        aee_ts_utc: Optional[datetime],
    ) -> None:
        """写入 DeviceLogEvent 并可选入队连续上送。"""
        if self._device_log_client is None:
            return
        output_subdir = payload.get("output_subdir")
        if not output_subdir:
            return
        local_path = Path(str(output_subdir))
        if not local_path.is_dir():
            return

        subtype = event_subtype
        meta_event_type = event_type
        if self._platform_collector is not None:
            try:
                meta = self._platform_collector.parse_metadata(local_path)
                meta_event_type = meta.event_type or meta_event_type
                subtype = meta.event_subtype or subtype
            except Exception:
                logger.debug(
                    "aee_reconciler_collector_metadata_fallback serial=%s",
                    self._serial,
                    exc_info=True,
                )

        event_id = self._device_log_client.create_local_event(
            serial=self._serial,
            platform=self._platform,
            event_type=meta_event_type,
            event_subtype=subtype or None,
            detected_at=detected_at,
            device_timestamp=aee_ts_utc,
            local_path=local_path,
            plan_run_id=self._plan_run_id,
            job_id=self._job_id,
            link_signal_seq_no=seq_no,
            size_bytes=self._device_log_client.dir_size_bytes(local_path),
        )
        if not event_id:
            return

        try:
            from ..event_uploader import EventUploader
        except ImportError:
            from agent.event_uploader import EventUploader

        if EventUploader.is_enabled():
            EventUploader.instance().enqueue_local_event(event={
                "id": event_id,
                "local_path": str(local_path),
                "serial": self._serial,
                "platform": self._platform,
                "event_type": meta_event_type,
                "detected_at": detected_at.isoformat(),
                "plan_run_id": self._plan_run_id,
                "job_id": self._job_id,
                "host_id": self._host_id,
            })
            logger.debug(
                "aee_reconciler_device_log_event_enqueued serial=%s event_id=%s",
                self._serial, event_id,
            )

    def _emit_rollback_signal(self) -> None:
        """连续 tick 错误超阈值时 emit rollback 信号 + 兜底提示。

        写一条 category='AEE' / event_type='RECONCILER_ROLLBACK' 的 log_signal,
        让 AnomalyDashboard / WatcherSummaryCard 能展示「Reconciler 已自关闭」
        状态(而不是默默消失)。emit 失败不阻塞 self-stop(只记 warning)。

        #78 子任务 2(参见 #72 现场:11M 行日志 0 emit 的盲区)。
        """
        try:
            self._emitter.emit(
                category="AEE",
                source="reconciler_rollback",
                path_on_device="",
                detected_at=datetime.now(timezone.utc),
                artifact_uri=None,
                extra={
                    "schema_version": 2,
                    "event_type": "RECONCILER_ROLLBACK",
                    "event_subtype": "TICK_ERROR_THRESHOLD",
                    "raw_event_type": "RECONCILER_ROLLBACK",
                    "package_name": "_reconciler_",
                    "aee_ts": datetime.now(timezone.utc).isoformat(),
                    "nfs_path": None,
                    "pull_source": "reconciler",
                    "entry_origin": "rollback",
                    "consecutive_errors": self._consecutive_tick_errors,
                    "threshold": self._max_consecutive_tick_errors,
                },
            )
        except Exception:
            logger.warning(
                "aee_reconciler_rollback_emit_failed serial=%s job=%d",
                self._serial, self._job_id,
            )


__all__ = [
    "AeeDbHistoryReconciler",
    "ReconcilerStats",
    "is_reconciler_enabled",
]
