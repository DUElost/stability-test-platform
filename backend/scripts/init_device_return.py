#!/usr/bin/env python3
"""Stage-B device-return initialization (ADR-0061 D1.7 / D5.11 / #3723 §3.0 第 9 条).

Default is ``--dry-run`` (no writes). Pass ``--apply`` to persist.
``--release-all-debt-leases`` is the rollback helper (audited).

Does not talk to production unless DATABASE_URL is pointed there by an
operator — this script refuses to guess a target.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import LeaseStatus
from backend.models.host import Device
from backend.services.audit_writer import record_audit
from backend.services.device_return import (
    DEBT_LEASE_REASON,
    init_device_return_sync,
    is_debt_lease,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("init_device_return")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview only (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist return_state / debt leases (clears dry-run).",
    )
    parser.add_argument(
        "--release-all-debt-leases",
        action="store_true",
        help="Rollback helper: release all ACTIVE return-debt leases (audited).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max devices to process (0 = all).",
    )
    args = parser.parse_args(argv)
    dry_run = not args.apply

    db = SessionLocal()
    try:
        if args.release_all_debt_leases:
            if dry_run:
                logger.info("dry-run: would release all debt leases")
                q = (
                    db.query(DeviceLease)
                    .filter(
                        DeviceLease.status == LeaseStatus.ACTIVE.value,
                        DeviceLease.reason == DEBT_LEASE_REASON,
                    )
                )
                logger.info("candidates=%s", q.count())
                return 0
            now = datetime.now(timezone.utc)
            leases = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                    DeviceLease.reason == DEBT_LEASE_REASON,
                )
                .all()
            )
            for lease in leases:
                if not is_debt_lease(lease):
                    continue
                lease.status = LeaseStatus.RELEASED.value
                lease.released_at = now
                record_audit(
                    db,
                    action="release_all_debt_leases",
                    resource_type="device",
                    resource_id=lease.device_id,
                    details={"lease_id": lease.id, "fencing_token": lease.fencing_token},
                    username="init_device_return",
                    strict=True,
                )
            db.commit()
            logger.info("released_debt_leases=%s", len(leases))
            return 0

        q = db.query(Device).filter(Device.retired_at.is_(None)).order_by(Device.id)
        if args.limit > 0:
            q = q.limit(args.limit)
        devices = q.all()
        ok = conflict = skipped = 0
        for device in devices:
            try:
                with db.begin_nested():
                    summary = init_device_return_sync(
                        db, device_id=device.id, dry_run=dry_run,
                    )
                status = summary.get("status")
                if status == "ok":
                    ok += 1
                elif status == "lease_conflict_retry":
                    conflict += 1
                else:
                    skipped += 1
                logger.info(
                    "device=%s serial=%s status=%s acquired=%s",
                    device.id,
                    device.serial,
                    status,
                    summary.get("acquired_debt_lease"),
                )
            except Exception:
                logger.exception("device=%s failed", device.id)
                skipped += 1
        if not dry_run:
            db.commit()
        logger.info(
            "done dry_run=%s ok=%s conflict=%s skipped=%s total=%s",
            dry_run, ok, conflict, skipped, len(devices),
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
