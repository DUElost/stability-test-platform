"""#1520 垂直切片：PlanRun archive 服务层直测。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.plan_run_archive import archive_plan_run_logs


@pytest.mark.asyncio
async def test_plan_run_not_found_404():
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await archive_plan_run_logs(
            db, 1, allow_retired=False, user_id=1, username="u",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_no_jobs_400():
    db = MagicMock()
    db.get = MagicMock(return_value=MagicMock())
    db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(HTTPException) as exc:
        await archive_plan_run_logs(
            db, 1, allow_retired=False, user_id=1, username="u",
        )
    assert exc.value.status_code == 400
    assert "no jobs" in exc.value.detail


@pytest.mark.asyncio
async def test_triggers_hosts_and_audits():
    db = MagicMock()
    db.get = MagicMock(return_value=MagicMock())
    db.query.return_value.filter.return_value.first.return_value = (1,)
    db.commit = MagicMock()

    with patch(
        "backend.services.plan_run_archive.iter_plan_run_scan_hosts",
        return_value=[("host-a", MagicMock())],
    ), patch(
        "backend.services.plan_run_archive.classify_recycle_targets",
        return_value=(["host-a"], [], []),
    ), patch(
        "backend.services.plan_run_archive.build_scan_now_payload",
        return_value={"serials": ["x"]},
    ), patch(
        "backend.services.plan_run_archive.emit_agent_control",
        new_callable=AsyncMock,
    ) as emit, patch(
        "backend.services.plan_run_archive.record_audit",
    ) as audit:
        out = await archive_plan_run_logs(
            db, 42, allow_retired=True, user_id=7, username="admin",
        )

    assert out["plan_run_id"] == 42
    assert out["triggered_hosts"] == ["host-a"]
    assert out["archived_now"] is True
    assert emit.await_count == 2
    audit.assert_called_once()
    db.commit.assert_called_once()
