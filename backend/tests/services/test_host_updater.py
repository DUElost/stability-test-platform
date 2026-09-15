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
    assert first.startswith("/tmp/stp-agent-")
    assert first.endswith(".tar.gz")


def test_build_remote_script_disables_agent_secret_sync_by_default():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
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
    assert "ENV_OVERRIDES_B64=" in script
    # #2180：env 哨兵（STP_ENV_SYNCED=/STP_ENV_PATH_MISSING=）改由 wrapper sync-env
    # 打在远端 stdout 上——控制面解析面不变，发射面契约见
    # tests/test_agent_priv_parser_contract.py::test_wrapper_sync_env_emits_control_plane_sentinels
    assert 'sudo "$PRIV" sync-env --overrides-b64 "$ENV_OVERRIDES_B64" --path-keys-b64 "$ENV_PATH_KEYS_B64"' in script
    assert "STP_ENV_SYNCED=" not in script


def test_build_remote_script_includes_allowlisted_env_overrides():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
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


def test_build_remote_script_delegates_sync_filters_to_wrapper():
    """#2180：host_updater 不再自持 rsync 过滤面。

    resources/mtbf/ 的 host-local 保护（冒烟 #214/#216「APK 不存在」根因）与
    resources/ 的 protect-only 语义（#1950）现由 wrapper 内部的固定 filter 承担，
    语义锁定见 tests/test_agent_priv_apply_code_protection.py 与
    tests/test_agent_priv_boundary.py::test_wrapper_protect_only_paths。
    """
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
    )

    assert 'sudo "$PRIV" apply-code --staged "$CODE_TMP"' in script
    # 裸 rsync 与自持 filter 必须全部退役（无「脚本内过滤」这条第二通道）
    assert "sudo rsync" not in script
    assert "--exclude=" not in script
    assert "--filter=" not in script


def test_build_remote_script_includes_agent_secret_update_when_enabled():
    secret = "sync-secret-1234567890"
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
        sync_agent_secret=True,
        agent_secret=secret,
    )

    assert 'SYNC_AGENT_SECRET="1"' in script
    assert f'AGENT_SECRET_B64="{base64.b64encode(secret.encode()).decode()}"' in script
    # #2180：明文/本地 heredoc 改写退役，密钥只以 b64 交 wrapper 落盘
    assert secret not in script
    assert 'sudo "$PRIV" sync-env --secret-b64 "$AGENT_SECRET_B64"' in script


def test_build_remote_script_injects_pip_index_url():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
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
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
    )

    assert 'DEPS_MARKER="$INSTALL_DIR/.deps_installed_sha"' in script
    assert "INSTALLED_REQ_SHA=" in script
    assert 'NEED_PIP=1' in script
    assert 'INSTALLED_REQ_SHA" != "$NEW_REQ_SHA"' in script
    mark = 'sudo "$PRIV" deps-marker --sha "$NEW_REQ_SHA"'
    assert mark in script
    # 成功后才写标记；失败路径仍 exit 1 且不 restart（既有）
    pip_fail = script.index("pip install failed")
    assert pip_fail < script.index(mark)
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
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
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
    # #2180：路径核验结果哨兵由 wrapper sync-env 发射（--path-keys-b64 入参保留）
    assert 'sync-env --overrides-b64 "$ENV_OVERRIDES_B64" --path-keys-b64 "$ENV_PATH_KEYS_B64"' in script
    assert "STP_ENV_PATH_MISSING=" not in script


def test_get_agent_code_version_returns_short_hash():
    version = get_agent_code_version()
    # In a git checkout this is a 7+ char hex short hash; outside git it's "".
    assert version == "" or all(c in "0123456789abcdef" for c in version)


def _build_script_with_wrapper(**overrides):
    kwargs = {
        "install_dir": "/opt/stability-test-agent",
        "service_name": "stability-test-agent",
        "code_tar_path": "/tmp/stp-agent-update-abc.tar.gz",
        "resources_tar_path": "",
        "user": "android",
        "group": "android",
    }
    kwargs.update(overrides)
    return _build_remote_script(**kwargs)


