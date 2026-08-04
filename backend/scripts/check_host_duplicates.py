"""#101 迁移前只读核查：host.name / host.ip 的存量重复行。

迁移 l3m4n5o6p7q8 会给 host.name / host.ip 加唯一约束，生产库若有重复行
迁移会失败。本脚本只 SELECT、不修改，输出重复分组的明细供运维决策
（保留心跳最新的一行，归并 device/lease 后删除旧行）。

Usage:
    python -m backend.scripts.check_host_duplicates
    python -m backend.scripts.check_host_duplicates --limit 50

Run with the same environment as the backend (DATABASE_URL etc.).
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from sqlalchemy import func, select

from backend.core.database import SessionLocal
from backend.models.host import Host


def _find_duplicates(db, column, limit: int | None = None):
    """Return {value: [row, ...]} for groups with count > 1 (NULL excluded)."""
    dup_values = [
        row[0]
        for row in db.execute(
            select(column)
            .where(column.is_not(None))
            .group_by(column)
            .having(func.count() > 1)
            .order_by(column)
        ).all()
    ]
    if not dup_values:
        return {}
    stmt = (
        select(Host.id, Host.name, Host.ip, Host.hostname, Host.last_heartbeat)
        .where(column.in_(dup_values))
        .order_by(column, Host.last_heartbeat.desc().nulls_last(), Host.id)
    )
    if limit and limit > 0:
        stmt = stmt.limit(limit)
    groups: dict[str, list[tuple]] = defaultdict(list)
    for row in db.execute(stmt).all():
        groups[str(row[0] if column is Host.ip else row[1])].append(tuple(row))
    return dict(groups)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Max rows to list (0 = no limit)")
    args = parser.parse_args()

    with SessionLocal() as db:
        ip_groups = _find_duplicates(db, Host.ip, args.limit or None)
        name_groups = _find_duplicates(db, Host.name, args.limit or None)

    print(f"duplicate_ip_groups={len(ip_groups)} duplicate_name_groups={len(name_groups)}")
    for label, groups in (("ip", ip_groups), ("name", name_groups)):
        for value, rows in groups.items():
            print(f"\n[{label}] {value} -> {len(rows)} rows")
            for row in rows:
                print(
                    f"  id={row[0]} name={row[1]} ip={row[2]} "
                    f"hostname={row[3]} last_heartbeat={row[4]}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
