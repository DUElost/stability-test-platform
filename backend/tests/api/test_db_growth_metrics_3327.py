"""#3327: PostgreSQL table-growth gauges are a read-only /metrics view."""

from __future__ import annotations

from sqlalchemy import text

from backend.services import db_growth_metrics


def test_pg_stat_user_tables_reaches_metrics(client, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    monkeypatch.setattr(db_growth_metrics, "_LAST_SAMPLE_MONOTONIC", None)

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.text
    assert 'stability_db_table_total_bytes{schema="public",table="host"}' in body
    assert 'stability_db_table_live_rows_estimate{schema="public",table="host"}' in body
    assert 'stability_db_table_dead_rows_estimate{schema="public",table="host"}' in body
    assert 'stability_db_table_seq_scans{schema="public",table="host"}' in body
    assert 'stability_db_table_idx_scans{schema="public",table="host"}' in body
    assert "stability_db_growth_snapshot_timestamp_seconds" in body


def test_failed_catalog_read_keeps_metrics_endpoint_and_stale_marker(
    client, monkeypatch,
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    monkeypatch.setattr(db_growth_metrics, "_LAST_SAMPLE_MONOTONIC", None)
    before = client.get("/metrics").text
    marker = next(
        line for line in before.splitlines()
        if line.startswith("stability_db_growth_snapshot_timestamp_seconds ")
    )

    monkeypatch.setattr(db_growth_metrics, "_QUERY", db_growth_metrics.text("SELECT missing_column"))
    monkeypatch.setattr(db_growth_metrics, "_LAST_SAMPLE_MONOTONIC", None)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert marker in response.text
    assert 'stability_db_table_total_bytes{schema="public",table="host"}' in response.text


def test_removed_table_does_not_leave_frozen_series(client, db_session, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    table = "metrics_growth_probe_3327"
    try:
        db_session.execute(text("CREATE TABLE metrics_growth_probe_3327 (id integer)"))
        db_session.commit()
        monkeypatch.setattr(db_growth_metrics, "_LAST_SAMPLE_MONOTONIC", None)
        assert f'table="{table}"' in client.get("/metrics").text
    finally:
        db_session.execute(text("DROP TABLE IF EXISTS metrics_growth_probe_3327"))
        db_session.commit()

    monkeypatch.setattr(db_growth_metrics, "_LAST_SAMPLE_MONOTONIC", None)
    assert f'table="{table}"' not in client.get("/metrics").text


def test_failed_catalog_read_rolls_back_shared_session(db_session, monkeypatch):
    """/metrics 各组共享 session：失败的读必须 rollback，后续组才能在同一次 scrape 里继续查询。"""
    monkeypatch.setattr(db_growth_metrics, "_QUERY", text("SELECT missing_column"))
    monkeypatch.setattr(db_growth_metrics, "_LAST_SAMPLE_MONOTONIC", None)

    db_growth_metrics.refresh_db_growth_gauges(db_session)

    assert db_session.execute(text("SELECT 1")).scalar() == 1
