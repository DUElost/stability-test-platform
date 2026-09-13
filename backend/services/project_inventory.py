"""项目/Fleet 读侧聚合（#1520 垂直切片第三刀：projects.py 去 0-service）。

覆盖：项目汇总聚合（设备数/在途 run/平台派生/成员型号）、inventory 型号级聚合与
摘要。路由退化为「解析 → 调服务 → 序列化」；本模块**只读**，不写任何状态。

口径（ADR-0029 v2.5 D10 派生）：设备数与平台按「成员型号 ⋈ 设备 model」派生，
型号的归属 = 其活跃成员行；SEED 成员不算映射（与设备页 `?unassigned=true` 同口径）。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.api.schemas.project import InventoryModelOut, InventorySummaryOut
from backend.models.host import Device
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.services.project_mapping import blank_to_none
from backend.services.project_registry import USER_SOURCE

#: 在途 run（与调度容量口径一致）：计入「运行中 run 数」的状态集
_ACTIVE_RUN_STATUSES = ("RUNNING", "QUEUED", "PRECHECK")


def platforms_map(db: Session, project_ids: list[int]) -> dict[int, list[str]]:
    """ADR-0029 P1-B：项目平台从设备派生（distinct(device.platform)）。

    事实层在 device；UNKNOWN（探测失败哨兵）不展示。列表场景一次聚合，
    避免逐项目 N+1。
    """
    if not project_ids:
        return {}
    # v2.5 D10：平台按成员型号派生（device.model ⋈ project_model）
    rows = db.execute(
        select(ProjectModel.project_id, Device.platform)
        .join(Device, Device.model == ProjectModel.match_value)
        .where(
            ProjectModel.project_id.in_(project_ids),
            ProjectModel.is_active.is_(True),
            Device.platform.is_not(None),
        )
        .distinct()
    ).all()
    buckets: dict[int, set[str]] = {}
    for pid, platform in rows:
        if platform and platform != "UNKNOWN":
            buckets.setdefault(pid, set()).add(platform)
    return {pid: sorted(values) for pid, values in buckets.items()}


def summary_rows_for(db: Session, pids: list[int]) -> dict[int, tuple[int, int]]:
    """项目设备数 + 在途 run 数聚合（#644 P2：单项目路径不再跑全表）。"""
    if not pids:
        return {}
    # v2.5 D10：设备数按成员型号派生（device.model ⋈ project_model）
    device_counts = dict(
        db.query(ProjectModel.project_id, func.count(Device.id))
        .join(Device, Device.model == ProjectModel.match_value)
        .filter(
            ProjectModel.project_id.in_(pids),
            ProjectModel.is_active.is_(True),
        )
        .group_by(ProjectModel.project_id)
        .all()
    )
    run_counts = dict(
        db.query(PlanRun.project_id, func.count(PlanRun.id))
        .filter(PlanRun.project_id.in_(pids), PlanRun.status.in_(_ACTIVE_RUN_STATUSES))
        .group_by(PlanRun.project_id)
        .all()
    )
    return {
        pid: (device_counts.get(pid, 0), run_counts.get(pid, 0))
        for pid in pids
    }


def summary_rows(db: Session) -> dict[int, tuple[int, int]]:
    """全量项目聚合（列表页用）；单项目详情走 summary_rows_for 的过滤版。"""
    pids = [pid for (pid,) in db.query(TestProject.id).all()]
    return summary_rows_for(db, pids)


def aggregate_inventory(
    rows: list[tuple[Optional[str], Optional[str]]],
    *,
    model_to_projects: dict[Optional[str], set[str]],
) -> list[InventoryModelOut]:
    """型号级聚合（ADR-0029 v2.5 D10 派生）。

    归属是型号级函数（device.model ⋈ project_model 活跃成员行）：
    mapped 型号全部设备归属、unmapped 全部未归属——unassigned_device_count
    不再逐设备累计（无例外，pinned 已废）。
    """
    buckets: dict[Optional[str], dict] = {}
    for raw_model, raw_platform in rows:
        model = blank_to_none(raw_model)
        bucket = buckets.get(model)
        if bucket is None:
            bucket = {"device_count": 0, "platforms": set()}
            buckets[model] = bucket
        bucket["device_count"] += 1
        platform = blank_to_none(raw_platform)
        if platform:
            bucket["platforms"].add(platform)
    items = [
        InventoryModelOut(
            model=model,
            device_count=bucket["device_count"],
            platforms=sorted(bucket["platforms"]),
            mapped_project_keys=sorted(model_to_projects.get(model, set())),
            unassigned_device_count=(
                bucket["device_count"]
                if not model_to_projects.get(model)
                else 0
            ),
        )
        for model, bucket in buckets.items()
    ]
    items.sort(key=lambda item: (-item.device_count, item.model or ""))
    return items


def rule_values_for_project(db: Session, project_id: int) -> list[str]:
    """项目活跃成员型号 → match_models 兼容列表（对外 API 契约）。

    唯一读侧派生来源（ProjectModel 活跃行）。
    """
    rows = db.execute(
        select(ProjectModel.match_value)
        .where(
            ProjectModel.project_id == project_id,
            ProjectModel.is_active.is_(True),
        )
        .order_by(ProjectModel.match_value)
    ).scalars().all()
    return list(rows)


def model_to_projects(db: Session) -> dict[Optional[str], set[str]]:
    """活跃成员行全量：model → {project_key}（v2.5 派生读预取）。"""
    mapping: dict[Optional[str], set[str]] = {}
    rows = db.execute(
        select(ProjectModel.match_value, TestProject.project_key)
        .join(TestProject, TestProject.id == ProjectModel.project_id)
        .where(
            ProjectModel.is_active.is_(True),
            TestProject.source == USER_SOURCE,
        )
    ).all()
    for raw, key in rows:
        model = blank_to_none(raw)
        if model is not None:
            mapping.setdefault(model, set()).add(key)
    return mapping


def load_inventory(db: Session) -> list[InventoryModelOut]:
    rows = (
        db.query(Device.model, Device.platform).all()
    )
    return aggregate_inventory(
        list(rows), model_to_projects=model_to_projects(db),
    )


def inventory_summary(db: Session, items: list[InventoryModelOut]) -> InventorySummaryOut:
    total = sum(item.device_count for item in items)
    user_mapped = sum(item.device_count - item.unassigned_device_count for item in items)
    unmapped_models = [
        item.model for item in items if not item.mapped_project_keys
    ]
    # ADR-0029 v2.5：严格未映射口径——型号无 **USER** 成员行的设备数
    # （SEED 成员不算映射，与 model_to_projects 同口径；设备页
    # ?unassigned=true 一致；型号级归属函数，无逐设备例外）。
    mapped_models = (
        select(ProjectModel.match_value)
        .join(TestProject, TestProject.id == ProjectModel.project_id)
        .where(
            ProjectModel.is_active.is_(True),
            TestProject.source == USER_SOURCE,
        )
    )
    unassigned_devices = db.query(func.count(Device.id)).filter(
        or_(Device.model.is_(None), ~Device.model.in_(mapped_models))
    ).scalar() or 0
    return InventorySummaryOut(
        total_devices=total,
        user_mapped_devices=user_mapped,
        distinct_models=len(items),
        unmapped_models=unmapped_models,
        unassigned_devices=unassigned_devices,
    )
