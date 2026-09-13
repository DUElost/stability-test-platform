"""Health /health schema revision guard (#1882)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_schema_revision():
    path = ROOT / "backend" / "core" / "schema_revision.py"
    spec = importlib.util.spec_from_file_location("schema_revision_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_code_head_revision_is_single_head():
    head = _load_schema_revision().code_head_revision()
    assert isinstance(head, str)
    assert head


def test_is_schema_at_head_matches_equal_revision():
    assert _load_schema_revision().is_schema_at_head("abc123", "abc123") is True


def test_is_schema_at_head_rejects_mismatch():
    assert _load_schema_revision().is_schema_at_head("old", "new") is False


def test_is_schema_at_head_rejects_missing_db_revision():
    assert _load_schema_revision().is_schema_at_head(None, "head") is False
