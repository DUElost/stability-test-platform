"""JobSession — Job 执行生命周期的原子单元。

职责链（三阶段退出）：
    Enter:
        设备租约持有注册 (LeaseRenewer 活跃集合)
          → Watcher 启动 (LogWatcherManager.start)
        → （调用方执行 Pipeline）
    Exit:
        Phase 1  Watcher 同步收尾（短 timeout，策略: policy.exit_drain_timeout_seconds）
        Phase 2  设备锁 & 活跃集合释放（必定执行，不被 Phase 1 阻塞）
        Phase 3  （隐式）outbox 剩余条目由 Agent 进程级 OutboxDrainer 异步补发

设计原则：
  1. 任何 Job 执行路径都必须经过 JobSession —— 绕过 = bug
  2. Watcher 启动失败按 WatcherPolicy.on_unavailable 决策 Job 生死（首发默认 DEGRADED）
  3. Phase 2 锁释放必定执行：即使 Phase 1 抛异常也不能拖住锁释放
  4. 返回 summary 给调用方（用于 complete_job 回传 watcher_* 字段）

命名约定：
  - 对外统一使用 "job_id"（= job_instance.id）
  - Agent 主链 _active_job_ids 即 job_id 集合，避免 run_id 语义债继续扩散

典型用法：
    with JobSession(job_payload, host_id, log_dir, lock_*) as session:
        result = engine.execute(pipeline_def)
    summary = session.summary.to_complete_payload()   # 发 /complete 时附带
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .watcher import LogWatcherManager, WatcherPolicy, OnUnavailableAction, WatcherStartError
from .watcher.contracts import ContractViolation, validate_claim_payload

logger = logging.getLogger(__name__)


def _parse_optional_iso(value: Any) -> Optional[datetime]:
    """解析 claim payload 可选 started_at；缺失/非法返回 None 而非抛异常。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        logger.warning("job_payload_started_at_unparseable value=%r", value)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class JobStartupError(Exception):
    """JobSession 启动阶段硬失败。调用方应把 Job 标记 FAILED。"""

    def __init__(self, message: str, *, reason_code: str):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass
class JobSessionSummary:
    """JobSession 结束时回传给 complete_job 的元数据。"""

    job_id: int
    watcher_id: Optional[str] = None
    watcher_started_at: Optional[datetime] = None
    watcher_stopped_at: Optional[datetime] = None
    watcher_capability: str = "unavailable"
    log_signal_count: int = 0
    # #96：per-source 拆分（log_signal_count = watcher + reconciler），
    # 便于诊断哪条路径没干活。控制面不改 schema，只读 log_signal_count；
    # 这两个字段进 payload 仅为运维可观测，后端未消费也无害。
    watcher_signal_count: int = 0
    reconciler_signal_count: int = 0
    watcher_stats: Dict[str, int] = field(default_factory=dict)
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)
    # M0/PR #2: AeeDbHistoryReconciler 运行期统计(灰度开启时回填)
    reconciler_stats: Dict[str, int] = field(default_factory=dict)

    def to_complete_payload(self) -> Dict[str, Any]:
        """以字符串形态嵌入 complete_job POST body（可 JSON 序列化）。"""
        return {
            "watcher_id":         self.watcher_id,
            "watcher_started_at": _iso(self.watcher_started_at),
            "watcher_stopped_at": _iso(self.watcher_stopped_at),
            "watcher_capability": self.watcher_capability,
            "log_signal_count":   self.log_signal_count,
            "watcher_signal_count":  self.watcher_signal_count,
            "reconciler_signal_count": self.reconciler_signal_count,
            "watcher_stats":      self.watcher_stats,
            # M0/Task2: Agent 无独立 /metrics 暴露面,reconciler 进程内计数(尤其
            # ticks_skipped_unchanged)通过 complete 通道带出,由后端桥接到中心 /metrics。
            "reconciler_stats":   self.reconciler_stats,
        }


