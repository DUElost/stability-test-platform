"""PlanRun / Job 查询与设备可达性判定（#1519 从 api.routes 下沉）。

Why: services 层（ai_assistant.plan_run_ops 等）需要这些判定，原实现住在
``api.routes.plan_runs`` 的私有符号里——services 反向 import api.routes，
分层方向被穿透（issue #1519：函数内局部 import 正是为规避循环导入的痕迹）。
下沉后依赖方向恢复 services ← api：路由层与本模块的消费方都从这里取。
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models.enums import DeviceStatus, HostStatus, JobStatus
from backend.models.host import Device
from backend.models.job import JobInstance

#: manual retry / manual exit 允许的作业状态（原 api.routes.plan_runs 常量）
MANUAL_ACTION_JOB_STATUSES = {JobStatus.RUNNING.value}

_ADB_LINK_ERROR_STATES = frozenset({"offline", "unknown", "unauthorized"})


def load_job_in_run(db: Session, run_id: int, job_id: int) -> JobInstance:
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")
    return job


def derive_device_link_status(
    device: Optional[Device],
    host_status: Optional[str],
) -> str:
    """设备 ADB / Host 可达性 — 与 Job 执行状态正交。

    这是断连语义的**唯一事实源**;``device_currently_disconnected`` 由它派生。
    """
    if device is None:
        return "unknown"
    if host_status == HostStatus.OFFLINE.value:
        return "host_offline"
    if (device.adb_state or "device").lower() in _ADB_LINK_ERROR_STATES:
        return "adb_error"
    if not device.adb_connected or device.status == DeviceStatus.OFFLINE.value:
        return "offline"
    return "online"


def device_currently_disconnected(
    device: Optional[Device],
    host_status: Optional[str],
) -> bool:
    """manual retry / ui_status 的断连门禁。

    Why: 必须与 ``derive_device_link_status`` 同源。两处各自判 adb_state 时,
         `unauthorized` 会被前者判成 adb_error、被后者放行,导致抽屉同时渲染
         「设备 ADB 不可达」警告条和「立即重试」按钮,且 POST 真的执行。
         当前 Agent 的 `collect_device_info` 会在 adb_state≠device 时一并把
         adb_connected 置 False(device_discovery.py:122),所以线上暂被兜住 ——
         但那是隐式字段配对约定,不该由两份判定规则各自假设。
    """
    if device is None:
        return False
    return derive_device_link_status(device, host_status) != "online"
