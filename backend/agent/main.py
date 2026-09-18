import logging
import os
import queue
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Set

# 自动加载 .env 文件（支持手动运行时读取配置）
# 优先加载当前工作目录的 .env，不覆盖已有环境变量
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass  # python-dotenv 未安装时跳过，由 systemd EnvironmentFile 提供变量

# 支持直接运行和作为包运行
if __name__ == "__main__" and __package__ is None:
    # 直接运行时的导入路径处理
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent.adb_wrapper import AdbWrapper
    from agent.recovery_executor import (
        run_recovery_sync_if_needed,
        trigger_recovery_sync_on_device_reconnect,
    )
    from agent.bootstrap_subsystems import start_disk_and_watcher_subsystems
    from agent.startup_identity import bootstrap_process_identity
    from agent.control_handler import ControlHandlerDeps, build_control_handler
    from agent.active_job_bindings import (
        ActiveJobOccupancy,
        JobRunnerStateSlot,
        build_deregister_active_job,
        build_on_lease_lost,
        build_register_active_job,
    )
    from agent.recovery_runtime import (
        ResumeJobSlot,
        build_cancel_recovery_job,
        build_execute_recovery_actions,
        build_resume_recovered_job,
        start_periodic_recovery_sync,
    )
    from agent.claim_loop import process_claim_tick
    from agent import device_discovery
    from agent.aee.state_migration import migrate_legacy_aee_state_keys
    from agent.artifact_uploader import ArtifactUploader
    from agent.config import BASE_DIR, ensure_dirs
    from agent.log_archiver import LogArchiver, collect_archive_heartbeat_metrics
    from agent.event_uploader import EventUploader
    from agent.local_disk_monitor import LocalDiskMonitor
    from agent.heartbeat_thread import HeartbeatThread
    from agent.job_runner import JobRunnerState, run_task_wrapper
    from agent.lease_renewer import LeaseRenewer
    from agent.mq.producer import StepTraceWriter
    from agent.outbox_drainer import OutboxDrainThread
    from agent.registry.local_db import LocalDB
    from agent.registry.patrol_checkpoint_store import PatrolCycleCheckpointStore
    from agent.registry.script_registry import ScriptRegistry
    from agent.scan_runner import ScanRunner
    from agent.step_trace_uploader import StepTraceUploader
    from agent.socketio_client import AgentSocketIOClient
else:
    from .adb_wrapper import AdbWrapper
    from .recovery_executor import (
        run_recovery_sync_if_needed,
        trigger_recovery_sync_on_device_reconnect,
    )
    from .bootstrap_subsystems import start_disk_and_watcher_subsystems
    from .startup_identity import bootstrap_process_identity
    from .control_handler import ControlHandlerDeps, build_control_handler
    from .active_job_bindings import (
        ActiveJobOccupancy,
        JobRunnerStateSlot,
        build_deregister_active_job,
        build_on_lease_lost,
        build_register_active_job,
    )
    from .recovery_runtime import (
        ResumeJobSlot,
        build_cancel_recovery_job,
        build_execute_recovery_actions,
        build_resume_recovered_job,
        start_periodic_recovery_sync,
    )
    from .claim_loop import process_claim_tick
    from . import device_discovery
    from .aee.state_migration import migrate_legacy_aee_state_keys
    from .artifact_uploader import ArtifactUploader
    from .config import BASE_DIR, ensure_dirs
    from .log_archiver import LogArchiver, collect_archive_heartbeat_metrics
    from .event_uploader import EventUploader
    from .local_disk_monitor import LocalDiskMonitor
    from .heartbeat_thread import HeartbeatThread
    from .job_runner import JobRunnerState, run_task_wrapper
    from .lease_renewer import LeaseRenewer
    from .operation_scheduler import OperationScheduler
    from .coordinator import HostRunCoordinator
    from .mq.producer import StepTraceWriter
    from .outbox_drainer import OutboxDrainThread
    from .registry.local_db import LocalDB
    from .registry.patrol_checkpoint_store import PatrolCycleCheckpointStore
    from .registry.script_registry import ScriptRegistry
    from .scan_runner import ScanRunner
    from .step_trace_uploader import StepTraceUploader
    from .socketio_client import AgentSocketIOClient

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Device Log Watcher feature flag —— 全局 STP_WATCHER_ENABLED 或 Plan 默认开启。
# Plan 执行默认开启 watcher（STP_WATCHER_PLAN_DEFAULT=true）时，即使全局 env=false
# 也会 configure 子系统，并在 claim Plan job 时启动 JobSession。
STP_WATCHER_ENABLED = os.getenv("STP_WATCHER_ENABLED", "true").lower() == "true"
STP_WATCHER_PLAN_DEFAULT = os.getenv("STP_WATCHER_PLAN_DEFAULT", "true").lower() == "true"

