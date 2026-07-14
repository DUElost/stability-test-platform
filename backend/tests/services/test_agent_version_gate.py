"""Agent version gate unit tests."""

from __future__ import annotations

import pytest

from backend.services.agent_version_gate import (
    agent_version_gate_enabled,
    agent_version_is_supported,
    resolve_agent_min_version,
)


def test_gate_disabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("STP_AGENT_MIN_VERSION", raising=False)
    assert resolve_agent_min_version() == ""
    assert agent_version_gate_enabled() is False
    assert agent_version_is_supported("1.0.0", "") is True


def test_gate_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("STP_AGENT_MIN_VERSION", "2.0.0")
    assert resolve_agent_min_version() == "2.0.0"
    assert agent_version_gate_enabled() is True
    assert agent_version_is_supported("2.1.0", "2.0.0") is True
    assert agent_version_is_supported("2.1", "2.0.0") is True
    assert agent_version_is_supported("1.9.9", "2.0.0") is False


def test_empty_agent_version_fails_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("STP_AGENT_MIN_VERSION", "2.0.0")
    assert agent_version_is_supported("", "2.0.0") is False
