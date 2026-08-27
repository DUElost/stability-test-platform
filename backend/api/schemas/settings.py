from backend.api.schemas.base import ORMBaseModel


class SettingsOut(ORMBaseModel):
    """系统设置概览（只读）。

    对应前端"系统设置"页展示的运行时配置。所有字段均来自运行时真实来源
    （env / 模块常量 / DB 探测 / 通知规则聚合），不做前端硬编码兜底。
    """

    platform_name: str
    timezone: str
    database_type: str
    database_connected: bool
    agent_heartbeat_interval_seconds: int
    offline_threshold_seconds: int
    device_offline_notification_enabled: bool
    task_failure_notification_enabled: bool
