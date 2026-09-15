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


def test_checker_skips_when_database_url_missing(tmp_path):
    """#2062：用显式 `--env-file` 指向空文件，避免默认回落到仓库 `.env.backend`。

    原用例只从 env 里剔掉 `DATABASE_URL`，而 checker 会回落读 `<repo>/.env.backend`
    ——在有该文件的机器（本仓即生产控制面）上会真的连库，断言失败且违反
    「测试不得连生产库」的边界。
    """
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    result = subprocess.run(
        [PY, str(CHECKER), "--env-file", str(empty_env)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "WARN" in result.stderr


class TestSchemaStateClassification:
    """#2062：`--allow-behind` 的判定必须是纯函数（可离线测）。"""

    def test_at_head(self):
        m = _load_checker_module()
        assert m.classify_schema_state("h1", "h1", {"h0", "h1"}) == "at_head"

    def test_behind_is_ancestor(self):
        m = _load_checker_module()
        assert m.classify_schema_state("h0", "h1", {"h0", "h1"}) == "behind"

    def test_empty_alembic_version_counts_as_behind(self):
        """未迁移（表空/无行）是「落后」，不是「超前」。"""
        m = _load_checker_module()
        assert m.classify_schema_state(None, "h1", {"h0", "h1"}) == "behind"

    def test_unknown_revision_is_ahead(self):
        m = _load_checker_module()
        assert m.classify_schema_state("bogus", "h1", {"h0", "h1"}) == "ahead"


def test_checker_fails_on_revision_mismatch():
    mod = _load_checker_module()
    with patch.object(mod, "code_head_revision", return_value="head123"):
        with patch.object(mod.psycopg, "connect") as connect_mock:
            cursor = connect_mock.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("old456",)

            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}, clear=False):
                with pytest.raises(SystemExit) as exc:
                    mod.main([])
            assert exc.value.code == 1


def test_checker_passes_when_revision_matches():
    mod = _load_checker_module()
    with patch.object(mod, "code_head_revision", return_value="head123"):
        with patch.object(mod.psycopg, "connect") as connect_mock:
            cursor = connect_mock.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("head123",)

            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}, clear=False):
                mod.main([])
