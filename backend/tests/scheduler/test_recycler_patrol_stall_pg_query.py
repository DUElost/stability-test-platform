from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql

from backend.scheduler import recycler


def test_patrol_stall_pg_query_limits_and_orders_in_sql():
    stmt = recycler._build_patrol_stall_candidates_stmt(
        datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    )
    sql = str(stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "json_extract" not in sql, (
        f"PostgreSQL path must stay on JSONB operators, not sqlite json_extract().\nGot: {sql}"
    )
    assert "jsonb_array_length" in sql, (
        f"Expected init-step-count gating to happen in SQL.\nGot: {sql}"
    )
    assert "->> 'interval_seconds'" in sql, (
        f"Expected patrol interval extraction from pipeline_def in SQL.\nGot: {sql}"
    )
    assert "step_trace.stage = 'init'" in sql and "step_trace.event_type = 'COMPLETED'" in sql, (
        f"Expected first-cycle anchor to depend on completed init traces.\nGot: {sql}"
    )
    assert "ORDER BY" in sql and "DESC" in sql, (
        f"Expected overdue ordering to stay in SQL.\nGot: {sql}"
    )
    assert f"LIMIT {recycler.PATROL_STALL_BATCH_LIMIT}" in sql, (
        f"Expected top-N limiting in SQL instead of Python-side full scan.\nGot: {sql}"
    )
