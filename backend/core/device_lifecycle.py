"""设备生命周期**纯口径**：陈旧度（#2962 A）与退役建议阈值（ADR-0057 D6/E4）。

本模块只放零依赖的纯函数与阈值常量，供所有层（含 `backend.api.schemas`
的派生字段）复用——SQL 判据在 `backend/services/device_lifecycle.py`
（那里需要 Device 模型，按分层契约 core 不得 import models）。

**陈旧度**是**现算**事实，不落库、可逆、设备回场自动恢复：

    ``status == OFFLINE`` 且 ``last_seen`` 早于 7 天（或为空）⇒ 陈旧。

语义分界来自 2026-09-20 只读实测：202 台 OFFLINE 里 188 台（93%）是陈旧库存，
「掉线」（心跳丢失数十秒～数天，可能回场）与「库存沉积」（数周没出现过）混在
同一计数里，容量口径（#106）、链选与 OFFLINE 指标都被污染。

**退役**是人工确认的终态，与陈旧度互补而不重复：陈旧**不会**自动转为退役，
只对「陈旧超过 30 天」的设备在列表给出**退役建议**（只提示，不动作）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

#: A 期阈值：`last_seen` 早于该值（或为空）且 OFFLINE ⇒ 陈旧（#2962 裁决）。
STALE_AFTER_DAYS = 7

#: E4 阈值：陈旧超过该天数 ⇒ 列表给出退役建议（只提示，不动作）。
RETIRE_SUGGEST_AFTER_DAYS = 30


def _ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    """把 naive 时间（历史/测试数据）与 SQLite 裸 SQL 的 ISO 串按 UTC 解释。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def normalize_now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def is_stale(
    status: Optional[str],
    last_seen: Optional[datetime],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """陈旧度纯函数（`#2962` 裁决口径）：OFFLINE 且 last_seen 早于 7 天/为空。

    只有 OFFLINE 会陈旧——ONLINE/BUSY/ERROR 都不是「库存沉积」形态；且陈旧可由
    下一次心跳自动恢复（不落库），不需要人工解除。
    """
    if status != "OFFLINE":
        return False
    seen = _ensure_utc(last_seen)
    if seen is None:
        # 裁决原文「（或为空）」：从未上报过的 OFFLINE 行同样算沉积。
        return True
    return seen < normalize_now(now) - timedelta(days=STALE_AFTER_DAYS)


def is_retire_suggested(
    status: Optional[str],
    last_seen: Optional[datetime],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """退役建议纯函数（E4）：陈旧且最后上报早于 30 天。

    `last_seen` 为空的设备**不给建议**——「超过 30 天」无从度量，且建议是要人
    对着清单确权的，宁可少提示也不误提示（陈旧判定本身仍然覆盖空值）。
    """
    if not is_stale(status, last_seen, now=now):
        return False
    seen = _ensure_utc(last_seen)
    if seen is None:
        return False
    return seen < normalize_now(now) - timedelta(days=RETIRE_SUGGEST_AFTER_DAYS)
