from __future__ import annotations

from cryptography.fernet import Fernet

from backend.models.host import Host


def test_migrate_legacy_host_ssh_credentials_persists_fields(monkeypatch, db_session):
    from backend.scripts.migrate_host_ssh_credentials import migrate_legacy_host_ssh_credentials

    monkeypatch.setenv("SSH_CREDENTIALS_FERNET_KEY", Fernet.generate_key().decode())
    host = Host(
        id="migrate-1",
        hostname="migrate-1",
        name="migrate-1",
        ip="192.168.1.150",
        ip_address="192.168.1.150",
        ssh_user="root",
        extra={"ssh_password": "legacy-pass", "ssh_key_path": "/tmp/legacy.key", "rack": "A5"},
        status="ONLINE",
    )
    db_session.add(host)
    db_session.commit()

    summary = migrate_legacy_host_ssh_credentials(db_session)

    assert summary["scanned"] == 1
    assert summary["changed"] == 1
    assert summary["passwords_migrated"] == 1
    assert summary["key_paths_migrated"] == 1

    db_session.refresh(host)
    assert host.ssh_password_enc
    assert host.ssh_key_path == "/tmp/legacy.key"
    assert host.extra == {"rack": "A5"}


def test_migrate_legacy_host_ssh_credentials_dry_run_keeps_db_unchanged(monkeypatch, db_session):
    from backend.scripts.migrate_host_ssh_credentials import migrate_legacy_host_ssh_credentials

    monkeypatch.setenv("SSH_CREDENTIALS_FERNET_KEY", Fernet.generate_key().decode())
    host = Host(
        id="migrate-2",
        hostname="migrate-2",
        name="migrate-2",
        ip="192.168.1.151",
        ip_address="192.168.1.151",
        extra={"ssh_password": "legacy-pass"},
        status="ONLINE",
    )
    db_session.add(host)
    db_session.commit()

    summary = migrate_legacy_host_ssh_credentials(db_session, dry_run=True)

    assert summary["changed"] == 1
    assert summary["dry_run"] is True

    db_session.refresh(host)
    assert host.ssh_password_enc in (None, "")
    assert host.extra == {"ssh_password": "legacy-pass"}
