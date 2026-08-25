"""Unit tests for precheck sync helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from backend.models.host import Host
from backend.services.host_updater import _AGENT_SOURCE_DIR
from backend.services.precheck.sync import (
    nfs_path_to_local,
    push_mismatched_scripts,
    sync_host_via_hot_update,
)


def test_nfs_path_to_local_maps_agent_prefix():
    rel = "scripts/check_device/v1.0.0/check_device.py"
    nfs = f"/opt/stability-test-agent/agent/{rel}"
    assert nfs_path_to_local(nfs) == str(_AGENT_SOURCE_DIR / rel)


def test_nfs_path_to_local_rejects_foreign_prefix():
    assert nfs_path_to_local("/mnt/nfs/scripts/foo.py") is None


def test_sync_host_via_hot_update_missing_host(db_session):
    ok, err = sync_host_via_hot_update("missing-host", db_session)
    assert ok is False
    assert err == "host_not_found"


def test_sync_host_via_hot_update_delegates_to_execute_hot_update(db_session):
    host = Host(id="h-1", hostname="agent1", ip="10.0.0.9", ssh_port=22)
    db_session.add(host)
    db_session.commit()

    with patch(
        "backend.services.precheck.sync.resolve_host_ssh_credentials",
        return_value=(MagicMock(user="u", password="p", key_path="", known_hosts_path=""), False),
    ), patch(
        "backend.services.precheck.sync.execute_hot_update",
        return_value={"ok": True},
    ) as hot_update:
        ok, err = sync_host_via_hot_update("h-1", db_session)

    assert ok is True
    assert err is None
    hot_update.assert_called_once()


def test_push_mismatched_scripts_requires_credentials(db_session):
    host = Host(id="h-2", hostname="agent2", ip="10.0.0.10", ssh_port=22)
    db_session.add(host)
    db_session.commit()

    with patch(
        "backend.services.precheck.sync.resolve_host_ssh_credentials",
        return_value=(MagicMock(user="u", password="", key_path="", known_hosts_path=""), False),
    ):
        ok, err = push_mismatched_scripts(
            "h-2",
            [{"name": "check_device", "nfs_path": "/opt/stability-test-agent/agent/x.py"}],
            db_session,
        )

    assert ok is False
    assert err == "no_ssh_credentials"


# ── 支撑文件治愈（#404 冒烟 run #222 缺口）───────────────────────────────────


class _FakeSFTP:
    def __init__(self):
        self.puts: list[tuple[str, str]] = []

    def stat(self, path):
        return MagicMock()

    def mkdir(self, path):  # pragma: no cover - stat 恒成功，不应触达
        raise AssertionError("mkdir should not be needed when stat succeeds")

    def put(self, local, remote):
        self.puts.append((local, remote))

    def close(self):
        pass


class _FakeClient:
    def __init__(self):
        self.sftp = _FakeSFTP()
        self.commands: list[str] = []

    def open_sftp(self):
        return self.sftp

    def exec_command(self, cmd):
        self.commands.append(cmd)

    def close(self):
        pass


def _push_with_fake_client(db_session, tmp_path, entries):
    """把 nfs_path 映射进 tmp_path 并以假 SSH 客户端执行 push。"""
    client = _FakeClient()
    prefix = "/opt/stability-test-agent/agent/"

    def fake_map(nfs_path):
        if not nfs_path.startswith(prefix):
            return None
        return str(tmp_path / nfs_path[len(prefix):])

    host = Host(id="h-push", hostname="agent3", ip="10.0.0.11", ssh_port=22)
    db_session.add(host)
    db_session.commit()

    with patch(
        "backend.services.precheck.sync.resolve_host_ssh_credentials",
        return_value=(MagicMock(user="u", password="p", key_path="", known_hosts_path=""), False),
    ), patch(
        "backend.services.precheck.sync._get_ssh_client", return_value=client,
    ), patch(
        "backend.services.precheck.sync.nfs_path_to_local", side_effect=fake_map,
    ):
        ok, err = push_mismatched_scripts("h-push", entries, db_session)
    return ok, err, client


def test_push_heals_support_files(db_session, tmp_path):
    entry_dir = tmp_path / "mtbf_setup" / "v1.3.0"
    entry_dir.mkdir(parents=True)
    (entry_dir / "mtbf_setup.py").write_text("print('entry')\n")
    (entry_dir / "_lib.py").write_text("LIB = 1\n")

    ok, err, client = _push_with_fake_client(db_session, tmp_path, [{
        "name": "mtbf_setup",
        "nfs_path": "/opt/stability-test-agent/agent/mtbf_setup/v1.3.0/mtbf_setup.py",
        "support_files": {"_lib.py": "sha-lib"},
    }])

    assert (ok, err) == (True, None)
    remotes = {r for _l, r in client.sftp.puts}
    assert "/opt/stability-test-agent/agent/mtbf_setup/v1.3.0/mtbf_setup.py" in remotes
    assert "/opt/stability-test-agent/agent/mtbf_setup/v1.3.0/_lib.py" in remotes
    # CR 清理与 chmod 覆盖支撑文件远端路径
    joined = "; ".join(client.commands)
    assert "mtbf_setup/v1.3.0/_lib.py" in joined


def test_push_reports_missing_local_support_file(db_session, tmp_path):
    entry_dir = tmp_path / "mtbf_finish" / "v1.4.0"
    entry_dir.mkdir(parents=True)
    (entry_dir / "mtbf_finish.py").write_text("print('entry')\n")
    # _lib.py 本地缺失（控制面自身不完整 → 如实 failed，不得静默"成功"）

    ok, err, client = _push_with_fake_client(db_session, tmp_path, [{
        "name": "mtbf_finish",
        "nfs_path": "/opt/stability-test-agent/agent/mtbf_finish/v1.4.0/mtbf_finish.py",
        "support_files": {"_lib.py": "sha-lib"},
    }])

    assert ok is False
    assert "support file not found" in (err or "")
    # 入口文件已推（pushed 计入 partial_fail 报文）
    assert "partial_fail: pushed=1" in (err or "")
    pushed_remote = {r for _l, r in client.sftp.puts}
    assert any(p.endswith("mtbf_finish.py") for p in pushed_remote)


def test_push_without_manifest_keeps_entry_only_shape(db_session, tmp_path):
    entry_dir = tmp_path / "check_device" / "v1.0.0"
    entry_dir.mkdir(parents=True)
    (entry_dir / "check_device.py").write_text("print('x')\n")
    (entry_dir / "_adb.py").write_text("# adb helper\n")   # 旧特例保持不变

    ok, err, client = _push_with_fake_client(db_session, tmp_path, [{
        "name": "check_device",
        "nfs_path": "/opt/stability-test-agent/agent/check_device/v1.0.0/check_device.py",
    }])

    assert (ok, err) == (True, None)
    remotes = sorted(r.rsplit("/", 1)[-1] for _l, r in client.sftp.puts)
    assert remotes == ["_adb.py", "check_device.py"]