def test_remote_script_requires_wrapper_fail_closed():
    """#2180 / ADR-0037 §5 Revisit #1：wrapper 是唯一提权面，缺失/旧版即失败。

    宽 sudoers 48/48 已清除（C 步），legacy 裸 sudo 面没有任何可用环境；
    回退分支不是「兼容」而是「必炸」，因此删分支 + selftest 前置 fail-closed。
    """
    script = _build_script_with_wrapper(
        sync_agent_secret=True, agent_secret="s3cr3t-value"
    )

    assert 'PRIV="/usr/local/sbin/stp-agent-priv"' in script
    assert 'sudo -n "$PRIV" selftest' in script
    assert "STP_PRIV_MODE=wrapper" in script
    # legacy 分支与哨兵必须彻底退役
    assert "STP_PRIV_FALLBACK" not in script
    assert "USE_PRIV_WRAPPER" not in script
    assert "sudo rsync" not in script
    assert "sudo tee" not in script
    assert "sudo chown" not in script
    assert "sudo systemctl" not in script
    # fail-closed：selftest 失败 → 可执行指引 + exit 1，且早于一切动作
    guidance = (
        "ERROR: stp-agent-priv selftest failed (missing/outdated wrapper?); "
        "run tools/ansible/playbooks/update_agent.yml on this host, then retry"
    )
    assert guidance in script
    guard = script.index('if ! sudo -n "$PRIV" selftest')
    assert guard < script.index('echo "STP_PRIV_MODE=wrapper"')
    assert guard < script.index('sudo "$PRIV" apply-code --staged "$CODE_TMP"')
    for expected in (
        'sudo "$PRIV" apply-code --staged "$CODE_TMP"',
        'sudo "$PRIV" install-schema --file "$CODE_TMP/stp_schemas/pipeline_schema.json"',
        'sudo "$PRIV" write-version --version "$CODE_VERSION"',
        'sudo "$PRIV" sync-env --secret-b64 "$AGENT_SECRET_B64"',
        'sudo "$PRIV" sync-env --overrides-b64 "$ENV_OVERRIDES_B64" --path-keys-b64 "$ENV_PATH_KEYS_B64"',
        'sudo "$PRIV" deps-marker --sha "$NEW_REQ_SHA"',
        'sudo "$PRIV" fix-ownership',
        'sudo "$PRIV" restart',
    ):
        assert expected in script, expected
    assert "sudo sha256sum" not in script


def test_parse_priv_mode_wrapper_only():
    """#2180：唯一 sentinel 是 STP_PRIV_MODE=wrapper；其余（含旧 legacy 行）为 unknown。"""
    assert _parse_priv_mode("noise\nSTP_PRIV_MODE=wrapper\n") == "wrapper"
    assert _parse_priv_mode("STP_PRIV_FALLBACK=legacy\n") == "unknown"
    assert _parse_priv_mode("STP_PRIV_MODE=legacy\n") == "unknown"
    assert _parse_priv_mode("") == "unknown"


# ── #1253（R14-F07）：服务重启后未 active 必须失败 ────────────────────────


def test_build_remote_script_exits_nonzero_when_service_not_active():
    """WARN 不算成功：is-active 重试窗口耗尽 → exit 1（API 得 ok=False）。"""
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
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
    assert _remote_failure_message(out, "", 1) == (
        "Remote script failed (exit=1): "
        "ERROR: service stability-test-agent not active 5s after restart"
    )


def test_remote_failure_message_reads_wrapper_stderr_sentinel():
    """#1942：wrapper 拒绝只打 stderr 哨兵，message 必须带上原因。"""
    from backend.services.host_updater import _remote_failure_message

    err = "STP_AGENT_PRIV_ERROR: INSTALL_DIR must not overlap a system directory: /etc vs /etc"
    assert _remote_failure_message("", err, 2) == (
        "Remote script failed (exit=2): STP_AGENT_PRIV_ERROR: "
        "INSTALL_DIR must not overlap a system directory: /etc vs /etc"
    )


def test_remote_failure_message_falls_back_to_stderr_tail():
    """#1942：argparse 用法错误（旧 wrapper 缺子命令）在 stderr 末行，须可诊断。"""
    from backend.services.host_updater import _remote_failure_message

    err = (
        "usage: stp-agent-priv [-h] {selftest,bootstrap,...,restart} ...\n"
        "stp-agent-priv: error: argument command: invalid choice: 'write-digest' "
        "(choose from selftest, bootstrap, apply-code, install-schema, "
        "write-version, sync-env, deps-marker, fix-ownership, restart)"
    )
    assert _remote_failure_message("", err, 2) == (
        "Remote script failed (exit=2): stp-agent-priv: error: argument command: "
        "invalid choice: 'write-digest' (choose from selftest, bootstrap, "
        "apply-code, install-schema, write-version, sync-env, deps-marker, "
        "fix-ownership, restart)"
    )


def test_remote_failure_message_falls_back_when_no_error_line():
    from backend.services.host_updater import _remote_failure_message

    assert _remote_failure_message("some log\nanother", "", 2) == "Remote script failed (exit=2)"


