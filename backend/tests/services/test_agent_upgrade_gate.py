"""#1520 垂直切片：Agent upgrade-gate HTTP 适配服务层直测。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock, patch

from backend.services.agent_upgrade_gate import (
    UpgradeGateReleaseRequest,
    UpgradeGateRequest,
    acquire_agent_upgrade_gate,
    raise_upgrade_gate_http,
    release_agent_upgrade_gate,
)
from backend.services.host_upgrade_gate import (
    HostHasActiveJobsError,
    HostNotFoundError,
)


class TestRaiseUpgradeGateHttp:
    def test_host_not_found_404(self):
        with pytest.raises(HTTPException) as exc:
            raise_upgrade_gate_http("h1", HostNotFoundError("missing"))
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "HOST_NOT_FOUND"

    def test_active_jobs_409(self):
        with pytest.raises(HTTPException) as exc:
            raise_upgrade_gate_http(
                "h1",
                HostHasActiveJobsError(active_jobs=[{"id": 1}]),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "HOST_HAS_ACTIVE_JOBS"


class TestAcquireReleaseGuards:
    def test_release_requires_holder(self):
        with pytest.raises(HTTPException) as exc:
            release_agent_upgrade_gate(
                db=MagicMock(),
                host_id="h1",
                payload=UpgradeGateReleaseRequest(holder="  "),
            )
        assert exc.value.status_code == 400

    def test_acquire_maps_domain_error(self):
        db = MagicMock()
        with patch(
            "backend.services.agent_upgrade_gate.begin_host_upgrade",
            side_effect=HostNotFoundError("gone"),
        ):
            with pytest.raises(HTTPException) as exc:
                acquire_agent_upgrade_gate(
                    db,
                    "h1",
                    UpgradeGateRequest(holder="ansible"),
                )
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "HOST_NOT_FOUND"
