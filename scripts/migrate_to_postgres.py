#!/usr/bin/env python3
"""
SQLite 到 PostgreSQL 数据迁移脚本

用法:
    python scripts/migrate_to_postgres.py --sqlite-path ./stability.db --postgres-url postgresql://user:pass@localhost/dbname

环境变量:
    SQLITE_PATH: SQLite 数据库路径
    POSTGRES_URL: PostgreSQL 连接 URL
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args():
    parser = argparse.ArgumentParser(description="Migrate data from SQLite to PostgreSQL")
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
        "--batch-size",
        type=int,
        default=1000,
        help="Batch size for inserts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without executing",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify data integrity after migration",
    )
    return parser.parse_args()


class MigrationStats:
    def __init__(self):
        self.tables: Dict[str, Dict[str, int]] = {}
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

    def start(self):
        self.start_time = datetime.now()

    def finish(self):
        self.end_time = datetime.now()

    def add_table(self, table_name: str, source_count: int, target_count: int):
        self.tables[table_name] = {
            "source": source_count,
            "target": target_count,
            "diff": target_count - source_count,
        }

    def print_summary(self):
        print("\n" + "=" * 60)
        print("Migration Summary")
        print("=" * 60)

        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()
            print(f"Duration: {duration:.2f} seconds")

        print(f"\n{'Table':<20} {'Source':>10} {'Target':>10} {'Diff':>10}")
        print("-" * 60)

        total_source = 0
        total_target = 0

        for table, counts in self.tables.items():
            diff_str = f"{counts['diff']:+d}" if counts['diff'] != 0 else "0"
            print(f"{table:<20} {counts['source']:>10} {counts['target']:>10} {diff_str:>10}")
            total_source += counts['source']
            total_target += counts['target']

        print("-" * 60)
        print(f"{'TOTAL':<20} {total_source:>10} {total_target:>10} {total_target - total_source:+d}")

        # 检查是否有差异
        has_errors = any(c['diff'] != 0 for c in self.tables.values())
        if has_errors:
            print("\n⚠️  WARNING: Some tables have count mismatches!")
        else:
            print("\n✅ All tables migrated successfully with matching counts.")


def connect_sqlite(path: str) -> sqlite3.Connection:
    """连接 SQLite 数据库"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def connect_postgres(url: str):
    """连接 PostgreSQL 数据库"""
    try:
        import psycopg2
        from psycopg2.extras import execute_batch
        return psycopg2.connect(url)
    except ImportError:
        print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)


def get_sqlite_tables(conn: sqlite3.Connection) -> List[str]:
    """获取 SQLite 中所有表名"""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'alembic_%'")
    return [row[0] for row in cursor.fetchall()]


def get_table_count(conn, table: str, is_sqlite: bool = True) -> int:
    """获取表的行数"""
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    return cursor.fetchone()[0]


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """迁移单个表的数据"""
    print(f"\nMigrating table: {table}")

    # 获取源数据行数
    source_count = get_table_count(sqlite_conn, table, is_sqlite=True)
    print(f"  Source rows: {source_count}")

    if source_count == 0:
        print(f"  Skipping empty table")
        return 0, 0

    if dry_run:
        print(f"  [DRY RUN] Would migrate {source_count} rows")
        return source_count, 0

    # 读取 SQLite 数据
    sqlite_cursor = sqlite_conn.cursor()
    sqlite_cursor.execute(f"SELECT * FROM {table}")

    # 获取列名
    columns = [description[0] for description in sqlite_cursor.description]
    print(f"  Columns: {', '.join(columns)}")

    # 构建 PostgreSQL INSERT 语句
    column_str = ', '.join(columns)
    placeholder_str = ', '.join(['%s'] * len(columns))
    insert_sql = f"INSERT INTO {table} ({column_str}) VALUES ({placeholder_str}) ON CONFLICT DO NOTHING"

    # 批量插入
    pg_cursor = pg_conn.cursor()
    total_inserted = 0
    batch = []

    for row in sqlite_cursor:
        # 转换数据类型
        converted_row = []
        for value in row:
            if isinstance(value, bool):
                converted_row.append(value)
            elif value is None:
                converted_row.append(None)
            else:
                converted_row.append(value)
        batch.append(converted_row)

        if len(batch) >= batch_size:
            pg_cursor.executemany(insert_sql, batch)
            pg_conn.commit()
            total_inserted += len(batch)
            print(f"  Inserted: {total_inserted}/{source_count}", end='\r')
            batch = []

    # 插入剩余数据
    if batch:
        pg_cursor.executemany(insert_sql, batch)
        pg_conn.commit()
        total_inserted += len(batch)

    print(f"  Inserted: {total_inserted}/{source_count}")

    # 获取目标数据行数
    target_count = get_table_count(pg_conn, table, is_sqlite=False)
    print(f"  Target rows: {target_count}")

    if target_count != source_count:
        print(f"  ⚠️  Count mismatch: {source_count} -> {target_count}")

    pg_cursor.close()
    return source_count, target_count