# ── #1903 / ADR-0040 §5.1（P0）：压缩级 9→6 + 整批一次构建 ──────────────


def test_build_tarball_uses_p0_compresslevel(monkeypatch):
    """压缩级默认 6（可显式覆盖），批量与单台路径共用同一默认。"""
    import backend.services.host_updater as hu

    captured: dict = {}

    class _FakeTar:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def add(self, *args, **kwargs):
            pass

    def fake_open(*args, **kwargs):
        captured.update(kwargs)
        return _FakeTar()

    monkeypatch.setattr(hu.tarfile, "open", fake_open)

    assert isinstance(hu._build_tarball(kind="code"), bytes)
    assert captured["mode"] == "w:gz"
    assert captured["compresslevel"] == hu._TARBALL_COMPRESSLEVEL == 6

    captured.clear()
    hu._build_tarball(kind="code", compresslevel=1)
    assert captured["compresslevel"] == 1


def test_execute_hot_update_reuses_prebuilt_tarball(monkeypatch):
    """传入预构建载荷时不得重复构建；缺省仍按需构建（单台路径行为不变）。"""
    import backend.services.host_updater as hu

    build_calls = {"n": 0}

    def fake_build(*args, **kwargs):
        build_calls["n"] += 1
        return b"built-tarball"

    def fake_connect(**kwargs):
        raise RuntimeError("stop before upload")

    monkeypatch.setattr(hu, "_build_tarball", fake_build)
    monkeypatch.setattr(hu, "_ssh_connect", fake_connect)

    result = hu.execute_hot_update(
        host_ip="10.0.0.1", code_drift=True, code_tarball=b"prebuilt-tarball",
    )
    assert result["ok"] is False
    assert build_calls["n"] == 0

    result = hu.execute_hot_update(host_ip="10.0.0.1", code_drift=True)
    assert result["ok"] is False
    assert build_calls["n"] == 1


def test_hot_update_direct_builds_tarball_once_for_all_hosts(monkeypatch):
    """--direct 整批只构建一次 tarball，并传给每台调用的 execute_hot_update。"""
    import types

    import backend.core.database as core_db
    import backend.core.ssh_security as ssh_sec
    import backend.models.host as host_mod
    import backend.scripts.batch_hot_update as bhu
    import backend.services.agent_version_info as avi
    import backend.services.host_upgrade_gate as gate_mod
    import backend.services.host_updater as hu_mod

    class _FakeHost:
        id = "h-1"
        hostname = "h-1"
        ip = "10.0.0.1"
        ssh_port = 22
        status = "ONLINE"

    class _Query:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self._rows

        def count(self):
            return 0

    class _FakeDB:
        def query(self, model):
            return _Query([_FakeHost()] if model is host_mod.Host else [])

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = {"build": 0, "exec": []}

    monkeypatch.setattr(core_db, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        ssh_sec,
        "resolve_host_ssh_credentials",
        lambda host, inventory_lookup=None: (
            types.SimpleNamespace(
                user="u", password="p", key_path="", known_hosts_path=""
            ),
            False,
        ),
    )
    monkeypatch.setattr(hu_mod, "_resolve_ssh_creds", lambda ip: None)
    monkeypatch.setattr(hu_mod, "get_agent_code_version", lambda: "deadbeef")

    def _fake_build(*args, **kwargs):
        calls["build"] += 1
        return b"T"

    def _fake_exec(**kwargs):
        calls["exec"].append(kwargs)
        return {"ok": True, "message": "OK"}

    monkeypatch.setattr(hu_mod, "_build_tarball", _fake_build)
    monkeypatch.setattr(hu_mod, "execute_hot_update", _fake_exec)
    monkeypatch.setattr(
        gate_mod,
        "begin_host_upgrade",
        lambda db, host_id, **kwargs: {"aborted_summary": None},
    )
    monkeypatch.setattr(gate_mod, "end_host_upgrade", lambda db, host_id, holder: None)

    import backend.services.artifact_digest as ad_mod

    from backend.services.artifact_digest import ConvergencePlan

    desired = "sha256:" + "a" * 64

    def _fake_plan(host, force=False):
        return ConvergencePlan(
            code_digest=desired, code_drift=True,
            resources_digest="sha256:" + "b" * 64,
            resources_drift=False, resources_skipped_empty=True,
            converged=False, no_op_result=None,
        )

    monkeypatch.setattr(ad_mod, "plan_convergence", _fake_plan)
    finalized = {"n": 0}
    monkeypatch.setattr(
        avi, "finalize_hot_update_outcome",
        lambda db, host, result, **kw: finalized.__setitem__("n", finalized["n"] + 1),
    )

    assert bhu._hot_update_direct(include_active=False, abort_running_jobs=False) == 0
    assert calls["build"] == 1
    assert len(calls["exec"]) == 1
    assert calls["exec"][0]["code_tarball"] == b"T"
    assert calls["exec"][0]["artifact_digest"] == desired
    assert finalized["n"] == 1


