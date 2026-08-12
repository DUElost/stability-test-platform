"""#213 D3 — orphan job_log_signal (job_id IS NULL) admin list."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models.job import JobLogSignal
from backend.services.job_log_signal import (
    ORPHAN_EXCLUDING_CALL_SITES,
    count_orphan_log_signals,
    list_orphan_log_signals,
)


def test_count_and_list_orphan_log_signals(
    db_session, sample_host, sample_job_instance,
):
    now = datetime.now(timezone.utc)
    orphan = JobLogSignal(
        job_id=None,
        host_id=sample_host.id,
        device_serial="orphan-serial",
        seq_no=1,
        category="AEE",
        source="reconciler",
        path_on_device="/data/aee_exp/db.01.ANR",
        detected_at=now,
        received_at=now,
        extra={"nfs_path": "/tmp/orphan"},
    )
    linked = JobLogSignal(
        job_id=sample_job_instance.id,
        host_id=sample_host.id,
        device_serial="linked-serial",
        seq_no=1,
        category="AEE",
        source="reconciler",
        path_on_device="/data/aee_exp/db.02.JE",
        detected_at=now,
        received_at=now,
        extra={"nfs_path": "/tmp/linked"},
    )
    db_session.add_all([orphan, linked])
    db_session.commit()

    assert count_orphan_log_signals(db_session) >= 1
    rows = list_orphan_log_signals(db_session, skip=0, limit=50)
    assert any(r.device_serial == "orphan-serial" for r in rows)
    assert all(r.job_id is None for r in rows)


def test_orphan_log_signals_endpoint_admin_only(
    client, admin_headers, auth_headers, db_session, sample_host,
):
    now = datetime.now(timezone.utc)
    db_session.add(JobLogSignal(
        job_id=None,
        host_id=sample_host.id,
        device_serial="api-orphan",
        seq_no=99,
        category="ANR",
        source="inotifyd",
        path_on_device="/data/anr/traces.txt",
        detected_at=now,
        received_at=now,
    ))
    db_session.commit()

    denied = client.get("/api/v1/log-signals/orphans", headers=auth_headers)
    assert denied.status_code in (401, 403)

    ok = client.get("/api/v1/log-signals/orphans?limit=20", headers=admin_headers)
    assert ok.status_code == 200
    body = ok.json()
    data = body.get("data") or body
    assert data["total"] >= 1
    assert any(i["device_serial"] == "api-orphan" for i in data["items"])
    assert data["excluding_call_sites"] == list(ORPHAN_EXCLUDING_CALL_SITES)


def test_upload_manager_import_fallback_uses_aee_paths():
    """#213 E2: no duplicated resolve_shared_storage_root in ImportError branch."""
    import inspect

    from backend.agent import upload_manager as um

    src = inspect.getsource(um)
    assert "from agent.aee.paths import resolve_shared_storage_root" in src
    assert 'for alias in ("STP_WATCHER_NFS_BASE_DIR"' not in src
