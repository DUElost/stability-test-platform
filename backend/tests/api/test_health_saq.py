"""Health endpoint SAQ readiness and Redis connectivity helpers."""

from __future__ import annotations

import logging

import pytest

from backend.tasks import saq_worker as saq_mod
import backend.main as main_mod


class TestHealthSaqReady:
    def test_health_includes_saq_ready_when_inprocess_enabled(self, client, monkeypatch):
        """TESTING=1 下 lifespan 不启动 Redis/SAQ——readiness 基础设施检查跳过，
        /health 恒 200（saq_ready 只进 payload）。"""
        monkeypatch.setenv("STP_ENABLE_INPROCESS_SAQ", "1")
        # Production shells may export STP_PLAN_ADMISSION_QUEUE_ENABLED=1;
        # keep this assertion independent of the host env.
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "0")
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: False)

        response = client.get("/health")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["data"]["saq_ready"] is False
        assert data["data"]["saq_inprocess_worker"] is True
        assert "socketio_redis_adapter_enabled" in data["data"]
        assert "agent_sid_registry_enabled" in data["data"]
        assert data["data"]["agent_sid_registry_enabled"] is False
        assert "admission_queue_flag" in data["data"]
        assert "admission_queue_pump_ready" in data["data"]
        assert "admission_queue_enabled" in data["data"]
        assert data["data"]["admission_queue_flag"] is False
        assert data["data"]["admission_queue_enabled"] is False

    def test_health_includes_saq_ready_when_inprocess_disabled(self, client, monkeypatch):
        """ADR-0026 P0: producer-only mode still reports saq_ready (enqueue health)."""
        monkeypatch.setenv("STP_ENABLE_INPROCESS_SAQ", "0")
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "0")
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: True)

        response = client.get("/health")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["data"]["saq_ready"] is True
        assert data["data"]["saq_inprocess_worker"] is False

    def test_health_live_is_plain_liveness(self, client):
        """R01-F05（#885）：/health/live 不做依赖检查，进程在即 200。"""
        response = client.get("/health/live")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "alive"


class TestHealthReadiness:
    """R01-F05（#885）：/health readiness 语义——关键依赖不可用即非 200。"""

    @staticmethod
    def _ready_env(monkeypatch, inprocess="1"):
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.setenv("STP_ENABLE_INPROCESS_SAQ", inprocess)
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "0")

    @staticmethod
    def _fake_redis(ping_raises=None):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        ping = AsyncMock(side_effect=ping_raises)
        if ping_raises is None:
            ping.return_value = True
        return SimpleNamespace(ping=ping, aclose=AsyncMock())

    def test_saq_not_ready_returns_503(self, client, monkeypatch):
        self._ready_env(monkeypatch)
        monkeypatch.setattr(main_mod, "redis_client", self._fake_redis())
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: False)

        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SAQ_NOT_READY"

    def test_saq_ready_returns_200(self, client, monkeypatch):
        self._ready_env(monkeypatch)
        monkeypatch.setattr(main_mod, "redis_client", self._fake_redis())
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: True)

        response = client.get("/health")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "healthy"

    def test_producer_mode_saq_not_ready_returns_503(self, client, monkeypatch):
        """ADR-0026 P0：producer 模式下 pump 就绪同样进 readiness。"""
        self._ready_env(monkeypatch, inprocess="0")
        monkeypatch.setattr(main_mod, "redis_client", self._fake_redis())
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: False)

        assert client.get("/health").status_code == 503

    def test_redis_unreachable_returns_503(self, client, monkeypatch):
        self._ready_env(monkeypatch)
        monkeypatch.setattr(
            main_mod, "redis_client",
            self._fake_redis(ping_raises=ConnectionError("refused")),
        )
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: True)

        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "REDIS_UNREACHABLE"

    def test_redis_ping_hang_times_out_returns_503(self, client, monkeypatch):
        """#1177: 黑洞分区下 ping 悬挂必须按超时失败，探针不得无限挂起。

        readiness 对慢/无响应 Redis 与不可达同判 503，且返回时间受
        _HEALTH_REDIS_PING_TIMEOUT 约束（此处缩短到 50ms 验证）。
        """
        import asyncio
        import time

        self._ready_env(monkeypatch)
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        async def _hung_ping():
            await asyncio.sleep(60)
            return True

        monkeypatch.setattr(
            main_mod, "redis_client",
            SimpleNamespace(ping=_hung_ping, aclose=AsyncMock()),
        )
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: True)
        monkeypatch.setattr(main_mod, "_HEALTH_REDIS_PING_TIMEOUT", 0.05)

        started = time.monotonic()
        response = client.get("/health")
        elapsed = time.monotonic() - started
        assert elapsed < 5, f"readiness probe hung for {elapsed:.1f}s"
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "REDIS_UNREACHABLE"

    def test_skip_infra_skips_saq_and_redis_checks(self, client, monkeypatch):
        """运维豁免（非生产类环境）与 lifespan 同条件：不检查 SAQ/Redis。"""
        self._ready_env(monkeypatch)
        monkeypatch.setenv("STP_SKIP_INFRA_CHECK", "1")
        monkeypatch.setattr(main_mod, "redis_client", None)
        monkeypatch.setattr(main_mod, "is_saq_ready", lambda: False)

        response = client.get("/health")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["saq_ready"] is False


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
        await saq_mod.verify_redis_connectivity("redis://user:s3cret@bad:6379/0")


@pytest.mark.asyncio
async def test_verify_redis_connectivity_failure_redacts_password(monkeypatch):
    async def _fake_from_url(*_args, **_kwargs):
        class _BadRedis:
            async def ping(self):
                raise ConnectionError("connection refused")

            async def aclose(self):
                return None

        return _BadRedis()

    monkeypatch.setattr(saq_mod.aioredis, "from_url", _fake_from_url)
    with pytest.raises(RuntimeError) as exc_info:
        await saq_mod.verify_redis_connectivity("redis://user:s3cret@bad:6379/0")

    assert "s3cret" not in str(exc_info.value)
    assert "redis://user:***@bad:6379/0" in str(exc_info.value)


def test_redis_ping_success_log_redacts_password(caplog):
    with caplog.at_level(logging.INFO, logger="backend.main"):
        main_mod._log_redis_ping_ok("redis://user:s3cret@redis.example:6379/0")

    assert "s3cret" not in caplog.text
    assert "redis://user:***@redis.example:6379/0" in caplog.text
