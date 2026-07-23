from backend.services.agent_env_sync import (
    PROTECTED_ENV_KEYS,
    hot_update_env_overrides,
    merge_env_overrides,
)


def test_hot_update_env_overrides_uses_install_layout_paths():
    overrides = hot_update_env_overrides("/opt/stability-test-agent")
    assert overrides["AGENT_INSTALL_DIR"] == "/opt/stability-test-agent"
    assert overrides["AIMONKEY_RESOURCE_DIR"] == (
        "/opt/stability-test-agent/agent/resources/aimonkey"
    )
    assert overrides["LOG_DIR"] == "/opt/stability-test-agent/logs"
    assert overrides["PYTHONPATH"] == "/opt/stability-test-agent"


def test_hot_update_env_overrides_includes_fleet_keys_from_control_plane(monkeypatch):
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/nfs/aee_events")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_AEE_NFS_ROOT"] == "/mnt/nfs/aee_events"
    assert overrides["STP_NFS_ROOT"] == "/mnt/nfs/aee_events"
    assert overrides["LOG_LEVEL"] == "DEBUG"


def test_hot_update_env_overrides_never_includes_protected_keys(monkeypatch):
    monkeypatch.setenv("HOST_ID", "must-not-sync")
    monkeypatch.setenv("API_URL", "http://evil.example")

    overrides = hot_update_env_overrides()

    assert "HOST_ID" not in overrides
    assert "API_URL" not in overrides


def test_merge_env_overrides_replaces_existing_key():
    lines = [
        "# monkey",
        "AIMONKEY_RESOURCE_DIR=/opt/stability-test-agent/resources/aimonkey",
        "HOST_ID=abc",
    ]
    overrides = hot_update_env_overrides()

    new_lines, updated = merge_env_overrides(lines, overrides)

    assert "AIMONKEY_RESOURCE_DIR" in updated
    assert "HOST_ID=abc" in new_lines
    assert all(key not in updated for key in PROTECTED_ENV_KEYS)


def test_merge_env_overrides_skips_protected_keys_even_if_in_overrides():
    lines = ["HOST_ID=keep-me", "API_URL=http://node.local:8000"]
    overrides = {"HOST_ID": "overwrite", "LOG_DIR": "/opt/stability-test-agent/logs"}

    new_lines, updated = merge_env_overrides(lines, overrides)

    assert "HOST_ID=keep-me" in new_lines
    assert "API_URL=http://node.local:8000" in new_lines
    assert updated == ["LOG_DIR"]


def test_merge_env_overrides_appends_missing_key():
    lines = ["HOST_ID=abc"]
    overrides = hot_update_env_overrides()

    new_lines, updated = merge_env_overrides(lines, overrides)

    assert "AIMONKEY_RESOURCE_DIR" in updated
    assert any(line.startswith("AIMONKEY_RESOURCE_DIR=") for line in new_lines)


def test_merge_env_overrides_preserves_comments_and_blank_lines():
    lines = ["", "# keep", "HOST_ID=abc", ""]
    overrides = hot_update_env_overrides()

    new_lines, _updated = merge_env_overrides(lines, overrides)

    assert new_lines[0] == ""
    assert new_lines[1] == "# keep"