# ── #1907 / ADR-0040 D2/D3/D6：ARTIFACT_DIGEST 写入、分段计时、批量 no-op ──


def test_remote_script_writes_artifact_digest_via_wrapper():
    """探活通过后写 ARTIFACT_DIGEST（#2180 后仅 wrapper write-digest 一条路）。"""
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
        artifact_digest="sha256:" + "a" * 64,
    )
    assert 'sudo "$PRIV" write-digest --digest "$ARTIFACT_DIGEST"' in script
    assert "sudo tee" not in script
    assert 'STP_ARTIFACT_DIGEST=$ARTIFACT_DIGEST' in script
    # 探活之后才写（失败不写 digest，保持旧值 → 下次按 drift 重做）
    assert script.index('STP_RESTART_PROBE_MS=') < script.index(
        'sudo "$PRIV" write-digest --digest "$ARTIFACT_DIGEST"'
    )


# ── #1942 → #2180：能力协商收敛为「脚本头 selftest」单点 ────────────────────


def test_remote_script_selftest_is_the_single_capability_probe():
    """#2180：子命令契约（含 write-digest/apply-resources）由 wrapper selftest
    前置校验；不再对单个子命令做运行期探测（#1942 的 write-digest 空跑探针退役）。

    语义：旧 wrapper（缺任一契约子命令）在 selftest 即 exit≠0 → fail-closed，
    早于 apply-code/restart，不产生「已部署却记失败」的半态。
    """
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
        artifact_digest="sha256:" + "a" * 64,
    )
    probe = 'sudo -n "$PRIV" selftest'
    assert probe in script
    assert 'write-digest --digest ""' not in script, "单子命令空跑探针已退役"
    assert "lacks write-digest" not in script
    assert (
        "ERROR: stp-agent-priv selftest failed (missing/outdated wrapper?); "
        "run tools/ansible/playbooks/update_agent.yml on this host, then retry"
    ) in script
    # 提前拦截：在 apply-code / restart 之前
    assert script.index(probe) < script.index('sudo "$PRIV" apply-code --staged "$CODE_TMP"')
    assert script.index(probe) < script.index('sudo "$PRIV" restart')


def test_remote_script_phase_timing_sentinels_present():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/t.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
    )
    assert "STP_REMOTE_APPLY_MS=" in script
    assert "STP_RESTART_PROBE_MS=" in script


def test_parse_phase_ms_sentinel():
    from backend.services.host_updater import _parse_phase_ms

    out = "STP_REMOTE_APPLY_MS=1200\nSTP_RESTART_PROBE_MS=3000\n"
    assert _parse_phase_ms(out, "STP_REMOTE_APPLY_MS") == 1200
    assert _parse_phase_ms(out, "STP_RESTART_PROBE_MS") == 3000
    assert _parse_phase_ms("nothing", "STP_REMOTE_APPLY_MS") == 0
    assert _parse_phase_ms("STP_REMOTE_APPLY_MS=abc", "STP_REMOTE_APPLY_MS") == 0


def test_execute_hot_update_result_carries_converged_fields(monkeypatch):
    """结果结构扩展：converged/reason/artifact_digest/phases 全路径存在。"""
    import backend.services.host_updater as hu

    def fake_connect(**kwargs):
        raise ConnectionError("stop before upload")

    monkeypatch.setattr(hu, "_ssh_connect", fake_connect)
    result = hu.execute_hot_update(host_ip="10.0.0.1", artifact_digest="sha256:" + "a" * 64)
    assert result["ok"] is False
    assert result["converged"] is False
    assert result["reason"] == "ssh_connect_failed"
    assert result["artifact_digest"] == "sha256:" + "a" * 64
    assert "phases" in result
    # code-scanning #80：异常原文只进日志——message 会被 API 原样回给调用方，
    # 不得携带 errno/底层文本（分类由稳定的 reason 承载）。
    assert "stop before upload" not in result["message"]


