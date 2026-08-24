from backend.services.agent_env_sync import (
    PROTECTED_ENV_KEYS,
    agent_path_keys_to_verify,
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
    # Prefer an explicit AEE root when STP_NFS_ROOT is unset; clear ambient
    # production-machine values so this suite stays hermetic.
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/nfs/aee_events")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "1")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_AEE_NFS_ROOT"] == "/mnt/nfs/aee_events"
    assert overrides["STP_NFS_ROOT"] == "/mnt/nfs/aee_events"
    assert overrides["LOG_LEVEL"] == "DEBUG"
    # #287：单一开关；UPLOADER/CONTINUOUS 双键已从 fleet 表移除。
    assert overrides["STP_DEVICE_LOG_EVENT_ENABLED"] == "1"
    assert "STP_EVENT_UPLOADER_ENABLED" not in overrides
    assert "STP_EVENT_UPLOADER_CONTINUOUS" not in overrides


def test_dle_uploader_flags_omitted_when_unset_on_control_plane(monkeypatch):
    """Empty control-plane values must not clobber per-host gray flags (#218)."""
    monkeypatch.delenv("STP_DEVICE_LOG_EVENT_ENABLED", raising=False)

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert "STP_DEVICE_LOG_EVENT_ENABLED" not in overrides


def test_dle_uploader_flags_propagate_explicit_zero(monkeypatch):
    """Explicit \"0\" is a real fleet value — must disable agents, not be skipped."""
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "0")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_DEVICE_LOG_EVENT_ENABLED"] == "0"


def test_mtbf_expected_testpoint_count_propagates_when_set(monkeypatch):
    """MTBF P0：套件 testpoint 期望数全 fleet 同值，随 hot-update 下发
    （避免手工 .env 漏配 → check 无期望值只报绝对数）。"""
    monkeypatch.setenv("STP_MTBF_EXPECTED_TESTPOINT_COUNT", "130")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_MTBF_EXPECTED_TESTPOINT_COUNT"] == "130"


def test_mtbf_task_times_never_synced(monkeypatch):
    """STP_MTBF_TASK_TIMES 是 host 级手工键（冒烟=1/生产=100），
    不得进入 fleet 同步白名单，控制面即使设置也不下发。"""
    monkeypatch.setenv("STP_MTBF_TASK_TIMES", "1")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert "STP_MTBF_TASK_TIMES" not in overrides


def test_control_plane_scan_tool_paths_are_not_synced_to_agents(monkeypatch):
    """The scan tool sits elsewhere on the agents; syncing the control plane's
    own path made every agent scan fail instantly (runs 124-129)."""
    monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/home/debian13/Start-Log-Scan/venv/bin/python")
    monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/home/debian13/Start-Log-Scan/start_log_scan.py")
    monkeypatch.delenv("STP_AGENT_DEDUP_SCAN_PYTHON", raising=False)
    monkeypatch.delenv("STP_AGENT_DEDUP_SCAN_SCRIPT", raising=False)

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert "STP_DEDUP_SCAN_PYTHON" not in overrides
    assert "STP_DEDUP_SCAN_SCRIPT" not in overrides


def test_agent_scoped_keys_are_written_to_unprefixed_agent_keys(monkeypatch):
    monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/home/debian13/Start-Log-Scan/venv/bin/python")
    monkeypatch.setenv(
        "STP_AGENT_DEDUP_SCAN_PYTHON", "/mnt/stp-aee/tools/Start-Log-Scan/venv/bin/python"
    )
    monkeypatch.setenv(
        "STP_AGENT_DEDUP_SCAN_SCRIPT", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py"
    )

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_DEDUP_SCAN_PYTHON"] == (
        "/mnt/stp-aee/tools/Start-Log-Scan/venv/bin/python"
    )
    assert overrides["STP_DEDUP_SCAN_SCRIPT"] == (
        "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py"
    )
    assert "STP_AGENT_DEDUP_SCAN_PYTHON" not in overrides


