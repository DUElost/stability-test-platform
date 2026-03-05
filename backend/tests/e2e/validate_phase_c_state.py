"""
Phase C 状态校验脚本（只读，不修改数据）。

用途：
1. 校验 host.id / device.host_id 是否已迁移为字符串类型
2. 校验旧表 hosts/devices 是否已清理
3. 校验 task_runs 中是否仍有遗留 RunStatus（QUEUED/DISPATCHED/FINISHED/CANCELED）

执行：
    python backend/tests/e2e/validate_phase_c_state.py

环境变量：
    STP_DATABASE_URL 或 DATABASE_URL
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Sequence

from sqlalchemy import create_engine, text


LEGACY_RUN_STATUSES = {"QUEUED", "DISPATCHED", "FINISHED", "CANCELED"}
PASS_TYPES = {"character varying", "text"}


def _resolve_database_url() -> Optional[str]:
    return os.getenv("STP_DATABASE_URL") or os.getenv("DATABASE_URL")


def _to_sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql://", "postgresql+psycopg://")
    )


def _table_exists(conn, table_name: str) -> bool:
    stmt = text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :table_name
        ) AS exists_flag
        """
    )
    return bool(conn.execute(stmt, {"table_name": table_name}).scalar())


def _column_data_type(conn, table_name: str, column_name: str) -> Optional[str]:
    stmt = text(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
          AND column_name = :column_name
        """
    )
    row = conn.execute(stmt, {"table_name": table_name, "column_name": column_name}).first()
    return row[0] if row else None


def _legacy_statuses(conn) -> Sequence[str]:
    if not _table_exists(conn, "task_runs"):
        return []
    rows = conn.execute(text("SELECT DISTINCT status FROM task_runs")).fetchall()
    return [str(r[0]).upper() for r in rows if r and r[0] is not None]


def main() -> int:
    raw_url = _resolve_database_url()
    if not raw_url:
        print("[FAIL] 缺少数据库连接：请设置 STP_DATABASE_URL 或 DATABASE_URL")
        return 2

    db_url = _to_sync_url(raw_url)
    engine = create_engine(db_url, future=True)
    failures = []
    infos = []

    try:
        with engine.connect() as conn:
            # 1) 新表存在性
            for table in ("host", "device"):
                if _table_exists(conn, table):
                    infos.append(f"[OK] 表存在: {table}")
                else:
                    failures.append(f"[FAIL] 缺少新表: {table}")

            # 2) 旧表清理状态
            for legacy in ("hosts", "devices"):
                if _table_exists(conn, legacy):
                    failures.append(f"[FAIL] 旧表仍存在: {legacy}")
                else:
                    infos.append(f"[OK] 旧表已清理: {legacy}")

            # 3) 字段类型
            host_id_type = _column_data_type(conn, "host", "id")
            if host_id_type in PASS_TYPES:
                infos.append(f"[OK] host.id 类型: {host_id_type}")
            else:
                failures.append(f"[FAIL] host.id 类型异常: {host_id_type}")

            device_host_id_type = _column_data_type(conn, "device", "host_id")
            if device_host_id_type in PASS_TYPES:
                infos.append(f"[OK] device.host_id 类型: {device_host_id_type}")
            else:
                failures.append(f"[FAIL] device.host_id 类型异常: {device_host_id_type}")

            # 4) C-2: 旧状态残留
            statuses = _legacy_statuses(conn)
            if not statuses:
                infos.append("[INFO] task_runs 不存在或为空：C-2 状态转换校验不适用")
            else:
                hit = sorted(set(statuses) & LEGACY_RUN_STATUSES)
                if hit:
                    failures.append(
                        "[FAIL] task_runs 存在遗留 RunStatus，需要执行 C-2 映射: "
                        + ", ".join(hit)
                    )
                else:
                    infos.append("[OK] task_runs 未检测到遗留 RunStatus")

    except Exception as exc:
        print(f"[FAIL] 执行校验失败: {exc}")
        return 2
    finally:
        engine.dispose()

    for line in infos:
        print(line)
    for line in failures:
        print(line)

    if failures:
        print(f"[SUMMARY] FAIL ({len(failures)} 项)")
        return 2

    print("[SUMMARY] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
