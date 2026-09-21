"""#2958 第五道闸：host 脚本在位矩阵的每日 sweep 作业。

- 触发：`SCRIPT_PRESENCE_SWEEP_CRON`（默认 `30 9 * * *`，**本机时区**——与
  `stp-script-guard.timer` 的 `OnCalendar` 同口径；每天只跑一次，避开链派发窗）；
- 语义：`services.script_presence.run_sweep`（可达集 → `verify_scripts` RPC → 五态 →
  整轮 upsert）；只读 host 侧，不占维护窗；
- 单例：注册在 `SINGLETON_SCHEDULE_IDS`（多控制面实例下靠 scheduler leadership 去重）；
- 失败形态：作业抛错由 APScheduler 记录（`stability_apscheduler_job_runs_total{outcome=error}`），
  **不回滚已有行**——旧行保持旧 `checked_at`，正是「新鲜度 = min(checked_at)」要暴露的形态。
"""
from __future__ import annotations

import logging
from typing import Any

from backend.services.script_presence import DEFAULT_HISTORY_DAYS, run_sweep

logger = logging.getLogger(__name__)


async def script_presence_sweep_job(*, days: int = DEFAULT_HISTORY_DAYS) -> dict[str, Any]:
    """跑一轮 sweep 并返回汇总（APScheduler 会把返回值记进 job 结果）。"""
    result = await run_sweep(days=days)
    logger.info(
        "script_presence_sweep_job_done sweep=%s hosts=%d rows=%d gaps=%d unknown=%d",
        result.get("sweep_id"), result.get("hosts", 0), result.get("rows", 0),
        result.get("counts", {}).get("missing", 0) + result.get("counts", {}).get("mismatch", 0),
        result.get("counts", {}).get("unknown", 0),
    )
    return result
