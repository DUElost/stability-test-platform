"""ADR-0026 P2-3 — verify scale indexes exist on ORM metadata / PG catalog."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun, PlanRunHost, PlanRunTargetDevice


EXPECTED_INDEXES = {
    "plan_run": {
        "idx_plan_run_admission_queue",  # QUEUED dequeue (partial)
        "idx_plan_run_status",
    },
    "plan_run_host": {
        "uq_plan_run_host",
        "idx_plan_run_host_host_phase",
    },
    "plan_run_target_device": {
        "uq_plan_run_target_device",
        "idx_prtd_device",
        "idx_prtd_plan_run_host",
        "idx_prtd_plan_run_sort",
    },
    "job_instance": {
        "idx_job_instance_plan_run_status",
        "idx_job_instance_patrol_heartbeat",
    },
}


def _orm_index_names(table) -> set[str]:
    names = {idx.name for idx in table.indexes if idx.name}
    for constraint in table.constraints:
        name = getattr(constraint, "name", None)
        if name:
            names.add(name)
    return names


@pytest.mark.parametrize("table_key,model", [
    ("plan_run", PlanRun),
    ("plan_run_host", PlanRunHost),
    ("plan_run_target_device", PlanRunTargetDevice),
    ("job_instance", JobInstance),
])
def test_adr0026_scale_indexes_declared_on_orm(table_key, model):
    declared = _orm_index_names(model.__table__)
    missing = EXPECTED_INDEXES[table_key] - declared
    assert not missing, f"{table_key} missing indexes: {sorted(missing)}"


def test_adr0026_admission_queue_index_is_partial_queued():
    """Pump FOR UPDATE SKIP LOCKED scan uses partial QUEUED index."""
    idx = next(
        i for i in PlanRun.__table__.indexes
        if i.name == "idx_plan_run_admission_queue"
    )
    dialect_opts = idx.dialect_options.get("postgresql", {})
    where = dialect_opts.get("where")
    assert where is not None
    assert "QUEUED" in str(where)


def test_adr0026_scale_indexes_present_in_postgres(db_session):
    """When running against PostgreSQL, assert indexes exist in the catalog."""
    bind = db_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL-only catalog check")

    insp = inspect(bind)
    for table_name, expected in EXPECTED_INDEXES.items():
        present = {ix["name"] for ix in insp.get_indexes(table_name)}
        # UniqueConstraints may appear as unique indexes.
        for uq in insp.get_unique_constraints(table_name):
            if uq.get("name"):
                present.add(uq["name"])
        missing = expected - present
        assert not missing, f"{table_name} PG missing: {sorted(missing)}"
