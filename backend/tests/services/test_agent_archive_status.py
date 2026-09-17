"""#1520 垂直切片：Agent archive_status 服务层直测。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock

from backend.services.agent_archive_status import get_agent_archive_status


@pytest.mark.asyncio
async def test_host_not_found_404():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await get_agent_archive_status(db, "missing-host")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reads_extra_fields():
    host = MagicMock()
    host.extra = {
        "archive": {"pending": 1},
        "capacity": {"free_gb": 10},
        "health": {"ok": True},
        "agent_version": "1.2.3",
    }
    db = AsyncMock()
    db.get = AsyncMock(return_value=host)
    out = await get_agent_archive_status(db, "host-1")
    assert out["host_id"] == "host-1"
    assert out["agent_metrics"] == {"pending": 1}
    assert out["capacity"] == {"free_gb": 10}
    assert out["health"] == {"ok": True}
    assert out["agent_version"] == "1.2.3"
    assert out["scan_status"] is None


@pytest.mark.asyncio
async def test_non_dict_extra_is_empty():
    host = MagicMock()
    host.extra = "bogus"
    db = AsyncMock()
    db.get = AsyncMock(return_value=host)
    out = await get_agent_archive_status(db, "host-2")
    assert out["agent_metrics"] is None
    assert out["capacity"] is None
