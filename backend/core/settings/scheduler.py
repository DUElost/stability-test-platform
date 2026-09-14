"""调度域 Settings（ADR-0042 P1 试点）。

覆盖「调度节奏与回收」三个文件的环境旋钮：`backend/scheduler/app_scheduler.py`、
`recycler.py`、`cron_scheduler.py`（对账/重试批处理域，即 counter/signal_link/
plan_chain/precheck 四个 reconciler，留在 P2 迁移）。

口径：

- 字段名 = 既有 env 名的小写形式（`RUN_RECYCLE_INTERVAL_SECONDS` →
  `run_recycle_interval_seconds`），不改名、不加前缀；
- 默认值与迁移前 `int(os.getenv(..., "<默认>"))` 逐一对齐（含 `str(24 * 3600)` 类）；
- 类型：原实现用 `float(...)` 的（`CRON_POLL_INTERVAL`、`SCHEDULE_DEDUP_WINDOW_SECONDS`）
  保持 float，其余 int；
- 惰性：`get_scheduler_settings()` 逐次取值（lru_cache），`reset_scheduler_settings_cache()`
  供测试与将来热更能力使用（控制面当前不热更，ADR-0042 D4）。
"""

from __future__ import annotations

from functools import lru_cache

from backend.core.settings.base import DomainSettings


class SchedulerSettings(DomainSettings):
    """调度节奏与回收旋钮（env 名 = 字段名大写）。"""

    # ── app_scheduler：作业注册周期（秒）──
    run_recycle_interval_seconds: int = 30
    session_watchdog_interval_seconds: int = 15
    reconciler_interval_seconds: int = 15
    cron_poll_interval: float = 30
    retention_cleanup_interval_seconds: int = 3600
    queue_depth_poll_interval_seconds: int = 15
    precheck_reaper_interval_seconds: int = 45
    chain_reconciler_interval_seconds: int = 60
    # 一天扫一次 expired jti 即可（refresh 黑名单只随主动登出增长；见原注释）
    revoked_token_cleanup_interval_seconds: int = 24 * 3600
    auto_archive_poll_interval_seconds: int = 120
    stp_admission_pump_interval_seconds: int = 5
    stp_counter_reconcile_interval_seconds: int = 300
    stp_signal_link_reconcile_interval_seconds: int = 300

    # ── recycler：批量、保留与宽限 ──
    recycler_batch_size: int = 200
    artifact_retention_days: int = 30
    patrol_stall_batch_limit: int = 100
    coordinator_heartbeat_timeout_seconds: int = 300
    post_completion_grace_seconds: int = 120
    post_completion_max_defer_seconds: int = 6 * 3600

    # ── cron_scheduler：计划保留与触发去重 ──
    plan_run_retention_days: int = 3
    schedule_dedup_window_seconds: float = 60


@lru_cache(maxsize=1)
def get_scheduler_settings() -> SchedulerSettings:
    """取调度域 Settings（ADR-0042：惰性 + 缓存；不读 `.env` 文件）。"""
    return SchedulerSettings()


def reset_scheduler_settings_cache() -> None:
    """清缓存——测试改 env 后调用；将来若控制面支持热更重读亦走这里。"""
    get_scheduler_settings.cache_clear()
