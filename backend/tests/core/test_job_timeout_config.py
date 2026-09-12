"""Tests for centralised job timeout configuration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib



def test_production_defaults(monkeypatch):
    monkeypatch.delenv("DISPATCHED_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RUN_DISPATCHED_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RUNNING_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RUN_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("UNKNOWN_GRACE_SECONDS", raising=False)
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    assert mod.DISPATCHED_TIMEOUT_SECONDS == 120
    assert mod.RUNNING_HEARTBEAT_TIMEOUT_SECONDS == 900
    assert mod.PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS == 300
    assert mod.PATROL_STALL_MULTIPLIER == 3
    assert mod.HOST_HEARTBEAT_TIMEOUT_SECONDS == 300
    assert mod.UNKNOWN_GRACE_SECONDS == 300


def test_host_heartbeat_timeout_env(monkeypatch):
    monkeypatch.setenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    assert mod.HOST_HEARTBEAT_TIMEOUT_SECONDS == 180


def test_host_heartbeat_timeout_single_source(monkeypatch):
    """#1518：消费面必须引用同一常量对象，禁止再各自 getenv。"""
    monkeypatch.delenv("HOST_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("ENV", "production")

    cfg = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(cfg)

    from backend.api.routes import devices, hosts
    from backend.services.precheck import reachability
    from backend.tasks import session_watchdog

    importlib.reload(devices)
    importlib.reload(hosts)
    importlib.reload(reachability)
    importlib.reload(session_watchdog)

    assert devices.HOST_HEARTBEAT_TIMEOUT_SECONDS is cfg.HOST_HEARTBEAT_TIMEOUT_SECONDS
    assert hosts.HOST_HEARTBEAT_TIMEOUT_SECONDS is cfg.HOST_HEARTBEAT_TIMEOUT_SECONDS
    assert reachability.HOST_HEARTBEAT_TIMEOUT_SECONDS is cfg.HOST_HEARTBEAT_TIMEOUT_SECONDS
    assert session_watchdog.HOST_HEARTBEAT_TIMEOUT_SECONDS is cfg.HOST_HEARTBEAT_TIMEOUT_SECONDS
    assert cfg.HOST_HEARTBEAT_TIMEOUT_SECONDS == 300


def test_legacy_env_aliases(monkeypatch):
    monkeypatch.setenv("RUN_DISPATCHED_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("RUN_HEARTBEAT_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    assert mod.DISPATCHED_TIMEOUT_SECONDS == 180
    assert mod.RUNNING_HEARTBEAT_TIMEOUT_SECONDS == 600


def test_preferred_env_names_override_legacy(monkeypatch):
    monkeypatch.setenv("DISPATCHED_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("RUNNING_HEARTBEAT_TIMEOUT_SECONDS", "450")
    monkeypatch.setenv("RUN_DISPATCHED_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    assert mod.DISPATCHED_TIMEOUT_SECONDS == 90
    assert mod.RUNNING_HEARTBEAT_TIMEOUT_SECONDS == 450


def test_patrol_running_timeout_env(monkeypatch):
    monkeypatch.setenv("PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    assert mod.PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS == 240


def test_running_heartbeat_timeout_grading(monkeypatch):
    monkeypatch.delenv("PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("RUNNING_HEARTBEAT_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("ENV", "development")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    class _Job:
        pipeline_def = {"lifecycle": {"patrol": {"interval_seconds": 60}}}
        patrol_cycle_count = 0
        last_patrol_heartbeat_at = None
        current_patrol_step = None

    class _PatrolJob:
        pipeline_def = {"lifecycle": {"patrol": {"interval_seconds": 60}}}
        patrol_cycle_count = 2
        last_patrol_heartbeat_at = object()
        current_patrol_step = None

    assert mod.running_heartbeat_timeout_seconds(_Job()) == 900
    assert mod.running_heartbeat_timeout_seconds(_PatrolJob()) == mod.PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS


def test_patrol_running_timeout_covers_long_interval(monkeypatch):
    monkeypatch.delenv("PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    class _PatrolJob:
        pipeline_def = {"lifecycle": {"patrol": {"interval_seconds": 600}}}
        patrol_cycle_count = 1
        last_patrol_heartbeat_at = object()
        current_patrol_step = None
        updated_at = datetime(2026, 5, 27, 3, 0, tzinfo=timezone.utc)
        next_retry_at = None

    assert mod.running_heartbeat_timeout_seconds(_PatrolJob()) == 1800


def test_patrol_running_timeout_covers_backoff_retry_window(monkeypatch):
    monkeypatch.delenv("PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    base_time = datetime(2026, 5, 27, 3, 0, tzinfo=timezone.utc)

    class _BackoffPatrolJob:
        pipeline_def = {"lifecycle": {"patrol": {"interval_seconds": 60}}}
        patrol_cycle_count = 4
        last_patrol_heartbeat_at = object()
        current_patrol_step = "monkey_check"
        updated_at = base_time
        next_retry_at = base_time + timedelta(seconds=480)

    assert mod.running_heartbeat_timeout_seconds(_BackoffPatrolJob()) == 660
