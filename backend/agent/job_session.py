"""JobSession — Job 执行生命周期的原子单元。

职责链（三阶段退出）：
    Enter:
        设备锁持有注册 (LockRenewal 活跃集合)
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
  - Agent 主链 _active_job_ids（原 _active_run_ids，治理已重命名）即 job_id 集合，
    新代码不再引入 run_id 字眼，避免语义债继续扩散

典型用法：
    with JobSession(job_payload, host_id, log_dir, lock_*) as session:
        result = engine.execute(pipeline_def)
    summary = session.summary.to_complete_payload()   # 发 /complete 时附带
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .watcher import LogWatcherManager, WatcherPolicy, OnUnavailableAction, WatcherStartError
from .watcher.contracts import ContractViolation, validate_claim_payload

logger = logging.getLogger(__name__)


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
    watcher_stats: Dict[str, int] = field(default_factory=dict)
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_complete_payload(self) -> Dict[str, Any]:
        """以字符串形态嵌入 complete_job POST body（可 JSON 序列化）。"""
        return {
            "watcher_id":         self.watcher_id,
            "watcher_started_at": _iso(self.watcher_started_at),
            "watcher_stopped_at": _iso(self.watcher_stopped_at),
            "watcher_capability": self.watcher_capability,
            "log_signal_count":   self.log_signal_count,
            "watcher_stats":      self.watcher_stats,
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
        lock_register,        # callable(job_id) —— 把 job_id 加入 LockRenewal 的活跃集合
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
        self._log_dir     = log_dir
        self._lock_reg    = lock_register
        self._lock_dereg  = lock_deregister
        self._dev_reg     = device_id_register
        self._dev_dereg   = device_id_deregister

        self._policy: WatcherPolicy = WatcherPolicy.from_job(self._payload)
        self._manager = LogWatcherManager.instance()
        self._handle = None
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
        try:
            self._handle = self._manager.start(
                host_id=self._host_id,
                serial=self._serial,
                job_id=self._job_id,
                log_dir=self._log_dir,
                policy=self._policy,
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

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """三阶段退出。

        Phase 1: 同步给 watcher 一个短 timeout 收尾（不阻塞锁释放）
        Phase 2: 必定执行的锁释放（即使 Phase 1 抛异常）
        Phase 3: 隐式 —— outbox 未发送条目由 Agent 进程级 OutboxDrainer 异步补发
        """
        drain_timeout = self._policy.exit_drain_timeout_seconds

        # ---- Phase 1: watcher 同步收尾 ----
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
                    self._summary.log_signal_count = int(stopped.stats.get("signals_emitted", 0))
        except Exception as stop_exc:
            # 关键：Phase 1 的任何异常绝不阻塞 Phase 2
            logger.exception("watcher_stop_failed_in_phase1 job_id=%d: %s", self._job_id, stop_exc)

        # ---- Phase 2: 锁释放（必定执行）----
        self._release_locks()

        logger.info(
            "job_session_exited job_id=%d capability=%s signals=%d drain_timeout=%.1fs exc=%s",
            self._job_id, self._summary.watcher_capability,
            self._summary.log_signal_count, drain_timeout,
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

        返回一个闭包 —— LockRenewal 收到 409 移除 run_id 时返回 True。
        """
        job_id = self._job_id

        def _check() -> bool:
            with lock:
                return job_id not in active_run_ids

        return _check

    # ------------------------------------------------------------------
    # 私有
    # ------------------------------------------------------------------

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