# 全局活跃 Job 追踪（语义上存的就是 job_instance.id）
# 命名约定：_active_job_ids / _active_jobs_lock
_active_job_ids: Set[int] = set()
_active_device_ids: Set[int] = set()  # per-device concurrency guard
# device_id → 占用它的 active job_id（与 _active_device_ids 同锁、同步增删）。
# 占位清理只允许归属 job 本人执行：迟到 worker 的 release 不得清掉继任 job 已
# 重占的占位（#1203，#1006 补偿的 None 分支无法区分「本 job 残留」与「已清 +
# 继任重占」两种 token 消失态）。
_active_device_owner: Dict[int, int] = {}
_active_job_tokens: Dict[int, str] = {}
_active_jobs_lock = threading.Lock()
_lock_renewal_stop_event = threading.Event()



def _migrate_legacy_aee_state_on_startup(db_path: str) -> Dict[str, Any]:
    """Promote legacy scan_aee state into watcher:aee namespace during agent startup."""
    summary = migrate_legacy_aee_state_keys(db_path)
    if (
        int(summary["processed_entries_migrated"]) > 0
        or int(summary["pending_pull_migrated"]) > 0
    ):
        logger.info(
            "startup_aee_state_namespace_migrated db_path=%s summary=%s",
            db_path,
            summary,
        )
    if summary.get("errors"):
        logger.warning(
            "startup_aee_state_namespace_migration_errors db_path=%s errors=%s",
            db_path,
            summary["errors"],
        )
    return summary


# 全局活跃 Job 追踪辅助函数（仅 per-device guard）
def _register_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.add(did)


def _deregister_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.discard(did)


def _check_agent_version(api_url: str, host_id: str, mount_points, host_info) -> None:
    """Send a single heartbeat and verify agent version meets backend's minimum.

    Exits the process if the agent is too old.
    """
    from . import __version__ as agent_version
    from .heartbeat import send_heartbeat

    try:
        resp = send_heartbeat(
            api_url,
            host_id,
            mount_points,
            host_info=host_info,
            agent_version=agent_version,
        )
    except Exception:
        logger.warning("version_check_heartbeat_failed — skipping version guard")
        return

    if resp is None:
        logger.warning("version_check_no_response — skipping version guard")
        return

    min_version = (resp.get("agent_min_version") or "").strip()
    if not min_version:
        return  # Backend doesn't enforce a minimum version yet

    if _version_lt(agent_version, min_version):
        logger.critical(
            "agent_version_too_old agent=%s required=%s — refusing to start",
            agent_version, min_version,
        )
        sys.exit(1)

    logger.info("version_check_ok agent=%s min=%s", agent_version, min_version)


def _version_lt(a: str, b: str) -> bool:
    """Compare two SemVer strings (no pre-release tags). Returns True if a < b."""
    try:
        parts_a = [int(x) for x in a.split(".")]
        parts_b = [int(x) for x in b.split(".")]
    except (ValueError, TypeError):
        return False  # Malformed versions → don't block
    # Pad shorter list with zeros
    max_len = max(len(parts_a), len(parts_b))
    parts_a += [0] * (max_len - len(parts_a))
    parts_b += [0] * (max_len - len(parts_b))
    return parts_a < parts_b





def _ensure_adb_server_on_startup(adb_path: str) -> bool:
    """Reconcile ADB fork-servers to the Agent's configured port.

    启动与 reload_config 共用：清理非目标端口的游离 daemon 并重启目标端口
    server，让全部 USB 设备重新注册。失败不阻塞启动/热更新，由心跳健康检查
    （adb_multiple_servers / device 数）兜底告警。
    """
    try:
        result = device_discovery.ensure_single_adb_server(adb_path)
        logger.info(
            "adb_server_reconciled port=%s killed_ports=%s started=%s skipped=%s",
            result.get("port"),
            [server.get("port") for server in result.get("killed", [])],
            result.get("started"),
            result.get("skipped"),
        )
        return True
    except Exception as exc:
        logger.error("adb_server_reconcile_failed: %s", exc)
        return False