def verify_migration(sqlite_conn: sqlite3.Connection, pg_conn, stats: MigrationStats):
    """验证迁移结果"""
    print("\n" + "=" * 60)
    print("Verification")
    print("=" * 60)

    for table in stats.tables.keys():
        source_count = get_table_count(sqlite_conn, table, is_sqlite=True)
        target_count = get_table_count(pg_conn, table, is_sqlite=False)

        if source_count == target_count:
            print(f"✅ {table}: {source_count} rows match")
        else:
            print(f"❌ {table}: MISMATCH - Source: {source_count}, Target: {target_count}")


def reset_postgres_sequences(pg_conn):
    """重置 PostgreSQL 序列"""
    print("\nResetting PostgreSQL sequences...")
    pg_cursor = pg_conn.cursor()

    # 获取所有需要重置的序列
    pg_cursor.execute("""
        SELECT c.relname, a.attname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE c.relkind = 'r'
        AND n.nspname = 'public'
        AND pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval%'
    """)

    for row in pg_cursor.fetchall():
        table_name, column_name = row
        sequence_name = f"{table_name}_{column_name}_seq"

        try:
            pg_cursor.execute(f"""
                SELECT setval('{sequence_name}', COALESCE((SELECT MAX({column_name}) FROM {table_name}), 0) + 1, false)
            """)
            print(f"  Reset {sequence_name}")
        except Exception as e:
            print(f"  Warning: Could not reset {sequence_name}: {e}")

    pg_conn.commit()
    pg_cursor.close()


def main():
    args = parse_args()

    if not args.postgres_url:
        print("Error: PostgreSQL URL not provided. Use --postgres-url or set POSTGRES_URL environment variable.")
        sys.exit(1)

    if not os.path.exists(args.sqlite_path):
        print(f"Error: SQLite database not found: {args.sqlite_path}")
        sys.exit(1)

    stats = MigrationStats()
    stats.start()

    print("=" * 60)
    print("SQLite to PostgreSQL Migration")
    print("=" * 60)
    print(f"SQLite: {args.sqlite_path}")
    print(f"PostgreSQL: {args.postgres_url.replace('://', '://***:***@')}")
    print(f"Batch size: {args.batch_size}")
    print(f"Dry run: {args.dry_run}")

    # 连接数据库
    print("\nConnecting to databases...")
    sqlite_conn = connect_sqlite(args.sqlite_path)
    pg_conn = connect_postgres(args.postgres_url)

    try:
        # 获取表列表
        tables = get_sqlite_tables(sqlite_conn)
        print(f"\nFound {len(tables)} tables: {', '.join(tables)}")

        # 迁移每个表
        for table in tables:
            try:
                source_count, target_count = migrate_table(
                    sqlite_conn,
                    pg_conn,
                    table,
                    batch_size=args.batch_size,
                    dry_run=args.dry_run,
                )
                stats.add_table(table, source_count, target_count)
            except Exception as e:
                print(f"\n❌ Error migrating table {table}: {e}")
                import traceback
                traceback.print_exc()
                stats.add_table(table, 0, 0)

        # 重置序列
        if not args.dry_run:
            reset_postgres_sequences(pg_conn)

        # 验证
        if args.verify and not args.dry_run:
            verify_migration(sqlite_conn, pg_conn, stats)

    finally:
        sqlite_conn.close()
        pg_conn.close()

    stats.finish()
    stats.print_summary()

    print("\n✅ Migration completed!")


if __name__ == "__main__":
    main()
