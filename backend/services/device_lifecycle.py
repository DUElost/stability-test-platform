"""设备生命周期的 **SQL 判据**（#2962 A / ADR-0057 D4）。

纯函数与阈值在 `backend/core/device_lifecycle.py`（零依赖，供 schema 派生字段
复用）；本模块只放需要 Device 模型的 SQL 条件——八面收口一律用「本判据 ∧
原判据」，不允许各面自拼谓词：

- `not_retired_condition()`：ADR-0057 D4 统一退役判据；
- `stale_condition()` / `not_stale_condition()`：#2962 A 陈旧度（现算）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, or_

from backend.core.device_lifecycle import STALE_AFTER_DAYS, normalize_now
from backend.models.host import Device


def not_retired_condition():
    """统一退役判据（ADR-0057 D4）：`device.retired_at IS NULL`。"""

    return Device.retired_at.is_(None)


def stale_condition(*, now: Optional[datetime] = None):
    """陈旧度 SQL 判据（与 `core.is_stale` 同口径）：OFFLINE ∧ (last_seen 空 ∨ 早于 7 天)。"""

    cutoff = normalize_now(now) - timedelta(days=STALE_AFTER_DAYS)
    return and_(
        Device.status == "OFFLINE",
        or_(Device.last_seen.is_(None), Device.last_seen < cutoff),
    )


def not_stale_condition(*, now: Optional[datetime] = None):
    """陈旧度补集（容量口径用）：非 OFFLINE，或 last_seen 仍在 7 天窗内。"""

    cutoff = normalize_now(now) - timedelta(days=STALE_AFTER_DAYS)
    return or_(
        Device.status != "OFFLINE",
        and_(Device.last_seen.isnot(None), Device.last_seen >= cutoff),
    )
