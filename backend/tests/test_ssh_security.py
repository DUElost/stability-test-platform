from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from backend.api.routes.logs import query_agent_logs
from backend.models.host import Host


def test_resolve_host_ssh_credentials_migrates_legacy_extra(monkeypatch, db_session):
    from backend.core.ssh_security import decrypt_ssh_password, resolve_host_ssh_credentials

    monkeypatch.setenv("SSH_CREDENTIALS_FERNET_KEY", Fernet.generate_key().decode())
    host = Host(
        id="401",
        hostname="legacy-ssh-host",
        name="legacy-ssh-host",
        ip="192.168.1.141",
        ip_address="192.168.1.141",
        ssh_user="root",
        extra={"ssh_password": "legacy-pass", "ssh_key_path": "/tmp/legacy.key", "rack": "A3"},
        status="ONLINE",
    )
    db_session.add(host)
    db_session.commit()

    creds, migrated = resolve_host_ssh_credentials(host, inventory_lookup=lambda _ip: None)

    assert migrated is True
    assert creds.password == "legacy-pass"
    assert creds.key_path == "/tmp/legacy.key"

    db_session.commit()
    db_session.refresh(host)
    assert host.ssh_password_enc
    assert decrypt_ssh_password(host.ssh_password_enc) == "legacy-pass"
    assert host.ssh_key_path == "/tmp/legacy.key"
    assert host.extra == {"rack": "A3"}


def test_encrypt_ssh_password_preserves_leading_and_trailing_whitespace(monkeypatch):
    from backend.core.ssh_security import decrypt_ssh_password, encrypt_ssh_password

    monkeypatch.setenv("SSH_CREDENTIALS_FERNET_KEY", Fernet.generate_key().decode())
    raw = "  secret-with-padding  "

    encrypted = encrypt_ssh_password(raw)

    assert encrypted
    assert decrypt_ssh_password(encrypted) == raw


def test_normalize_remote_log_path_rejects_traversal(monkeypatch):
    from backend.core.ssh_security import normalize_remote_log_path

    monkeypatch.setenv("STP_SSH_LOG_ROOTS", "/opt/stability-test-agent/logs,/var/log")

    with pytest.raises(ValueError, match=r"\.\."):
        normalize_remote_log_path("/opt/stability-test-agent/logs/../secrets.txt")


def test_create_ssh_client_uses_reject_policy_and_known_hosts(monkeypatch, tmp_path):
    from backend.core import ssh_security

    key_file = tmp_path / "id_rsa"
    key_file.write_text("dummy-key", encoding="utf-8")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("dummy-host-key", encoding="utf-8")

    fake_client = MagicMock()
    fake_paramiko = SimpleNamespace(
        SSHClient=lambda: fake_client,
        RejectPolicy=ssh_security.paramiko.RejectPolicy,
    )
    monkeypatch.setattr(ssh_security, "paramiko", fake_paramiko)

    client = ssh_security.create_ssh_client(
        hostname="192.168.1.50",
        port=22,
        username="root",
        key_path=str(key_file),
        known_hosts_path=str(known_hosts),
        timeout=12,
    )

    assert client is fake_client
    fake_client.load_system_host_keys.assert_called_once()
    fake_client.load_host_keys.assert_called_once_with(str(known_hosts))
    policy = fake_client.set_missing_host_key_policy.call_args.args[0]
    assert policy.__class__.__name__ == "RejectPolicy"
    fake_client.connect.assert_called_once()


def test_query_agent_logs_rejects_path_outside_allowed_roots(monkeypatch, db_session):
    host = Host(
        id="402",
        hostname="log-sec-host",
        name="log-sec-host",
        ip="192.168.1.142",
        ip_address="192.168.1.142",
        status="ONLINE",
    )
    db_session.add(host)
    db_session.commit()
    monkeypatch.setenv("STP_SSH_LOG_ROOTS", "/opt/stability-test-agent/logs,/var/log")

    query = SimpleNamespace(host_id="402", log_path="/etc/passwd", lines=50)

    with pytest.raises(HTTPException) as excinfo:
        query_agent_logs(query, db_session, True)

    assert excinfo.value.status_code == 400


def test_trust_host_key_appends_scanned_key(monkeypatch, tmp_path):
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("10.0.0.1 ssh-ed25519 OLDKEY\n", encoding="utf-8")

    fake_completed = SimpleNamespace(returncode=0, stdout="10.0.0.99 ssh-ed25519 AAAAFAKE\n", stderr="")
    monkeypatch.setattr(
        ssh_security.subprocess,
        "run",
        lambda *args, **kwargs: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 22, str(known_hosts))

    assert ok is True
    assert reason == "ok"
    content = known_hosts.read_text(encoding="utf-8")
    assert "10.0.0.99 ssh-ed25519 AAAAFAKE" in content
    # Old entry for a different IP is preserved.
    assert "10.0.0.1 ssh-ed25519 OLDKEY" in content


