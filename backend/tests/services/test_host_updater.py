import base64
import json

from backend.services.agent_env_sync import hot_update_env_overrides
from backend.services.host_updater import (
    _build_remote_script,
    _parse_deps_refreshed,
    _parse_env_paths_missing,
    _parse_env_synced,
    _parse_priv_mode,
    get_agent_code_version,
)


def test_remote_tar_path_is_unique_per_operation():
    """#960：并发热更新不能共用固定远端 tar 路径。"""
    from backend.services.host_updater import _remote_tar_path

    first, second = _remote_tar_path(), _remote_tar_path()
    assert first != second
    assert first.startswith("/tmp/stp-agent-update-")
    assert first.endswith(".tar.gz")


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


def test_build_remote_script_retries_pip_when_deps_marker_stale():
    """#948: requirements 哈希未变但安装成功标记缺失/陈旧时仍须 pip。"""
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
    )

    assert 'DEPS_MARKER="$INSTALL_DIR/.deps_installed_sha"' in script
    assert "INSTALLED_REQ_SHA=" in script
    assert 'NEED_PIP=1' in script
    assert 'INSTALLED_REQ_SHA" != "$NEW_REQ_SHA"' in script
    assert 'sudo tee "$DEPS_MARKER"' in script
    # 成功后才写标记；失败路径仍 exit 1 且不 restart（既有）
    pip_ok_marker = script.index('sudo tee "$DEPS_MARKER"')
    pip_fail = script.index("pip install failed")
    assert pip_fail < pip_ok_marker
    assert "service NOT restarted" in script


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


def _build_script_with_wrapper(**overrides):
    kwargs = {
        "install_dir": "/opt/stability-test-agent",
        "service_name": "stability-test-agent",
        "tar_path": "/tmp/stp-agent-update-abc.tar.gz",
        "user": "android",
        "group": "android",
    }
    kwargs.update(overrides)
    return _build_remote_script(**kwargs)


def test_remote_script_prefers_privilege_wrapper_with_legacy_fallback():
    """#1250：优先 stp-agent-priv；未迁移主机回退旧 sudo 面并留哨兵。"""
    script = _build_script_with_wrapper(
        sync_agent_secret=True, agent_secret="s3cr3t-value"
    )

    assert 'PRIV="/usr/local/sbin/stp-agent-priv"' in script
    assert 'sudo -n "$PRIV" selftest' in script
    assert "STP_PRIV_MODE=wrapper" in script
    assert "STP_PRIV_FALLBACK=legacy" in script
    for expected in (
        'sudo "$PRIV" apply-code --staged "$TMPDIR"',
        'sudo "$PRIV" install-schema --file "$TMPDIR/stp_schemas/pipeline_schema.json"',
        'sudo "$PRIV" write-version --version "$CODE_VERSION"',
        'sudo "$PRIV" sync-env --secret-b64 "$AGENT_SECRET_B64"',
        'sudo "$PRIV" sync-env --overrides-b64 "$ENV_OVERRIDES_B64" --path-keys-b64 "$ENV_PATH_KEYS_B64"',
        'sudo "$PRIV" deps-marker --sha "$NEW_REQ_SHA"',
        'sudo "$PRIV" fix-ownership',
        'sudo "$PRIV" restart',
    ):
        assert expected in script, expected
    # legacy 分支保留（迁移期回退），但不再用宽规则读文件
    assert "sudo rsync -av --delete" in script
    assert "sudo sha256sum" not in script


def test_parse_priv_mode_reads_sentinels():
    assert _parse_priv_mode("noise\nSTP_PRIV_MODE=wrapper\n") == "wrapper"
    assert _parse_priv_mode("STP_PRIV_FALLBACK=legacy\n") == "legacy"
    assert _parse_priv_mode("") == "unknown"


# ── #1253（R14-F07）：服务重启后未 active 必须失败 ────────────────────────


def test_build_remote_script_exits_nonzero_when_service_not_active():
    """WARN 不算成功：is-active 重试窗口耗尽 → exit 1（API 得 ok=False）。"""
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        tar_path="/tmp/stp-agent-update.tar.gz",
        user="android",
        group="android",
        sync_agent_secret=False,
        agent_secret="",
    )
    assert "WARN: service may not be running" not in script, "旧 WARN 分支必须移除"
    assert "SERVICE_ACTIVE=0" in script
    assert "ERROR: service $SERVICE_NAME not active 5s after restart" in script
    # 失败路径以 exit 1 结束（紧凑：ERROR 行之后紧跟 exit）
    assert "    exit 1\nfi" in script or "exit 1" in script
    # 成功路径仍打 OK
    assert "OK: service restarted successfully" in script


def test_remote_failure_message_prefers_error_line():
    from backend.services.host_updater import _remote_failure_message

    out = "STP_DEPS_REFRESHED=0\nERROR: service stability-test-agent not active 5s after restart"
    assert _remote_failure_message(out, 1) == (
        "Remote script failed (exit=1): "
        "ERROR: service stability-test-agent not active 5s after restart"
    )


def test_remote_failure_message_falls_back_when_no_error_line():
    from backend.services.host_updater import _remote_failure_message

    assert _remote_failure_message("some log\nanother", 2) == "Remote script failed (exit=2)"
