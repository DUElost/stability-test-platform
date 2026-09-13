"""Health /health schema revision guard (#1882)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import backend.core.schema_revision as schema_revision_mod
import backend.main as main_mod

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.schema_revision import code_head_revision, is_schema_at_head


def test_code_head_revision_is_single_head():
    head = code_head_revision()
    assert isinstance(head, str)
    assert head


def test_is_schema_at_head_matches_equal_revision():
    assert is_schema_at_head("abc123", "abc123") is True


def test_is_schema_at_head_rejects_mismatch():
    assert is_schema_at_head("old", "new") is False


def test_is_schema_at_head_rejects_missing_db_revision():
    assert is_schema_at_head(None, "head") is False


class TestHealthSchemaRevision:
    def test_schema_not_at_head_returns_503_in_production(self, client, monkeypatch):
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setattr(schema_revision_mod, "code_head_revision", lambda: "head123")
        monkeypatch.setattr(
            schema_revision_mod,
            "database_revision",
            AsyncMock(return_value="old456"),
        )

        response = client.get("/health")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SCHEMA_NOT_AT_HEAD"

    def test_schema_at_head_includes_revision_fields(self, client, monkeypatch):
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "0")
        monkeypatch.setattr(schema_revision_mod, "code_head_revision", lambda: "head123")
        monkeypatch.setattr(
            schema_revision_mod,
            "database_revision",
            AsyncMock(return_value="head123"),
        )
        monkeypatch.setattr(main_mod, "redis_client", None)
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: True)

        response = client.get("/health")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["alembic_revision"] == "head123"
        assert data["alembic_head"] == "head123"
