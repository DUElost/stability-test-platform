"""#2983 切片③：控制面 host 健康探针周期 sweep。"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.host_health_probe import run_probe_sweep_once

logger = logging.getLogger(__name__)


def host_health_probe_sweep_once() -> dict[str, Any]:
    """APScheduler 入口：跑一轮 fleet 探针并返回汇总。"""
    result = run_probe_sweep_once()
    logger.info(
        "host_health_probe_sweep_job_done selected=%d ok=%d errors=%d strike_open=%d",
        result.get("selected", 0),
        result.get("ok", 0),
        result.get("errors", 0),
        result.get("strike_open", 0),
    )
    return result
