import os
os.environ['DATABASE_URL'] = 'postgresql://postgres@localhost:5432/stability_db'

from backend.core.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM users"))
    print(f"Users in DB: {result.scalar()}")

    result = conn.execute(text("SELECT COUNT(*) FROM hosts"))
    print(f"Hosts in DB: {result.scalar()}")

    result = conn.execute(text("SELECT COUNT(*) FROM devices"))
    print(f"Devices in DB: {result.scalar()}")

    result = conn.execute(text("SELECT COUNT(*) FROM tasks"))
    print(f"Tasks in DB: {result.scalar()}")
