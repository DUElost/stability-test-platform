"""agent_installer — RunConsole 安装路径单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services.agent_installer import (
    INSTALL_API_URL_ENV,
    get_active_install_console_id,
    prepare_install_agent,
    start_install_agent_runconsole,
)


@pytest.fixture(autouse=True)
def install_api_url(monkeypatch):
    """I4：安装脚本非交互，回连地址必须由控制面环境注入。"""
    monkeypatch.setenv(INSTALL_API_URL_ENV, "https://stp.example.com")


@pytest.fixture
def mock_host():
    host = MagicMock()
    host.id = "host-abc"
    host.ip = "10.0.0.5"
    host.ssh_port = 22
    return host


def test_prepare_install_agent_missing_host():
    with patch("backend.services.agent_installer.SessionLocal") as sl:
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = None
        out = prepare_install_agent("missing")
    assert out["ok"] is False
    assert "not found" in out["message"]


def test_prepare_install_agent_ok(mock_host):
    creds = MagicMock()
    creds.user = "android"
    creds.password = "secret"
    creds.key_path = None

    with (
        patch("backend.services.agent_installer.SessionLocal") as sl,
        patch(
            "backend.services.agent_installer.resolve_host_ssh_credentials",
            return_value=(creds, False),
        ),
    ):
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = mock_host
        out = prepare_install_agent("host-abc")

    assert out["ok"] is True
    assert out["host_id"] == "host-abc"
    assert "ansible-playbook" in out["cmd"][0]
    assert "agent_host_id=host-abc" in out["cmd"]
    # I4：非交互安装链必须把控制面 origin 传给 playbook（否则 API_URL 为空）
    assert "agent_api_url=https://stp.example.com" in out["cmd"]
    assert out["api_url"] == "https://stp.example.com"
    out["cleanup"]()


def test_prepare_install_agent_requires_api_url(mock_host, monkeypatch):
    """缺 STP_AGENT_INSTALL_API_URL → fail-closed，不构造 ansible 命令。"""
    monkeypatch.delenv(INSTALL_API_URL_ENV, raising=False)
    out = prepare_install_agent("host-abc")
    assert out["ok"] is False
    assert INSTALL_API_URL_ENV in out["message"]


@pytest.mark.parametrize(
    "value",
    [
        "stp.example.com",
        "ftp://stp.example.com",
        "https://user:pw@stp.example.com",
        "https://stp.example.com/prefix",
        "https://stp.example.com:notaport",
        "{{ stp_public_url }}",
    ],
)
def test_prepare_install_agent_rejects_non_origin(mock_host, monkeypatch, value):
    monkeypatch.setenv(INSTALL_API_URL_ENV, value)
    out = prepare_install_agent("host-abc")
    assert out["ok"] is False
    assert "cmd" not in out


def test_prepare_install_agent_passes_install_options(mock_host):
    """install_options → -e（agent_install_root → ansible agent_install_dir）。"""
    creds = MagicMock()
    creds.user = "android"
    creds.password = "secret"
    creds.key_path = None

    with (
        patch("backend.services.agent_installer.SessionLocal") as sl,
        patch(
            "backend.services.agent_installer.resolve_host_ssh_credentials",
            return_value=(creds, False),
        ),
    ):
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = mock_host
        out = prepare_install_agent(
            "host-abc",
            install_options={
                "agent_install_root": "/srv/stability-test-agent",
                "agent_local_aee_root": "/mnt/hdd/aee_events",
            },
        )

    assert out["ok"] is True
    assert "agent_install_dir=/srv/stability-test-agent" in out["cmd"]
    assert "agent_local_aee_root=/mnt/hdd/aee_events" in out["cmd"]
    out["cleanup"]()


def test_prepare_install_agent_rejects_relative_install_root(mock_host):
    out = prepare_install_agent(
        "host-abc", install_options={"agent_install_root": "opt/agent"},
    )
    assert out["ok"] is False
    assert "agent_install_root" in out["message"]


def test_prepare_install_agent_passes_the_share_server_host(mock_host, monkeypatch):
    """#2181：站点自建存储时，NFS 服务端就是站点入口主机（不是端口/路径）。"""
    monkeypatch.setenv(INSTALL_API_URL_ENV, "https://stp.example.com:8443")
    creds = MagicMock()
    creds.user = "android"
    creds.password = "secret"
    creds.key_path = None

    with (
        patch("backend.services.agent_installer.SessionLocal") as sl,
        patch(
            "backend.services.agent_installer.resolve_host_ssh_credentials",
            return_value=(creds, False),
        ),
    ):
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = mock_host
        out = prepare_install_agent("host-abc")

    assert out["ok"] is True
    assert "agent_nfs_server=stp.example.com" in out["cmd"]
    out["cleanup"]()