def test_trust_host_key_returns_failure_when_keyscan_empty(monkeypatch, tmp_path):
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.touch()

    fake_completed = SimpleNamespace(returncode=1, stdout="", stderr="no route")
    monkeypatch.setattr(
        ssh_security.subprocess,
        "run",
        lambda *args, **kwargs: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 22, str(known_hosts))

    assert ok is False
    assert "no keys" in reason


def test_trust_host_key_refuses_silent_replace_on_key_change(monkeypatch, tmp_path):
    """#908：既有条目与扫描结果不同 → 默认拒绝且不动文件（可审计换钥才替换）。"""
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("10.0.0.99 ssh-ed25519 T0xES0VZ\n", encoding="utf-8")

    fake_completed = SimpleNamespace(
        returncode=0, stdout="10.0.0.99 ssh-ed25519 TkVXS0VZ\n", stderr="",
    )
    monkeypatch.setattr(
        ssh_security.subprocess, "run", lambda *a, **k: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 22, str(known_hosts))

    assert ok is False
    assert "host key changed" in reason
    assert "explicit replace required" in reason
    assert "SHA256:" in reason, "拒绝原因须含新旧指纹"
    content = known_hosts.read_text(encoding="utf-8")
    assert "T0xES0VZ" in content, "未确认时旧条目必须保持不动"


def test_trust_host_key_replaces_with_explicit_consent(monkeypatch, tmp_path):
    """显式 allow_replace=True → 替换并返回新旧指纹（调用方据此审计）。"""
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("10.0.0.99 ssh-ed25519 T0xES0VZ\n", encoding="utf-8")

    fake_completed = SimpleNamespace(
        returncode=0, stdout="10.0.0.99 ssh-ed25519 TkVXS0VZ\n", stderr="",
    )
    monkeypatch.setattr(
        ssh_security.subprocess, "run", lambda *a, **k: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key(
        "10.0.0.99", 22, str(known_hosts), allow_replace=True,
    )

    assert ok is True
    assert reason.startswith("replaced old=SHA256:")
    assert "new=SHA256:" in reason
    content = known_hosts.read_text(encoding="utf-8")
    assert "T0xES0VZ" not in content
    assert "10.0.0.99 ssh-ed25519 TkVXS0VZ" in content


def test_trust_host_key_nondefault_port_ignores_22_port_entry(monkeypatch, tmp_path):
    """#1655：非 22 端口信任只与同 token 的 [ip]:port 条目比较。

    既有 22 端口条目行首 token 不同，此前会混进比较集合 → 集合恒不相等 →
    即使密钥材料一致也被误判「host key changed」拒绝。
    """
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    line = "10.0.0.99 ssh-ed25519 T0xES0VZ"
    known_hosts.write_text(line + "\n", encoding="utf-8")

    fake_completed = SimpleNamespace(
        returncode=0, stdout="[10.0.0.99]:2222 ssh-ed25519 T0xES0VZ\n", stderr="",
    )
    monkeypatch.setattr(
        ssh_security.subprocess, "run", lambda *a, **k: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 2222, str(known_hosts))

    assert ok is True, f"同密钥材料不应被误判换钥: {reason}"
    assert reason == "ok"
    content = known_hosts.read_text(encoding="utf-8")
    assert "[10.0.0.99]:2222 ssh-ed25519 T0xES0VZ" in content


def test_trust_host_key_nondefault_port_same_token_key_change_still_refused(
    monkeypatch, tmp_path,
):
    """#1655 回归守卫：同 token 下密钥真的变化 → 仍须拒绝（不能放过换钥）。"""
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "[10.0.0.99]:2222 ssh-ed25519 T0xES0VZ\n", encoding="utf-8",
    )

    fake_completed = SimpleNamespace(
        returncode=0, stdout="[10.0.0.99]:2222 ssh-ed25519 TkVXS0VZ\n", stderr="",
    )
    monkeypatch.setattr(
        ssh_security.subprocess, "run", lambda *a, **k: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 2222, str(known_hosts))

    assert ok is False
    assert "host key changed" in reason
    assert "explicit replace required" in reason


def test_trust_host_key_nondefault_port_preserves_22_port_entry(monkeypatch, tmp_path):
    """#1709：非 22 端口信任不得删除裸 ip（22 端口）条目。

    #1655 把比较集收窄到同 token，但删除集仍同时剔除裸 ip 与 [ip]:port——
    密钥不同的 22 端口条目**从未参与比较**就被静默删除，绕过 #908 换钥守卫。
    """
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "10.0.0.99 ssh-ed25519 T0xES0VZ\n", encoding="utf-8",
    )

    fake_completed = SimpleNamespace(
        returncode=0, stdout="[10.0.0.99]:2222 ssh-ed25519 TkVXS0VZ\n", stderr="",
    )
    monkeypatch.setattr(
        ssh_security.subprocess, "run", lambda *a, **k: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 2222, str(known_hosts))

    assert ok is True
    assert reason == "ok"
    content = known_hosts.read_text(encoding="utf-8")
    assert "10.0.0.99 ssh-ed25519 T0xES0VZ" in content, (
        "22 端口既有条目不得被非 22 端口信任删除（删除集须与比较集同域）"
    )
    assert "[10.0.0.99]:2222 ssh-ed25519 TkVXS0VZ" in content


