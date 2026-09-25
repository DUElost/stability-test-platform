"""#1520 垂直切片：Agent artifacts 摄取服务层直测。"""

from __future__ import annotations

import pytest
from backend.services.errors import ServiceError
from unittest.mock import AsyncMock, patch

from backend.services.agent_artifacts import (
    ArtifactIn,
    _ARTIFACT_TYPE_WHITELIST,
    ingest_agent_artifact,
)


def _payload(**overrides):
    base = dict(
        storage_uri="/mnt/stp-aee/jobs/1/aee_crash/x.bin",
        artifact_type="aee_crash",
        fencing_token="tok",
        agent_instance_id="a1",
        host_id="h1",
        device_serial="s1",
    )
    base.update(overrides)
    return ArtifactIn(**base)


class TestArtifactWhitelist:
    def test_known_types(self):
        assert "aee_crash" in _ARTIFACT_TYPE_WHITELIST
        assert "bugreport" in _ARTIFACT_TYPE_WHITELIST
        assert "anr" not in _ARTIFACT_TYPE_WHITELIST


class TestIngestGuards:
    @pytest.mark.asyncio
    async def test_empty_storage_uri_400(self):
        with pytest.raises(ServiceError) as exc:
            await ingest_agent_artifact(
                db=None,  # type: ignore[arg-type]
                job_id=1,
                payload=_payload(storage_uri=""),
            )
        assert exc.value.status == 400

    @pytest.mark.asyncio
    async def test_unlisted_type_400(self):
        with (
            patch(
                "backend.services.agent_artifacts.resolve_local_artifact_path",
                return_value="/ok",
            ),
        ):
            with pytest.raises(ServiceError) as exc:
                await ingest_agent_artifact(
                    db=AsyncMock(),
                    job_id=1,
                    payload=_payload(artifact_type="anr"),
                )
        assert exc.value.status == 400

    @pytest.mark.asyncio
    async def test_job_missing_404(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with patch(
            "backend.services.agent_artifacts.resolve_local_artifact_path",
            return_value="/ok",
        ), patch(
            "backend.services.agent_artifacts.require_job_bound_upload_lease",
            new_callable=AsyncMock,
        ):
            with pytest.raises(ServiceError) as exc:
                await ingest_agent_artifact(db, 99, _payload())
        assert exc.value.status == 404
