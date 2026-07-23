"""Tests for canonical AIMonkey path resolution."""

from __future__ import annotations

from pathlib import Path

from backend.agent import aimonkey_paths


def test_resolve_bundle_uses_agent_resources_by_default(tmp_path, monkeypatch):
    install_root = tmp_path / "stability-test-agent"
    agent_dir = install_root / "agent"
    bundle = agent_dir / "resources" / "aimonkey" / "AIMonkeyTest_20260317"
    bundle.mkdir(parents=True)
    (bundle / "MonkeyTest.py").write_text("# fixture\n", encoding="utf-8")

    monkeypatch.setattr(aimonkey_paths, "AGENT_DIR", agent_dir)
    monkeypatch.delenv("AIMONKEY_RESOURCE_DIR", raising=False)

    assert aimonkey_paths.resolve_aimonkey_bundle_dir({}) == bundle


def test_resolve_bundle_honors_explicit_param(tmp_path, monkeypatch):
    custom = tmp_path / "custom" / "AIMonkeyTest_20260317"
    custom.mkdir(parents=True)

    monkeypatch.delenv("AIMONKEY_RESOURCE_DIR", raising=False)

    assert aimonkey_paths.resolve_aimonkey_bundle_dir(
        {"aimonkey_dir": str(custom)}
    ) == custom.resolve()


def test_resolve_bundle_honors_env_resource_root(tmp_path, monkeypatch):
    resource_root = tmp_path / "resources" / "aimonkey"
    bundle = resource_root / "AIMonkeyTest_20260317"
    bundle.mkdir(parents=True)

    monkeypatch.setenv("AIMONKEY_RESOURCE_DIR", str(resource_root))
    monkeypatch.setattr(aimonkey_paths, "AGENT_DIR", tmp_path / "agent")

    assert aimonkey_paths.resolve_aimonkey_bundle_dir({}) == bundle


def test_config_get_aimonkey_resource_dir_points_at_agent_tree(tmp_path, monkeypatch):
    from backend.agent import aimonkey_paths, config

    agent_dir = tmp_path / "agent"
    resource_root = agent_dir / "resources" / "aimonkey"
    resource_root.mkdir(parents=True)

    monkeypatch.setattr(aimonkey_paths, "AGENT_DIR", agent_dir)
    monkeypatch.delenv("AIMONKEY_RESOURCE_DIR", raising=False)

    assert config.get_aimonkey_resource_dir() == str(resource_root)