def test_control_plane_nfs_root_never_leaks_to_agents(monkeypatch):
    monkeypatch.setenv("STP_NFS_ROOT", "/home/debian13/stability-test-platform/storage/nfs")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/stp-aee")
    monkeypatch.delenv("STP_AGENT_NFS_ROOT", raising=False)

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_NFS_ROOT"] == "/mnt/stp-aee"


def test_agent_nfs_root_source_key_is_ignored(monkeypatch):
    """STP_AGENT_NFS_ROOT 不再是独立中心存储坐标；脚本 env 只镜像 STP_AEE_NFS_ROOT。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/stp-aee")
    monkeypatch.setenv("STP_AGENT_NFS_ROOT", "/somewhere/else")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_NFS_ROOT"] == "/mnt/stp-aee"
    assert "STP_AGENT_NFS_ROOT" not in overrides


def test_agent_path_keys_to_verify_covers_synced_paths(monkeypatch):
    monkeypatch.setenv(
        "STP_AGENT_DEDUP_SCAN_SCRIPT", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py"
    )
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/stp-aee")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")
    keys = agent_path_keys_to_verify(overrides)

    assert "STP_DEDUP_SCAN_SCRIPT" in keys
    assert "STP_AEE_NFS_ROOT" in keys
    assert "LOG_DIR" not in keys


def test_hot_update_env_overrides_never_includes_protected_keys(monkeypatch):
    monkeypatch.setenv("HOST_ID", "must-not-sync")
    monkeypatch.setenv("API_URL", "http://evil.example")
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", "/mnt/hdd/aee_events")
    # Even if set on the control plane, PRUNE_LOCAL must never enter fleet payload (#217).
    monkeypatch.setenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", "1")

    overrides = hot_update_env_overrides()

    assert "HOST_ID" not in overrides
    assert "API_URL" not in overrides
    # Per-host L1 path — dual-disk vs SSD-only fleets must not share one value.
    assert "STP_AEE_LOCAL_ROOT" not in overrides
    assert "STP_EVENT_UPLOADER_PRUNE_LOCAL" not in overrides


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


def test_flash_firmware_keys_propagate_when_set(monkeypatch):
    """Honor 刷机自动化（flash_firmware v1.2.0）：版本 pin 与开关全 fleet
    同值，控制面设置一次、hot-update 下发。"""
    monkeypatch.setenv("STP_FLASH_FIRMWARE_VERSION", "8.0.1.100")
    monkeypatch.setenv("STP_FLASH_FIRMWARE_ROOT", "/mnt/stp-aee/firmware")
    monkeypatch.setenv("STP_FLASH_SKIP_IF_CURRENT", "false")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    assert overrides["STP_FLASH_FIRMWARE_VERSION"] == "8.0.1.100"
    assert overrides["STP_FLASH_FIRMWARE_ROOT"] == "/mnt/stp-aee/firmware"
    assert overrides["STP_FLASH_SKIP_IF_CURRENT"] == "false"


def test_flash_firmware_keys_omitted_when_unset(monkeypatch):
    """空值不推：缺省版本走各族 NFS latest.json 指针，Agent 本地值保留。"""
    for key in ("STP_FLASH_FIRMWARE_VERSION", "STP_FLASH_FIRMWARE_ROOT",
                "STP_FLASH_SKIP_IF_CURRENT", "STP_FLASH_VERIFY_VERSION"):
        monkeypatch.delenv(key, raising=False)

    overrides = hot_update_env_overrides("/opt/stability-test-agent")

    for key in ("STP_FLASH_FIRMWARE_VERSION", "STP_FLASH_FIRMWARE_ROOT",
                "STP_FLASH_SKIP_IF_CURRENT", "STP_FLASH_VERIFY_VERSION"):
        assert key not in overrides


def test_flash_firmware_root_is_path_verified(monkeypatch):
    """固件根是路径键：hot-update 后远端校验其存在，配错在推送时就暴露。"""
    monkeypatch.setenv("STP_FLASH_FIRMWARE_ROOT", "/mnt/stp-aee/firmware")

    overrides = hot_update_env_overrides("/opt/stability-test-agent")
    keys = agent_path_keys_to_verify(overrides)

    assert "STP_FLASH_FIRMWARE_ROOT" in keys
    assert "STP_FLASH_FIRMWARE_VERSION" not in keys
