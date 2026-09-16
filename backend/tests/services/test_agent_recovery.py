"""#1520 垂直切片：Agent recovery 业务规则的服务层直测。

刻意不经 TestClient / 路由薄壳——grace 刷新门禁与 LEGACY 类拒绝分支可直接驱动。
API / 锁序回归仍由 `test_agent_dual_write.py`、`test_recovery_sync_lock_order_2015.py` 覆盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models.enums import JobStatus, LeaseStatus
from backend.services.agent_recovery import (
    _DEVICE_LOCK_LEASE_SECONDS,
    resume_expired_lease_for_recovery,
    rotate_recovery_lease_token,
)


def _lease(**kwargs):
    base = dict(
        status=LeaseStatus.ACTIVE.value,
        device_id=1,
        agent_instance_id="old-agent",
        lease_generation=1,
        fencing_token="1:1",
        renewed_at=None,
        expires_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _job(**kwargs):
    base = dict(
        status=JobStatus.UNKNOWN.value,
        ended_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestResumeExpiredLeaseForRecovery:
    @pytest.mark.asyncio
    async def test_refreshes_within_grace(self):
        now = datetime.now(timezone.utc)
        lease = _lease()
        job = _job(ended_at=now - timedelta(seconds=60))
        ok = await resume_expired_lease_for_recovery(
            db=None,  # type: ignore[arg-type]
            lease=lease,  # type: ignore[arg-type]
            job=job,  # type: ignore[arg-type]
            agent_instance_id="new-agent",
            now=now,
            grace_seconds=300,
        )
        assert ok is True
        assert lease.agent_instance_id == "new-agent"
        assert lease.expires_at == now + timedelta(seconds=_DEVICE_LOCK_LEASE_SECONDS)
        assert lease.renewed_at == now

    @pytest.mark.asyncio
    async def test_rejects_outside_grace(self):
        now = datetime.now(timezone.utc)
        lease = _lease()
        job = _job(ended_at=now - timedelta(seconds=400))
        ok = await resume_expired_lease_for_recovery(
            db=None,  # type: ignore[arg-type]
            lease=lease,  # type: ignore[arg-type]
            job=job,  # type: ignore[arg-type]
            agent_instance_id="new-agent",
            now=now,
            grace_seconds=300,
        )
        assert ok is False
        assert lease.agent_instance_id == "old-agent"

    @pytest.mark.asyncio
    async def test_rejects_non_unknown_job(self):
        now = datetime.now(timezone.utc)
        ok = await resume_expired_lease_for_recovery(
            db=None,  # type: ignore[arg-type]
            lease=_lease(),  # type: ignore[arg-type]
            job=_job(status=JobStatus.RUNNING.value),  # type: ignore[arg-type]
            agent_instance_id="new-agent",
            now=now,
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_rejects_inactive_lease(self):
        now = datetime.now(timezone.utc)
        ok = await resume_expired_lease_for_recovery(
            db=None,  # type: ignore[arg-type]
            lease=_lease(status=LeaseStatus.RELEASED.value),  # type: ignore[arg-type]
            job=_job(),  # type: ignore[arg-type]
            agent_instance_id="new-agent",
            now=now,
        )
        assert ok is False


class TestRotateRecoveryLeaseToken:
    @pytest.mark.asyncio
    async def test_missing_device_409(self):
        class _Result:
            def scalars(self):
                return self

            def first(self):
                return None

        class _Db:
            async def execute(self, *_a, **_k):
                return _Result()

        lease = _lease(device_id=42)
        with pytest.raises(HTTPException) as exc:
            await rotate_recovery_lease_token(
                _Db(),  # type: ignore[arg-type]
                lease,  # type: ignore[arg-type]
                agent_instance_id="new-agent",
            )
        assert exc.value.status_code == 409
        assert "recovery device not found" in exc.value.detail
