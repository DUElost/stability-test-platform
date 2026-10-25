"""B6-U9 / #3723 §3.9.4b: empty migration and legacy data on testcontainers PG."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host

ROOT = Path(__file__).resolve().parents[3]
PARENT = "u8b6d3752a01"
REVISION = "u9b6d3793a01"
EXPECTED_EXPIRY = datetime(9999, 1, 1, tzinfo=timezone.utc)
NORMAL_EXPIRY = datetime(2026, 10, 25, 12, tzinfo=timezone.utc) + timedelta(hours=1)


def _run(env, *args, backend=False):
    completed = subprocess.run(
        [sys.executable, "-m", *args],
        cwd=ROOT / "backend" if backend else ROOT,
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _snapshot(engine):
    # Fresh psycopg reads in UTC+14 also prove migrated history can be decoded.
    with engine.connect() as connection:
        assert connection.scalar(text("SHOW TIMEZONE")) == "Pacific/Kiritimati"
        return connection.execute(select(
            DeviceLease.id, DeviceLease.lease_type, DeviceLease.status,
            DeviceLease.expires_at, DeviceLease.fencing_token,
            DeviceLease.reason, DeviceLease.holder,
        ).order_by(DeviceLease.id)).all()


def test_empty_upgrade_and_legacy_expiry_roundtrip():
    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        url = pg.get_connection_url()
        env = dict(os.environ, DATABASE_URL=url, TEST_DATABASE_URL=url)
        _run(env, "alembic", "upgrade", "head", backend=True)
        _run(env, "backend.scripts.check_schema_sync")
        _run(env, "backend.scripts.check_seed_identity")
        _run(env, "alembic", "downgrade", PARENT, backend=True)

        engine = create_engine(url, connect_args={"options": "-c timezone=Pacific/Kiritimati"})
        try:
            with Session(engine) as db:
                host = Host(id="u9-migration", hostname="u9-migration", status="ONLINE")
                db.add(host)
                db.flush()
                cases = [
                    (status, lease_type)
                    for status in ("ACTIVE", "RELEASED")
                    for lease_type in ("MAINTENANCE", "JOB")
                ] + [("ACTIVE", "JOB"), ("ACTIVE", "MAINTENANCE")]
                leases = []
                for index, (status, lease_type) in enumerate(cases):
                    device = Device(serial=f"U9-MIGRATION-{index}", host_id=host.id, status="ONLINE")
                    db.add(device)
                    db.flush()
                    lease = DeviceLease(
                        device_id=device.id, host_id=host.id,
                        lease_type=lease_type, status=status,
                        fencing_token=f"{device.id}:1", lease_generation=1,
                        agent_instance_id="platform", reason="归还欠账", holder="platform",
                        expires_at=EXPECTED_EXPIRY if index == 5 else NORMAL_EXPIRY,
                    )
                    db.add(lease)
                    leases.append(lease)
                db.flush()
                legacy_ids = [lease.id for lease in leases[:4]]
                normal_id, boundary_id = leases[4].id, leases[5].id
                # Write old values as SQL literals so setup never decodes year 10K.
                db.execute(update(DeviceLease).where(DeviceLease.id.in_(legacy_ids)).values(
                    expires_at=text("TIMESTAMPTZ '9999-12-31 23:59:59+00'"),
                ))
                db.commit()

            _run(env, "alembic", "upgrade", REVISION, backend=True)
            migrated = _snapshot(engine)
            assert len(migrated) == 6
            assert {(row.status, row.lease_type) for row in migrated if row.id in legacy_ids} == {
                (status, lease_type)
                for status in ("ACTIVE", "RELEASED")
                for lease_type in ("MAINTENANCE", "JOB")
            }
            for row in migrated:
                assert row.expires_at == (NORMAL_EXPIRY if row.id == normal_id else EXPECTED_EXPIRY)
            assert next(row for row in migrated if row.id == boundary_id).expires_at == EXPECTED_EXPIRY

            _run(env, "alembic", "upgrade", REVISION, backend=True)
            assert _snapshot(engine) == migrated
            _run(env, "alembic", "downgrade", PARENT, backend=True)
            assert _snapshot(engine) == migrated
            # Replay the actual revision body, not only an already-at-head no-op.
            _run(env, "alembic", "upgrade", REVISION, backend=True)
            assert _snapshot(engine) == migrated
        finally:
            engine.dispose()
