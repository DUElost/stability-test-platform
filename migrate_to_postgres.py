"""
SQLite to PostgreSQL data migration script.
Run this after the PostgreSQL tables are created.
"""
import os
import sqlite3
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Source SQLite database
SQLITE_DB = "d:/Tinno_auto/Stability-Tools/stability-test-platform/stability.db"

# Target PostgreSQL database
POSTGRES_URL = "postgresql://postgres:postgres@localhost:5432/stability_db"


def get_sqlite_columns(cursor, table_name):
    """Get column names from SQLite table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def migrate_table(table_name: str):
    """Migrate data from SQLite to PostgreSQL for a specific table."""
    # Connect to SQLite
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_cursor = sqlite_conn.cursor()

    # Get columns from both databases
    sqlite_cols = get_sqlite_columns(sqlite_cursor, table_name)

    # Get data from SQLite
    sqlite_cursor.execute(f"SELECT * FROM {table_name}")
    rows = sqlite_cursor.fetchall()

    if not rows:
        print(f"  No data to migrate for {table_name}")
        sqlite_conn.close()
        return

    # Connect to PostgreSQL to get column info
    engine = create_engine(POSTGRES_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Get PostgreSQL table columns
        result = session.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}'"))
        pg_cols = [row[0] for row in result.fetchall()]

        # Build INSERT statement for columns that exist in both
        common_cols = [col for col in sqlite_cols if col in pg_cols]

        # Build placeholders
        placeholders = ', '.join([f':{col}' for col in common_cols])
        insert_sql = text(f"INSERT INTO {table_name} ({', '.join(common_cols)}) VALUES ({placeholders})")

        # Insert each row
        for row in rows:
            row_dict = dict(zip(sqlite_cols, row))
            # Filter to only include common columns
            data = {k: v for k, v in row_dict.items() if k in common_cols}
            # Convert datetime strings to datetime objects
            for k, v in data.items():
                if isinstance(v, str):
                    # Try to parse datetime
                    try:
                        data[k] = datetime.fromisoformat(v.replace('Z', '+00:00'))
                    except:
                        pass
                # Convert integer to boolean for specific columns
                if k == 'adb_connected' and v is not None:
                    data[k] = bool(v)
                if k == 'enabled' and v is not None:
                    data[k] = bool(v)
            session.execute(insert_sql, data)

        session.commit()
        print(f"  Migrated {len(rows)} rows to {table_name}")

    except Exception as e:
        session.rollback()
        print(f"  Error migrating {table_name}: {e}")
        raise
    finally:
        session.close()
        sqlite_conn.close()


def migrate_users():
    """Migrate users table."""
    migrate_table('users')


def migrate_hosts():
    """Migrate hosts table."""
    migrate_table('hosts')


def migrate_devices():
    """Migrate devices table."""
    migrate_table('devices')


def migrate_tasks():
    """Migrate tasks table."""
    migrate_table('tasks')


def migrate_task_runs():
    """Migrate task_runs table."""
    migrate_table('task_runs')


def migrate_task_templates():
    """Migrate task_templates table."""
    migrate_table('task_templates')


def migrate_log_artifacts():
    """Migrate log_artifacts table."""
    migrate_table('log_artifacts')


def migrate_deployments():
    """Migrate deployments table."""
    migrate_table('deployments')


if __name__ == "__main__":
    print("Starting data migration from SQLite to PostgreSQL...")
    print(f"Source: {SQLITE_DB}")
    print(f"Target: {POSTGRES_URL}")
    print()

    try:
        print("Migrating users...")
        migrate_users()

        print("Migrating hosts...")
        migrate_hosts()

        print("Migrating devices...")
        migrate_devices()

        print("Migrating tasks...")
        migrate_tasks()

        print("Migrating task_runs...")
        migrate_task_runs()

        print("Migrating task_templates...")
        migrate_task_templates()

        print("Migrating log_artifacts...")
        migrate_log_artifacts()

        print("Migrating deployments...")
        migrate_deployments()

        print()
        print("Migration completed successfully!")

    except Exception as e:
        print(f"\nMigration failed: {e}")
        exit(1)
