"""Downgrade 数据保护 — 阻止误跑破坏性 downgrade 丢数据。

数据迁移类 migration（ADR-0020 workflow→plan）的 downgrade 会全表 DELETE/drop
核心业务表（plan_run / plan / plan_step），误执行会丢数据且不可恢复（upgrade
不会重新迁移，因为源 workflow 表可能已被 drop）。本模块在 downgrade 开头检测
目标表是否有数据，非空则抛 RuntimeError 阻止；设环境变量
STP_ALLOW_DESTRUCTIVE_DOWNGRADE=1 强制放行（即「确认命令」）。

用法（migration 的 downgrade 开头）：
    from backend.core.migration_guard import guard_nonempty
    guard_nonempty(["plan_run", "plan"], migration_id="z3a4b5c6d7e8")

列类型收窄（如 varchar→integer）另见 ``guard_integer_castable``：存在不可
安全 cast 的非空行时先拒绝，避免 PG ``invalid input syntax`` 在远离真正危险
步骤处中断降级演练（#832）。
"""
from __future__ import annotations

import os
import re

from alembic import op
from sqlalchemy import inspect, text

_FORCE_ENV = "STP_ALLOW_DESTRUCTIVE_DOWNGRADE"

# Identifiers only — never interpolate untrusted input into SQL.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Matches values that PostgreSQL ``::integer`` accepts for typical audit ids
# (signed digit string). Host-derived ids like ``172-21-15-80`` fail this.
_INTEGER_TEXT_RE_SQL = r"^-?[0-9]+$"


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


def guard_integer_castable(
    table: str,
    column: str,
    *,
    migration_id: str,
) -> None:
    """若 ``table.column`` 存在无法安全 ``::integer`` 的非空值，抛 RuntimeError。

    用于 varchar→integer 收窄的 downgrade：裸 ``::integer`` 在含 host 派生 id
    （``172-21-15-80``）等行上会 ``invalid input syntax`` 中止，且失败点离真正
    危险步骤很远、误导排障。强制放行时调用方须使用 NULL-safe ``USING``
    （非整型行置 NULL），见 ``integer_cast_using``。
    """
    if os.getenv(_FORCE_ENV, "") == "1":
        return
    if not _IDENT_RE.match(table) or not _IDENT_RE.match(column):
        raise ValueError(f"invalid SQL identifier: table={table!r} column={column!r}")
    bind = op.get_bind()
    insp = inspect(bind)
    if not insp.has_table(table):
        return
    cnt = bind.execute(
        text(
            f'SELECT COUNT(*) FROM "{table}" '
            f"WHERE \"{column}\" IS NOT NULL "
            f"AND \"{column}\" !~ :pat"
        ),
        {"pat": _INTEGER_TEXT_RE_SQL},
    ).scalar() or 0
    if cnt > 0:
        raise RuntimeError(
            f"[{migration_id}] downgrade cannot cast {table}.{column} to integer: "
            f"{cnt} non-integer row(s) (e.g. host-derived ids). "
            f"Data is not safely reversible. "
            f"To force (non-integer values become NULL), set {_FORCE_ENV}=1 and re-run: "
            f"{_FORCE_ENV}=1 python -m alembic downgrade <rev>"
        )


def integer_cast_using(column: str) -> str:
    """PostgreSQL USING 表达式：可解析为整数则 cast，否则 NULL。"""
    if not _IDENT_RE.match(column):
        raise ValueError(f"invalid SQL identifier: column={column!r}")
    return (
        f"CASE WHEN \"{column}\" ~ '{_INTEGER_TEXT_RE_SQL}' "
        f"THEN \"{column}\"::integer ELSE NULL END"
    )
