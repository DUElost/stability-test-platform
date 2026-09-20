import logging
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Set

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
    from agent.graceful_shutdown import shutdown_agent_runtime
    from agent.startup_guards import (
        check_agent_version,
        ensure_adb_server_on_startup,
    )
    from agent.local_runtime import (
        connect_socketio_with_early_control,
        initialize_local_stores,
        replay_early_control_commands,
    )
    from agent.heartbeat_bindings import (
        RecoveryActionsSlot,
        build_heartbeat_thread,
    )
    from agent.config import ensure_dirs
    from agent.job_runner import JobRunnerState, run_task_wrapper
    from agent.lease_renewer import LeaseRenewer
    from agent.mq.producer import StepTraceWriter
    from agent.outbox_drainer import OutboxDrainThread
    from agent.step_trace_uploader import StepTraceUploader
    from agent.patrol_recovery import build_patrol_job_not_running_handler
    from agent.operation_scheduler import OperationScheduler
    from agent.coordinator import HostRunCoordinator
else:
    from .adb_wrapper import AdbWrapper
    from .recovery_executor import (
        run_recovery_sync_if_needed,
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
    from .graceful_shutdown import shutdown_agent_runtime
    from .startup_guards import (
        check_agent_version,
        ensure_adb_server_on_startup,
    )
    from .local_runtime import (
        connect_socketio_with_early_control,
        initialize_local_stores,
        replay_early_control_commands,
    )
    from .heartbeat_bindings import (
        RecoveryActionsSlot,
        build_heartbeat_thread,
    )
    from .config import ensure_dirs
    from .job_runner import JobRunnerState, run_task_wrapper
    from .lease_renewer import LeaseRenewer
    from .operation_scheduler import OperationScheduler
    from .coordinator import HostRunCoordinator
    from .mq.producer import StepTraceWriter
    from .outbox_drainer import OutboxDrainThread
    from .step_trace_uploader import StepTraceUploader
    from .patrol_recovery import build_patrol_job_not_running_handler

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


# 全局活跃 Job 追踪辅助函数（仅 per-device guard）
def _register_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.add(did)


def _deregister_active_device(did: int) -> None:
    with _active_jobs_lock:
        _active_device_ids.discard(did)


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
    ensure_adb_server_on_startup(adb_path)
    sio_client, early_control_queue = connect_socketio_with_early_control(
        api_url, host_id, agent_secret
    )
    stores = initialize_local_stores(api_url=api_url, agent_secret=agent_secret)
    local_db = stores.local_db
    patrol_checkpoint_store = stores.patrol_checkpoint_store
    script_registry = stores.script_registry

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
        ensure_adb_server=ensure_adb_server_on_startup,
    )


    # ADR-0019 Phase 1/3c: capacity getters live inside build_heartbeat_thread
    recovery_actions_slot = RecoveryActionsSlot()

    # One-shot protocol gate: do not start workers/background threads when this
    # Agent build is below the backend's minimum supported version.
    check_agent_version(api_url, host_id, mount_points, host_info)

    heartbeat_thread = build_heartbeat_thread(
        api_url=api_url,
        host_id=host_id,
        adb_path=adb_path,
        mount_points=mount_points,
        host_info=host_info,
        poll_interval=poll_interval,
        sio_client=sio_client,
        script_registry=script_registry,
        local_db=local_db,
        mq_producer=mq_producer,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        agent_version=_agent_pkg_version,
        agent_code_revision=_agent_code_revision,
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        active_device_ids=_active_device_ids,
        recovery_actions_slot=recovery_actions_slot,
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
    replay_early_control_commands(early_control_queue, _handle_control)

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
    recovery_actions_slot.value = _execute_recovery_actions_impl_closure

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
        shutdown_agent_runtime(
            coordinator=coordinator,
            operation_scheduler=operation_scheduler,
            job_runner_state=job_runner_state,
            executor=executor,
            step_trace_uploader=step_trace_uploader,
            outbox_drain=outbox_drain,
            log_signal_drainer=log_signal_drainer,
            recovery_sync_stop=_recovery_sync_stop,
            recovery_sync_thread=_recovery_sync_thread,
            heartbeat_thread=heartbeat_thread,
            lease_renewer=lease_renewer,
            mq_producer=mq_producer,
            local_db=local_db,
            sio_client=sio_client,
        )


if __name__ == "__main__":
    main()
