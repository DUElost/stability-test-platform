import logging
import os
import sys
import threading
from typing import Dict, Set

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
    from agent.bootstrap_subsystems import start_disk_and_watcher_subsystems
    from agent.startup_identity import bootstrap_process_identity
    from agent.control_handler import ControlHandlerDeps, build_control_handler
    from agent.agent_loop import run_agent_loop
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
    from agent.host_control_plane import start_host_control_plane
    from agent.job_runtime import start_job_runtime
    from agent.config import ensure_dirs
    from agent.job_runner import run_task_wrapper
    from agent.mq.producer import StepTraceWriter
else:
    from .adb_wrapper import AdbWrapper
    from .bootstrap_subsystems import start_disk_and_watcher_subsystems
    from .startup_identity import bootstrap_process_identity
    from .control_handler import ControlHandlerDeps, build_control_handler
    from .agent_loop import run_agent_loop
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
    from .host_control_plane import start_host_control_plane
    from .job_runtime import start_job_runtime
    from .config import ensure_dirs
    from .job_runner import run_task_wrapper
    from .mq.producer import StepTraceWriter
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

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

    plane = start_host_control_plane(
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        agent_secret=agent_secret,
        local_db=local_db,
        heartbeat_thread=heartbeat_thread,
        control_deps=control_deps,
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        active_device_ids=_active_device_ids,
        active_job_tokens=_active_job_tokens,
        active_device_owner=_active_device_owner,
        lock_renewal_stop_event=_lock_renewal_stop_event,
    )
    operation_scheduler = plane.operation_scheduler
    coordinator = plane.coordinator
    job_runner_slot = plane.job_runner_slot
    lease_renewer = plane.lease_renewer
    _register_active_job = plane.register_active_job
    _deregister_active_job = plane.deregister_active_job

    # 真实 control handler 在 deps 就绪后注册；回放启动窗口暂存命令（P2-2a）
    sio_client.set_control_handler(_handle_control)
    replay_early_control_commands(early_control_queue, _handle_control)

    runtime = start_job_runtime(
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        agent_secret=agent_secret,
        local_db=local_db,
        lease_renewer=lease_renewer,
        register_active_job=_register_active_job,
        deregister_active_job=_deregister_active_job,
        job_runner_slot=job_runner_slot,
        recovery_actions_slot=recovery_actions_slot,
        control_deps=control_deps,
        coordinator=coordinator,
        operation_scheduler=operation_scheduler,
        adb=adb,
        mq_producer=mq_producer,
        script_registry=script_registry,
        patrol_checkpoint_store=patrol_checkpoint_store,
        run_task_wrapper=run_task_wrapper,
        active_jobs_lock=_active_jobs_lock,
        active_job_ids=_active_job_ids,
        active_device_ids=_active_device_ids,
        active_job_tokens=_active_job_tokens,
        active_device_owner=_active_device_owner,
        device_id_register=_register_active_device,
        device_id_deregister=_deregister_active_device,
        watcher_globally_enabled=STP_WATCHER_ENABLED,
        watcher_plan_default=STP_WATCHER_PLAN_DEFAULT,
    )
    run_agent_loop(
        api_url=api_url,
        poll_interval=poll_interval,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        plane=plane,
        runtime=runtime,
        heartbeat_thread=heartbeat_thread,
        adb=adb,
        mq_producer=mq_producer,
        script_registry=script_registry,
        patrol_checkpoint_store=patrol_checkpoint_store,
        run_task_wrapper=run_task_wrapper,
        log_signal_drainer=log_signal_drainer,
        local_db=local_db,
        sio_client=sio_client,
    )


if __name__ == "__main__":
    main()
