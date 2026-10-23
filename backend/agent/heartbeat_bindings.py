"""Heartbeat thread wiring extracted from ``main`` (#736).

Builds the host heartbeat with capacity callbacks and a late-bound recovery
reconnect hook (filled after ``build_execute_recovery_actions``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional, Set

from .artifact_uploader import ArtifactUploader
from .heartbeat_thread import HeartbeatThread
from .device_return import INIT_CHECK_CAPABILITY_KEY, ReturnDebtConsumer
from .log_archiver import collect_archive_heartbeat_metrics
from .pipeline_engine import PipelineEngine
from .recovery_executor import trigger_recovery_sync_on_device_reconnect
from .scan_runner import ScanRunner

try:
    from .version_info import read_artifact_digest
except ImportError:  # pragma: no cover - flat deploy layout
    from agent.version_info import read_artifact_digest


@dataclass
class RecoveryActionsSlot:
    """Late-bound ``execute_recovery_actions`` used by device-reconnect hook."""

    value: Optional[Callable[..., Any]] = None


def build_active_count_getters(
    lock: threading.Lock,
    job_ids: Set[int],
    device_ids: Set[int],
) -> tuple[Callable[[], int], Callable[[], int]]:
    """Thread-safe active job / device counters for heartbeat capacity."""

    def get_active_job_count() -> int:
        with lock:
            return len(job_ids)

    def get_active_device_count() -> int:
        with lock:
            return len(device_ids)

    return get_active_job_count, get_active_device_count


def build_heartbeat_thread(
    *,
    api_url: str,
    host_id: str,
    adb_path: str,
    mount_points: Any,
    host_info: Any,
    poll_interval: float,
    sio_client: Any,
    script_registry: Any,
    local_db: Any,
    mq_producer: Any,
    agent_instance_id: str,
    boot_id: str,
    agent_version: str,
    agent_code_revision: str,
    active_jobs_lock: threading.Lock,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    recovery_actions_slot: RecoveryActionsSlot,
) -> HeartbeatThread:
    """Construct (unstarted) HeartbeatThread with outbox / recovery callbacks."""
    get_active_job_count, get_active_device_count = build_active_count_getters(
        active_jobs_lock, active_job_ids, active_device_ids
    )

    def _get_scheduler():
        # The existing late-bound concurrency_snapshot is a bound method of
        # the host-global scheduler (host_control_plane.py). Never create a
        # second scheduler; the consumer refuses work until this owner exists.
        return getattr(getattr(heartbeat, "_get_operation_stats", None), "__self__", None)

    def _device_active(device_id: int) -> bool:
        with active_jobs_lock:
            if device_id in active_device_ids:
                return True
        # Crash recovery can still own a device before its worker is rebound.
        return any(row.get("device_id") == device_id for row in local_db.get_active_jobs())

    local_db.set_state(INIT_CHECK_CAPABILITY_KEY, "0")
    debt_consumer = ReturnDebtConsumer(api_url=api_url, adb_path=adb_path,
        script_registry=script_registry, local_db=local_db,
        get_scheduler=_get_scheduler, is_active=_device_active, engine_factory=PipelineEngine)

    def _on_devices_reconnected(serials: Any) -> bool:
        debt_consumer.reconnected(serials)
        execute = recovery_actions_slot.value
        if execute is None:
            return False
        return bool(
            trigger_recovery_sync_on_device_reconnect(
                reconnected_serials=serials,
                local_db=local_db,
                api_url=api_url,
                host_id=host_id,
                agent_instance_id=agent_instance_id,
                boot_id=boot_id,
                execute_actions=execute,
            )
        )

    heartbeat = HeartbeatThread(
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
        get_active_job_count=get_active_job_count,
        get_active_device_count=get_active_device_count,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        agent_version=agent_version,
        agent_code_revision=agent_code_revision,
        agent_artifact_digest=lambda: read_artifact_digest(),
        get_outbox_counts=lambda: {
            "terminal_outbox_pending": local_db.count_pending_terminals(),
            "log_signal_outbox_pending": local_db.count_pending_log_signals(),
            # #302: 死信总量随心跳上报（历史累计，跨 Agent 重启保留）。
            "log_signal_dead_letter_total": local_db.count_log_signal_dead_letters(),
            # #762/#742: 终态 outbox 死信行数（distinct 卡死行口径；事件计数见
            # drainer snapshot 的 conflicts_retained_total，勿当积压 gauge）。
            "terminal_outbox_dead_letter_total": local_db.count_terminal_dead_letters(),
            # #739 面②/#2188 D 步：分片登记失败（半交付）进程级累计，重启清零。
            "scan_shard_register_failure_total": ScanRunner.shard_register_failure_total(),
            # #3217：crash artifact 投递的提交数与三路丢失（进程级累计，重启清零）。
            **ArtifactUploader.instance().heartbeat_counts(),
        },
        # ADR-0025 Sprint 2: 上报归档指标到 extra['archive']（禁用时回调返回 None）
        get_archive_metrics=collect_archive_heartbeat_metrics,
        on_devices_reconnected=_on_devices_reconnected,
        # ADR-0026 P2-2: apply server log_rate_limit hint to SocketIO batcher
        on_log_rate_limit=mq_producer.set_log_rate_limit,
        on_return_state=debt_consumer.receive,
    )
    return heartbeat