class JobSession:
    """Job 执行生命周期上下文。

    由 Agent main.py 在 claim 到 job 后立即包裹 pipeline 执行。
    """

    def __init__(
        self,
        *,
        job_payload: Dict[str, Any],
        host_id: str,
        log_dir: str,
        lock_register,        # callable(job_id, ...) —— 把 job_id 加入 LeaseRenewer 的活跃集合
        lock_deregister,      # callable(job_id) —— 对应移除
        device_id_register=None,
        device_id_deregister=None,
    ):
        # 必需字段校验（fail-fast，避免进到 watcher 启动才发现 payload 缺失）
        self._payload = _validate_payload(job_payload)
        self._job_id      = int(self._payload["id"])
        self._device_id   = int(self._payload["device_id"])
        self._serial      = self._payload["device_serial"]
        self._host_id     = host_id
        self._started_at  = _parse_optional_iso(self._payload.get("started_at"))
        self._log_dir     = log_dir
        self._lock_reg    = lock_register
        self._lock_dereg  = lock_deregister
        self._dev_reg     = device_id_register
        self._dev_dereg   = device_id_deregister

        self._policy: WatcherPolicy = WatcherPolicy.from_job(self._payload)
        self._manager = LogWatcherManager.instance()
        self._handle = None
        self._reconciler = None     # M0/PR #2: 灰度开启时由 __enter__ 启动
        self._locks_released = False
        self._summary = JobSessionSummary(
            job_id=self._job_id,
            policy_snapshot=self._policy.to_dict(),
        )

    # ------------------------------------------------------------------
    # 上下文协议
    # ------------------------------------------------------------------

    def __enter__(self) -> "JobSession":
        # 1. 注册设备锁续期（已有 main.py 机制）
        self._lock_reg(self._job_id)
        if self._dev_reg:
            self._dev_reg(self._device_id)

        # 2. 启动 Watcher（契约：默认关联，不可绕过）
        plan_run_id_raw = self._payload.get("plan_run_id")
        plan_run_id: Optional[int] = None
        if plan_run_id_raw is not None:
            try:
                plan_run_id = int(plan_run_id_raw)
            except (TypeError, ValueError):
                logger.warning(
                    "job_session_invalid_plan_run_id job=%s value=%r",
                    self._job_id, plan_run_id_raw,
                )
        try:
            self._handle = self._manager.start(
                host_id=self._host_id,
                serial=self._serial,
                job_id=self._job_id,
                log_dir=self._log_dir,
                policy=self._policy,
                fencing_token=str(self._payload["fencing_token"]),
                plan_run_id=plan_run_id,
            )
            self._summary.watcher_id         = self._handle.watcher_id
            self._summary.watcher_started_at = self._handle.started_at
            self._summary.watcher_capability = self._handle.capability
            logger.info(
                "job_session_entered job_id=%d serial=%s watcher_id=%s cap=%s policy.on_unavailable=%s",
                self._job_id, self._serial, self._handle.watcher_id,
                self._handle.capability, self._policy.on_unavailable.value,
            )
        except WatcherStartError as exc:
            self._handle_start_failure(exc)
        except Exception as exc:
            # 未知异常一律等同于 start failure，兜底释放锁
            self._release_locks()
            raise JobStartupError(
                f"watcher_start_unexpected_error: {exc}",
                reason_code="watcher_start_unexpected",
            ) from exc

        # 3. M0/PR #2: 灰度开启 AeeDbHistoryReconciler(失败仅记 WARN,不阻 Job)
        self._maybe_start_aee_reconciler()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """三阶段退出。

        Phase 1: 同步给 watcher 一个短 timeout 收尾（不阻塞锁释放）
        Phase 2: 必定执行的锁释放（即使 Phase 1 抛异常）
        Phase 3: 隐式 —— outbox 未发送条目由 Agent 进程级 OutboxDrainer 异步补发

        M0/PR #2: Phase 1 之前先停 reconciler — 避免后台线程在锁释放后继续 adb shell。
        """
        drain_timeout = self._policy.exit_drain_timeout_seconds

        # ---- Phase 1a: 停 AeeDbHistoryReconciler(若启动) ----
        if self._reconciler is not None:
            try:
                stats = self._reconciler.stop(timeout=min(drain_timeout, 3.0))
                self._summary.reconciler_stats = dict(stats.to_dict())
            except Exception:
                logger.exception(
                    "aee_reconciler_stop_failed_in_phase1 job_id=%d", self._job_id,
                )
            self._reconciler = None

        # ---- Phase 1b: watcher 同步收尾 ----
        w_sig = 0  # 先初始化：watcher 未启动 / stop 返回 None / stop 抛异常 三条路径都归零
        try:
            if self._handle is not None:
                stopped = self._manager.stop(
                    self._handle.watcher_id,
                    drain=True,
                    timeout=drain_timeout,
                )
                if stopped is not None:
                    self._summary.watcher_stopped_at = stopped.stopped_at
                    self._summary.watcher_stats = dict(stopped.stats)
                    # 防御性：仅当 stats 报告了非负整数才采纳（异常路径可能缺字段）
                    w_sig = int(stopped.stats.get("signals_emitted", 0) or 0)
                else:
                    w_sig = 0
        except Exception as stop_exc:
            # 关键：Phase 1 的任何异常绝不阻塞 Phase 2
            logger.exception("watcher_stop_failed_in_phase1 job_id=%d: %s", self._job_id, stop_exc)
            w_sig = 0

        # reconciler.signals_emitted 已在 Phase 1a 写入 summary.reconciler_stats；
        # 若 reconciler 未启动则该 dict 为空，取 0。
        r_sig = int(self._summary.reconciler_stats.get("signals_emitted", 0) or 0)
        # log_signal_count = watcher + reconciler —— #96：之前只取 watcher 计数,
        # 遗漏 reconciler 发射的信号,曾导致 job_session_exited signals=0 误判 reconciler 失效。
        # 控制面 /complete 把此值作 lower-bound 同步(只增不减),与 /log-signals 端点
        # 的实插累加不冲突。
        self._summary.log_signal_count = w_sig + r_sig
        self._summary.watcher_signal_count = w_sig
        self._summary.reconciler_signal_count = r_sig

        # ---- Phase 2: 锁释放（必定执行）----
        self._release_locks()

        logger.info(
            "job_session_exited job_id=%d capability=%s signals=%d "
            "(watcher=%d reconciler=%d) drain_timeout=%.1fs exc=%s",
            self._job_id, self._summary.watcher_capability,
            self._summary.log_signal_count, w_sig, r_sig, drain_timeout,
            exc_type.__name__ if exc_type else None,
        )
        # 不吞异常 —— 让 pipeline 错误正常抛给调用方
        return False

    # ------------------------------------------------------------------
    # 访问器
    # ------------------------------------------------------------------

    @property
    def summary(self) -> JobSessionSummary:
        return self._summary

    @property
    def policy(self) -> WatcherPolicy:
        return self._policy

    def is_aborted_checker(self, active_run_ids: set[int], lock) -> callable:
        """兼容现有 pipeline_engine.is_aborted 回调签名。

        返回一个闭包 —— LeaseRenewer 收到 409 移除 run_id 时返回 True。
        """
        job_id = self._job_id

        def _check() -> bool:
            with lock:
                return job_id not in active_run_ids

        return _check

    # ------------------------------------------------------------------
    # 私有
    # ------------------------------------------------------------------

    def _resolve_reconciler_class(self, platform: str):
        """ADR-0032 D6: route reconciler by device.platform."""
        try:
            from .device_platform import (
                PLATFORM_MTK,
                PLATFORM_QCOM,
                PLATFORM_UNISOC,
                PLATFORM_UNKNOWN,
            )
        except Exception:
            logger.exception("reconciler_platform_import_failed job_id=%d", self._job_id)
            return None

        key = (platform or "").strip().upper()
        if key == PLATFORM_UNISOC:
            from .aee.unisoc_reconciler import UnisocUniviewReconciler
            return UnisocUniviewReconciler
        if key in (PLATFORM_MTK, PLATFORM_UNKNOWN):
            from .aee.reconciler import AeeDbHistoryReconciler
            return AeeDbHistoryReconciler
        if key == PLATFORM_QCOM:
            return None
        return None

    def _maybe_start_aee_reconciler(self) -> None:
        """ADR-0032 D6/D8: platform reconciler (MTK AEE or UNISOC uniview)."""
        try:
            from .aee.reconciler import is_reconciler_enabled
        except Exception:
            logger.exception("platform_reconciler_import_failed job_id=%d", self._job_id)
            return

        if not is_reconciler_enabled(self._host_id):
            return
        if self._handle is None or self._handle.impl is None:
            return
        if self._handle.capability in ("skipped", "unavailable"):
            return

        try:
            from .aee.paths import get_aee_local_root, shanghai_mmdd
            from .aee.device_log_event_client import DeviceLogEventClient
            from .aee.collector import get_collector_for_platform
            from .device_platform import detect_device_platform

            local_root = get_aee_local_root()
            run_date_stamp = (
                shanghai_mmdd(self._started_at) if self._started_at is not None else None
            )
            adb_path = self._manager.get_dep("adb_path") or "adb"
            platform = detect_device_platform(adb_path, self._serial)
            reconciler_cls = self._resolve_reconciler_class(platform)
            if reconciler_cls is None:
                return
            device_log_client = DeviceLogEventClient.from_env(
                api_url=self._manager.get_dep("api_url") or "",
                agent_secret=self._manager.get_dep("agent_secret") or "",
                host_id=self._host_id,
            )
            plan_run_id = self._payload.get("plan_run_id")
            if plan_run_id is not None:
                try:
                    plan_run_id = int(plan_run_id)
                except (TypeError, ValueError):
                    plan_run_id = None

            self._reconciler = reconciler_cls(
                signal_emitter=self._handle.impl.emitter,
                state_store=self._manager.get_dep("local_db"),
                serial=self._serial,
                job_id=self._job_id,
                host_id=self._host_id,
                adb_path=adb_path,
                local_root=local_root,
                run_date_stamp=run_date_stamp,
                plan_run_id=plan_run_id,
                platform=platform,
                device_log_client=device_log_client,
                platform_collector=get_collector_for_platform(platform),
            )
            if not self._reconciler.start():
                self._reconciler = None
                raise RuntimeError("platform_reconciler_preflight_failed")
            logger.info(
                "platform_reconciler_active job_id=%d serial=%s platform=%s",
                self._job_id, self._serial, platform,
            )
        except Exception:
            logger.exception("platform_reconciler_start_failed job_id=%d", self._job_id)
            self._reconciler = None
            try:
                if self._handle is not None and self._handle.impl is not None:
                    self._handle.impl.set_aee_reconciler_active(False)
            except Exception:
                pass

    def _handle_start_failure(self, exc: WatcherStartError) -> None:
        """按 policy.on_unavailable 决策 Job 走向。"""
        action = self._policy.on_unavailable

        if action == OnUnavailableAction.SKIP:
            logger.warning(
                "watcher_start_failed_skip job_id=%d code=%s reason=%s — policy=SKIP, 继续执行",
                self._job_id, exc.code, exc,
            )
            self._summary.watcher_capability = "skipped"
            return

        if action == OnUnavailableAction.DEGRADED:
            logger.warning(
                "watcher_start_failed_degraded job_id=%d code=%s reason=%s — policy=DEGRADED",
                self._job_id, exc.code, exc,
            )
            self._summary.watcher_capability = "unavailable"
            return

        # FAIL: 立即释放锁，抛 JobStartupError
        logger.error(
            "watcher_start_failed_hard job_id=%d code=%s reason=%s — policy=FAIL",
            self._job_id, exc.code, exc,
        )
        self._release_locks()
        raise JobStartupError(
            f"watcher_start_failed: {exc.code}: {exc}",
            reason_code=f"watcher_{exc.code}",
        ) from exc

    def _release_locks(self) -> None:
        """幂等释放：多次调用安全（Phase 2 防御）。"""
        if self._locks_released:
            return
        self._locks_released = True
        try:
            self._lock_dereg(self._job_id)
        except Exception:
            logger.exception("lock_deregister_failed job_id=%d", self._job_id)
        if self._dev_dereg:
            try:
                self._dev_dereg(self._device_id)
            except Exception:
                logger.exception("device_deregister_failed device_id=%d", self._device_id)


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------

def _validate_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """JobSession 专用的 payload 校验。

    复用 contracts.validate_claim_payload；违反契约时升级为 JobStartupError
    （让调用方能用单一 except 捕获）。
    """
    try:
        return validate_claim_payload(payload)
    except ContractViolation as e:
        raise JobStartupError(
            f"job_payload_contract_violation: {e}",
            reason_code="payload_contract_violation",
        ) from e


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
