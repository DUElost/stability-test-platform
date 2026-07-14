"""LogWatcherManager — 进程级单例，持有所有 DeviceLogWatcher 实例。

阶段 5A 真实现（替换 stub）：
    - start(): CapabilityProber.probe → 按 policy.on_unavailable 决策 → 创建 DeviceLogWatcher
               → watcher.start() → 写 watcher_state='active'
    - stop():  watcher.stop(drain, timeout) → 回填 stats → watcher_state='stopped'
    - 任何失败路径都不留孤儿：watcher 创建/启动失败时回滚注册表，watcher_state 写 'failed'

不变量（invariants）：
    - 同一 serial 同时最多一个 watcher（一设备一 Job 的 Agent 侧自保）
    - 每个 watcher 绑定 (host_id, serial, job_id) 三元组
    - stop(drain=True) 必须在 release_device_lock 之前调用（由 JobSession 保证顺序）
    - handle 登记 ≡ watcher_state 登记 ≡ watcher.start() 成功（三者原子一致）
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .device_watcher import DeviceLogWatcher
from .exceptions import WatcherStartError
from .policy import OnUnavailableAction, WatcherPolicy
from .puller import LogPuller
from .sources import (
    CapabilityProber,
    ProbeResult,
    WatcherCapability,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 内部结构
# ----------------------------------------------------------------------

@dataclass
class WatcherHandle:
    """单个 DeviceLogWatcher 的元信息 + 引用。"""

    watcher_id: str
    host_id: str
    serial: str
    job_id: int
    log_dir: str
    policy: WatcherPolicy
    capability: str = "unknown"         # inotifyd_root | inotifyd_shell | polling | unavailable
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    # 真实 watcher 对象（DeviceLogWatcher 实例），阶段 5A 后不再是 None
    impl: Optional[DeviceLogWatcher] = None
    probe_result: Optional[ProbeResult] = None
    # 运行期统计（stop 时由 DeviceLogWatcher 回填）
    stats: Dict[str, int] = field(default_factory=lambda: {
        "events_total":    0,
        "events_dropped":  0,
        "pulls_ok":        0,
        "pulls_failed":    0,
        "signals_emitted": 0,
    })


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------

# 工厂类型签名（便于测试替换）：
ProberFactory = Callable[[Any, float], CapabilityProber]
WatcherFactory = Callable[..., DeviceLogWatcher]


class LogWatcherManager:
    """进程级单例。入口都需要先调用 `LogWatcherManager.instance()`。"""

    _instance: Optional["LogWatcherManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._watchers: Dict[str, WatcherHandle] = {}          # serial -> handle
        self._id_index: Dict[str, str] = {}                    # watcher_id -> serial
        # 由 main.py 注入；用于 emitter / probe 依赖
        self._deps: Dict[str, Any] = {}
        self._configured: bool = False
        # 可替换：测试可注入 stub prober / watcher_factory
        # NOTE: lambda 包装是因为 CapabilityProber.__init__ 把 timeout_seconds 设为
        # keyword-only，而工厂契约按位置传 (adb, timeout)；不包装会 TypeError
        self._prober_factory: ProberFactory = (
            lambda adb, timeout: CapabilityProber(adb, timeout_seconds=timeout)
        )
        self._watcher_factory: WatcherFactory = DeviceLogWatcher

    # ------------------------------------------------------------------
    # 单例与依赖注入
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "LogWatcherManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        """仅供测试：销毁单例（同时尽力停掉残留 watcher）。"""
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    for handle in cls._instance.list_active():
                        cls._instance.stop(handle.watcher_id, drain=False, timeout=0.5)
                except Exception:
                    pass
            cls._instance = None

    def configure(
        self,
        *,
        adb,
        local_db,
        adb_path: str = "adb",
        sio_client=None,
        api_url: str = "",
        agent_secret: str = "",
        nfs_base_dir: str = "",
        agent_instance_id: str = "",
        prober_factory: Optional[ProberFactory] = None,
        watcher_factory: Optional[WatcherFactory] = None,
    ) -> None:
        """一次性注入运行期依赖（由 Agent main.py 启动时调用）。

        参数：
            adb               AdbWrapper 对象（CapabilityProber + LogPuller 用）
            adb_path          adb 二进制路径（InotifydSource.Popen 用）
            local_db          LocalDB（SignalEmitter / watcher_state 用）
            nfs_base_dir      5B1：LogPuller 的 NFS 挂载根；空串 = 禁用 puller（降级只记元数据）
            prober_factory    可注入的 Prober 工厂（测试替身）
            watcher_factory   可注入的 Watcher 工厂（测试替身）
        """
        self._deps = {
            "adb":          adb,
            "adb_path":     adb_path,
            "local_db":     local_db,
            "sio_client":    sio_client,
            "api_url":      api_url,
            "agent_secret": agent_secret,
            "nfs_base_dir": nfs_base_dir,
            "agent_instance_id": agent_instance_id,
        }
        if prober_factory is not None:
            self._prober_factory = prober_factory
        if watcher_factory is not None:
            self._watcher_factory = watcher_factory
        self._configured = True
        logger.info(
            "log_watcher_manager_configured adb_path=%s nfs_base_dir=%s",
            adb_path, nfs_base_dir or "<disabled>",
        )

    def is_configured(self) -> bool:
        return self._configured

    def get_dep(self, key: str, default: Any = None) -> Any:
        """对外暴露 configure() 注入的依赖(供 JobSession 启动 reconciler 时复用)。

        不允许写;调用方只读。未注入返回 default。
        """
        return self._deps.get(key, default)

    # ------------------------------------------------------------------
    # 启停
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        host_id: str,
        serial: str,
        job_id: int,
        log_dir: str,
        policy: WatcherPolicy,
        fencing_token: str,
    ) -> WatcherHandle:
        """启动一个 DeviceLogWatcher（5A 真实现）。

        流程：
            1. 占位登记 serial（防并发重入）
            2. CapabilityProber.probe(serial, policy) → ProbeResult
            3. 若 capability=UNAVAILABLE:
                 - on_unavailable=FAIL     → 抛 WatcherStartError(code='probe_failed')
                 - on_unavailable=DEGRADED → capability 记为 'unavailable'，不创建 DeviceLogWatcher
                                             （保留 handle 便于 stop 语义一致）
                 - on_unavailable=SKIP     → 同上（不创建 watcher，不写 watcher_state）
            4. 否则创建 DeviceLogWatcher + start；失败 → 回滚登记 + watcher_state='failed' + 抛
            5. watcher_state 写 'active'

        失败 Policy 说明：
            SKIP 模式下返回 handle.impl=None、capability='skipped'；不写 watcher_state，
            因为"跳过"意味着 Agent 本轮不参与 watcher 子系统。
        """
        if not self._configured:
            raise WatcherStartError(
                "LogWatcherManager not configured — call configure(adb, adb_path, local_db, ...) first",
                code="not_configured",
                context={"serial": serial, "job_id": job_id},
            )

        # Step 1: 占位登记
        watcher_id = f"wch-{uuid.uuid4().hex[:12]}"
        with self._lock:
            if serial in self._watchers:
                existing = self._watchers[serial]
                raise WatcherStartError(
                    f"watcher already running on serial={serial} "
                    f"(existing_job={existing.job_id}, new_job={job_id})",
                    code="already_running",
                    context={"serial": serial, "existing_job": existing.job_id},
                )
            handle = WatcherHandle(
                watcher_id=watcher_id,
                host_id=host_id,
                serial=serial,
                job_id=job_id,
                log_dir=log_dir,
                policy=policy,
                started_at=datetime.now(timezone.utc),
            )
            self._watchers[serial] = handle
            self._id_index[watcher_id] = serial

        # Step 2: 能力探测
        try:
            prober = self._prober_factory(self._deps["adb"], policy.probe_timeout_seconds)
            probe_result = prober.probe(serial, policy)
        except Exception as exc:
            self._unregister(watcher_id, serial)
            logger.exception(
                "watcher_probe_failed serial=%s job=%d", serial, job_id,
            )
            raise WatcherStartError(
                f"probe_failed serial={serial}: {exc}",
                code="probe_failed",
                context={"serial": serial, "job_id": job_id, "cause": str(exc)[:200]},
            ) from exc

        handle.probe_result = probe_result
        handle.capability = probe_result.capability.value

        # Step 3: capability=UNAVAILABLE 处理
        if probe_result.capability is WatcherCapability.UNAVAILABLE:
            action = policy.on_unavailable
            if action is OnUnavailableAction.FAIL:
                self._unregister(watcher_id, serial)
                raise WatcherStartError(
                    f"probe_capability_unavailable serial={serial} reasons={probe_result.reasons}",
                    code="probe_failed",
                    context={"serial": serial, "job_id": job_id, "reasons": probe_result.reasons},
                )
            if action is OnUnavailableAction.SKIP:
                # skip 模式：保留 handle 便于 stop 语义一致，但不创建 watcher、不写 watcher_state
                handle.capability = "skipped"
                logger.info(
                    "watcher_skipped serial=%s job=%d reasons=%s",
                    serial, job_id, probe_result.reasons,
                )
                return handle
            # DEGRADED: 保留 handle 但不创建 DeviceLogWatcher；capability 保持 'unavailable'
            self._record_watcher_state(
                handle, state="active", last_error=f"degraded:{probe_result.reasons}",
            )
            logger.warning(
                "watcher_degraded serial=%s job=%d reasons=%s",
                serial, job_id, probe_result.reasons,
            )
            return handle

        # Step 4: 创建真实 DeviceLogWatcher 并 start
        try:
            # M0/PR #2: 若 reconciler 灰度开启且匹配 host 白名单 → 告知 watcher 跳过
            # AEE/VENDOR_AEE emit(reconciler 唯一 emit 这两类)
            try:
                from ..aee.reconciler import is_reconciler_enabled
                aee_reconciler_active = is_reconciler_enabled(host_id)
            except Exception:
                aee_reconciler_active = False
            watcher = self._watcher_factory(
                adb_path=self._deps["adb_path"],
                local_db=self._deps["local_db"],
                host_id=host_id,
                serial=serial,
                job_id=job_id,
                policy=policy,
                capability=probe_result.capability,
                probe_result=probe_result,
                aee_reconciler_active=aee_reconciler_active,
                fencing_token=fencing_token,
                agent_instance_id=str(self._deps.get("agent_instance_id") or ""),
            )
            # 5B1：若配置了 NFS 根目录，且实盘 inotifyd 能力可用，则注入 LogPuller
            # 为 AEE / VENDOR_AEE 事件异步拉 crash 文件并富化 envelope
            nfs_base_dir = str(self._deps.get("nfs_base_dir") or "")
            sonic_output_dir = None
            if nfs_base_dir:
                try:
                    from ..aee.paths import resolve_sonic_output_dir_for_job

                    sonic_output_dir = resolve_sonic_output_dir_for_job(
                        adb=self._deps["adb"],
                        serial=serial,
                        job_id=job_id,
                        state_store=self._deps["local_db"],
                    )
                except Exception:
                    logger.exception(
                        "watcher_sonic_output_dir_failed serial=%s job=%d",
                        serial, job_id,
                    )
            if (
                nfs_base_dir
                and probe_result.capability in (
                    WatcherCapability.INOTIFYD_ROOT,
                    WatcherCapability.INOTIFYD_SHELL,
                )
                and hasattr(watcher, "attach_puller")
            ):
                puller = LogPuller(
                    adb=self._deps["adb"],
                    nfs_base_dir=nfs_base_dir,
                    job_id=job_id,
                    host_id=host_id,
                    serial=serial,
                    on_pull_done=watcher._on_pull_done,
                    pull_timeout_seconds=policy.pull_timeout_seconds,
                    max_file_mb=policy.pull_max_file_mb,
                    sonic_output_dir=str(sonic_output_dir) if sonic_output_dir else None,
                    bugreport_enabled=sonic_output_dir is not None,
                )
                watcher.attach_puller(puller)
            watcher.start()
        except Exception as exc:
            # 启动失败：回滚注册表 + watcher_state='failed'
            self._record_watcher_state(
                handle, state="failed", stopped_at=datetime.now(timezone.utc),
                last_error=f"start_failed:{exc}"[:500],
            )
            self._unregister(watcher_id, serial)
            logger.exception(
                "watcher_start_failed serial=%s job=%d", serial, job_id,
            )
            if isinstance(exc, WatcherStartError):
                raise
            raise WatcherStartError(
                f"watcher_start_failed serial={serial}: {exc}",
                code="start_failed",
                context={"serial": serial, "job_id": job_id, "cause": str(exc)[:200]},
            ) from exc

        handle.impl = watcher
        # Step 5: watcher_state='active'
        self._record_watcher_state(handle, state="active")
        logger.info(
            "watcher_started watcher_id=%s serial=%s job_id=%d log_dir=%s capability=%s",
            watcher_id, serial, job_id, log_dir, handle.capability,
        )
        return handle

    def stop(
        self,
        watcher_id: str,
        *,
        drain: bool = True,
        timeout: float = 5.0,
    ) -> Optional[WatcherHandle]:
        """停止指定 watcher。

        退出协议（重要）：
          - drain=True 表示"尝试同步 flush outbox，最长等待 timeout 秒"
          - 超时后**立即返回**，不再阻塞调用方（JobSession 需释放设备锁）
          - outbox 中未发送的条目由 Agent 进程级 OutboxDrainer 异步补发
          - watcher.stop() 内部 SignalEmitter → LocalDB UNIQUE 保证幂等

        返回 handle（包含最终 stats），未找到返回 None（幂等）。
        """
        with self._lock:
            serial = self._id_index.pop(watcher_id, None)
            if serial is None:
                logger.warning("watcher_stop_unknown watcher_id=%s (already stopped?)", watcher_id)
                return None
            handle = self._watchers.pop(serial, None)

        if handle is None:
            return None

        # 停 DeviceLogWatcher（可能为 None：degraded / skipped 路径）
        last_error: Optional[str] = None
        if handle.impl is not None:
            try:
                stats = handle.impl.stop(drain=drain, timeout=timeout)
                handle.stats.update(stats.to_dict())
            except Exception as exc:
                last_error = f"stop_failed:{exc}"[:500]
                logger.exception(
                    "watcher_stop_impl_failed watcher_id=%s serial=%s",
                    watcher_id, handle.serial,
                )

        handle.stopped_at = datetime.now(timezone.utc)

        # watcher_state='stopped'（仅当之前写过 'active'）
        if handle.capability != "skipped":
            self._record_watcher_state(
                handle, state="stopped",
                stopped_at=handle.stopped_at, last_error=last_error,
            )

        logger.info(
            "watcher_stopped watcher_id=%s serial=%s job_id=%d drain=%s timeout=%.1f stats=%s",
            handle.watcher_id, handle.serial, handle.job_id, drain, timeout, handle.stats,
        )
        return handle

    # ------------------------------------------------------------------
    # 查询 / 重建
    # ------------------------------------------------------------------

    def get_by_id(self, watcher_id: str) -> Optional[WatcherHandle]:
        with self._lock:
            serial = self._id_index.get(watcher_id)
            return self._watchers.get(serial) if serial else None

    def get_by_serial(self, serial: str) -> Optional[WatcherHandle]:
        with self._lock:
            return self._watchers.get(serial)

    def list_active(self) -> list[WatcherHandle]:
        with self._lock:
            return list(self._watchers.values())

    def reconcile_on_startup(self, active_jobs: Optional[list[dict]] = None) -> int:
        """Agent 重启后清理上次进程残留的 active watcher_state（M4/T4-4）。

        新进程刚启动时内存登记表(self._watchers)必为空，故 LocalDB 中任何
        state='active' 的 watcher_state 都是上次进程崩溃/重启遗留的脏记录 →
        统一标记为 'stopped'(last_error='agent_restart_stale_cleanup')，避免脏
        记录无限累积、污染 watcher_state 监控与重启诊断。返回清理的残留记录数。

        边界说明：
          - 完整的「按服务端 active_jobs 重新挂载 DeviceLogWatcher」留待阶段 6；
            active_jobs 当前仅用于日志，为该扩展预留入参。
          - AEE 增量在重启后由 AeeDbHistoryReconciler 的 baseline snapshot +
            持久化 processed key 补齐（watcher-consolidate-aee 方案 §6），不依赖本方法。
        """
        local_db = self._deps.get("local_db")
        if local_db is None:
            logger.info("reconcile_on_startup skipped: local_db not configured")
            return 0
        try:
            stale = local_db.list_active_watcher_states()
        except Exception:
            logger.exception("reconcile_on_startup list_active_watcher_states failed")
            return 0

        now = datetime.now(timezone.utc)
        cleaned = 0
        for row in stale:
            watcher_id = row.get("watcher_id")
            if not watcher_id:
                continue
            try:
                local_db.update_watcher_state(
                    watcher_id,
                    state="stopped",
                    stopped_at=now,
                    last_error="agent_restart_stale_cleanup",
                )
                cleaned += 1
            except Exception:
                logger.exception(
                    "reconcile_on_startup cleanup_failed watcher_id=%s", watcher_id,
                )
        logger.info(
            "reconcile_on_startup cleaned_stale=%d active_jobs=%d",
            cleaned, len(active_jobs) if active_jobs else 0,
        )
        return cleaned

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _unregister(self, watcher_id: str, serial: str) -> None:
        """从 in-memory 登记表中移除；不抛异常。"""
        with self._lock:
            self._id_index.pop(watcher_id, None)
            self._watchers.pop(serial, None)

    def _record_watcher_state(
        self,
        handle: WatcherHandle,
        *,
        state: str,
        stopped_at: Optional[datetime] = None,
        last_error: Optional[str] = None,
    ) -> None:
        """写 LocalDB.watcher_state；失败只记日志，不影响主流程。"""
        local_db = self._deps.get("local_db")
        if local_db is None:
            return
        try:
            local_db.upsert_watcher_state(
                watcher_id=handle.watcher_id,
                job_id=handle.job_id,
                serial=handle.serial,
                host_id=handle.host_id,
                state=state,
                capability=handle.capability,
                started_at=handle.started_at,
                stopped_at=stopped_at,
                last_error=last_error,
            )
        except Exception:
            logger.exception(
                "watcher_state_upsert_failed watcher_id=%s state=%s",
                handle.watcher_id, state,
            )
