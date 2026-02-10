#!/usr/bin/env python3
"""
双写验证脚本

在 PostgreSQL 迁移期间，验证 SQLite 和 PostgreSQL 数据一致性

用法:
    python scripts/verify_dual_write.py --sqlite-path ./stability.db --postgres-url postgresql://user:pass@localhost/dbname
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Tuple


def parse_args():
    parser = argparse.ArgumentParser(description="Verify dual-write consistency")
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("SQLITE_PATH", "./stability.db"),
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.getenv("POSTGRES_URL"),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--tables",
        default="hosts,devices,tasks,task_runs,log_artifacts",
        help="Comma-separated list of tables to verify",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Run verification in a loop with specified interval (seconds), 0 for single run",
    )
    return parser.parse_args()


def connect_sqlite(path: str) -> sqlite3.Connection:
    """连接 SQLite 数据库"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def connect_postgres(url: str):
    """连接 PostgreSQL 数据库"""
    try:
        import psycopg2
        return psycopg2.connect(url)
    except ImportError:
        print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)


def get_row_count(conn, table: str, is_sqlite: bool = True) -> int:
    """获取表的行数"""
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    return cursor.fetchone()[0]


def get_checksum(conn, table: str, is_sqlite: bool = True) -> str:
    """
    计算表的校验和
    使用所有行的哈希值组合
    """
    cursor = conn.cursor()

    # 获取所有列名
    if is_sqlite:
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
    else:
        cursor.execute(f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        columns = [row[0] for row in cursor.fetchall()]

    if not columns:
        return "N/A"

    # 构建校验和查询
    # 使用所有列的字符串拼接计算哈希
    column_concat = " || '|' || ".join(columns)

    if is_sqlite:
        cursor.execute(f"""
            SELECT HEX(MD5(GROUP_CONCAT({column_concat}, '|')))
            FROM {table}
        """)
    else:
        cursor.execute(f"""
            SELECT MD5(string_agg({column_concat}::text, '|' ORDER BY id))
            FROM {table}
        """)

    result = cursor.fetchone()[0]
    return result or "EMPTY"


def verify_table(sqlite_conn, pg_conn, table: str) -> Tuple[bool, Dict]:
    """验证单个表的一致性"""
    result = {
        "table": table,
        "sqlite_count": 0,
        "postgres_count": 0,
        "count_match": False,
        "sqlite_checksum": "N/A",
        "postgres_checksum": "N/A",
        "checksum_match": False,
    }

    try:
        # 获取行数
        result["sqlite_count"] = get_row_count(sqlite_conn, table, is_sqlite=True)
        result["postgres_count"] = get_row_count(pg_conn, table, is_sqlite=False)
        result["count_match"] = result["sqlite_count"] == result["postgres_count"]

        # 如果行数一致且不为0，计算校验和
        if result["count_match"] and result["sqlite_count"] > 0:
            result["sqlite_checksum"] = get_checksum(sqlite_conn, table, is_sqlite=True)
            result["postgres_checksum"] = get_checksum(pg_conn, table, is_sqlite=False)
            result["checksum_match"] = result["sqlite_checksum"] == result["postgres_checksum"]

        return result["count_match"] and (result["checksum_match"] or result["sqlite_count"] == 0), result

    except Exception as e:
        result["error"] = str(e)
        return False, result


def print_results(results: List[Dict], timestamp: datetime):
    """打印验证结果"""
    print(f"\n{'='*80}")
    print(f"Verification Report - {timestamp.isoformat()}")
    print(f"{'='*80}")
    print(f"{'Table':<20} {'SQLite':>10} {'PostgreSQL':>12} {'Count':>8} {'Checksum':>10}")
    print(f"{'-'*80}")

    all_passed = True

    for result in results:
        table = result["table"]
        sqlite_count = result["sqlite_count"]
        pg_count = result["postgres_count"]
        count_ok = "✅" if result["count_match"] else "❌"
        checksum_ok = "✅" if result["checksum_match"] else ("⚠️" if result["sqlite_count"] > 0 else "-")

        if not result.get("count_match") or not result.get("checksum_match"):
            all_passed = False

        print(f"{table:<20} {sqlite_count:>10} {pg_count:>12} {count_ok:>8} {checksum_ok:>10}")

        if "error" in result:
            print(f"  Error: {result['error']}")

    print(f"{'-'*80}")

    if all_passed:
        print("✅ All tables are consistent!")
    else:
        print("❌ Some tables have inconsistencies!")

    return all_passed


def main():
    args = parse_args()

    if not args.postgres_url:
        print("Error: PostgreSQL URL not provided")
        sys.exit(1)

    if not os.path.exists(args.sqlite_path):
        print(f"Error: SQLite database not found: {args.sqlite_path}")
        sys.exit(1)

    tables = [t.strip() for t in args.tables.split(",")]

    print("="*80)
    print("Dual-Write Verification")
    print("="*80)
    print(f"SQLite: {args.sqlite_path}")
    print(f"PostgreSQL: {args.postgres_url.replace('://', '://***:***@')}")
    print(f"Tables: {', '.join(tables)}")

    if args.interval > 0:
        print(f"Running every {args.interval} seconds (Ctrl+C to stop)")

    first_run = True

    while first_run or args.interval > 0:
        first_run = False

        # 连接数据库
        sqlite_conn = connect_sqlite(args.sqlite_path)
        pg_conn = connect_postgres(args.postgres_url)

        try:
            results = []
            for table in tables:
                _, result = verify_table(sqlite_conn, pg_conn, table)
                results.append(result)

            all_passed = print_results(results, datetime.now())

        finally:
            sqlite_conn.close()
            pg_conn.close()

        if args.interval > 0:
            import time
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n\nStopping verification loop.")
                break
        else:
            sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
