"""ADR-0067 D3 — 项目日志预设应用判定与 #2649 有效设备集划分（单一事实源）。

本模块为叶服务（不依赖任何其他 ``backend.services.*``），供
``plan_dispatcher_core``、``plan_dispatcher_sync``、``plan_confirmation``、
``plan_parameter_projection`` 与 ``backend/api/routes/plans.py`` 共用。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.schemas.project_log_profile import normalize_log_profile
from backend.core.device_serial import is_placeholder_serial
from backend.models.host import Device, Host
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel

TARGET_SCRIPT_NAME = "log_prepare"

STATUS_APPLIED = "applied"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_SKIPPED_UNBOUND_PLAN = "skipped_unbound_plan"
STATUS_SKIPPED_PROJECT_INACTIVE = "skipped_project_inactive"
STATUS_SKIPPED_EMPTY_PROFILE = "skipped_empty_profile"
STATUS_SKIPPED_DEVICE_MISMATCH = "skipped_device_mismatch"
STATUS_PENDING_DEVICES = "pending_devices"

DISPATCH_STATUSES = frozenset({
    STATUS_APPLIED,
    STATUS_NOT_APPLICABLE,
    STATUS_SKIPPED_UNBOUND_PLAN,
    STATUS_SKIPPED_PROJECT_INACTIVE,
    STATUS_SKIPPED_EMPTY_PROFILE,
    STATUS_SKIPPED_DEVICE_MISMATCH,
})

ALL_STATUSES = DISPATCH_STATUSES | {STATUS_PENDING_DEVICES}


@dataclass(frozen=True)
class DispatchDevicePartition:
    """#2649 有效判定设备集拆分（preview 与 prepare 共用同一口径，ADR-0067 D3.1）。"""

    requested_device_ids: list[int]
    evaluated_device_ids: list[int]
    excluded_serial_conflict_device_ids: list[int]
    serial_conflicts: list[dict[str, Any]]


def _step_attr(step: Any, name: str) -> Any:
    if isinstance(step, Mapping):
        return step.get(name)
    return getattr(step, name, None)


def has_enabled_log_prepare_step(steps: Iterable[Any]) -> bool:
    """Plan 是否至少包含一个启用中的 ``script:log_prepare`` 步骤。"""
    for step in steps:
        if _step_attr(step, "enabled") is False:
            continue
        if _step_attr(step, "script_name") == TARGET_SCRIPT_NAME:
            return True
    return False


