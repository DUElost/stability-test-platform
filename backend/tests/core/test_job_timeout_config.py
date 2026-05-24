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
