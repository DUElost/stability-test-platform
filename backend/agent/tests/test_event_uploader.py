"""EventUploader unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.event_uploader import (
    EventUploader,
    _MAX_RETRIES,
    _UploadJob,
    _recover_states,
)


@pytest.fixture(autouse=True)
def reset_uploader(monkeypatch):
    # #287：默认开；显式清掉开关与旧键，保证用例间互不影响。
    monkeypatch.delenv("STP_DEVICE_LOG_EVENT_ENABLED", raising=False)
    monkeypatch.delenv("STP_EVENT_UPLOADER_ENABLED", raising=False)
    monkeypatch.delenv("STP_EVENT_UPLOADER_CONTINUOUS", raising=False)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/tmp/stp-aee-nfs-test")
    EventUploader._reset_for_tests()
    yield
    EventUploader._reset_for_tests()


def test_enabled_by_default(monkeypatch):
    """#287：未设 STP_DEVICE_LOG_EVENT_ENABLED 时组件默认开启。"""
    monkeypatch.delenv("STP_DEVICE_LOG_EVENT_ENABLED", raising=False)
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1")
    assert EventUploader.is_enabled() is True


def test_explicit_zero_disables(monkeypatch):
    """#287：=0 是唯一关闭方式；过滤模型下非 force 的 LOCAL 入队仍被拒绝。"""
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "0")
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1")
    assert EventUploader.is_enabled() is False
    assert up.enqueue_local_event(event={"id": "1", "local_path": "/tmp/x"}) is False


def test_recover_states_only_upload_pending():
    """#380/#287: 30s 快速轮询只覆盖 UPLOAD_PENDING（过滤模型）。

    LOCAL 是「有意不传」；UPLOADING/UPLOAD_FAILED 由 600s 慢速循环负责，
    两循环状态集不重叠，否则重试上限被轮询绕过（attempt 每 30s 归零）。
    """
    assert _recover_states() == "UPLOAD_PENDING"


def test_enqueue_dedups_active_event(tmp_path, monkeypatch):
    """#380: 同一 event_id 在队列/在传/退避重试中时，重复入队被拒绝。

    force 路径（HddSpill 溢出语义）用于验证去重行为本身。
    """
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1", nfs_root=str(tmp_path))
    event = {"id": "evt-dup", "local_path": str(tmp_path / "d")}
    assert up.enqueue_local_event(event=event, force=True) is True
    assert up.enqueue_local_event(event=event, force=True) is False
    assert up.enqueue_local_event(event=event) is False
    # 消费掉 job（模拟完成）后可再次入队
    job = up._queue.get_nowait()
    up._forget_active(job.event_id)
    assert up.enqueue_local_event(event=event, force=True) is True


def test_run_upload_releases_active_on_terminal(tmp_path, monkeypatch):
    """#380: 终态出口释放 in-flight 标记；退避重试期间保留。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    src = tmp_path / "evt_dir"
    src.mkdir()
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1", nfs_root=str(tmp_path / "nfs"))
    job = _UploadJob(
        event_id="evt-term", local_path=str(src), plan_run_id=1, serial="d",
        platform="MTK", event_type="KE", detected_at="2026-08-09T10:00:00+00:00",
        host_id="h1",
    )
    # 经公开入队路径建立 in-flight 标记（enqueue 时写入 _active_ids）
    up._active_ids.add(job.event_id)
    posted = []

    def fake_post(url, **kwargs):
        posted.append(kwargs.get("json"))
        return MagicMock(status_code=200)

    with patch("backend.agent.event_uploader.requests.post", side_effect=fake_post), patch(
        "backend.agent.event_uploader.UploadManager._copytree_safe",
        side_effect=OSError("cifs down"),
    ), patch("backend.agent.event_uploader.threading.Timer"):
        up._run_upload_holding_slot(job)
        # 第一次失败 → 退避重试排队（rescheduled），保留 active 标记
        assert job.rescheduled is True
        assert "evt-term" in up._active_ids
        # 耗尽重试（把 attempt 推到上限）→ 终态 UPLOAD_FAILED，释放标记
        job.attempt = _MAX_RETRIES - 1
        job.rescheduled = False
        up._run_upload_holding_slot(job)
    assert posted[-1]["events"][0]["state"] == "UPLOAD_FAILED"
    assert "evt-term" not in up._active_ids


def test_missing_local_patches_pull_failed_when_remote_absent(tmp_path, monkeypatch):
    """#380: 本地目录缺失且远端无副本 → 终态 PULL_FAILED（不再永久卡 UPLOAD_PENDING）。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1", nfs_root=str(tmp_path / "nfs"))
    job = _UploadJob(
        event_id="evt-gone", local_path=str(tmp_path / "never_pulled"), plan_run_id=7,
        serial="d", platform="MTK", event_type="KE",
        detected_at="2026-08-09T10:00:00+00:00", host_id="h1",
    )
    posted = []

    def fake_post(url, **kwargs):
        posted.append(kwargs.get("json"))
        return MagicMock(status_code=200)

    with patch("backend.agent.event_uploader.requests.post", side_effect=fake_post):
        up._upload_one(job)
    assert posted[-1]["events"][0]["state"] == "PULL_FAILED"