def extract_applied_log_profile(
    project_log_profile: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    """仅当 ``status == 'applied'`` 且存在非空规范化键值时返回第 3 层预设。"""
    if not isinstance(project_log_profile, Mapping):
        return None
    if project_log_profile.get("status") != STATUS_APPLIED:
        return None
    raw = project_log_profile.get("effective_profile")
    if not isinstance(raw, Mapping):
        return None
    cleaned = {k: deepcopy(v) for k, v in raw.items() if v is not None}
    return cleaned or None


def extract_preview_log_profile(
    project_log_profile: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    """L1 静态投影（含 ``pending_devices`` 预估）与 preview 共用的第 3 层预设提取器。"""
    if not isinstance(project_log_profile, Mapping):
        return None
    if project_log_profile.get("status") not in {STATUS_APPLIED, STATUS_PENDING_DEVICES}:
        return None
    raw = project_log_profile.get("effective_profile")
    if not isinstance(raw, Mapping):
        return None
    cleaned = {k: deepcopy(v) for k, v in raw.items() if v is not None}
    return cleaned or None


def partition_serial_conflict_devices(
    db: Session,
    requested_device_ids: list[int],
    *,
    preclassified_unavailable: Optional[list[dict[str, Any]]] = None,
) -> DispatchDevicePartition:
    """将请求设备集按 #2649 ``serial_conflict`` 拆分为有效判定集与剔除集（ADR-0067 D3.1）。

    - ``prepare_plan_run`` 可传入 ``_classify_dispatch_devices_sync`` 已判定的
      ``unavailable``，避免二次扫表；
    - ``preview_plan_dispatch_sync`` 无预先分类时直接按完全一致的占位序列号规则
      （排除退役主机/退役设备短路，与 ``_classify_dispatch_devices_sync`` 同源）识别。
    """
    requested = list(requested_device_ids)
    unique_ids = sorted(set(requested))
    if not unique_ids:
        return DispatchDevicePartition(
            requested_device_ids=requested,
            evaluated_device_ids=[],
            excluded_serial_conflict_device_ids=[],
            serial_conflicts=[],
        )

    if preclassified_unavailable is not None:
        conflicts = [
            dict(entry)
            for entry in preclassified_unavailable
            if entry.get("reason") == "serial_conflict"
        ]
    else:
        rows = db.execute(
            select(
                Device.id,
                Device.host_id,
                Device.serial,
                Device.retired_at.label("device_retired_at"),
                Host.retired_at.label("host_retired_at"),
            )
            .select_from(Device)
            .outerjoin(Host, Device.host_id == Host.id)
            .where(Device.id.in_(unique_ids))
        ).all()
        by_id = {row.id: row for row in rows}
        conflicts = []
        for did in unique_ids:
            snap = by_id.get(did)
            if snap is None or snap.host_id is None:
                continue
            if snap.host_retired_at is not None or snap.device_retired_at is not None:
                continue
            if is_placeholder_serial(snap.serial):
                conflicts.append({
                    "id": did,
                    "reason": "serial_conflict",
                    "serial": snap.serial,
                    "host_id": snap.host_id,
                })

    excluded_set = {int(entry["id"]) for entry in conflicts}
    excluded_ids = sorted(excluded_set)
    evaluated_ids = [did for did in unique_ids if did not in excluded_set]
    return DispatchDevicePartition(
        requested_device_ids=requested,
        evaluated_device_ids=evaluated_ids,
        excluded_serial_conflict_device_ids=excluded_ids,
        serial_conflicts=conflicts,
    )


def _resolution_payload(
    *,
    status: str,
    project_id: Optional[int],
    project_key: Optional[str],
    log_profile_version: Optional[int],
    effective_profile: Optional[dict[str, Any]],
    evaluated_device_ids: list[int],
    excluded_serial_conflict_device_ids: list[int],
    mismatch_summary: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "project_id": project_id,
        "project_key": project_key,
        "log_profile_version": log_profile_version,
        "effective_profile": deepcopy(effective_profile) if effective_profile else {},
        "evaluated_device_ids": list(evaluated_device_ids),
        "excluded_serial_conflict_device_ids": list(excluded_serial_conflict_device_ids),
        "mismatch_summary": deepcopy(mismatch_summary) if mismatch_summary is not None else None,
    }


def _safe_normalize_profile(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    try:
        return normalize_log_profile(raw)
    except Exception:  # noqa: BLE001 — 防御历史上可能绕过校验的脏列
        return None


def resolve_project_log_profile_static(
    db: Session,
    steps: Iterable[Any],
    *,
    plan_project_id: Optional[int] = None,
    project_key: Optional[str] = None,
) -> dict[str, Any]:
    """无目标设备的静态 L1 投影判定（``GET /plans/{id}/parameter-projection`` 与草稿投影）。

    - 满足条件 1–4 → ``status = 'pending_devices'``，并携带规范化 ``effective_profile``
      供 L1 展示计划基线预估；
    - 草稿传入不存在 / ``SEED`` / ``ARCHIVED`` 的 ``project_key`` → 返回
      ``skipped_project_inactive``，绝不抛出 404/409 中断编辑（ADR-0067 D3.5）。
    """
    normalized_key = project_key.strip() if isinstance(project_key, str) else None
    if normalized_key == "":
        normalized_key = None

    project: Optional[TestProject] = None
    if plan_project_id is not None:
        project = db.get(TestProject, plan_project_id)
    elif normalized_key is not None:
        project = db.execute(
            select(TestProject).where(TestProject.project_key == normalized_key)
        ).scalar_one_or_none()

    resolved_project_id = project.id if project is not None else plan_project_id
    resolved_project_key = project.project_key if project is not None else normalized_key
    resolved_version = int(project.log_profile_version) if project is not None else None

    if not has_enabled_log_prepare_step(steps):
        return _resolution_payload(
            status=STATUS_NOT_APPLICABLE,
            project_id=resolved_project_id,
            project_key=resolved_project_key,
            log_profile_version=resolved_version,
            effective_profile=None,
            evaluated_device_ids=[],
            excluded_serial_conflict_device_ids=[],
        )

    if plan_project_id is None and normalized_key is None:
        return _resolution_payload(
            status=STATUS_SKIPPED_UNBOUND_PLAN,
            project_id=None,
            project_key=None,
            log_profile_version=None,
            effective_profile=None,
            evaluated_device_ids=[],
            excluded_serial_conflict_device_ids=[],
        )

    if (
        project is None
        or project.source != "USER"
        or project.status != "ACTIVE"
    ):
        return _resolution_payload(
            status=STATUS_SKIPPED_PROJECT_INACTIVE,
            project_id=resolved_project_id,
            project_key=resolved_project_key,
            log_profile_version=resolved_version,
            effective_profile=None,
            evaluated_device_ids=[],
            excluded_serial_conflict_device_ids=[],
        )

    cleaned = _safe_normalize_profile(project.log_profile)
    if not cleaned:
        return _resolution_payload(
            status=STATUS_SKIPPED_EMPTY_PROFILE,
            project_id=project.id,
            project_key=project.project_key,
            log_profile_version=int(project.log_profile_version),
            effective_profile=None,
            evaluated_device_ids=[],
            excluded_serial_conflict_device_ids=[],
        )

    return _resolution_payload(
        status=STATUS_PENDING_DEVICES,
        project_id=project.id,
        project_key=project.project_key,
        log_profile_version=int(project.log_profile_version),
        effective_profile=cleaned,
        evaluated_device_ids=[],
        excluded_serial_conflict_device_ids=[],
    )


def resolve_project_log_profile_for_dispatch(
    db: Session,
    plan: Any,
    steps: Iterable[Any],
    *,
    evaluated_device_ids: list[int],
    excluded_serial_conflict_device_ids: list[int],
) -> dict[str, Any]:
    """preview 与 prepare_plan_run 共用的单次 D3.2 五条件判定器（ADR-0067 D3 / D4.3）。

    前置条件：调用方在 ``prepare_plan_run`` 中已通过 ``load_plan_graph_for_confirmation``
    按 ``Plan (FOR SHARE) -> TestProject (FOR SHARE)`` 锁定并将 ``TestProject`` 刷新入
    session identity map；本函数经 ``db.get(TestProject, plan.project_id)`` 复用同一
    已锁定实例，并仅执行一次 ``READ COMMITTED`` 归属查询。
    """
    eval_ids = sorted(set(int(d) for d in evaluated_device_ids))
    excl_ids = sorted(set(int(d) for d in excluded_serial_conflict_device_ids))
    plan_project_id = getattr(plan, "project_id", None)

    project: Optional[TestProject] = (
        db.get(TestProject, plan_project_id)
        if plan_project_id is not None
        else None
    )
    resolved_project_key = project.project_key if project is not None else None
    resolved_version = int(project.log_profile_version) if project is not None else None

    if not has_enabled_log_prepare_step(steps):
        return _resolution_payload(
            status=STATUS_NOT_APPLICABLE,
            project_id=plan_project_id,
            project_key=resolved_project_key,
            log_profile_version=resolved_version,
            effective_profile=None,
            evaluated_device_ids=eval_ids,
            excluded_serial_conflict_device_ids=excl_ids,
        )

    if plan_project_id is None:
        return _resolution_payload(
            status=STATUS_SKIPPED_UNBOUND_PLAN,
            project_id=None,
            project_key=None,
            log_profile_version=None,
            effective_profile=None,
            evaluated_device_ids=eval_ids,
            excluded_serial_conflict_device_ids=excl_ids,
        )

    if (
        project is None
        or project.source != "USER"
        or project.status != "ACTIVE"
    ):
        return _resolution_payload(
            status=STATUS_SKIPPED_PROJECT_INACTIVE,
            project_id=plan_project_id,
            project_key=resolved_project_key,
            log_profile_version=resolved_version,
            effective_profile=None,
            evaluated_device_ids=eval_ids,
            excluded_serial_conflict_device_ids=excl_ids,
        )

    cleaned = _safe_normalize_profile(project.log_profile)
    if not cleaned:
        return _resolution_payload(
            status=STATUS_SKIPPED_EMPTY_PROFILE,
            project_id=project.id,
            project_key=project.project_key,
            log_profile_version=int(project.log_profile_version),
            effective_profile=None,
            evaluated_device_ids=eval_ids,
            excluded_serial_conflict_device_ids=excl_ids,
        )

    if not eval_ids:
        return _resolution_payload(
            status=STATUS_SKIPPED_DEVICE_MISMATCH,
            project_id=project.id,
            project_key=project.project_key,
            log_profile_version=int(project.log_profile_version),
            effective_profile=None,
            evaluated_device_ids=eval_ids,
            excluded_serial_conflict_device_ids=excl_ids,
            mismatch_summary={
                "unmapped_count": 0,
                "other_project_keys": [],
                "other_project_device_count": 0,
            },
        )

    # 单次 READ COMMITTED 读快照（ADR-0067 D4.3）：
    # Device.model ⋈ ProjectModel(is_active=TRUE) ⋈ TestProject
    rows = db.execute(
        select(
            Device.id,
            ProjectModel.project_id.label("mapped_project_id"),
            TestProject.project_key.label("mapped_project_key"),
        )
        .select_from(Device)
        .outerjoin(
            ProjectModel,
            (ProjectModel.match_value == Device.model)
            & (ProjectModel.is_active.is_(True)),
        )
        .outerjoin(TestProject, TestProject.id == ProjectModel.project_id)
        .where(Device.id.in_(eval_ids))
    ).all()

    mapped_by_device: dict[int, set[tuple[int, Optional[str]]]] = {}
    for row in rows:
        bucket = mapped_by_device.setdefault(int(row.id), set())
        if row.mapped_project_id is not None:
            bucket.add((int(row.mapped_project_id), row.mapped_project_key))

    unmapped_count = 0
    other_project_device_count = 0
    other_project_keys: set[str] = set()
    all_match = True

    for did in eval_ids:
        mapped = mapped_by_device.get(did)
        if not mapped:
            unmapped_count += 1
            all_match = False
            continue
        mapped_ids = {pid for pid, _ in mapped}
        if mapped_ids == {project.id}:
            continue
        all_match = False
        other_project_device_count += 1
        for pid, pkey in mapped:
            if pid != project.id:
                other_project_keys.add(pkey or f"project:{pid}")

    if all_match:
        return _resolution_payload(
            status=STATUS_APPLIED,
            project_id=project.id,
            project_key=project.project_key,
            log_profile_version=int(project.log_profile_version),
            effective_profile=cleaned,
            evaluated_device_ids=eval_ids,
            excluded_serial_conflict_device_ids=excl_ids,
            mismatch_summary=None,
        )

    return _resolution_payload(
        status=STATUS_SKIPPED_DEVICE_MISMATCH,
        project_id=project.id,
        project_key=project.project_key,
        log_profile_version=int(project.log_profile_version),
        effective_profile=None,
        evaluated_device_ids=eval_ids,
        excluded_serial_conflict_device_ids=excl_ids,
        mismatch_summary={
            "unmapped_count": unmapped_count,
            "other_project_keys": sorted(other_project_keys),
            "other_project_device_count": other_project_device_count,
        },
    )
