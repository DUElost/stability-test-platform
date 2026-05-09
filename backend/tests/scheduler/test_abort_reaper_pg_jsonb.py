"""P1: verify abort reaper query compiles to valid PostgreSQL JSONB operators.

This test does NOT require a database — it only checks SQL compilation.
"""

from __future__ import annotations

import pytest


def test_abort_reaper_query_uses_pg_jsonb_operators_not_json_extract():
    """P1: abort reaper query MUST use PG JSONB -> / ->> operators,
    NOT MySQL/SQLite json_extract()."""
    from sqlalchemy import Column, Integer, String, select
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql import JSONB as PGJSONB
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    class _Job(Base):
        __tablename__ = "job_instance"
        id = Column(Integer, primary_key=True)
        plan_run_id = Column(Integer)
        status = Column(String)

    class _PlanRun(Base):
        __tablename__ = "plan_run"
        id = Column(Integer, primary_key=True)
        run_context = Column(PGJSONB)

    # Replicate the exact expression from _reconcile_aborted_running_jobs
    abort_at_text = _PlanRun.run_context["abort_requested"]["at"].astext
    stmt = (
        select(_Job, _PlanRun)
        .join(_PlanRun, _PlanRun.id == _Job.plan_run_id)
        .where(
            _Job.status == "RUNNING",
            abort_at_text.isnot(None),
            abort_at_text < "2025-01-01T00:00:00+00:00",
        )
    )

    sql = str(stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "json_extract" not in sql, (
        f"json_extract is MySQL/SQLite only, not valid for PostgreSQL.\nGot: {sql}"
    )
    assert "->>" in sql, (
        f"Expected ->> operator for text extraction from JSONB.\nGot: {sql}"
    )
    assert "->" in sql, (
        f"Expected -> operator for JSONB key access.\nGot: {sql}"
    )