def test_batch_direct_converged_no_op_skips_gate_and_ssh(monkeypatch):
    """整批 digest-matched：零构建、零 SSH、零门禁，仅统一留痕 converged。"""
    import backend.core.database as core_db
    import backend.models.host as host_mod
    import backend.scripts.batch_hot_update as bhu
    import backend.services.agent_version_info as avi
    import backend.services.artifact_digest as ad_mod
    import backend.services.host_upgrade_gate as gate_mod
    import backend.services.host_updater as hu_mod

    desired = "sha256:" + "c" * 64

    class _FakeHost:
        id = "h-1"
        hostname = "h-1"
        ip = "10.0.0.1"
        ssh_port = 22
        status = "ONLINE"
        agent_artifact_digest = desired
        agent_resources_digest = ""

    class _Query:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self._rows

        def count(self):
            return 0

    class _FakeDB:
        def query(self, model):
            return _Query([_FakeHost()] if model is host_mod.Host else [])

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = {"build": 0, "exec": 0, "gate": 0, "finalize": 0}

    monkeypatch.setattr(core_db, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(hu_mod, "_resolve_ssh_creds", lambda ip: None)
    monkeypatch.setattr(hu_mod, "get_agent_code_version", lambda: "deadbeef")
    monkeypatch.setattr(hu_mod, "_build_tarball", lambda *a, **k: calls.__setitem__("build", calls["build"] + 1) or b"T")
    monkeypatch.setattr(hu_mod, "execute_hot_update", lambda **k: calls.__setitem__("exec", calls["exec"] + 1) or {"ok": True})
    monkeypatch.setattr(gate_mod, "begin_host_upgrade", lambda *a, **k: calls.__setitem__("gate", calls["gate"] + 1) or {})
    monkeypatch.setattr(gate_mod, "end_host_upgrade", lambda *a, **k: None)
    from backend.services.artifact_digest import ConvergencePlan

    monkeypatch.setattr(
        ad_mod, "plan_convergence",
        lambda host, force=False: ConvergencePlan(
            code_digest=desired, code_drift=False,
            resources_digest=desired, resources_drift=False,
            resources_skipped_empty=False,
            converged=True,
            no_op_result={
                "ok": True, "converged": True, "reason": "digest-matched",
                "message": "converged", "duration_ms": 0, "deps_refreshed": False,
                "env_keys_synced": [], "env_paths_missing": {}, "code_version": "",
                "priv_mode": "unknown", "artifact_digest": desired, "phases": {},
            },
        ),
    )
    monkeypatch.setattr(
        avi, "finalize_hot_update_outcome",
        lambda db, host, result, **kw: calls.__setitem__("finalize", calls["finalize"] + 1),
    )

    rc = bhu._hot_update_direct(include_active=False, abort_running_jobs=False)
    assert rc == 0
    assert calls["build"] == 0
    assert calls["exec"] == 0
    assert calls["gate"] == 0
    assert calls["finalize"] == 1


def test_build_remote_script_protects_resources_tree():
    """#1950 / ADR-0040 §4.3 P2 前置 → #2180：resources 保护面收归 wrapper。

    protect-only（防源树删除传播清掉大件）与 mtbf exclude 均在
    wrapper apply-code/apply-resources 的固定 filter 内，远端脚本侧不再出现
    任何 filter 字面量——语义锁定见
    tests/test_agent_priv_boundary.py::test_wrapper_protect_only_paths。
    """
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
    )

    assert 'sudo "$PRIV" apply-code --staged "$CODE_TMP"' in script
    assert "--filter=" not in script
    assert "--exclude=" not in script


def test_build_remote_script_writes_resources_digest():
    """#1963 P2 切片① → #2180：resources 身份落第二文件（仅 wrapper --kind）。"""
    res_digest = "sha256:" + "b" * 64
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
        artifact_digest="sha256:" + "a" * 64,
        resources_digest=res_digest,
    )
    assert f'RESOURCES_DIGEST="{res_digest}"' in script
    # 仅 wrapper 通道；空跑探测/WARN 放行随 #2180 退役（selftest 前置兜底）
    assert 'sudo "$PRIV" write-digest --kind resources --digest "$RESOURCES_DIGEST"' in script
    assert 'write-digest --digest "" --kind resources' not in script
    assert "WARN: resources digest not written (outdated wrapper)" not in script
    assert "sudo tee" not in script


def test_build_remote_script_omits_resources_block_when_empty():
    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/stp-agent-update.tar.gz",
        resources_tar_path="",
        user="android",
        group="android",
        artifact_digest="sha256:" + "a" * 64,
        resources_digest="",
    )
    # 空值时整层被 [ -n "$RESOURCES_TARB_PATH" ] 守护（运行时惰性）：
    # digest 写入段位于层 guard 之内，不会执行
    assert 'RESOURCES_DIGEST=""' in script
    guard = script.index('if [ -n "$RESOURCES_TARB_PATH" ]; then')
    write_at = script.index('write-digest --kind resources --digest "$RESOURCES_DIGEST"')
    assert guard < write_at
