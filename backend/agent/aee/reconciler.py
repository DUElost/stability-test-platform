"""AeeDbHistoryReconciler — Per-Job daemon for AEE db_history diff (M0 / PR #2).

职责（KISS）：
    - per-Job daemon thread:周期性 diff `/data/aee_exp/db_history` +
      `/data/vendor/aee_exp/db_history`,通过 `process_device_logs` 复用
      patrol 同款 diff / pull / verify 逻辑(processor.py)
    - 每个新条目通过共享的 `SignalEmitter` 单次 emit 一条 log_signal,
      `category` 按 aee_type 映射,`source="reconciler"`,`extra` 携带
      `event_type / package_name / aee_ts / nfs_path / pull_source`
    - 双节奏:基线 180s;若上一轮有新条目则切到突发 60s × N 轮再回落
    - D2:每轮先 `cat db_history` 算 sha256,内容未变且无 pending 重试时跳过本轮
      `process_device_logs`(计入 reconciler_skip_unchanged_total);
      内容变化视为有新行候选 → 触发 burst;#1044:仍有 pending_pull 时即使
      hash 未变也必须跑 process（失败补采与「发现新历史」解耦）
    - #1719 emit 补偿通道:processor 在 processed 落盘前落 emit 意图占位；
      本类在效果前补幂等 keys（seq_no / 事件 UUID）并每轮 sweep 重放未完成
      占位（见 emit_intent.py），保证崩溃后"不重复、不丢失"。
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
from uuid import uuid4

from ..watcher.contracts import ContractViolation
from .db_history import load_processed_lines, save_processed_lines, state_key
from .emit_intent import (
    MAX_REPLAY_ATTEMPTS,
    load_intents,
    new_intent_record,
    save_intents,
)
from .paths import PathOutsideRootError, get_aee_local_root, resolve_path_under_aee_local
from .metadata import resolve_device_log_event_type
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
    # #2394-①「落成未采到」可发现化（UNISOC reconciler 回填；MTK 路恒 0）：
    dirs_abandoned: int = 0            # 达 #2272 上限被放弃的目录数（按目录名去重）
    dirs_oversized_skipped: int = 0    # #2252 降级态（超限仅取元数据）目录数（本拍快照）
    unresolved_dirs: int = 0           # 最近一拍「已列到但未落 processed 且未放弃」集合大小

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
            "dirs_abandoned":          self.dirs_abandoned,
            "dirs_oversized_skipped":  self.dirs_oversized_skipped,
            "unresolved_dirs":         self.unresolved_dirs,
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_AEE_TYPE_TO_CATEGORY = {
    "aee_exp":        "AEE",
    "vendor_aee_exp": "VENDOR_AEE",
}


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    """解析意图簿里的 ISO 时间戳；坏值返回 None（不猜）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
        on_self_shutdown: Optional[Callable[[], None]] = None,
    ) -> None:
        self._emitter = signal_emitter
        # #806：连续错误自关闭后通知外部（JobSession → watcher 复位 emit 抑制位）。
        self._on_self_shutdown = on_self_shutdown
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
        # #1044: 上轮 runtime process 仍有 pending_pull → hash 未变也不得跳过
        self._runtime_has_pending = False
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
                    # #806：自关闭必须让 watcher 复位抑制位，否则该 Job 余下生命
                    # 周期 AEE/VENDOR_AEE 信号与 DLE 注册静默全黑（inotifyd 兜底
                    # 只有在 active=False 时才真正接管）。
                    self._notify_self_shutdown()
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

        D2:先比对 db_history 内容 hash;全部可读且未变、且无 runtime pending
        时跳过 process_device_logs(计 ticks_skipped_unchanged +
        reconciler_skip_unchanged_total),返回 0 且不视为"新行候选"
        (不触发/重置 burst)。hash 变化、不可读、或仍有 pending_pull(#1044)
        则照常 process;并把"hash 变化"也算作新行候选 → 即便本轮 pulled=0
        (已被 patrol 抢先 pull)仍触发 burst。
        """
        self.stats.ticks_total += 1

        # #1719：先 sweep emit 意图簿（补偿重启前「已 processed 但 emit 未发生」
        # 的条目），再做本轮 diff/pull/emit。
        try:
            self._sweep_emit_intents()
        except Exception:
            self.stats.tick_errors += 1
            logger.exception(
                "aee_emit_intent_sweep_failed serial=%s job=%d",
                self._serial, self._job_id,
            )

        baseline_new = 0
        if not self._baseline_snapshot_done:
            baseline_new, baseline_has_more = self._run_baseline_snapshot()
            self._baseline_snapshot_done = not baseline_has_more

        changed = self._db_history_changed()
        if changed is False and not self._runtime_has_pending:
            self.stats.ticks_skipped_unchanged += 1
            self._last_had_new_candidate = baseline_new > 0
            record_reconciler_skip_unchanged(self._host_id)
            logger.debug(
                "aee_reconciler_skip_unchanged serial=%s job=%d", self._serial, self._job_id,
            )
            return baseline_new

        def _on_runtime_intent(payload: Dict[str, Any]) -> None:
            scoped_payload = dict(payload)
            scoped_payload["entry_origin"] = "runtime"
            self._record_intent_placeholder(scoped_payload)

        def _on_runtime_entry(payload: Dict[str, Any]) -> None:
            scoped_payload = dict(payload)
            scoped_payload["entry_origin"] = "runtime"
            self._handle_new_entry(scoped_payload)

        def _on_runtime_pull_failed(payload: Dict[str, Any]) -> None:
            scoped_payload = dict(payload)
            scoped_payload["entry_origin"] = "runtime"
            self._handle_pull_failed(scoped_payload)

        result = process_device_logs(
            serial=self._serial,
            job_id=self._job_id,
            state_store=self._state_store,
            adb_path=self._adb_path,
            config=self._cfg,
            local_root=self._local_root,
            run_date_stamp=self._run_date_stamp,
            on_new_entry=_on_runtime_entry,
            on_pull_failed=_on_runtime_pull_failed,
            on_entry_intent=_on_runtime_intent,
            shell_fn=self._shell_fn,
            pull_fn=self._pull_fn,
            stop_event=self._stop_evt,
        )
        self._runtime_has_pending = int(result.pending_remaining) > 0
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
        # #1044: pending 重试本身不视为"新行候选"(不重置 burst)。
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
        baseline_prefix = self._baseline_prefix()
        # #802: runtime pass 可能已处理 baseline 尚未重放的行（写入共享
        # processed）——先从 baseline pending 摘除这些行，否则后续 baseline
        # 分片轮会对同一行再次 emit（新 seq_no 绕过控制面 (job,seq) 幂等，
        # watcher-summary / 异常率双倍计）。只摘 pending 重放路径，不改
        # baseline 首次发现的可见性判定（下方注释约束不变）。
        self._drop_runtime_processed_from_baseline_pending(baseline_prefix, self._serial)
        baseline_cfg = replace(
            self._cfg,
            state_key_prefix=baseline_prefix,
            max_entries_per_run=self._baseline_chunk_size,
        )
        baseline_lines_by_type: Dict[str, Set[str]] = {
            "aee_exp": set(),
            "vendor_aee_exp": set(),
        }

        def _on_baseline_intent(payload: Dict[str, Any]) -> None:
            scoped_payload = dict(payload)
            scoped_payload["detected_at_override"] = datetime.now(timezone.utc)
            scoped_payload["entry_origin"] = "baseline"
            self._record_intent_placeholder(scoped_payload)

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
            on_entry_intent=_on_baseline_intent,
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

    def _drop_runtime_processed_from_baseline_pending(
        self,
        baseline_prefix: str,
        serial: str,
    ) -> int:
        """#802: 从 baseline pending 摘除共享 processed 已含的行。

        runtime pass 无分片上限：首轮会把 baseline 分片未覆盖的积压行一并
        处理并写入共享 ``watcher:aee`` processed；这些行仍留在 baseline 的
        pending 里，后续 baseline 分片轮重放时再次 emit（新 seq_no）。
        返回摘除行数（观测用）。
        """
        from .processor import _load_pending_tasks, _save_pending_tasks

        dropped = 0
        for aee_type in ("aee_exp", "vendor_aee_exp"):
            shared_key = state_key(serial, aee_type, prefix=self._state_prefix)
            shared = load_processed_lines(self._state_store, shared_key)
            if not shared:
                continue
            pending_key = (
                f"{baseline_prefix}:{serial}:{aee_type}:pending_pull"
            )
            pending = _load_pending_tasks(self._state_store, pending_key)
            if not pending:
                continue
            remaining = {k: v for k, v in pending.items() if k not in shared}
            if len(remaining) != len(pending):
                _save_pending_tasks(self._state_store, pending_key, remaining)
                dropped += len(pending) - len(remaining)
        if dropped:
            logger.info(
                "aee_baseline_pending_dedup serial=%s job=%d dropped=%d",
                serial, self._job_id, dropped,
            )
        return dropped

    def _runtime_aee_types(self) -> Set[str]:
        result: Set[str] = set()
        for remote_aee_path in self._cfg.aee_paths:
            result.add("vendor_aee_exp" if "vendor" in remote_aee_path else "aee_exp")
        return result or {"aee_exp", "vendor_aee_exp"}

    # ------------------------------------------------------------------
    # 新条目回调 → emit log_signal
    # ------------------------------------------------------------------

    def _handle_pull_failed(self, payload: Dict[str, Any]) -> None:
        """#1044: pull/verify 失败时仍落可观测 signal + PULL_FAILED DLE。

        非 exhausted 不标记 processed（pending 继续重试）；exhausted 由 processor 记入 processed（#829）。
        """
        try:
            aee_type = str(payload.get("aee_type") or "")
            category = _AEE_TYPE_TO_CATEGORY.get(aee_type)
            if not category:
                logger.warning(
                    "aee_reconciler_pull_failed_unknown_aee_type serial=%s job=%d aee_type=%r",
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
            error: str = str(payload.get("error") or "pull_failed")
            exhausted = bool(payload.get("exhausted"))
            detected_at = datetime.now(timezone.utc)
            aee_ts_utc = to_utc(parse_timestamp(aee_ts))

            extra: Dict[str, Any] = {
                "schema_version": 2,
                "event_type": event_type,
                "event_subtype": event_subtype,
                "raw_event_type": raw_event_type,
                "package_name": pkg_name,
                "aee_ts": aee_ts,
                "aee_ts_utc": aee_ts_utc.isoformat() if aee_ts_utc else None,
                "nfs_path": None,
                "pull_source": "reconciler",
                "entry_origin": entry_origin,
                "pull_failed": True,
                "pull_error": error,
                "pull_retry_exhausted": exhausted,
            }

            seq_no = self._emitter.emit(
                category=category,
                source="reconciler",
                path_on_device=db_path,
                detected_at=detected_at,
                artifact_uri=None,
                extra=extra,
            )
            self.stats.signals_emitted += 1
            dle_payload = self._build_pull_failed_payload(
                detected_at=detected_at,
                event_type=resolve_device_log_event_type(event_type, event_subtype),
                event_subtype=event_subtype,
                aee_ts_utc=aee_ts_utc,
                seq_no=seq_no,
                event_id=str(uuid4()),
            )
            if dle_payload and self._device_log_client is not None:
                self._device_log_client.post_event_payload(dle_payload)
            logger.info(
                "aee_reconciler_pull_failed serial=%s job=%d cat=%s pkg=%s err=%s exhausted=%s",
                self._serial, self._job_id, category, pkg_name, error, exhausted,
            )
        except ContractViolation as exc:
            self.stats.signals_dropped += 1
            logger.warning(
                "aee_reconciler_pull_failed_contract_violation serial=%s job=%d err=%s",
                self._serial, self._job_id, exc,
            )
        except Exception:
            self.stats.signals_dropped += 1
            logger.exception(
                "aee_reconciler_pull_failed_emit_failed serial=%s job=%d payload=%s",
                self._serial, self._job_id, payload,
            )

    def _baseline_prefix(self) -> str:
        """baseline 分片的 processed 键命名空间（job 级隔离）。"""
        return f"watcher_baseline:{self._job_id}"

    def _lookup_cross_prefix_intent(
        self, aee_type: str, line: str, *, exclude_prefix: str,
    ) -> "dict | None":
        """#1862：跨前缀 done 墓碑回查——返回另一前缀簿中同 line 的 done 记录副本。

        baseline→runtime 迁移丢失窗口（baseline 落 processed→emit done→崩溃于
        merge 前）后，runtime 重拉同 line 会在本前缀簿新建记录并分配**新
        seq_no** 重复 emit；反方向（runtime 已 emit done、baseline 分片重扫）
        同理。复用墓碑使本前缀幂等返回——emit 至多一次、幂等键稳定。
        未 done 的跨前缀占位不在此复用（keys 尚未分配，复用无法稳定幂等键，
        留 Revisit）。
        """
        other = (
            self._baseline_prefix()
            if exclude_prefix == self._state_prefix
            else self._state_prefix
        )
        other_intents = load_intents(
            self._state_store, state_key(self._serial, aee_type, prefix=other),
        )
        record = other_intents.get(line)
        if isinstance(record, dict) and record.get("done"):
            return dict(record)
        return None

    def _state_prefix_for(self, payload: Dict[str, Any]) -> str:
        """解析 payload 所属 processed 键命名空间（processor 透传优先）。"""
        return str(payload.get("state_key_prefix") or self._state_prefix)

    def _record_intent_placeholder(self, payload: Dict[str, Any]) -> None:
        """processor.on_entry_intent 钩子：processed 落盘前先落 emit 意图占位。

        #1719：占位语义 = 「该条目已在本地 finalize，emit 必须发生」。已有
        记录（重拉/重放路径）不覆盖——保留既有 keys / 首次观测时间戳。
        #2044：写失败不吞。占位没落盘时 processor 必须知道——它照常推进 processed
        后，该行既无意图记录可供 sweep 重放、也不会再被重拉，条目将静默永久丢失。
        失败原样上抛，由 processor 判「不 finalize」并保留 pending 下一拍重试
        （异常日志落在 processor 侧，避免双份 traceback）。
        """
        aee_type = str(payload.get("aee_type") or "")
        line = str(payload.get("line") or "")
        if not aee_type or not line:
            return
        processed_key = state_key(
            self._serial, aee_type, prefix=self._state_prefix_for(payload),
        )
        intents = load_intents(self._state_store, processed_key)
        if line in intents:
            return
        override = payload.get("detected_at_override")
        override_iso = (
            override.isoformat() if isinstance(override, datetime) else ""
        )
        record = new_intent_record(
            payload=payload,
            job_id=self._job_id,
            detected_at_iso=override_iso or datetime.now(timezone.utc).isoformat(),
            entry_origin=str(payload.get("entry_origin") or "runtime"),
            detected_at_override_iso=override_iso,
        )
        intents[line] = record
        save_intents(self._state_store, processed_key, intents)

    def _intent_payload(
        self, aee_type: str, line: str, record: Dict[str, Any], prefix: str,
    ) -> Dict[str, Any]:
        """由意图记录重建 on_new_entry 形状 payload（sweep 重放用）。"""
        payload: Dict[str, Any] = {
            "line":             line,
            "parsed":           dict(record.get("parsed") or {}),
            "aee_type":         aee_type,
            "output_subdir":    str(record.get("output_subdir") or ""),
            "entry_origin":     str(record.get("entry_origin") or "runtime"),
            "state_key_prefix": prefix,
        }
        override = _parse_iso_dt(record.get("detected_at_override"))
        if override is not None:
            payload["detected_at_override"] = override
        return payload

    def _ensure_emit_for_intent(
        self,
        payload: Dict[str, Any],
        record: Dict[str, Any],
        intents: Dict[str, dict],
        processed_key: str,
    ) -> None:
        """确保该条目的 emit+DLE 已发起（幂等）：先持久化 keys，再效果，再标 done。

        keys 已存在（崩溃重放）→ 复用 seq_no/envelope/DLE payload，不重新
        分配、不重建 payload——重放与首发在控制面收敛为同一行/同一 id。
        """
        aee_type = str(payload.get("aee_type") or record.get("aee_type") or "")
        category = _AEE_TYPE_TO_CATEGORY.get(aee_type)
        if not category:
            raise ContractViolation(f"unknown aee_type {aee_type!r}")

        parsed: Dict[str, Any] = dict(payload.get("parsed") or record.get("parsed") or {})
        db_path: str = str(parsed.get("db_path") or "")
        aee_ts: str = str(parsed.get("timestamp") or "")
        pkg_name: str = str(parsed.get("pkg_name") or "") or "unknown"
        event_type: str = str(parsed.get("event_type") or "") or "UNKNOWN"
        raw_event_type: str = str(parsed.get("raw_event_type") or "")
        event_subtype: str = str(parsed.get("event_subtype") or "") or "其他"
        entry_origin: str = (
            str(payload.get("entry_origin") or record.get("entry_origin") or "runtime")
        )
        output_subdir = payload.get("output_subdir") or record.get("output_subdir") or ""

        # #88:detected_at 是「控制面观测到该信号的时刻」,必须来自服务端时钟
        # ——设备时钟不可信。首次观测（占位/回调）时固化进意图记录，重放复用，
        # 避免同一条目跨重启得到两个不同的 detected_at。
        detected_at = _parse_iso_dt(record.get("detected_at"))
        if detected_at is None:
            override = payload.get("detected_at_override")
            detected_at = (
                override if isinstance(override, datetime) else datetime.now(timezone.utc)
            )
        if detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=timezone.utc)
        record["detected_at"] = detected_at.isoformat()

        # 设备自报时间换算成真实 UTC 后另存,便于排查设备时钟漂移。
        aee_ts_utc = to_utc(parse_timestamp(aee_ts))

        seq_no = record.get("seq_no")
        if seq_no is None:
            extra: Dict[str, Any] = {
                # §2.2 schema_version 2:演进兼容标记。mobilelog_pulled /
                # bugreport_exported 不在此填 — emit 早于 mobilelog/bugreport
                # 副作用，此刻两者尚未发生，故按 §2.2「可选」留空。
                "schema_version": 2,
                "event_type": event_type,
                "event_subtype": event_subtype,
                "raw_event_type": raw_event_type,
                "package_name": pkg_name,
                "aee_ts": aee_ts,
                # #88:设备自报时间换算出的**真实** UTC;设备没给时区时为 None。
                "aee_ts_utc": aee_ts_utc.isoformat() if aee_ts_utc else None,
                "nfs_path": str(output_subdir) if output_subdir else None,
                "pull_source": "reconciler",
                "entry_origin": entry_origin,
            }
            seq_no, envelope = self._emitter.prepare(
                category=category,
                source="reconciler",
                path_on_device=db_path,
                detected_at=detected_at,
                artifact_uri=str(output_subdir) if output_subdir else None,
                extra=extra,
            )
            dle_payload = self._build_device_log_event_payload(
                payload,
                seq_no=seq_no,
                detected_at=detected_at,
                event_type=event_type,
                event_subtype=event_subtype,
                aee_ts_utc=aee_ts_utc,
                event_id=str(uuid4()),
            )
            record["seq_no"] = int(seq_no)
            record["signal_envelope"] = envelope
            record["dle_payload"] = dle_payload
            record["job_id"] = self._job_id
            intents[str(payload.get("line") or "")] = record
            # 先持久化 keys，再产生效果——崩溃落在两者之间时重放复用同一幂等键
            save_intents(self._state_store, processed_key, intents)
            logger.debug(
                "aee_emit_intent_keys_persisted serial=%s job=%d seq=%d",
                self._serial, self._job_id, seq_no,
            )
        else:
            seq_no = int(seq_no)
            envelope = record.get("signal_envelope") or {}
            dle_payload = record.get("dle_payload")

        # 效果（幂等）：outbox `INSERT OR IGNORE`；DLE 按预分配 id upsert
        if envelope:
            self._emitter.enqueue(seq_no, envelope)
        if dle_payload and self._device_log_client is not None:
            event_id = self._device_log_client.post_event_payload(dle_payload)
            if not event_id:
                logger.info(
                    "aee_reconciler_device_log_event_fallback_signal_only "
                    "serial=%s job=%d seq=%d",
                    self._serial, self._job_id, seq_no,
                )

        record["done"] = True
        intents[str(payload.get("line") or "")] = record
        save_intents(self._state_store, processed_key, intents)
        self.stats.signals_emitted += 1
        logger.debug(
            "aee_reconciler_emit serial=%s job=%d cat=%s pkg=%s subtype=%s",
            self._serial, self._job_id, category, pkg_name, event_subtype,
        )

    def _sweep_emit_intents(self) -> int:
        """#1719：完成/清理 emit 意图簿（每轮 tick 开头调用）。

        - ``!done`` → 重放（keys 缺失则新分配；同幂等键），成功标 done；
          失败计 attempts，达上限丢弃并计 signals_dropped（防无限重放）。
        - ``done`` 且 line 已 processed → 清理；runtime 簿按自身 processed，
          baseline 簿按 **runtime** processed（#2034：墓碑是 runtime 重拉时唯一
          的幂等键来源，不能被本前缀的 finalize 提前抹掉）；
        - line 未 processed → 保留（重拉路径会复用 keys，避免重复 emit）。
        返回本轮重放条数。
        """
        replayed = 0
        for prefix in (self._state_prefix, self._baseline_prefix()):
            for aee_type in ("aee_exp", "vendor_aee_exp"):
                processed_key = state_key(self._serial, aee_type, prefix=prefix)
                intents = load_intents(self._state_store, processed_key)
                if not intents:
                    continue
                processed = load_processed_lines(self._state_store, processed_key)
                # #2034 成因 B：tick_once 先 sweep 再拉取，而 baseline 簿的 done
                # 墓碑正是 runtime 重拉时唯一的幂等键来源。按「本前缀 processed」
                # 清理会在崩溃后的第 1 个 tick 就删掉它（baseline 早已 finalize），
                # 早于 runtime pass 的重拉——#1862 的窗口原样留着。baseline 簿因此
                # 按 merge 的落点（runtime 前缀 processed）判定，墓碑活过丢失窗口。
                finalized = processed
                if prefix != self._state_prefix:
                    finalized = load_processed_lines(
                        self._state_store,
                        state_key(self._serial, aee_type, prefix=self._state_prefix),
                    )
                changed = False
                for line, record in list(intents.items()):
                    if record.get("done"):
                        if line in finalized:
                            del intents[line]
                            changed = True
                        continue
                    payload = self._intent_payload(aee_type, line, record, prefix)
                    try:
                        self._ensure_emit_for_intent(
                            payload, record, intents, processed_key,
                        )
                        replayed += 1
                    except Exception:
                        record["attempts"] = int(record.get("attempts", 0)) + 1
                        intents[line] = record
                        changed = True
                        if record["attempts"] >= MAX_REPLAY_ATTEMPTS:
                            del intents[line]
                            self.stats.signals_dropped += 1
                            logger.error(
                                "aee_emit_intent_replay_dropped serial=%s job=%d "
                                "attempts=%d line=%.120s",
                                self._serial, self._job_id,
                                record["attempts"], line,
                            )
                        else:
                            logger.warning(
                                "aee_emit_intent_replay_failed serial=%s job=%d "
                                "attempts=%d line=%.120s",
                                self._serial, self._job_id,
                                record["attempts"], line,
                                exc_info=True,
                            )
                        continue
                    if line in processed:
                        del intents[line]
                        changed = True
                if changed:
                    save_intents(self._state_store, processed_key, intents)
        if replayed:
            logger.info(
                "aee_emit_intent_replayed serial=%s job=%d n=%d",
                self._serial, self._job_id, replayed,
            )
        return replayed

    def _handle_new_entry(self, payload: Dict[str, Any]) -> None:
        """processor.on_new_entry 回调:把新落盘的 AEE 条目 emit 成 log_signal。

        payload shape 见 processor.process_device_logs docstring。

        #1719：效果经 emit 意图簿执行——命中占位（processor 先落）则补 keys
        后重放；无占位（历史数据/直接调用）则就地补记录。done 记录直接返回
        （幂等重入）。
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

            line = str(payload.get("line") or "")
            processed_prefix = self._state_prefix_for(payload)
            processed_key = state_key(self._serial, aee_type, prefix=processed_prefix)
            intents = load_intents(self._state_store, processed_key)
            record = intents.get(line)
            # #1862：回查另一前缀簿的 done 墓碑（baseline 已 emit、merge 前崩溃
            # → runtime 重拉同一行），复用原 keys/seq_no 而非新分配。
            # #2034 成因 A：判据不能是「本前缀簿无记录」——processor 在
            # on_new_entry 之前总是先落占位，且占位与 emit 用同一个
            # state_key_prefix，本前缀簿因此必然已有一条未 done 的占位，回查在
            # 生产路径上永不触发。真正的判据是「本前缀没有可用幂等键」。
            if record is None or not (record.get("done") or record.get("seq_no") is not None):
                tombstone = self._lookup_cross_prefix_intent(
                    aee_type, line, exclude_prefix=processed_prefix,
                )
                if tombstone is not None:
                    record = tombstone
                    intents[line] = record
                    save_intents(self._state_store, processed_key, intents)
            if record is None:
                override = payload.get("detected_at_override")
                override_iso = (
                    override.isoformat() if isinstance(override, datetime) else ""
                )
                record = new_intent_record(
                    payload=payload,
                    job_id=self._job_id,
                    detected_at_iso=override_iso or datetime.now(timezone.utc).isoformat(),
                    entry_origin=str(payload.get("entry_origin") or "runtime"),
                    detected_at_override_iso=override_iso,
                )
                intents[line] = record
            elif record.get("done"):
                # 已完成（重拉幂等重入/竞态）：不再产生效果
                return

            self._ensure_emit_for_intent(payload, record, intents, processed_key)
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

    def _build_device_log_event_payload(
        self,
        payload: Dict[str, Any],
        *,
        seq_no: int,
        detected_at: datetime,
        event_type: str,
        event_subtype: str,
        aee_ts_utc: Optional[datetime],
        event_id: str,
    ) -> Optional[Dict[str, Any]]:
        """组装 DeviceLogEvent payload（LOCAL / PULL_FAILED），**不发送**。

        #1719：拆出组装步骤，让调用方先把 payload 连同幂等 keys 写进意图簿、
        再产生效果；崩溃重放复用同一 payload（``id`` = 预分配 UUID）。
        #287：过滤模型下 LOCAL 不直接入队——upload_task 按 scan xls 引用标记
        UPLOAD_PENDING 后由 EventUploader 轮询上送。
        """
        if self._device_log_client is None:
            return None
        event_type = resolve_device_log_event_type(event_type, event_subtype)
        output_subdir = payload.get("output_subdir")
        if not output_subdir:
            return self._build_pull_failed_payload(
                detected_at=detected_at,
                event_type=event_type,
                event_subtype=event_subtype,
                aee_ts_utc=aee_ts_utc,
                seq_no=seq_no,
                event_id=event_id,
            )
        try:
            if self._local_root is not None:
                local_path = resolve_path_under_aee_local(
                    str(output_subdir), root=self._local_root,
                )
            else:
                local_path = resolve_path_under_aee_local(str(output_subdir))
        except PathOutsideRootError:
            logger.warning(
                "aee_reconciler_local_path_outside_root serial=%s job=%d path=%s",
                self._serial, self._job_id, output_subdir,
            )
            return self._build_pull_failed_payload(
                detected_at=detected_at,
                event_type=event_type,
                event_subtype=event_subtype,
                aee_ts_utc=aee_ts_utc,
                seq_no=seq_no,
                event_id=event_id,
            )
        if not local_path.is_dir():
            return self._build_pull_failed_payload(
                detected_at=detected_at,
                event_type=event_type,
                event_subtype=event_subtype,
                aee_ts_utc=aee_ts_utc,
                seq_no=seq_no,
                event_id=event_id,
            )

        subtype = event_subtype
        parsed_type = None
        if self._platform_collector is not None:
            try:
                meta = self._platform_collector.parse_metadata(local_path)
                parsed_type = meta.event_type
                subtype = meta.event_subtype or subtype
            except Exception:
                logger.debug(
                    "aee_reconciler_collector_metadata_fallback serial=%s",
                    self._serial,
                    exc_info=True,
                )
        meta_event_type = resolve_device_log_event_type(
            parsed_type,
            subtype,
            event_type,
            paths=(str(local_path),),
        )
        return self._device_log_client.build_local_event_payload(
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
            event_id=event_id,
        )

    def _build_pull_failed_payload(
        self,
        *,
        seq_no: int,
        detected_at: datetime,
        event_type: str,
        event_subtype: str,
        aee_ts_utc: Optional[datetime],
        event_id: str,
        local_path: str = "",
    ) -> Optional[Dict[str, Any]]:
        if self._device_log_client is None:
            return None
        return self._device_log_client.build_pull_failed_payload(
            serial=self._serial,
            platform=self._platform,
            event_type=event_type,
            event_subtype=event_subtype or None,
            detected_at=detected_at,
            device_timestamp=aee_ts_utc,
            plan_run_id=self._plan_run_id,
            job_id=self._job_id,
            link_signal_seq_no=seq_no,
            local_path=local_path,
            event_id=event_id,
        )

    def _notify_self_shutdown(self) -> None:
        """#806：自关闭后通知外部（JobSession → watcher 复位 emit 抑制位）。

        回调失败只记日志：自关闭本身必须完成（防 #72 现场的死循环）。
        """
        callback = self._on_self_shutdown
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.exception(
                "aee_reconciler_self_shutdown_notify_failed serial=%s job=%d",
                self._serial, self._job_id,
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
