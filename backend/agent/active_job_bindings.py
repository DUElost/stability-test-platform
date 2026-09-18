"""Active-job occupancy bindings extracted from ``main`` (#736).

Owns lease-lost callback wiring and register/deregister closures that keep
in-memory occupancy sets, fencing tokens, and local SQLite active_job rows in
sync. ``job_runner_state`` is late-bound via ``JobRunnerStateSlot`` (created
after LeaseRenewer starts, same order as the former nested closures).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set

from .recovery_executor import _cleanup_after_job_exit, handle_lease_lost


@dataclass
class JobRunnerStateSlot:
    """Mutable cell so lease-lost can see JobRunnerState after it is constructed."""

    value: Any = None


@dataclass(frozen=True)
class ActiveJobOccupancy:
    """Shared in-memory occupancy sets (module globals in ``main``)."""

    lock: Any
    job_ids: Set[int]
    device_ids: Set[int]
    job_tokens: Dict[int, str]
    device_owner: Dict[int, int]


def build_on_lease_lost(
    *,
    occupancy: ActiveJobOccupancy,
    job_runner_slot: JobRunnerStateSlot,
    coordinator: Any,
    local_db: Any,
) -> Callable[[int, Optional[int]], None]:
    """ADR-0019 Phase 3b: lease-lost → abort + occupancy cleanup (#799)."""

    def on_lease_lost(jid: int, device_id: Optional[int]) -> None:
        # #799: 顺序与占位语义见 handle_lease_lost——先杀在跑脚本（换 hosting
        # 前必须有「本机已停手」的硬保证），设备占位保留到 worker 真正退出。
        handle_lease_lost(
            job_id=jid,
            device_id=device_id,
            job_runner_state=job_runner_slot.value,
            coordinator=coordinator,
            active_jobs_lock=occupancy.lock,
            active_job_ids=occupancy.job_ids,
            active_device_ids=occupancy.device_ids,
            active_job_tokens=occupancy.job_tokens,
            active_device_owner=occupancy.device_owner,
            local_db=local_db,
        )

    return on_lease_lost


def build_register_active_job(
    *,
    occupancy: ActiveJobOccupancy,
    lease_renewer: Any,
    local_db: Any,
) -> Callable[..., None]:
    """ADR-0019 Phase 2b/3b: claim-side occupancy + fencing + SQLite row."""

    def register_active_job(
        jid: int,
        fencing_token: str = "",
        device_id: Optional[int] = None,
        device_serial: str = "",
        local_worker_token: str = "",
    ) -> None:
        effective_worker_token = local_worker_token or fencing_token
        with occupancy.lock:
            occupancy.job_ids.add(jid)
            occupancy.job_tokens[jid] = effective_worker_token
            if device_id is not None:
                occupancy.device_ids.add(device_id)  # Phase 3b: 注册时同步占位 device
                occupancy.device_owner[device_id] = jid
        if fencing_token:
            lease_renewer.set_fencing_token(
                jid,
                fencing_token,
                device_id,
                effective_worker_token,
            )
        if device_id is not None:
            local_db.save_active_job(jid, device_id, fencing_token, device_serial)

    return register_active_job


def build_deregister_active_job(
    *,
    occupancy: ActiveJobOccupancy,
    lease_renewer: Any,
    local_db: Any,
) -> Callable[..., None]:
    """Worker exit path → ``_cleanup_after_job_exit``."""

    def deregister_active_job(
        jid: int,
        fencing_token: str = "",
        local_worker_token: str = "",
    ) -> None:
        _cleanup_after_job_exit(
            job_id=jid,
            fencing_token=fencing_token,
            local_worker_token=local_worker_token,
            active_jobs_lock=occupancy.lock,
            active_job_ids=occupancy.job_ids,
            active_device_ids=occupancy.device_ids,
            active_job_tokens=occupancy.job_tokens,
            active_device_owner=occupancy.device_owner,
            lease_renewer=lease_renewer,
            local_db=local_db,
        )

    return deregister_active_job
