"""Unit coverage for #417 DEGRADED enum rebuild migration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest


def test_migration_revision_chain():
    from backend.alembic.versions import w9x0y1z2a3b4_drop_plan_run_status_degraded as m

    assert m.revision == "w9x0y1z2a3b4"
    assert m.down_revision == "v8w9x0y1z2a3"
    assert "DEGRADED" not in m.PLAN_RUN_STATUS_VALUES
    assert m.PLAN_RUN_STATUS_VALUES_WITH_DEGRADED[-1] == "DEGRADED"


def test_upgrade_noop_on_non_postgres():
    from backend.alembic.versions import w9x0y1z2a3b4_drop_plan_run_status_degraded as m

    bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    with mock.patch.object(m.op, "get_bind", return_value=bind):
        m.upgrade()  # no raise


def test_upgrade_noop_when_label_absent():
    from backend.alembic.versions import w9x0y1z2a3b4_drop_plan_run_status_degraded as m

    bind = mock.MagicMock()
    bind.dialect.name = "postgresql"
    bind.execute.return_value.scalar.return_value = None  # label absent
    with (
        mock.patch.object(m.op, "get_bind", return_value=bind),
        mock.patch.object(m, "_replace_plan_run_status_enum") as replace,
    ):
        m.upgrade()
    replace.assert_not_called()


def test_upgrade_refuses_when_rows_present():
    from backend.alembic.versions import w9x0y1z2a3b4_drop_plan_run_status_degraded as m

    bind = mock.MagicMock()
    bind.dialect.name = "postgresql"
    # label exists, then row count > 0
    bind.execute.return_value.scalar.side_effect = [1, 2]
    with mock.patch.object(m.op, "get_bind", return_value=bind):
        with pytest.raises(RuntimeError, match="refusing to drop enum label"):
            m.upgrade()


def test_upgrade_rebuilds_when_safe():
    from backend.alembic.versions import w9x0y1z2a3b4_drop_plan_run_status_degraded as m

    bind = mock.MagicMock()
    bind.dialect.name = "postgresql"
    bind.execute.return_value.scalar.side_effect = [1, 0]  # label present, 0 rows
    with (
        mock.patch.object(m.op, "get_bind", return_value=bind),
        mock.patch.object(m, "_replace_plan_run_status_enum") as replace,
    ):
        m.upgrade()
    replace.assert_called_once_with(bind, m.PLAN_RUN_STATUS_VALUES)
