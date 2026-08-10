"""EventUploader unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.event_uploader import EventUploader, _UploadJob


@pytest.fixture(autouse=True)
def reset_uploader(monkeypatch):
    monkeypatch.delenv("STP_EVENT_UPLOADER_ENABLED", raising=False)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/tmp/stp-aee-nfs-test")
    EventUploader._reset_for_tests()
    yield
    EventUploader._reset_for_tests()


def test_enqueue_skipped_when_disabled():
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1")
    assert up.enqueue_local_event(event={"id": "1", "local_path": "/tmp/x"}) is False


def test_upload_one_marks_remote(tmp_path, monkeypatch):
    monkeypatch.setenv("STP_EVENT_UPLOADER_ENABLED", "1")
    src = tmp_path / "event_dir"
    src.mkdir()
    (src / "a.txt").write_text("hello", encoding="utf-8")

    nfs = tmp_path / "nfs"
    up = EventUploader.instance()
    up.configure(api_url="http://127.0.0.1:8000", agent_secret="secret", host_id="host-1", nfs_root=str(nfs))

    patches = []
    posted = []

    def fake_post(url, **kwargs):
        posted.append(kwargs.get("json"))
        resp = MagicMock()
        resp.status_code = 200
        return resp

    patches.append(patch("backend.agent.event_uploader.requests.post", side_effect=fake_post))
    patches.append(patch(
        "backend.agent.event_uploader.resolve_upload_devices_dir",
        return_value=nfs / "devices" / "99",
    ))

    for p in patches:
        p.start()
    try:
        job = _UploadJob(
            event_id="00000000-0000-0000-0000-000000000001",
            local_path=str(src),
            plan_run_id=99,
            serial="dev1",
            platform="MTK",
            event_type="KE",
            detected_at="2026-08-09T10:00:00+00:00",
            host_id="host-1",
        )
        up._upload_one(job)
        assert (nfs / "devices" / "99" / src.name).is_dir()
        assert posted[-1]["events"][0]["state"] == "REMOTE"
    finally:
        for p in patches:
            p.stop()
