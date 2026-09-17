"""#1520 垂直切片：Agent claim_jobs 服务层直测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.services.agent_claim import (
    ClaimRequest,
    JobOut,
    claim_agent_jobs,
    enrich_job_metadata,
)


class TestEnrichJobMetadata:
    @pytest.mark.asyncio
    async def test_empty_jobs_returns_empty_maps(self):
        serial_map, watcher_map = await enrich_job_metadata(
            db=None,  # type: ignore[arg-type]
            jobs=[],
        )
        assert serial_map == {}
        assert watcher_map == {}


class TestClaimAgentJobsGuards:
    @pytest.mark.asyncio
    async def test_upgrade_required_426(self):
        with (
            patch(
                "backend.services.agent_version_gate.resolve_agent_min_version",
                return_value="2.0.0",
            ),
            patch(
                "backend.services.agent_version_gate.agent_version_is_supported",
                return_value=False,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await claim_agent_jobs(
                    db=None,  # type: ignore[arg-type]
                    payload=ClaimRequest(
                        host_id="h1",
                        capacity=1,
                        agent_version="1.0.0",
                    ),
                )
        assert exc.value.status_code == 426
        assert exc.value.detail["code"] == "AGENT_UPGRADE_REQUIRED"

    @pytest.mark.asyncio
    async def test_empty_claim_returns_empty_list(self):
        with (
            patch(
                "backend.services.agent_version_gate.resolve_agent_min_version",
                return_value="",
            ),
            patch(
                "backend.services.agent_claim.claim_jobs_for_host",
                new_callable=AsyncMock,
                return_value=([], {}),
            ),
        ):
            out = await claim_agent_jobs(
                db=MagicMock(),
                payload=ClaimRequest(host_id="h1", capacity=2),
            )
        assert out == []


class TestJobOutSchema:
    def test_requires_fencing_token(self):
        job = JobOut(
            id=1,
            device_id=10,
            host_id="h1",
            status="RUNNING",
            pipeline_def={},
            fencing_token="tok-1",
        )
        assert job.fencing_token == "tok-1"
        assert job.watcher_policy is None
