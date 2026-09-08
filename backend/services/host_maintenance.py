"""主机维护窗口（#960 · R04-F17）。

Why: 热更新只做「发起前一次」的活跃 Job 检查（409 / abort drain / 前端预检），
     检查结束到上传、rsync、重启之间是**无互斥的窗口** —— 期间仍可新派发或
     claim 到该主机，重启会打断刚派下去的作业；并发热更新还共用固定远端 tar
     路径会互相覆盖。本模块给这个窗口一个持久互斥：窗口内派发与 claim 都跳过
     该主机（plan_dispatcher_sync / agent_api claim 两侧都查）。

How to apply: 窗口只存「截止时刻 + 持有者」（host.maintenance_until /
     maintenance_holder）—— 持有进程崩溃时窗口到点自然失效，不需要对账清扫，
     也不会把主机永久钉在维护态；持有者用于并发热更新互相识别（已有非过期
     窗口即拒绝，无论持有者是谁）。
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.host import Host

logger = logging.getLogger(__name__)

# 窗口上限：热更新（建包 + 上传 + rsync + 重启 + 依赖安装）实测分钟级，15 min
# 是宽松上限 —— 只用于给崩溃进程兜底，正常路径由 finally 主动释放。
DEFAULT_TTL_SECONDS = 900
_MAX_TTL_SECONDS = 3600


class HostMaintenanceConflict(RuntimeError):
    """该主机已在维护窗口中（并发热更新 / 上一次窗口未释放）。"""


def _ttl() -> int:
    raw = os.getenv("STP_HOST_MAINTENANCE_TTL_SECONDS", "")
    try:
        value = int(raw) if raw else DEFAULT_TTL_SECONDS
    except ValueError:
        value = DEFAULT_TTL_SECONDS
    return max(1, min(value, _MAX_TTL_SECONDS))


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def in_maintenance_window(
    maintenance_until: Optional[datetime], now: Optional[datetime] = None,
) -> bool:
    """截止时刻是否仍在未来（NULL 或已过期 = 无窗口）。

    派发与 claim 侧共用这一个判据：过期窗口等同无窗口，进程崩溃无需清扫。
    """
    until = _as_utc(maintenance_until)
    if until is None:
        return False
    now_utc = _as_utc(now) or datetime.now(timezone.utc)
    return until > now_utc


def acquire_maintenance_window(
    db: Session, host_id: str, holder: str, ttl_seconds: Optional[int] = None,
) -> bool:
    """占用窗口；已被占用（未过期）返回 False。占用成功后立即提交。

    提交是必须的：热更新本身是分钟级同步操作，未提交的窗口对其他进程不可见，
    等于没有互斥。
    """
    row = db.execute(
        select(Host).where(Host.id == host_id).with_for_update()
    ).scalars().first()
    if row is None:
        return False
    if in_maintenance_window(row.maintenance_until):
        logger.warning(
            "host_maintenance_conflict host=%s holder=%s existing_holder=%s",
            host_id, holder, row.maintenance_holder,
        )
        return False
    row.maintenance_until = datetime.now(timezone.utc) + timedelta(
        seconds=ttl_seconds or _ttl(),
    )
    row.maintenance_holder = holder
    db.commit()
    return True


def release_maintenance_window(db: Session, host_id: str, holder: str) -> None:
    """只在持有者匹配时清窗口 —— 避免迟到的释放擦掉别人新开的窗口。"""
    row = db.execute(
        select(Host).where(Host.id == host_id).with_for_update()
    ).scalars().first()
    if row is None:
        return
    if row.maintenance_holder and row.maintenance_holder != holder:
        logger.warning(
            "host_maintenance_release_skipped host=%s holder=%s actual=%s",
            host_id, holder, row.maintenance_holder,
        )
        return
    row.maintenance_until = None
    row.maintenance_holder = ""
    db.commit()


@contextmanager
def maintenance_window(
    db: Session, host_id: str, holder: str, ttl_seconds: Optional[int] = None,
) -> Iterator[None]:
    """热更新专用：占用窗口 → 执行 → finally 释放（异常路径也释放）。"""
    if not acquire_maintenance_window(db, host_id, holder, ttl_seconds):
        raise HostMaintenanceConflict(f"host {host_id} already in maintenance window")
    try:
        yield
    finally:
        try:
            release_maintenance_window(db, host_id, holder)
        except Exception:
            logger.exception("host_maintenance_release_failed host=%s", host_id)
