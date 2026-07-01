"""migration_guard 逻辑单测 — 验证 downgrade 数据保护的四条路径。"""
from unittest import mock

import pytest

from backend.core import migration_guard


def _setup_mocks(has_table: bool, count: int):
    bind = mock.MagicMock()
    bind.execute.return_value.scalar.return_value = count
    insp = mock.MagicMock()
    insp.has_table.return_value = has_table
    op_mock = mock.MagicMock()
    op_mock.get_bind.return_value = bind
    inspect_patch = mock.patch.object(migration_guard, "inspect", mock.Mock(return_value=insp))
    op_patch = mock.patch.object(migration_guard, "op", op_mock)
    return bind, inspect_patch, op_patch


def test_guard_blocks_when_nonempty(monkeypatch):
    monkeypatch.delenv("STP_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    _, insp_p, op_p = _setup_mocks(has_table=True, count=5)
    with insp_p, op_p:
        with pytest.raises(RuntimeError, match="would destroy data"):
            migration_guard.guard_nonempty(["plan_run"], migration_id="z3a4b5c6d7e8")


def test_guard_allows_when_empty(monkeypatch):
    monkeypatch.delenv("STP_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    _, insp_p, op_p = _setup_mocks(has_table=True, count=0)
    with insp_p, op_p:
        migration_guard.guard_nonempty(["plan_run"], migration_id="z3a4b5c6d7e8")  # no raise


def test_guard_force_env_bypasses(monkeypatch):
    monkeypatch.setenv("STP_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    op_mock = mock.MagicMock()
    op_mock.get_bind.side_effect = AssertionError("should not call get_bind when forced")
    with mock.patch.object(migration_guard, "op", op_mock):
        migration_guard.guard_nonempty(["plan_run"], migration_id="z3a4b5c6d7e8")  # no raise, no call


def test_guard_skips_missing_table(monkeypatch):
    monkeypatch.delenv("STP_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    _, insp_p, op_p = _setup_mocks(has_table=False, count=99)
    with insp_p, op_p:
        migration_guard.guard_nonempty(["plan_run"], migration_id="z3a4b5c6d7e8")  # no raise


def test_guard_error_message_lists_all_blocked_tables(monkeypatch):
    monkeypatch.delenv("STP_ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    bind = mock.MagicMock()
    bind.execute.return_value.scalar.return_value = 3
    insp = mock.MagicMock()
    insp.has_table.return_value = True
    op_mock = mock.MagicMock()
    op_mock.get_bind.return_value = bind
    with mock.patch.object(migration_guard, "inspect", mock.Mock(return_value=insp)), \
         mock.patch.object(migration_guard, "op", op_mock):
        with pytest.raises(RuntimeError) as exc:
            migration_guard.guard_nonempty(["plan", "plan_step"], migration_id="y2z3a4b5c6d7")
    msg = str(exc.value)
    assert "plan=3 rows" in msg
    assert "plan_step=3 rows" in msg
    assert "STP_ALLOW_DESTRUCTIVE_DOWNGRADE=1" in msg