def main() -> None:
    api_url = os.getenv("API_URL", "http://127.0.0.1:8000")

    # 确保运行时目录存在
    ensure_dirs()

    identity = bootstrap_process_identity(api_url)
    host_info = identity.host_info
    agent_instance_id = identity.agent_instance_id
    boot_id = identity.boot_id
    host_id = identity.host_id
    _agent_pkg_version = identity.agent_version
    _agent_code_revision = identity.agent_code_revision
    poll_interval = identity.poll_interval
    mount_points = identity.mount_points
    adb_path = identity.adb_path
    agent_secret = identity.agent_secret

    adb = AdbWrapper(adb_path=adb_path)
    _ensure_adb_server_on_startup(adb_path)
    # 启动 WebSocket 客户端（best-effort，失败时降级到 HTTP）
    sio_client = AgentSocketIOClient(api_url, host_id, agent_secret)
    # P2-2a：先注册转发 handler 再 connect——启动窗口内到达的 control 命令
    # 入队暂存，真实 handler 就绪后统一回放，不再静默丢弃。
    _early_control_queue: "queue.Queue[dict]" = queue.Queue()

    def _early_control_handler(data: dict) -> None:
        _early_control_queue.put(data)

    sio_client.set_control_handler(_early_control_handler)
    sio_client.connect()
    # Start background reconnect loop for auto-recovery on disconnect
    sio_client.start_reconnect_loop()

    # 初始化本地 SQLite WAL 缓存
    local_db = LocalDB()
    db_path = str(BASE_DIR / "agent_state.db")
    local_db.initialize(db_path)
    try:
        from .aee.device_log_event_client import bind_local_db as _bind_dle_db
    except ImportError:
        from agent.aee.device_log_event_client import bind_local_db as _bind_dle_db
    _bind_dle_db(local_db)
    _migrate_legacy_aee_state_on_startup(db_path)

    patrol_checkpoint_store = PatrolCycleCheckpointStore(BASE_DIR / "patrol_checkpoint.db")
    patrol_checkpoint_store.initialize()

    script_registry = ScriptRegistry(local_db, api_url, agent_secret)
    script_registry.initialize()

    log_signal_drainer = start_disk_and_watcher_subsystems(
        local_db=local_db,
        api_url=api_url,
        agent_secret=agent_secret,
        host_id=str(host_id),
        agent_instance_id=agent_instance_id,
        adb=adb,
        adb_path=adb_path,
        sio_client=sio_client,
    )

    # Step trace local writer (Redis XADD removed in Phase 4; HTTP upload via StepTraceUploader)
    mq_producer = StepTraceWriter("", host_id, local_db=local_db)
    # ADR-0026 P2-2: wire pipeline _MQStepLogger → SocketIO batched step_log
    mq_producer.bind_sio_client(sio_client)

    # Assigned after the executor is created; the control closure also handles
    # commands received during the small startup window.
    job_runner_state: Optional[JobRunnerState] = None
    control_deps = ControlHandlerDeps()
    _handle_control = build_control_handler(
        deps=control_deps,
        mq_producer=mq_producer,
        host_id=str(host_id),
        api_url=api_url,
        agent_secret=agent_secret,
        adb_path=adb_path,
        local_db=local_db,
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        ensure_adb_server=_ensure_adb_server_on_startup,
    )


    # ADR-0019 Phase 1: capacity helper — thread-safe active job count
    def _get_active_job_count() -> int:
        with _active_jobs_lock:
            return len(_active_job_ids)

    # ADR-0019 Phase 3c: active device count for effective_slots
    def _get_active_device_count() -> int:
        with _active_jobs_lock:
            return len(_active_device_ids)

    _execute_recovery_actions = None

    # One-shot protocol gate: do not start workers/background threads when this
    # Agent build is below the backend's minimum supported version.
    _check_agent_version(api_url, host_id, mount_points, host_info)

    # 启动心跳守护线程（独立于任务执行循环）
    try:
        from agent.version_info import read_artifact_digest
    except ImportError:  # pragma: no cover - 部署形态分支
        from .version_info import read_artifact_digest

    heartbeat_thread = HeartbeatThread(
        api_url=api_url,
        host_id=host_id,
        adb_path=adb_path,
        mount_points=mount_points,
        host_info=host_info,
        poll_interval=poll_interval,
        sio_client=sio_client,
        catalog_versions=lambda: {
            "script_catalog_version": script_registry.version,
        },
        on_scripts_outdated=script_registry.initialize,
        get_active_job_count=_get_active_job_count,
        get_active_device_count=_get_active_device_count,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        agent_version=_agent_pkg_version,
        agent_code_revision=_agent_code_revision,
        agent_artifact_digest=lambda: read_artifact_digest(),
        agent_resources_digest=lambda: read_artifact_digest("resources"),
        get_outbox_counts=lambda: {
            "terminal_outbox_pending": local_db.count_pending_terminals(),
            "log_signal_outbox_pending": local_db.count_pending_log_signals(),
            # #302: 死信总量随心跳上报（历史累计，跨 Agent 重启保留）。
            "log_signal_dead_letter_total": local_db.count_log_signal_dead_letters(),
            # #762/#742: 终态 outbox 死信行数（distinct 卡死行口径；事件计数见 drainer
            # snapshot 的 conflicts_retained_total，勿当积压 gauge）。
            "terminal_outbox_dead_letter_total": local_db.count_terminal_dead_letters(),
            # #739 面②/#2188 D 步：分片登记失败（半交付）进程级累计，重启清零。
            "scan_shard_register_failure_total": ScanRunner.shard_register_failure_total(),
        },
        # ADR-0025 Sprint 2: 上报归档指标到 extra['archive']（归档禁用时回调返回 None）
        get_archive_metrics=collect_archive_heartbeat_metrics,
        on_devices_reconnected=lambda serials: (
            trigger_recovery_sync_on_device_reconnect(
                reconnected_serials=serials,
                local_db=local_db,
                api_url=api_url,
                host_id=host_id,
                agent_instance_id=agent_instance_id,
                boot_id=boot_id,
                execute_actions=_execute_recovery_actions,
            )
            if _execute_recovery_actions is not None
            else False
        ),
        # ADR-0026 P2-2: apply server log_rate_limit hint to SocketIO batcher
        on_log_rate_limit=mq_producer.set_log_rate_limit,
    )
    heartbeat_thread.start()

    # ADR-0026 Step 5b: create host-global scheduler + coordinator BEFORE
    # any component that references them (LeaseRenewer, claim loop, etc.).
    operation_scheduler = OperationScheduler()
    # Late-bind: HeartbeatThread starts before scheduler exists.
    heartbeat_thread._get_operation_stats = operation_scheduler.concurrency_snapshot
    coordinator = HostRunCoordinator(
        api_url, host_id, agent_instance_id, agent_secret=agent_secret,
        local_db=local_db,
    )
    # ADR-0026 Step 5b: wire scheduler to coordinator for abort/cancel
    coordinator.set_scheduler(operation_scheduler)
    # Start the per-host coordinator heartbeat (reports coordinator
    # heartbeats + per-job execution_state to control plane).
    coordinator.start()
    control_deps.coordinator = coordinator
    control_deps.operation_scheduler = operation_scheduler
    control_deps.heartbeat_thread = heartbeat_thread


    occupancy = ActiveJobOccupancy(
        lock=_active_jobs_lock,
        job_ids=_active_job_ids,
        device_ids=_active_device_ids,
        job_tokens=_active_job_tokens,
        device_owner=_active_device_owner,
    )
    job_runner_slot = JobRunnerStateSlot()
    # ADR-0019 Phase 3b: lease 丢失回调（409 时 LeaseRenewer 内部已清理，此处处理外部状态）
    _on_lease_lost = build_on_lease_lost(
        occupancy=occupancy,
        job_runner_slot=job_runner_slot,
        coordinator=coordinator,
        local_db=local_db,
    )

    # 启动 lease 续租器
    lease_renewer = LeaseRenewer(
        api_url,
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        lock_renewal_stop_event=_lock_renewal_stop_event,
        agent_instance_id=agent_instance_id,
        on_lease_lost=_on_lease_lost,
        host_id=host_id,
        coordinator=coordinator,
    )
    lease_renewer.start()

    # ADR-0019 Phase 2b + Phase 3a/3b: 活跃 job 注册/注销（捕获 lease_renewer + local_db）
    _register_active_job = build_register_active_job(
        occupancy=occupancy,
        lease_renewer=lease_renewer,
        local_db=local_db,
    )
    _deregister_active_job = build_deregister_active_job(
        occupancy=occupancy,
        lease_renewer=lease_renewer,
        local_db=local_db,
    )

    # 真实 control handler 在 deps 就绪后注册；回放启动窗口暂存命令（P2-2a）
    sio_client.set_control_handler(_handle_control)
    # 回放启动窗口内暂存的命令（P2-2a）
    while True:
        try:
            early_data = _early_control_queue.get_nowait()
        except queue.Empty:
            break
        try:
            _handle_control(early_data)
        except Exception:
            logger.exception("early_control_replay_failed command=%s", early_data.get("command"))

    # 启动终态 Outbox Drain 线程
    outbox_drain = OutboxDrainThread(api_url, local_db, interval=15.0)
    outbox_drain.start()

    # ── ADR-0019 Phase 3a: Recovery Sync ──
    resume_slot = ResumeJobSlot()
    _cancel_recovery_job = build_cancel_recovery_job(job_runner_slot)
    _execute_recovery_actions_impl_closure = build_execute_recovery_actions(
        lease_renewer=lease_renewer,
        local_db=local_db,
        outbox_drain=outbox_drain,
        register_active_job=_register_active_job,
        resume_slot=resume_slot,
        cancel_recovery_job=_cancel_recovery_job,
    )
    _execute_recovery_actions = _execute_recovery_actions_impl_closure

    from .patrol_recovery import build_patrol_job_not_running_handler

    patrol_job_not_running_recovery = build_patrol_job_not_running_handler(
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        local_db=local_db,
        execute_actions=_execute_recovery_actions_impl_closure,
    )

    # StepTrace HTTP 批量上报（Phase 3.7: acked=0 补传 → Phase 4: 唯一上报路径）
    step_trace_uploader = StepTraceUploader(
        api_url, local_db, agent_secret=agent_secret, interval=5.0,
    )
    step_trace_uploader.start()

    # ADR-0026 Step 5b: thread pool sized for ALL devices the host manages
    # (up to ~50), NOT permit-limited. Concurrent script/ADB operations are
    # gated by the OperationScheduler; distinct device jobs share the pool
    # and wait for their turn.
    # ADR-0026 Step 5b: independent pool for admitted jobs (no longer
    # permit‑limited — the OperationScheduler gates concurrency separately).
    max_workers = int(os.getenv("STP_JOB_WORKER_POOL_SIZE", "50"))
    executor = ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="task-worker"
    )
    job_runner_state = JobRunnerState(
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        active_device_ids=_active_device_ids,
        active_job_tokens=_active_job_tokens,
        running_worker_tokens={},
        watcher_globally_enabled=STP_WATCHER_ENABLED,
        watcher_plan_default=STP_WATCHER_PLAN_DEFAULT,
        lock_register=_register_active_job,
        lock_deregister=_deregister_active_job,
        device_id_register=_register_active_device,
        device_id_deregister=_deregister_active_device,
        active_device_owner=_active_device_owner,
        on_job_not_running_recovery=patrol_job_not_running_recovery,
    )
    control_deps.job_runner_state = job_runner_state
    job_runner_slot.value = job_runner_state

    resume_slot.value = build_resume_recovered_job(
        agent_instance_id=agent_instance_id,
        coordinator=coordinator,
        executor=executor,
        adb=adb,
        api_url=api_url,
        host_id=host_id,
        job_runner_state=job_runner_state,
        mq_producer=mq_producer,
        script_registry=script_registry,
        local_db=local_db,
        patrol_checkpoint_store=patrol_checkpoint_store,
        operation_scheduler=operation_scheduler,
        step_trace_uploader=step_trace_uploader,
        run_task_wrapper=run_task_wrapper,
    )

    # Recovery sync execution（启动一次 + #784 周期兜底）
    run_recovery_sync_if_needed(
        local_db=local_db,
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        execute_actions=_execute_recovery_actions_impl_closure,
    )
    _recovery_sync_stop, _recovery_sync_thread, _recovery_sync_interval = (
        start_periodic_recovery_sync(
            local_db=local_db,
            api_url=api_url,
            host_id=host_id,
            agent_instance_id=agent_instance_id,
            boot_id=boot_id,
            execute_actions=_execute_recovery_actions_impl_closure,
        )
    )
    # SIGTERM / SIGINT graceful shutdown
    _shutdown_event = threading.Event()

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("received_%s, initiating graceful shutdown", sig_name)
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        while not _shutdown_event.is_set():
            try:
                process_claim_tick(
                    api_url=api_url,
                    host_id=host_id,
                    agent_instance_id=agent_instance_id,
                    occupancy=occupancy,
                    heartbeat_thread=heartbeat_thread,
                    register_active_job=_register_active_job,
                    deregister_active_job=_deregister_active_job,
                    lease_renewer=lease_renewer,
                    local_db=local_db,
                    coordinator=coordinator,
                    executor=executor,
                    adb=adb,
                    job_runner_state=job_runner_state,
                    mq_producer=mq_producer,
                    script_registry=script_registry,
                    patrol_checkpoint_store=patrol_checkpoint_store,
                    operation_scheduler=operation_scheduler,
                    step_trace_uploader=step_trace_uploader,
                    run_task_wrapper=run_task_wrapper,
                )
            except Exception:
                logger.exception("agent_loop_failed", extra={"host_id": host_id})
            # Use event wait instead of sleep so SIGTERM wakes us immediately
            _shutdown_event.wait(poll_interval)
    finally:
        logger.info("agent_shutting_down, waiting for active tasks to finish...")
        coordinator.stop()
        operation_scheduler.shutdown()
        # R07-F12 (#1012): scheduler.shutdown() only wakes permit waiters; also
        # cancel already-running cruises so the executor drain below is bounded
        # and SIGTERM actually ends a patrol loop instead of retrying it.
        if job_runner_state is not None:
            try:
                for _jid in list(job_runner_state.active_runners):
                    job_runner_state.request_abort(_jid)
            except Exception:
                logger.exception("shutdown_cancel_runners_failed")
        executor.shutdown(wait=True, cancel_futures=False)
        # Flush step traces via HTTP before shutdown
        try:
            flushed = step_trace_uploader.drain_sync()
            if flushed:
                logger.info("shutdown_step_trace_flushed count=%d", flushed)
        except Exception:
            logger.exception("shutdown_step_trace_flush_failed")
        step_trace_uploader.stop()
        # Final outbox drain: flush any un-acked terminal states
        try:
            flushed = outbox_drain.drain_sync()
            if flushed:
                logger.info("shutdown_outbox_flushed count=%d", flushed)
        except Exception:
            logger.exception("shutdown_outbox_flush_failed")
        outbox_drain.stop()
        # #784: LogArchiver / LocalDiskMonitor / EventUploader 在 watcher 门控
        # 之外启动——停机必须同作用域，不能包进 log_signal_drainer 分支，否则
        # watcher 禁用时 local_db.close() 后 daemon 仍 tick → LocalDB is closed。
        try:
            LocalDiskMonitor.instance().stop(timeout=5.0)
        except Exception:
            logger.exception("shutdown_local_disk_monitor_stop_failed")
        try:
            LogArchiver.instance().stop(timeout=5.0)
        except Exception:
            logger.exception("shutdown_log_archiver_stop_failed")
        try:
            EventUploader.instance().stop(timeout=5.0)
        except Exception:
            logger.exception("shutdown_event_uploader_stop_failed")
        # log_signal_outbox drainer + ArtifactUploader（仅 watcher 子系统启用时）
        if log_signal_drainer is not None:
            try:
                flushed = log_signal_drainer.tick_once()
                if flushed:
                    logger.info("shutdown_log_signal_flushed count=%d", flushed)
            except Exception:
                logger.exception("shutdown_log_signal_flush_failed")
            log_signal_drainer.stop(timeout=5.0)
            try:
                ArtifactUploader.instance().stop(drain=True, timeout=5.0)
            except Exception:
                logger.exception("shutdown_artifact_uploader_stop_failed")
        _recovery_sync_stop.set()
        try:
            _recovery_sync_thread.join(timeout=5.0)
        except Exception:
            logger.exception("shutdown_recovery_sync_join_failed")
        heartbeat_thread.stop()
        lease_renewer.stop()
        mq_producer.close()
        local_db.close()
        sio_client.disconnect()
        logger.info("agent_shutdown_complete")


if __name__ == "__main__":
    main()
