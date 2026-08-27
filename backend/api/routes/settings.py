import logging
import os

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, require_admin
from backend.api.routes.heartbeat import HEARTBEAT_INTERVAL_BASE
from backend.api.routes.hosts import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.api.schemas import SettingsOut
from backend.core.database import engine, get_db
from backend.models.notification import AlertRule, EventType

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
logger = logging.getLogger(__name__)

_PLATFORM_NAME = os.getenv("STP_PLATFORM_NAME", "Stability Test Platform")
_PLATFORM_TIMEZONE = os.getenv("STP_TIMEZONE", "Asia/Shanghai")


def _database_connected() -> bool:
    """数据库连通性探测（与 main.health 同思路的同步 engine 版）。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - 探测失败即断开，记录即可
        logger.warning("settings db probe failed: %s", exc)
        return False


def _notification_enabled(db: Session, event_type: EventType) -> bool:
    return (
        db.query(AlertRule.id)
        .filter(AlertRule.event_type == event_type, AlertRule.enabled.is_(True))
        .first()
        is not None
    )


@router.get("", response_model=ApiResponse[SettingsOut])
def get_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[SettingsOut]:
    """系统设置概览（只读聚合）。

    返回运行时真实配置：平台名 / 时区来自 env（可覆盖），心跳间隔与离线阈值来自
    控制面自身读用的同一模块常量（单一来源），通知开关按事件类型聚合启用规则。
    """
    return ok(
        SettingsOut(
            platform_name=_PLATFORM_NAME,
            timezone=_PLATFORM_TIMEZONE,
            database_type=engine.dialect.name,
            database_connected=_database_connected(),
            agent_heartbeat_interval_seconds=HEARTBEAT_INTERVAL_BASE,
            offline_threshold_seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS,
            device_offline_notification_enabled=_notification_enabled(
                db, EventType.DEVICE_OFFLINE
            ),
            task_failure_notification_enabled=_notification_enabled(
                db, EventType.RUN_FAILED
            ),
        )
    )
