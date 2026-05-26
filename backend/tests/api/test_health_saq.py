"""Health endpoint SAQ readiness and Redis connectivity helpers."""

from __future__ import annotations

import pytest

from backend.tasks import saq_worker as saq_mod
import backend.main as main_mod


class TestHealthSaqReady:
    def test_health_includes_saq_ready_when_inprocess_enabled(self, client, monkeypatch):
        monkeypatch.setenv("STP_ENABLE_INPROCESS_SAQ", "1")
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: False)

        response = client.get("/health")
        assert response.status_code in (200, 503)
        data = response.json()
        if response.status_code == 200:
            assert data["data"]["saq_ready"] is False

    def test_health_omits_saq_ready_when_inprocess_disabled(self, client, monkeypatch):
        monkeypatch.setenv("STP_ENABLE_INPROCESS_SAQ", "0")
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: True)

        response = client.get("/health")
        assert response.status_code in (200, 503)
        data = response.json()
        if response.status_code == 200:
            assert "saq_ready" not in data["data"]


@pytest.mark.asyncio
async def test_verify_redis_connectivity_success(monkeypatch):
    class _FakeRedis:
        async def ping(self):
            return True

        async def aclose(self):
            return None

    async def _fake_from_url(*_args, **_kwargs):
        return _FakeRedis()

    monkeypatch.setattr(saq_mod.aioredis, "from_url", _fake_from_url)
    await saq_mod.verify_redis_connectivity("redis://test:6379/0")


@pytest.mark.asyncio
async def test_verify_redis_connectivity_failure(monkeypatch):
    async def _fake_from_url(*_args, **_kwargs):
        class _BadRedis:
            async def ping(self):
                raise ConnectionError("connection refused")

            async def aclose(self):
                return None

        return _BadRedis()

    monkeypatch.setattr(saq_mod.aioredis, "from_url", _fake_from_url)
    with pytest.raises(RuntimeError, match="Redis unreachable"):
        await saq_mod.verify_redis_connectivity("redis://bad:6379/0")
