"""Tests for tools/dev/check_alembic_at_head.py (#1882)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "dev" / "check_alembic_at_head.py"
PY = sys.executable


def _load_checker_module():
    spec = importlib.util.spec_from_file_location("check_alembic_at_head", CHECKER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_code_head_revision_returns_single_head():
    mod = _load_checker_module()
    head = mod.code_head_revision()
    assert isinstance(head, str)
    assert head


def test_checker_skips_when_database_url_missing():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    result = subprocess.run(
        [PY, str(CHECKER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "WARN" in result.stderr


def test_checker_fails_on_revision_mismatch():
    mod = _load_checker_module()
    with patch.object(mod, "code_head_revision", return_value="head123"):
        with patch.object(mod.psycopg, "connect") as connect_mock:
            cursor = connect_mock.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("old456",)

            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}, clear=False):
                with pytest.raises(SystemExit) as exc:
                    mod.main()
            assert exc.value.code == 1


def test_checker_passes_when_revision_matches():
    mod = _load_checker_module()
    with patch.object(mod, "code_head_revision", return_value="head123"):
        with patch.object(mod.psycopg, "connect") as connect_mock:
            cursor = connect_mock.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("head123",)

            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}, clear=False):
                mod.main()
