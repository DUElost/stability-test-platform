"""ADR-0026 P1 step 1/1.1 — snapshot-table schema contracts.

Schema-only guarantees that must hold BEFORE the admission-queue feature flag
ever writes snapshot rows:
  - deleting a PlanRun (retention-style direct delete) cascades to
    plan_run_host + plan_run_target_device (no FK blockage);
  - a target-device row cannot reference another PlanRun's host-group row
    (composite consistency FK);
  - (plan_run_id, device_id) uniqueness on the target snapshot.
"""
from __future__ import annotations

import pytest
import sqlalchemy.exc
from sqlalchemy import select

from backend.models.host import Device, Host
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost, PlanRunTargetDevice


@pytest.fixture
def snapshot_fixture(db_session):
    plan = Plan(name="snapshot-schema")
    h1 = Host(id="snap-h1", hostname="snap-h1")
    h2 = Host(id="snap-h2", hostname="snap-h2")
    db_session.add_all([plan, h1, h2])
    db_session.flush()
    d1 = Device(serial="snap-d1", host_id="snap-h1")
    d2 = Device(serial="snap-d2", host_id="snap-h1")
    db_session.add_all([d1, d2])
    db_session.flush()

    r1 = PlanRun(plan_id=plan.id, status="RUNNING", failure_threshold=0.05,
                 plan_snapshot={}, run_type="MANUAL")
    r2 = PlanRun(plan_id=plan.id, status="RUNNING", failure_threshold=0.05,
                 plan_snapshot={}, run_type="MANUAL")
    db_session.add_all([r1, r2])
    db_session.flush()

    g1 = PlanRunHost(plan_run_id=r1.id, host_id="snap-h1", device_count=2)
    g2 = PlanRunHost(plan_run_id=r2.id, host_id="snap-h2", device_count=0)
    db_session.add_all([g1, g2])
    db_session.flush()

    t1 = PlanRunTargetDevice(
        plan_run_id=r1.id, plan_run_host_id=g1.id,
        device_id=d1.id, host_id_snapshot="snap-h1", sort_order=0,
    )
    db_session.add(t1)
    db_session.commit()
    return {"r1": r1, "r2": r2, "g1": g1, "g2": g2, "d1": d1, "d2": d2}


class TestSnapshotTableContracts:
    def test_plan_run_delete_cascades_to_snapshot_rows(
        self, db_session, snapshot_fixture,
    ):
        """Retention cleanup deletes PlanRun directly (cron_scheduler
        run_retention_cleanup) — snapshot children must cascade, not block."""
        r1 = snapshot_fixture["r1"]
        r1_id = r1.id
        db_session.delete(r1)
        db_session.commit()

        assert db_session.execute(
            select(PlanRunHost).where(PlanRunHost.plan_run_id == r1_id)
        ).scalars().first() is None
        assert db_session.execute(
            select(PlanRunTargetDevice).where(PlanRunTargetDevice.plan_run_id == r1_id)
        ).scalars().first() is None

    def test_target_device_rejects_cross_run_host_group(
        self, db_session, snapshot_fixture,
    ):
        """Composite FK: a target row of PlanRun r1 must not reference
        PlanRun r2's host-group row — two individually-valid FKs, invalid pair."""
        bad = PlanRunTargetDevice(
            plan_run_id=snapshot_fixture["r1"].id,
            plan_run_host_id=snapshot_fixture["g2"].id,  # belongs to r2
            device_id=snapshot_fixture["d2"].id,
            host_id_snapshot="snap-h2",
        )
        db_session.add(bad)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_target_device_unique_per_run(self, db_session, snapshot_fixture):
        dup = PlanRunTargetDevice(
            plan_run_id=snapshot_fixture["r1"].id,
            plan_run_host_id=snapshot_fixture["g1"].id,
            device_id=snapshot_fixture["d1"].id,  # already targeted by r1
            host_id_snapshot="snap-h1",
        )
        db_session.add(dup)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_same_run_host_group_accepts(self, db_session, snapshot_fixture):
        ok_row = PlanRunTargetDevice(
            plan_run_id=snapshot_fixture["r1"].id,
            plan_run_host_id=snapshot_fixture["g1"].id,
            device_id=snapshot_fixture["d2"].id,
            host_id_snapshot="snap-h1",
            sort_order=1,
        )
        db_session.add(ok_row)
        db_session.commit()
        assert ok_row.id is not None