def test_start_install_runconsole_registers_active():
    rc = MagicMock()
    rc.start.return_value = "con-test-1"
    rc.log_file_path.return_value = MagicMock()

    prep = {
        "ok": True,
        "host_id": "host-abc",
        "ip": "10.0.0.5",
        "cmd": ["ansible-playbook", "pb.yml"],
        "env": {},
        "cwd": "/tmp/ansible",
        "cleanup": MagicMock(),
        "cmd_line": "ansible-playbook pb.yml",
    }

    with (
        patch("backend.services.agent_installer.prepare_install_agent", return_value=prep),
        patch("backend.services.agent_installer.RunConsole") as rc_cls,
        patch("builtins.open", MagicMock()),
    ):
        rc_cls.instance.return_value = rc
        out = start_install_agent_runconsole("host-abc", initiated_by="admin")

    assert out["ok"] is True
    assert out["console_run_id"] == "con-test-1"
    assert get_active_install_console_id("host-abc") == "con-test-1"
    prep["cleanup"].assert_not_called()


def test_start_install_runconsole_busy_does_not_double_start():
    """I4：同 host 重复触发不得再起第二个 ansible（RunKeyBusy 原样回传现有 run）。"""
    from backend.services.run_console import RunKeyBusyError

    prep = {
        "ok": True,
        "host_id": "host-abc",
        "ip": "10.0.0.5",
        "cmd": ["ansible-playbook", "pb.yml"],
        "env": {},
        "cwd": "/tmp/ansible",
        "cleanup": MagicMock(),
        "cmd_line": "ansible-playbook pb.yml",
    }
    rc = MagicMock()
    rc.start.side_effect = RunKeyBusyError("run_key busy")

    with (
        patch("backend.services.agent_installer.prepare_install_agent", return_value=prep),
        patch("backend.services.agent_installer.RunConsole") as rc_cls,
        patch(
            "backend.services.agent_installer.get_active_install_console_id",
            return_value="con-existing",
        ),
    ):
        rc_cls.instance.return_value = rc
        out = start_install_agent_runconsole("host-abc", initiated_by="admin")

    assert out["ok"] is False
    assert out["console_run_id"] == "con-existing"
    assert "in progress" in out["message"]
    # 未启动成功必须清理临时 inventory（失败路径不留 .stp-install-*.ini）
    prep["cleanup"].assert_called_once()


def test_prepare_install_agent_key_only_passes_private_key_to_inventory(mock_host):
    """#1252：仅私钥凭据必须写 ansible_ssh_private_key_file，且不写空密码键。"""
    creds = MagicMock()
    creds.user = "android"
    creds.password = ""
    creds.key_path = "/home/ops/.ssh/id_ed25519"

    with (
        patch("backend.services.agent_installer.SessionLocal") as sl,
        patch(
            "backend.services.agent_installer.resolve_host_ssh_credentials",
            return_value=(creds, False),
        ),
    ):
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = mock_host
        out = prepare_install_agent("host-abc")

    assert out["ok"] is True
    inv_path = Path(out["cmd"][out["cmd"].index("-i") + 1])
    try:
        content = inv_path.read_text(encoding="utf-8")
        assert "ansible_ssh_private_key_file=/home/ops/.ssh/id_ed25519" in content
        assert "ansible_password=" not in content
        assert "ansible_become_password=" not in content
    finally:
        # 断言失败也必须清理临时 inventory（失败路径曾残留 .stp-install-*.ini）
        out["cleanup"]()
    assert not inv_path.exists()