def test_missing_local_patches_remote_when_remote_present(tmp_path, monkeypatch):
    """#380: 本地已删（prune 后 REMOTE patch 失败竞态）而远端仍在 → 恢复 REMOTE。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    nfs = tmp_path / "nfs"
    dst = nfs / "devices" / "7" / "evt_dir"
    dst.mkdir(parents=True)
    (dst / "a.txt").write_text("x", encoding="utf-8")
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1", nfs_root=str(nfs))
    job = _UploadJob(
        event_id="evt-remote-only", local_path=str(tmp_path / "evt_dir"), plan_run_id=7,
        serial="d", platform="MTK", event_type="KE",
        detected_at="2026-08-09T10:00:00+00:00", host_id="h1",
    )
    posted = []

    def fake_post(url, **kwargs):
        posted.append(kwargs.get("json"))
        return MagicMock(status_code=200)

    with patch(
        "backend.agent.event_uploader.resolve_upload_devices_dir",
        return_value=nfs / "devices" / "7",
    ), patch("backend.agent.event_uploader.requests.post", side_effect=fake_post):
        up._upload_one(job)
    payload = posted[-1]["events"][0]
    assert payload["state"] == "REMOTE"
    assert payload["remote_path"] == str(dst)


def test_prune_after_upload_flag_forces_prune(tmp_path, monkeypatch):
    """#382: HddSpill 溢出事件（prune_after_upload）不受灰度开关约束。"""
    monkeypatch.delenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", raising=False)
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    src = tmp_path / "event_dir"
    src.mkdir()
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1", nfs_root=str(tmp_path / "nfs"))
    job = _UploadJob(
        event_id="e-spill", local_path=str(src), plan_run_id=1, serial="d", platform="MTK",
        event_type="KE", detected_at="2026-08-09T10:00:00+00:00", host_id="h1",
        prune_after_upload=True,
    )
    with patch("backend.agent.event_uploader.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        up._maybe_prune_local(job, remote_path="/nfs/devices/1/event_dir")
    assert not src.exists()


def test_upload_one_marks_remote(tmp_path, monkeypatch):
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
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


def test_upload_one_rejects_path_outside_local_root(tmp_path, monkeypatch):
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path / "aee"))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    (tmp_path / "aee").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    up = EventUploader.instance()
    up.configure(
        api_url="http://127.0.0.1:8000",
        agent_secret="secret",
        host_id="host-1",
        nfs_root=str(tmp_path / "nfs"),
    )

    with patch("backend.agent.event_uploader.requests.post") as mock_post:
        up._upload_one(_UploadJob(
            event_id="00000000-0000-0000-0000-000000000002",
            local_path=str(outside),
            plan_run_id=1,
            serial="dev1",
            platform="MTK",
            event_type="KE",
            detected_at="2026-08-09T10:00:00+00:00",
            host_id="host-1",
        ))
    mock_post.assert_not_called()