def test_trust_host_key_22_port_does_not_touch_other_port_entry(monkeypatch, tmp_path):
    """#1709 反向：22 端口信任不比较、也不删除 [ip]:port 条目。"""
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(
        "10.0.0.99 ssh-ed25519 T0xES0VZ\n"
        "[10.0.0.99]:2222 ssh-ed25519 TkVXS0VZ\n",
        encoding="utf-8",
    )

    fake_completed = SimpleNamespace(
        returncode=0, stdout="10.0.0.99 ssh-ed25519 T0xES0VZ\n", stderr="",
    )
    monkeypatch.setattr(
        ssh_security.subprocess, "run", lambda *a, **k: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 22, str(known_hosts))

    assert ok is True
    assert reason == "ok"
    content = known_hosts.read_text(encoding="utf-8")
    assert content.count("[10.0.0.99]:2222 ssh-ed25519 TkVXS0VZ") == 1


def test_trust_host_key_same_key_rescan_is_ok(monkeypatch, tmp_path):
    """同键重扫不触发换钥路径（幂等，不需要确认）。"""
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    line = "10.0.0.99 ssh-ed25519 T0xES0VZ"
    known_hosts.write_text(line + "\n", encoding="utf-8")

    fake_completed = SimpleNamespace(returncode=0, stdout=line + "\n", stderr="")
    monkeypatch.setattr(
        ssh_security.subprocess, "run", lambda *a, **k: fake_completed,
    )

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 22, str(known_hosts))
    assert ok is True
    assert reason == "ok"


def test_host_key_fingerprints_stable_shape():
    from backend.core import ssh_security

    import base64, hashlib
    blob = base64.b64encode(b"key-material").decode("ascii")
    fps = ssh_security.host_key_fingerprints([f"10.0.0.1 ssh-ed25519 {blob}"])
    expected = "SHA256:" + base64.b64encode(
        hashlib.sha256(b"key-material").digest()
    ).decode("ascii").rstrip("=")
    assert fps == [expected]
    assert ssh_security.host_key_fingerprints(["not-a-known-hosts-line"]) == []


def test_normalize_known_hosts_path_accepts_configured_shapes():
    """绝对路径与 ``~/`` 前缀是文档承认的两种配置形态（含归一化）。"""
    from backend.core.ssh_security import normalize_known_hosts_path

    assert normalize_known_hosts_path("/etc/stp/known_hosts") == "/etc/stp/known_hosts"
    assert normalize_known_hosts_path("/etc//stp/known_hosts") == "/etc/stp/known_hosts"
    assert normalize_known_hosts_path("~/ssh/known_hosts") == "~/ssh/known_hosts"
    # 空 = 未配置：由调用方回落 ~/.ssh/known_hosts，不得变成相对路径 ""
    assert normalize_known_hosts_path("") == ""
    assert normalize_known_hosts_path("   ") == ""


@pytest.mark.parametrize("unsafe", [
    "etc/stp/known_hosts",              # 相对路径：落点取决于进程 cwd
    "../etc/known_hosts",
    "/etc/stp/../../tmp/known_hosts",   # 归一化后会逃出声明位置
    "~root/.ssh/known_hosts",           # 指名他人 home
    "/tmp/known_hosts\n../escape",      # 换行把单值变成多行
    "/tmp/known_hosts\x00",
])
def test_normalize_known_hosts_path_rejects(unsafe):
    """code-scanning #78：落点必须先过守卫，才可能被 mkdir/touch/重写。"""
    from backend.core.ssh_security import (
        SshSecurityConfigError,
        normalize_known_hosts_path,
    )

    with pytest.raises(SshSecurityConfigError):
        normalize_known_hosts_path(unsafe)


def test_resolve_known_hosts_path_validates_env_supplied_value(monkeypatch):
    """显式参数为空不等于安全：``STP_SSH_KNOWN_HOSTS`` 同域受守卫。"""
    from pathlib import Path

    from backend.core import ssh_security

    monkeypatch.setenv("STP_SSH_KNOWN_HOSTS", "/etc/stp/../../tmp/kh")
    with pytest.raises(ssh_security.SshSecurityConfigError):
        ssh_security._resolve_known_hosts_path("")

    monkeypatch.setenv("STP_SSH_KNOWN_HOSTS", "")
    assert ssh_security._resolve_known_hosts_path("") == Path.home() / ".ssh" / "known_hosts"


def test_trust_host_key_refuses_unsafe_path_before_touching_fs(monkeypatch):
    """非法落点：不建目录、不 touch、不执行 ssh-keyscan，且不抛（best-effort 契约）。"""
    from backend.core import ssh_security

    def _no_keyscan(*_args, **_kwargs):
        raise AssertionError("被拒的落点不得进入 ssh-keyscan/写入阶段")

    monkeypatch.setattr(ssh_security.subprocess, "run", _no_keyscan)

    ok, reason = ssh_security.trust_host_key("10.0.0.99", 22, "etc/stp/known_hosts")

    assert ok is False
    assert "known_hosts path must be absolute" in reason
