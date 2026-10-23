"""#3723 §3.4.2 — real CLI main(argv) NOWAIT lock release + failure isolation.

Service-helper tests cannot substitute for these two mandatory paths.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import sqlalchemy
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import LeaseStatus
from backend.models.host import Device, Host
from backend.scripts import init_device_return as init_cli
from backend.services.device_return import init_device_return_sync

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")
pytestmark = pytest.mark.skipif(
    not IS_PG, reason="§3.4.2 NOWAIT / CLI txn isolation requires PostgreSQL"
)

_NOW = datetime.now(timezone.utc)


def _ensure_open_source_index(db: Session) -> None:
    db.execute(text(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_device_return_debt_open_source
        ON device_return_debt (
            device_id, family, source_kind, (COALESCE(source_job_id, 0))
        )
        WHERE state = 'open'
        """
    ))
    db.commit()


def _seed_plain_device(db: Session, *, serial_prefix: str = "CLI") -> Device:
    """Minimal online device (no job / lease) for init CLI coverage."""
    _ensure_open_source_index(db)
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"cli-{suffix}",
        hostname=f"cli-{suffix}",
        status="ONLINE",
        last_heartbeat=_NOW,
        created_at=_NOW,
    )
    device = Device(
        serial=f"{serial_prefix}-{suffix}",
        host_id=host.id,
        status="ONLINE",
        tags=[],
        created_at=_NOW,
        last_seen=_NOW,
        lease_generation=1,
    )
    db.add_all([host, device])
    db.commit()
    db.refresh(device)
    return device


def _nowait_lock_error(db: Session, device_id: int) -> BaseException | None:
    """Probe FOR UPDATE NOWAIT; None means lock acquired (row free or ours)."""
    try:
        db.execute(
            select(Device).where(Device.id == device_id).with_for_update(nowait=True)
        )
        return None
    except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
        db.rollback()
        return exc