def test_prune_local_skipped_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", raising=False)
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    src = tmp_path / "event_dir"
    src.mkdir()
    up = EventUploader.instance()
    up.configure(api_url="http://x", agent_secret="s", host_id="h1", nfs_root=str(tmp_path / "nfs"))
    job = _UploadJob(
        event_id="e1", local_path=str(src), plan_run_id=1, serial="d", platform="MTK",
        event_type="KE", detected_at="2026-08-09T10:00:00+00:00", host_id="h1",
    )
    with patch("backend.agent.event_uploader.shutil.rmtree") as mock_rm:
        up._maybe_prune_local(job, remote_path="/nfs/devices/1/event_dir")
    mock_rm.assert_not_called()
    assert src.is_dir()


def test_prune_local_deletes_and_patches_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", "1")
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "1")
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    src = tmp_path / "event_dir"
    src.mkdir()
    up = EventUploader.instance()
    up.configure(api_url="http://127.0.0.1:8000", agent_secret="secret", host_id="h1", nfs_root=str(tmp_path / "nfs"))
    posted = []

    def fake_post(url, **kwargs):
        posted.append(kwargs.get("json"))
        resp = MagicMock()
        resp.status_code = 200
        return resp

    job = _UploadJob(
        event_id="e1", local_path=str(src), plan_run_id=1, serial="d", platform="MTK",
        event_type="KE", detected_at="2026-08-09T10:00:00+00:00", host_id="h1",
    )
    with patch("backend.agent.event_uploader.requests.post", side_effect=fake_post):
        up._maybe_prune_local(job, remote_path="/nfs/devices/1/event_dir")
    assert not src.exists()
    assert posted[-1]["events"][0]["state"] == "PRUNED"


def test_prune_local_rmtree_failure_does_not_patch_pruned(tmp_path, monkeypatch):
    """#217: state=PRUNED only after local delete succeeds."""
    monkeypatch.setenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", "1")
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "1")
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    src = tmp_path / "event_dir"
    src.mkdir()
    up = EventUploader.instance()
    up.configure(
        api_url="http://127.0.0.1:8000",
        agent_secret="secret",
        host_id="h1",
        nfs_root=str(tmp_path / "nfs"),
    )
    job = _UploadJob(
        event_id="e1", local_path=str(src), plan_run_id=1, serial="d", platform="MTK",
        event_type="KE", detected_at="2026-08-09T10:00:00+00:00", host_id="h1",
    )
    with patch(
        "backend.agent.event_uploader.shutil.rmtree",
        side_effect=OSError("busy"),
    ), patch("backend.agent.event_uploader.requests.post") as mock_post:
        up._maybe_prune_local(job, remote_path="/nfs/devices/1/event_dir")
    mock_post.assert_not_called()
    assert src.is_dir()


def test_prune_local_refuses_aee_root(tmp_path, monkeypatch):
    monkeypatch.setenv("STP_EVENT_UPLOADER_PRUNE_LOCAL", "1")
    monkeypatch.setenv("STP_DEVICE_LOG_EVENT_ENABLED", "1")
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr("backend.agent.aee.paths._mount_fstype_for_path", lambda _p: "ext4")
    up = EventUploader.instance()
    up.configure(api_url="http://127.0.0.1:8000", agent_secret="secret", host_id="h1", nfs_root=str(tmp_path / "nfs"))
    job = _UploadJob(
        event_id="e1", local_path=str(tmp_path), plan_run_id=1, serial="d", platform="MTK",
        event_type="KE", detected_at="2026-08-09T10:00:00+00:00", host_id="h1",
    )
    with patch("backend.agent.event_uploader.shutil.rmtree") as mock_rm, patch(
        "backend.agent.event_uploader.requests.post",
    ) as mock_post:
        up._maybe_prune_local(job, remote_path="/nfs/devices/1/x")
    mock_rm.assert_not_called()
    mock_post.assert_not_called()
    assert tmp_path.is_dir()
