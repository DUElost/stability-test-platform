"""Downgrade 数据保护 — 阻止误跑破坏性 downgrade 丢数据。

数据迁移类 migration（ADR-0020 workflow→plan）的 downgrade 会全表 DELETE/drop
核心业务表（plan_run / plan / plan_step），误执行会丢数据且不可恢复（upgrade
不会重新迁移，因为源 workflow 表可能已被 drop）。本模块在 downgrade 开头检测
目标表是否有数据，非空则抛 RuntimeError 阻止；设环境变量
STP_ALLOW_DESTRUCTIVE_DOWNGRADE=1 强制放行（即「确认命令」）。

用法（migration 的 downgrade 开头）：
    from backend.core.migration_guard import guard_nonempty
    guard_nonempty(["plan_run", "plan"], migration_id="z3a4b5c6d7e8")
"""
from __future__ import annotations

import os

from alembic import op
from sqlalchemy import inspect, text

_FORCE_ENV = "STP_ALLOW_DESTRUCTIVE_DOWNGRADE"


def guard_nonempty(table_names: list[str], *, migration_id: str) -> None:
    """若任一目标表非空，抛 RuntimeError 阻止 downgrade。

    设 STP_ALLOW_DESTRUCTIVE_DOWNGRADE=1 时直接放行（明确确认数据丢失）。
    表不存在（已被先前 downgrade 删除）则跳过，不阻塞。
    """
    if os.getenv(_FORCE_ENV, "") == "1":
        return
    bind = op.get_bind()
    insp = inspect(bind)
    blocked: list[tuple[str, int]] = []
    for t in table_names:
        if not insp.has_table(t):
            continue
        cnt = bind.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
        if cnt > 0:
            blocked.append((t, cnt))
    if blocked:
        detail = ", ".join(f"{t}={c} rows" for t, c in blocked)
        raise RuntimeError(
            f"[{migration_id}] downgrade would destroy data ({detail}). "
            f"This is irreversible (upgrade won't re-migrate). "
            f"To force, set {_FORCE_ENV}=1 and re-run: "
            f"{_FORCE_ENV}=1 python -m alembic downgrade <rev>"
        )
