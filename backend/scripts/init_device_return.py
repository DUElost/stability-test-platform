#!/usr/bin/env python3
"""Stage-B device-return initialization (ADR-0061 D1.7 / D5.11 / #3723 §3.4.2).

Default is ``--dry-run`` (no writes). Pass ``--apply`` to persist.
``--release-all-debt-leases`` is the rollback helper (audited).

§3.4.2: device-id keyset pages (100), per-device Session/top-level txn,
claim CAS retry (max 3, backoff 0.1s/0.2s), exit 1 on any failure/exhaustion.

Does not talk to production unless DATABASE_URL is pointed there by an
operator — this script refuses to guess a target.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

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

# §3.4.2 — tests may monkeypatch PAGE_SIZE to a small value for cross-page coverage.
PAGE_SIZE = 100
MAX_CLAIM_ATTEMPTS = 3
# Backoff after attempt 1 and 2 respectively (total wait 0.1s then 0.2s).
CLAIM_BACKOFF_SECONDS = (0.1, 0.2)

# Optional test hook: called with device_id immediately before opening that
# device's Session (proves prior device txn is closed / locks released).
_device_enter_hook: Callable[[int], None] | None = None


def _fetch_device_id_page(*, after_id: int, limit: int) -> list[int]:
    """Short-lived read txn: keyset page of non-retired device ids (id ASC)."""
    if limit <= 0:
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(Device.id)
            .filter(Device.retired_at.is_(None), Device.id > after_id)
            .order_by(Device.id.asc())
            .limit(limit)
            .all()
        )
        return [int(r[0]) for r in rows]
    finally:
        db.close()


def _classify_status(status: str | None) -> str:
    if status == "ok":
        return "ok"
    if status == "occupied_debt_recorded":
        return "occupied"
    if status in {"retired_skipped", "not_found"}:
        return "skipped"
    if status == "conflict_exhausted":
        return "conflict_exhausted"
    if status == "failed":
        return "failed"
    if status == "lease_conflict_retry":
        return "conflict_exhausted"
    return "skipped"


def _process_one_device(device_id: int, *, dry_run: bool) -> dict[str, Any]:
    """Independent Session / top-level txn per device with claim CAS retry.

    dry-run: full rollback + close before returning.
    apply: commit + close on success/occupied/skip; rollback on failure.
    """
    if _device_enter_hook is not None:
        _device_enter_hook(device_id)

    last_summary: dict[str, Any] | None = None
    for attempt in range(1, MAX_CLAIM_ATTEMPTS + 1):
        db = SessionLocal()
        try:
            try:
                summary = init_device_return_sync(
                    db, device_id=device_id, dry_run=dry_run,
                )
            except IntegrityError:
                db.rollback()
                last_summary = {
                    "device_id": device_id,
                    "status": "lease_conflict_retry",
                    "attempt": attempt,
                }
                if attempt < MAX_CLAIM_ATTEMPTS:
                    time.sleep(CLAIM_BACKOFF_SECONDS[attempt - 1])
                    continue
                return {
                    "device_id": device_id,
                    "status": "conflict_exhausted",
                    "attempt": attempt,
                }

            status = summary.get("status")
            if status == "lease_conflict_retry":
                # Whole-device txn must roll back; retry on a fresh Session.
                db.rollback()
                last_summary = dict(summary)
                last_summary["attempt"] = attempt
                if attempt < MAX_CLAIM_ATTEMPTS:
                    time.sleep(CLAIM_BACKOFF_SECONDS[attempt - 1])
                    continue
                summary = dict(summary)
                summary["status"] = "conflict_exhausted"
                summary["attempt"] = attempt
                return summary

            if dry_run:
                db.rollback()
            else:
                db.commit()
            return summary
        except Exception:
            db.rollback()
            logger.exception("device=%s failed", device_id)
            return {"device_id": device_id, "status": "failed", "attempt": attempt}
        finally:
            db.close()

    return last_summary or {
        "device_id": device_id,
        "status": "conflict_exhausted",
    }


def _run_release_all_debt_leases(*, dry_run: bool) -> int:
    db = SessionLocal()
    try:
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
        released = 0
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
            released += 1
        db.commit()
        logger.info("released_debt_leases=%s", released)
        return 0
    except Exception:
        db.rollback()
        logger.exception("release_all_debt_leases failed")
        return 1
    finally:
        db.close()


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
        help="Optional max devices to process across pages (0 = all).",
    )
    args = parser.parse_args(argv)
    dry_run = not args.apply

    if args.release_all_debt_leases:
        return _run_release_all_debt_leases(dry_run=dry_run)

    stats = {
        "ok": 0,
        "occupied": 0,
        "skipped": 0,
        "failed": 0,
        "conflict_exhausted": 0,
    }
    processed = 0
    after_id = 0
    remaining = args.limit if args.limit > 0 else None

    while True:
        page_limit = PAGE_SIZE
        if remaining is not None:
            if remaining <= 0:
                break
            page_limit = min(PAGE_SIZE, remaining)

        page_ids = _fetch_device_id_page(after_id=after_id, limit=page_limit)
        if not page_ids:
            break

        for device_id in page_ids:
            summary = _process_one_device(device_id, dry_run=dry_run)
            bucket = _classify_status(summary.get("status"))
            stats[bucket] = stats.get(bucket, 0) + 1
            processed += 1
            if remaining is not None:
                remaining -= 1
            logger.info(
                "device=%s status=%s acquired=%s bucket=%s",
                device_id,
                summary.get("status"),
                summary.get("acquired_debt_lease"),
                bucket,
            )

        after_id = page_ids[-1]
        if len(page_ids) < page_limit:
            break
        if remaining is not None and remaining <= 0:
            break

    hard_fail = stats["failed"] + stats["conflict_exhausted"]
    exit_code = 1 if hard_fail else 0
    logger.info(
        "done dry_run=%s ok=%s occupied=%s skipped=%s failed=%s "
        "conflict_exhausted=%s total=%s exit=%s",
        dry_run,
        stats["ok"],
        stats["occupied"],
        stats["skipped"],
        stats["failed"],
        stats["conflict_exhausted"],
        processed,
        exit_code,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
