"""#1300（R15-R01）—— 显式 TEST_DATABASE_URL 的机器护栏。"""
from __future__ import annotations

import logging

import pytest

from backend.core.db_url_guard import UnsafeTestDatabaseUrl, guard_test_database_url


def test_isolated_naming_passes():
    url = "postgresql+psycopg://stp:pw@127.0.0.1:5432/stp_test"
    assert guard_test_database_url(url) == url


def test_isolated_naming_case_insensitive():
    url = "postgresql+psycopg://u:p@db.internal:5432/MyTestDB"
    assert guard_test_database_url(url) == url


@pytest.mark.parametrize("dbname", ["stp", "production", "appdb", ""])
def test_non_test_dbname_rejected(dbname):
    url = f"postgresql+psycopg://u:p@db.internal:5432/{dbname}"
    with pytest.raises(UnsafeTestDatabaseUrl, match="must contain 'test'"):
        guard_test_database_url(url)


def test_non_postgres_scheme_rejected():
    with pytest.raises(UnsafeTestDatabaseUrl, match="PostgreSQL"):
        guard_test_database_url("sqlite:///prod.db")


def test_identical_to_runtime_database_url_rejected():
    url = "postgresql+psycopg://stp:pw@127.0.0.1:5432/stp_test"
    with pytest.raises(UnsafeTestDatabaseUrl, match="identical to DATABASE_URL"):
        guard_test_database_url(url, runtime_database_url=url)


def test_different_from_runtime_database_url_passes():
    url = "postgresql+psycopg://stp:pw@127.0.0.1:5432/stp_test"
    runtime = "postgresql+psycopg://stp:pw@10.0.0.9:5432/stp_test"
    assert guard_test_database_url(url, runtime_database_url=runtime) == url


def test_override_env_bypasses_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("STP_ALLOW_UNSAFE_TEST_DATABASE_URL", "1")
    url = "postgresql+psycopg://u:p@prod-host:5432/production"
    with caplog.at_level(logging.WARNING, logger="backend.core.test_db_guard"):
        assert guard_test_database_url(url) == url
    assert any("test_db_guard_bypassed" in r.message for r in caplog.records)
