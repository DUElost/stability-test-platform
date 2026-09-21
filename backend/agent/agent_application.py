"""Thin AgentApplication shell extracted from ``main`` (#736).

Owns process-local active-job occupancy state and lifecycle phases that wire
existing extract modules:

- ``initialize`` — identity / stores / control handler build
- ``start_background_tasks`` — heartbeat + host control plane
- ``register_handlers`` — SocketIO control + early-command replay
- ``start_job_runtime`` — pool / recovery plane
- ``run_loop`` — claim loop; ``graceful_shutdown`` runs in its ``finally``
  via ``shutdown_agent_runtime`` (not inlined here)
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Set

from .adb_wrapper import AdbWrapper
from .agent_loop import run_agent_loop
from .bootstrap_subsystems import start_disk_and_watcher_subsystems
from .config import ensure_dirs
from .control_handler import ControlHandlerDeps, build_control_handler
from .heartbeat_bindings import RecoveryActionsSlot, build_heartbeat_thread
from .host_control_plane import HostControlPlane, start_host_control_plane
from .job_runner import run_task_wrapper
from .job_runtime import JobRuntime, start_job_runtime
from .local_runtime import (
    connect_socketio_with_early_control,
    initialize_local_stores,
    replay_early_control_commands,
)
from .mq.producer import StepTraceWriter
from .startup_guards import (
    check_agent_version,
    enforce_single_instance,
    ensure_adb_server_on_startup,
)
from .startup_identity import bootstrap_process_identity

# Device Log Watcher feature flag —— 全局 STP_WATCHER_ENABLED 或 Plan 默认开启。
# Plan 执行默认开启 watcher（STP_WATCHER_PLAN_DEFAULT=true）时，即使全局 env=false
# 也会 configure 子系统，并在 claim Plan job 时启动 JobSession。
STP_WATCHER_ENABLED = os.getenv("STP_WATCHER_ENABLED", "true").lower() == "true"
STP_WATCHER_PLAN_DEFAULT = os.getenv("STP_WATCHER_PLAN_DEFAULT", "true").lower() == "true"


@dataclass
class _BootContext:
    """Mutable bag passed between AgentApplication lifecycle phases."""

    api_url: str
    host_id: str
    agent_instance_id: str
    boot_id: str
    agent_secret: str
    adb_path: str
    poll_interval: float
    mount_points: Any
    host_info: Any
    agent_pkg_version: str
    agent_code_revision: str
    adb: Any
    sio_client: Any
    early_control_queue: List[Any]
    local_db: Any
    patrol_checkpoint_store: Any
    script_registry: Any
    log_signal_drainer: Any
    mq_producer: Any
    control_deps: ControlHandlerDeps
    handle_control: Any
    recovery_actions_slot: RecoveryActionsSlot
    heartbeat_thread: Any = None
    plane: HostControlPlane | None = None
    runtime: JobRuntime | None = None


class AgentApplication:
    """Process-scoped Agent runtime: occupancy state + phased startup → loop."""

    def __init__(self) -> None:
        # 全局活跃 Job 追踪（语义上存的就是 job_instance.id）
        self.active_job_ids: Set[int] = set()
        self.active_device_ids: Set[int] = set()  # per-device concurrency guard
        # device_id → 占用它的 active job_id（与 active_device_ids 同锁、同步增删）。
        # 占位清理只允许归属 job 本人执行：迟到 worker 的 release 不得清掉继任 job 已
        # 重占的占位（#1203，#1006 补偿的 None 分支无法区分「本 job 残留」与「已清 +
        # 继任重占」两种 token 消失态）。
        self.active_device_owner: Dict[int, int] = {}
        self.active_job_tokens: Dict[int, str] = {}
        self.active_jobs_lock = threading.Lock()
        self.lock_renewal_stop_event = threading.Event()

    def _register_active_device(self, did: int) -> None:
        with self.active_jobs_lock:
            self.active_device_ids.add(did)

    def _deregister_active_device(self, did: int) -> None:
        with self.active_jobs_lock:
            self.active_device_ids.discard(did)

    def initialize(self) -> _BootContext:
        """Identity, local stores, control-handler build (not yet registered)."""
        api_url = os.getenv("API_URL", "http://127.0.0.1:8000")
        ensure_dirs()

        identity = bootstrap_process_identity(api_url)
        adb = AdbWrapper(adb_path=identity.adb_path)
        ensure_adb_server_on_startup(identity.adb_path)
        sio_client, early_control_queue = connect_socketio_with_early_control(
            api_url, identity.host_id, identity.agent_secret
        )
        stores = initialize_local_stores(
            api_url=api_url, agent_secret=identity.agent_secret
        )
        log_signal_drainer = start_disk_and_watcher_subsystems(
            local_db=stores.local_db,
            api_url=api_url,
            agent_secret=identity.agent_secret,
            host_id=str(identity.host_id),
            agent_instance_id=identity.agent_instance_id,
            adb=adb,
            adb_path=identity.adb_path,
            sio_client=sio_client,
        )

        # Step trace local writer (Redis XADD removed in Phase 4; HTTP via uploader)
        mq_producer = StepTraceWriter(
            "", identity.host_id, local_db=stores.local_db
        )
        # ADR-0026 P2-2: wire pipeline _MQStepLogger → SocketIO batched step_log
        mq_producer.bind_sio_client(sio_client)

        control_deps = ControlHandlerDeps()
        handle_control = build_control_handler(
            deps=control_deps,
            mq_producer=mq_producer,
            host_id=str(identity.host_id),
            api_url=api_url,
            agent_secret=identity.agent_secret,
            adb_path=identity.adb_path,
            local_db=stores.local_db,
            active_jobs_lock=self.active_jobs_lock,
            active_job_ids=self.active_job_ids,
            ensure_adb_server=ensure_adb_server_on_startup,
        )

        return _BootContext(
            api_url=api_url,
            host_id=identity.host_id,
            agent_instance_id=identity.agent_instance_id,
            boot_id=identity.boot_id,
            agent_secret=identity.agent_secret,
            adb_path=identity.adb_path,
            poll_interval=identity.poll_interval,
            mount_points=identity.mount_points,
            host_info=identity.host_info,
            agent_pkg_version=identity.agent_version,
            agent_code_revision=identity.agent_code_revision,
            adb=adb,
            sio_client=sio_client,
            early_control_queue=early_control_queue,
            local_db=stores.local_db,
            patrol_checkpoint_store=stores.patrol_checkpoint_store,
            script_registry=stores.script_registry,
            log_signal_drainer=log_signal_drainer,
            mq_producer=mq_producer,
            control_deps=control_deps,
            handle_control=handle_control,
            recovery_actions_slot=RecoveryActionsSlot(),
        )

    def start_background_tasks(self, ctx: _BootContext) -> None:
        """Version gate, heartbeat, and host control plane."""
        check_agent_version(
            ctx.api_url, ctx.host_id, ctx.mount_points, ctx.host_info
        )
        heartbeat_thread = build_heartbeat_thread(
            api_url=ctx.api_url,
            host_id=ctx.host_id,
            adb_path=ctx.adb_path,
            mount_points=ctx.mount_points,
            host_info=ctx.host_info,
            poll_interval=ctx.poll_interval,
            sio_client=ctx.sio_client,
            script_registry=ctx.script_registry,
            local_db=ctx.local_db,
            mq_producer=ctx.mq_producer,
            agent_instance_id=ctx.agent_instance_id,
            boot_id=ctx.boot_id,
            agent_version=ctx.agent_pkg_version,
            agent_code_revision=ctx.agent_code_revision,
            active_jobs_lock=self.active_jobs_lock,
            active_job_ids=self.active_job_ids,
            active_device_ids=self.active_device_ids,
            recovery_actions_slot=ctx.recovery_actions_slot,
        )
        heartbeat_thread.start()
        ctx.heartbeat_thread = heartbeat_thread
        ctx.plane = start_host_control_plane(
            api_url=ctx.api_url,
            host_id=ctx.host_id,
            agent_instance_id=ctx.agent_instance_id,
            agent_secret=ctx.agent_secret,
            local_db=ctx.local_db,
            heartbeat_thread=heartbeat_thread,
            control_deps=ctx.control_deps,
            active_jobs_lock=self.active_jobs_lock,
            active_job_ids=self.active_job_ids,
            active_device_ids=self.active_device_ids,
            active_job_tokens=self.active_job_tokens,
            active_device_owner=self.active_device_owner,
            lock_renewal_stop_event=self.lock_renewal_stop_event,
        )

    def register_handlers(self, ctx: _BootContext) -> None:
        """Bind real control handler and replay the early-control window (P2-2a)."""
        ctx.sio_client.set_control_handler(ctx.handle_control)
        replay_early_control_commands(ctx.early_control_queue, ctx.handle_control)

    def start_job_plane(self, ctx: _BootContext) -> None:
        """Outbox / recovery / executor plane (after handlers are live)."""
        assert ctx.plane is not None
        ctx.runtime = start_job_runtime(
            api_url=ctx.api_url,
            host_id=ctx.host_id,
            agent_instance_id=ctx.agent_instance_id,
            boot_id=ctx.boot_id,
            agent_secret=ctx.agent_secret,
            local_db=ctx.local_db,
            lease_renewer=ctx.plane.lease_renewer,
            register_active_job=ctx.plane.register_active_job,
            deregister_active_job=ctx.plane.deregister_active_job,
            job_runner_slot=ctx.plane.job_runner_slot,
            recovery_actions_slot=ctx.recovery_actions_slot,
            control_deps=ctx.control_deps,
            coordinator=ctx.plane.coordinator,
            operation_scheduler=ctx.plane.operation_scheduler,
            adb=ctx.adb,
            mq_producer=ctx.mq_producer,
            script_registry=ctx.script_registry,
            patrol_checkpoint_store=ctx.patrol_checkpoint_store,
            run_task_wrapper=run_task_wrapper,
            active_jobs_lock=self.active_jobs_lock,
            active_job_ids=self.active_job_ids,
            active_device_ids=self.active_device_ids,
            active_job_tokens=self.active_job_tokens,
            active_device_owner=self.active_device_owner,
            device_id_register=self._register_active_device,
            device_id_deregister=self._deregister_active_device,
            watcher_globally_enabled=STP_WATCHER_ENABLED,
            watcher_plan_default=STP_WATCHER_PLAN_DEFAULT,
        )

    def run_loop(self, ctx: _BootContext) -> None:
        """Claim loop; graceful_shutdown is the ``finally`` of ``run_agent_loop``."""
        assert ctx.plane is not None and ctx.runtime is not None
        assert ctx.heartbeat_thread is not None
        run_agent_loop(
            api_url=ctx.api_url,
            poll_interval=ctx.poll_interval,
            host_id=ctx.host_id,
            agent_instance_id=ctx.agent_instance_id,
            plane=ctx.plane,
            runtime=ctx.runtime,
            heartbeat_thread=ctx.heartbeat_thread,
            adb=ctx.adb,
            mq_producer=ctx.mq_producer,
            script_registry=ctx.script_registry,
            patrol_checkpoint_store=ctx.patrol_checkpoint_store,
            run_task_wrapper=run_task_wrapper,
            log_signal_drainer=ctx.log_signal_drainer,
            local_db=ctx.local_db,
            sio_client=ctx.sio_client,
        )

    def run(self) -> None:
        """Phased bootstrap → claim loop (blocks until shutdown)."""
        ctx = self.initialize()
        self.start_background_tasks(ctx)
        self.register_handlers(ctx)
        self.start_job_plane(ctx)
        self.run_loop(ctx)


def run_agent_application() -> None:
    """Entry used by ``main`` — one process, one ``AgentApplication``."""
    # #2961：单实例守卫必须早于 AgentApplication 的任何动作——版本门控心跳、
    # HOST_ID 注册、设备上报都在它内部，第二个进程一旦走到那里就开始叠加。
    enforce_single_instance()
    AgentApplication().run()
