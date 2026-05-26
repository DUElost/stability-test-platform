"""Tests for centralised job timeout configuration."""

from __future__ import annotations

import importlib

import pytest


def test_production_defaults(monkeypatch):
    monkeypatch.delenv("DISPATCHED_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RUN_DISPATCHED_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RUNNING_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("RUN_HEARTBEAT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("UNKNOWN_GRACE_SECONDS", raising=False)
    monkeypatch.setenv("ENV", "production")

    mod = importlib.import_module("backend.core.job_timeout_config")
    importlib.reload(mod)

    assert mod.DISPATCHED_TIMEOUT_SECONDS == 120
    assert mod.RUNNING_HEARTBEAT_TIMEOUT_SECONDS == 900
    assert mod.PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS == 300
    assert mod.UNKNOWN_GRACE_SECONDS == 300


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
