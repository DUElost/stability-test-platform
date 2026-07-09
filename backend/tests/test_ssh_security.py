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


def test_trust_host_key_replaces_prior_entry_for_same_ip(monkeypatch, tmp_path):
    from backend.core import ssh_security

    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("10.0.0.99 ssh-ed25519 OLDKEY\n", encoding="utf-8")

    fake_completed = SimpleNamespace(returncode=0, stdout="10.0.0.99 ssh-ed25519 NEWKEY\n", stderr="")
    monkeypatch.setattr(
        ssh_security.subprocess,
        "run",
        lambda *args, **kwargs: fake_completed,
    )

    ok, _ = ssh_security.trust_host_key("10.0.0.99", 22, str(known_hosts))

    assert ok is True
    content = known_hosts.read_text(encoding="utf-8")
    assert "OLDKEY" not in content
    assert "10.0.0.99 ssh-ed25519 NEWKEY" in content