def test_prepare_install_agent_password_inventory_unchanged(mock_host):
    """#1252 回归：密码凭据路径仍写 ansible_password / ansible_become_password。"""
    creds = MagicMock()
    creds.user = "android"
    creds.password = "secret"
    creds.key_path = ""

    with (
        patch("backend.services.agent_installer.SessionLocal") as sl,
        patch(
            "backend.services.agent_installer.resolve_host_ssh_credentials",
            return_value=(creds, False),
        ),
    ):
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = mock_host
        out = prepare_install_agent("host-abc")

    inv_path = Path(out["cmd"][out["cmd"].index("-i") + 1])
    try:
        content = inv_path.read_text(encoding="utf-8")
        assert "ansible_password=secret" in content
        assert "ansible_become_password=secret" in content
        assert "ansible_ssh_private_key_file" not in content
    finally:
        out["cleanup"]()


class TestInstallOutcomeRecording:
    """ADR-0044 D3：安装终态由 console 回调落库（审计 + agent_installed + last_install）。

    会话纪律：每个断言用独立短会话并关闭——回调自己会 `close()` 掉注入的会话，
    测试若再复用它就会留下 idle-in-transaction，卡住下一个用例的 TRUNCATE。
    """

    @staticmethod
    def _run(status: str, *, exit_code: int | None, run_id: str = "con-out-1"):
        run = MagicMock()
        run.run_id = run_id
        run.status = status
        run.exit_code = exit_code
        return run

    @staticmethod
    def _seed_host(engine, host_id: str) -> None:
        from backend.models.host import Host
        from sqlalchemy.orm import Session

        with Session(bind=engine) as session:
            session.add(
                Host(id=host_id, hostname=host_id, status="ONLINE", ip="192.0.2.90", ssh_port=22)
            )
            session.commit()

    @staticmethod
    def _record(engine, monkeypatch, host_id: str, run) -> None:
        import backend.services.agent_installer as installer
        from sqlalchemy.orm import Session

        session = Session(bind=engine)
        monkeypatch.setattr(installer, "SessionLocal", lambda: session)
        installer._record_install_outcome(host_id, run, "admin")

    @staticmethod
    def _extra(engine, host_id: str) -> dict:
        from backend.models.host import Host
        from sqlalchemy.orm import Session

        with Session(bind=engine) as session:
            return dict((session.get(Host, host_id).extra or {}))

    def test_success_sets_marker_and_records_outcome(self, engine, monkeypatch):
        from backend.models.audit import AuditLog
        from sqlalchemy.orm import Session

        self._seed_host(engine, "out-ok")
        self._record(engine, monkeypatch, "out-ok", self._run("SUCCESS", exit_code=0))

        extra = self._extra(engine, "out-ok")
        assert extra["agent_installed"] is True
        # 运行/结果不走 extra（心跳会按 allowlist 重建它）——审计才是持久证据
        assert "last_install" not in extra

        with Session(bind=engine) as session:
            row = (
                session.query(AuditLog)
                .filter(AuditLog.action == "install_agent", AuditLog.resource_id == "out-ok")
                .one()
            )
            assert row.details["ok"] is True and row.details["rc"] == 0
            assert row.details["console_status"] == "SUCCESS"
            assert row.details["console_run_id"] == "con-out-1"
            assert row.details["initiated_by"] == "admin"

    def test_failure_records_outcome_without_marking_installed(self, engine, monkeypatch):
        from sqlalchemy.orm import Session
        self._seed_host(engine, "out-fail")
        self._record(engine, monkeypatch, "out-fail", self._run("FAILED", exit_code=2))

        extra = self._extra(engine, "out-fail")
        assert "agent_installed" not in extra
        with Session(bind=engine) as session:
            from backend.models.audit import AuditLog

            row = (
                session.query(AuditLog)
                .filter(AuditLog.action == "install_agent", AuditLog.resource_id == "out-fail")
                .one()
            )
            assert row.details["ok"] is False
            assert row.details["console_status"] == "FAILED"

    def test_canceled_is_recorded_as_canceled(self, engine, monkeypatch):
        from sqlalchemy.orm import Session
        self._seed_host(engine, "out-cancel")
        self._record(engine, monkeypatch, "out-cancel", self._run("CANCELED", exit_code=-15))

        with Session(bind=engine) as session:
            from backend.models.audit import AuditLog

            row = (
                session.query(AuditLog)
                .filter(AuditLog.action == "install_agent", AuditLog.resource_id == "out-cancel")
                .one()
            )
            assert row.details["console_status"] == "CANCELED"
            assert row.details["ok"] is False