def _active_lease_count(db: Session, device_id: int) -> int:
    return (
        db.query(DeviceLease)
        .filter(
            DeviceLease.device_id == device_id,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
        .count()
    )


def _open_debt_count(db: Session, device_id: int) -> int:
    return (
        db.query(DeviceReturnDebt)
        .filter(
            DeviceReturnDebt.device_id == device_id,
            DeviceReturnDebt.state == "open",
        )
        .count()
    )


class TestInitCliNowaitCrossDevice:
    """§3.4.2 #5: before device-2 entry, independent NOWAIT must lock device-1."""

    @pytest.mark.parametrize("apply_flag", [False, True], ids=["dry-run", "apply"])
    def test_main_releases_device1_lock_before_device2_entry(
        self, db_session, monkeypatch, apply_flag
    ):
        d1 = _seed_plain_device(db_session, serial_prefix="NWA")
        d2 = _seed_plain_device(db_session, serial_prefix="NWB")
        assert d1.id < d2.id
        db_session.commit()
        db_session.expire_all()

        # Restrict CLI to exactly these two devices (id ASC), ignoring ambient rows.
        only = sorted([d1.id, d2.id])

        def fetch_only_two(*, after_id: int, limit: int):
            return [i for i in only if i > after_id][:limit]

        monkeypatch.setattr(init_cli, "_fetch_device_id_page", fetch_only_two)
        monkeypatch.setattr(init_cli, "PAGE_SIZE", 10)

        nowait_ok: dict[str, bool] = {}

        def on_enter(device_id: int) -> None:
            if device_id != d2.id:
                return
            # Second-device entry: device-1 txn must already be closed.
            probe = SessionLocal()
            try:
                err = _nowait_lock_error(probe, d1.id)
                nowait_ok["device1"] = err is None
                if err is None:
                    probe.rollback()
            finally:
                probe.close()

        monkeypatch.setattr(init_cli, "_device_enter_hook", on_enter)

        argv = ["--apply"] if apply_flag else ["--dry-run"]
        rc = init_cli.main(argv)
        assert rc == 0, f"CLI exit={rc} apply={apply_flag}"
        assert nowait_ok.get("device1") is True, (
            "NOWAIT on device1 at device2 entry failed — prior Session still held "
            f"the row lock (apply={apply_flag})"
        )

        # Dry-run: zero business writes. Apply: at most one ACTIVE lease per device.
        db_session.expire_all()
        if not apply_flag:
            assert _open_debt_count(db_session, d1.id) == 0
            assert _open_debt_count(db_session, d2.id) == 0
            assert db_session.get(Device, d1.id).return_state in (None, {})
            assert db_session.get(Device, d2.id).return_state in (None, {})
            assert _active_lease_count(db_session, d1.id) == 0
            assert _active_lease_count(db_session, d2.id) == 0
        else:
            assert _active_lease_count(db_session, d1.id) <= 1
            assert _active_lease_count(db_session, d2.id) <= 1


class TestInitCliFailureIsolation:
    """§3.4.2 #5: device1 committed + device2 real failure → device1 kept, device2 clean."""

    def test_main_apply_keeps_device1_when_device2_fails(
        self, db_session, monkeypatch
    ):
        d1 = _seed_plain_device(db_session, serial_prefix="FIA")
        d2 = _seed_plain_device(db_session, serial_prefix="FIB")
        assert d1.id < d2.id
        db_session.commit()

        only = sorted([d1.id, d2.id])

        def fetch_only_two(*, after_id: int, limit: int):
            return [i for i in only if i > after_id][:limit]

        monkeypatch.setattr(init_cli, "_fetch_device_id_page", fetch_only_two)

        real_init = init_device_return_sync

        def flaky_init(db, *, device_id, dry_run=True):
            summary = real_init(db, device_id=device_id, dry_run=dry_run)
            if device_id == d2.id:
                # Real failure after service wrote into this Session — CLI must
                # roll back only this device's txn.
                raise RuntimeError("injected device2 failure")
            return summary

        monkeypatch.setattr(init_cli, "init_device_return_sync", flaky_init)

        rc = init_cli.main(["--apply"])
        assert rc == 1

        db_session.expire_all()
        d1_row = db_session.get(Device, d1.id)
        d2_row = db_session.get(Device, d2.id)
        assert d1_row.return_state, "device1 apply must remain committed"
        assert isinstance(d1_row.return_state, dict) and len(d1_row.return_state) > 0
        # device2: no partial return_state / open debts from the failed txn
        assert d2_row.return_state in (None, {})
        assert _open_debt_count(db_session, d2.id) == 0
        assert _active_lease_count(db_session, d2.id) == 0
        # No second ACTIVE on device1 either
        assert _active_lease_count(db_session, d1.id) <= 1


class TestInitCliKeysetLimitAndRetry:
    def test_keyset_limit_spans_pages(self, db_session, monkeypatch):
        devices = [
            _seed_plain_device(db_session, serial_prefix=f"KS{i}")
            for i in range(3)
        ]
        devices.sort(key=lambda d: d.id)
        db_session.commit()
        id_set = {d.id for d in devices}

        seen_pages: list[list[int]] = []

        def tracking_fetch(*, after_id: int, limit: int):
            ids = [i for i in sorted(id_set) if i > after_id][:limit]
            seen_pages.append(list(ids))
            return ids

        monkeypatch.setattr(init_cli, "_fetch_device_id_page", tracking_fetch)
        monkeypatch.setattr(init_cli, "PAGE_SIZE", 1)

        processed: list[int] = []

        def on_enter(device_id: int) -> None:
            processed.append(device_id)

        monkeypatch.setattr(init_cli, "_device_enter_hook", on_enter)

        rc = init_cli.main(["--dry-run", "--limit", "2"])
        assert rc == 0
        assert processed == [devices[0].id, devices[1].id]
        assert len(seen_pages) >= 2, "limit=2 with PAGE_SIZE=1 must cross pages"
        assert all(len(p) <= 1 for p in seen_pages)

    def test_claim_conflict_retries_then_exhausts(self, db_session, monkeypatch):
        d1 = _seed_plain_device(db_session, serial_prefix="CAS")
        db_session.commit()

        def fetch_one(*, after_id: int, limit: int):
            if after_id >= d1.id:
                return []
            return [d1.id][:limit]

        monkeypatch.setattr(init_cli, "_fetch_device_id_page", fetch_one)

        sleeps: list[float] = []
        monkeypatch.setattr(init_cli.time, "sleep", lambda s: sleeps.append(s))

        calls = {"n": 0}

        def always_conflict(db, *, device_id, dry_run=True):
            calls["n"] += 1
            return {
                "device_id": device_id,
                "status": "lease_conflict_retry",
                "dry_run": dry_run,
            }

        monkeypatch.setattr(init_cli, "init_device_return_sync", always_conflict)

        rc = init_cli.main(["--apply"])
        assert rc == 1
        assert calls["n"] == 3
        assert sleeps == [0.1, 0.2]

    def test_dry_run_zero_writes_idempotent(self, db_session, monkeypatch):
        d1 = _seed_plain_device(db_session, serial_prefix="DRY")
        db_session.commit()
        before_state = db_session.get(Device, d1.id).return_state
        before_debts = _open_debt_count(db_session, d1.id)
        before_leases = _active_lease_count(db_session, d1.id)

        def fetch_one(*, after_id: int, limit: int):
            if after_id >= d1.id:
                return []
            return [d1.id][:limit]

        monkeypatch.setattr(init_cli, "_fetch_device_id_page", fetch_one)

        assert init_cli.main(["--dry-run"]) == 0
        assert init_cli.main(["--dry-run"]) == 0

        db_session.expire_all()
        assert db_session.get(Device, d1.id).return_state == before_state
        assert _open_debt_count(db_session, d1.id) == before_debts
        assert _active_lease_count(db_session, d1.id) == before_leases

    def test_apply_idempotent_no_second_active(self, db_session, monkeypatch):
        d1 = _seed_plain_device(db_session, serial_prefix="IDM")
        db_session.commit()

        def fetch_one(*, after_id: int, limit: int):
            if after_id >= d1.id:
                return []
            return [d1.id][:limit]

        monkeypatch.setattr(init_cli, "_fetch_device_id_page", fetch_one)

        assert init_cli.main(["--apply"]) == 0
        assert init_cli.main(["--apply"]) == 0

        db_session.expire_all()
        assert _active_lease_count(db_session, d1.id) <= 1
        state = db_session.get(Device, d1.id).return_state
        assert isinstance(state, dict) and state
