"""#135 — script catalog digest cache."""

from unittest.mock import MagicMock

import pytest

from backend.services.script_catalog_version import (
    catalog_digest,
    compute_script_catalog_version,
    invalidate_script_catalog_version_cache,
)


class TestScriptCatalogVersionCache:
    def setup_method(self):
        invalidate_script_catalog_version_cache()

    def teardown_method(self):
        invalidate_script_catalog_version_cache()

    def test_cache_avoids_repeated_db_execute(self, monkeypatch):
        monkeypatch.setenv("STP_SCRIPT_CATALOG_VERSION_CACHE_TTL", "60")
        db = MagicMock()
        row = MagicMock()
        row.name = "demo"
        row.version = "1.0.0"
        row.content_sha256 = "abc"
        db.execute.return_value.all.return_value = [row]

        first = compute_script_catalog_version(db)
        second = compute_script_catalog_version(db)

        assert first == second
        assert db.execute.call_count == 1

    def test_zero_ttl_disables_cache(self, monkeypatch):
        monkeypatch.setenv("STP_SCRIPT_CATALOG_VERSION_CACHE_TTL", "0")
        db = MagicMock()
        row = MagicMock()
        row.name = "demo"
        row.version = "1.0.0"
        row.content_sha256 = "abc"
        db.execute.return_value.all.return_value = [row]

        compute_script_catalog_version(db)
        compute_script_catalog_version(db)

        assert db.execute.call_count == 2

    def test_invalidate_forces_refresh(self, monkeypatch):
        monkeypatch.setenv("STP_SCRIPT_CATALOG_VERSION_CACHE_TTL", "60")
        db = MagicMock()
        row = MagicMock()
        row.name = "demo"
        row.version = "1.0.0"
        row.content_sha256 = "abc"
        db.execute.return_value.all.return_value = [row]

        compute_script_catalog_version(db)
        invalidate_script_catalog_version_cache()
        compute_script_catalog_version(db)

        assert db.execute.call_count == 2

    def test_orm_insert_invalidates_cache(self, monkeypatch, db_session):
        """Script row commits must bust cache (heartbeat outdated detection)."""
        monkeypatch.setenv("STP_SCRIPT_CATALOG_VERSION_CACHE_TTL", "60")
        from backend.models.script import Script

        before = compute_script_catalog_version(db_session)
        db_session.add(Script(
            name="cache_bust_probe", display_name="cache_bust_probe",
            category="device", script_type="python", version="1.0.0",
            nfs_path="/s/cache_bust_probe/v1.0.0/cache_bust_probe.py",
            content_sha256="sha-probe", param_schema={}, default_params={},
            is_active=True,
        ))
        db_session.commit()
        after = compute_script_catalog_version(db_session)
        assert before != after

    def test_digest_unchanged_by_cache_layer(self):
        entries = [("s", "1.0.0", "sha")]
        assert catalog_digest(entries) == catalog_digest(entries)
