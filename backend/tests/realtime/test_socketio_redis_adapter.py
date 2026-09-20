"""ADR-0027 P3-2 — SocketIO Redis adapter unit tests (no live Redis required)."""

from __future__ import annotations

from backend.realtime.socketio_redis import (
    _redact_redis_url,
    build_socketio_client_manager,
    socketio_redis_adapter_enabled,
    socketio_redis_channel,
)


def test_adapter_default_off(monkeypatch):
    monkeypatch.delenv("STP_SOCKETIO_REDIS_ADAPTER", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    assert socketio_redis_adapter_enabled() is False
    assert build_socketio_client_manager() is None


def test_adapter_disabled_explicit(monkeypatch):
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "0")
    monkeypatch.delenv("TESTING", raising=False)
    assert socketio_redis_adapter_enabled() is False
    assert build_socketio_client_manager() is None


def test_adapter_forced_off_under_testing(monkeypatch):
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    monkeypatch.setenv("TESTING", "1")
    assert socketio_redis_adapter_enabled() is False
    assert build_socketio_client_manager() is None


def test_adapter_builds_redis_manager(monkeypatch):
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("STP_SOCKETIO_REDIS_CHANNEL", "stp-test-sio")
    assert socketio_redis_adapter_enabled() is True
    assert socketio_redis_channel() == "stp-test-sio"
    mgr = build_socketio_client_manager()
    assert mgr is not None
    assert type(mgr).__name__ == "AsyncRedisManager"


def test_create_sio_server_attaches_manager(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-sio-redis")
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "1")
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    import backend.realtime.socketio_server as sio_mod

    # Reset singleton so create_sio_server rebuilds.
    monkeypatch.setattr(sio_mod, "_sio", None)
    monkeypatch.setattr(sio_mod, "_agent_ns", None)

    sio = sio_mod.create_sio_server()
    assert sio.manager is not None
    assert type(sio.manager).__name__ == "AsyncRedisManager"


def test_create_sio_server_default_in_memory(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-sio-redis")
    monkeypatch.setenv("STP_SOCKETIO_REDIS_ADAPTER", "0")
    monkeypatch.delenv("TESTING", raising=False)

    import backend.realtime.socketio_server as sio_mod

    monkeypatch.setattr(sio_mod, "_sio", None)
    monkeypatch.setattr(sio_mod, "_agent_ns", None)

    sio = sio_mod.create_sio_server()
    # Default manager is AsyncManager (in-process), not AsyncRedisManager.
    assert type(sio.manager).__name__ != "AsyncRedisManager"


def test_redact_redis_url_strips_password():
    assert (
        _redact_redis_url("redis://user:s3cret@redis.example:6379/0")
        == "redis://user:***@redis.example:6379/0"
    )
    assert _redact_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/0"
