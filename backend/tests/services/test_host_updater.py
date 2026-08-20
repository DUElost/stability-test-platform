import base64
import json

from backend.services.agent_env_sync import hot_update_env_overrides
from backend.services.host_updater import (
    _build_remote_script,
    _parse_deps_refreshed,
    _parse_env_paths_missing,
    _parse_env_synced,
    get_agent_code_version,
)


def test_build_remote_script_disables_agent_secret_sync_by_default():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
        sync_agent_secret=False,
        agent_secret="",
    )

    assert 'SYNC_AGENT_SECRET="0"' in script
    assert 'AGENT_SECRET_B64=""' in script
    assert 'export PIP_INDEX_URL=""' in script
    assert "STP_DEPS_REFRESHED=" in script
    assert "sha256sum" in script
    assert "STP_ENV_SYNCED=" in script
    assert "ENV_OVERRIDES_B64=" in script


def test_build_remote_script_includes_allowlisted_env_overrides():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
    )
    overrides = hot_update_env_overrides("/opt/stability-test-agent")
    expected_b64 = base64.b64encode(
        json.dumps(overrides, sort_keys=True).encode("utf-8")
    ).decode("ascii")

    assert f'ENV_OVERRIDES_B64="{expected_b64}"' in script
    decoded = json.loads(base64.b64decode(expected_b64).decode("utf-8"))
    assert decoded["AIMONKEY_RESOURCE_DIR"] == (
        "/opt/stability-test-agent/agent/resources/aimonkey"
    )
    assert decoded["AGENT_INSTALL_DIR"] == "/opt/stability-test-agent"


def test_build_remote_script_preserves_host_local_mtbf_resources():
    """resources/mtbf/ 是 host 级手工布放（APK 不在仓库 tarball 内），
    rsync --delete 必须排除它，否则每次 hot-update 清空 MTBF 资源
    （冒烟 #214/#216「APK 不存在」根因）。"""
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
    )

    assert "sudo rsync -av --delete" in script
    assert "--exclude='resources/mtbf/'" in script
    # 排除项必须在 --delete 生效范围内（同一条 rsync 命令）
    delete_pos = script.index("sudo rsync -av --delete")
    exclude_pos = script.index("--exclude='resources/mtbf/'")
    assert exclude_pos > delete_pos


def test_build_remote_script_includes_agent_secret_update_when_enabled():
    secret = "sync-secret-1234567890"
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
        sync_agent_secret=True,
        agent_secret=secret,
    )

    assert 'SYNC_AGENT_SECRET="1"' in script
    assert f'AGENT_SECRET_B64="{base64.b64encode(secret.encode()).decode()}"' in script
    assert 'env_path = pathlib.Path(os.environ["INSTALL_DIR"]) / ".env"' in script
    assert 'line.startswith("AGENT_SECRET=")' in script


def test_build_remote_script_injects_pip_index_url():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
        sync_agent_secret=False,
        agent_secret="",
        pip_index_url="https://pypi.example.com/simple",
    )
    assert 'export PIP_INDEX_URL="https://pypi.example.com/simple"' in script


def test_parse_deps_refreshed_reads_sentinel():
    assert _parse_deps_refreshed("noise\nSTP_DEPS_REFRESHED=1\nOK: service restarted") is True
    assert _parse_deps_refreshed("STP_DEPS_REFRESHED=0") is False
    assert _parse_deps_refreshed("no sentinel here") is False


def test_parse_env_synced_reads_sentinel():
    assert _parse_env_synced("STP_ENV_SYNCED=AIMONKEY_RESOURCE_DIR\nOK") == [
        "AIMONKEY_RESOURCE_DIR"
    ]
    assert _parse_env_synced("STP_ENV_SYNCED=\nOK") == []
    assert _parse_env_synced("no sentinel") == []


def test_parse_env_paths_missing_reads_sentinel():
    missing = {"STP_DEDUP_SCAN_SCRIPT": "/home/debian13/a,b/start_log_scan.py"}
    payload = base64.b64encode(json.dumps(missing).encode()).decode()
    out = f"STP_ENV_SYNCED=STP_DEDUP_SCAN_SCRIPT\nSTP_ENV_PATH_MISSING={payload}\nOK"

    assert _parse_env_paths_missing(out) == missing
    assert _parse_env_paths_missing("STP_ENV_PATH_MISSING=\nOK") == {}
    assert _parse_env_paths_missing("no sentinel") == {}
    assert _parse_env_paths_missing("STP_ENV_PATH_MISSING=not-base64!!") == {}


def test_build_remote_script_verifies_agent_path_keys(monkeypatch):
    monkeypatch.setenv(
        "STP_AGENT_DEDUP_SCAN_SCRIPT", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py"
    )
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
    )

    for line in script.splitlines():
        if line.startswith('ENV_PATH_KEYS_B64="'):
            payload = line.split('"')[1]
            break
    else:
        raise AssertionError("ENV_PATH_KEYS_B64 not injected")

    assert "STP_DEDUP_SCAN_SCRIPT" in json.loads(base64.b64decode(payload).decode())
    assert "STP_ENV_PATH_MISSING=" in script


def test_get_agent_code_version_returns_short_hash():
    version = get_agent_code_version()
    # In a git checkout this is a 7+ char hex short hash; outside git it's "".
    assert version == "" or all(c in "0123456789abcdef" for c in version)
