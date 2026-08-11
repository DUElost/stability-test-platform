"""中心存储根解析：core 与 agent 副本同语义；artifact 未配置时报明确错误。"""

from __future__ import annotations

import pytest

from backend.api.routes.stats import _disk_usage_percent_from_extra
from backend.core.artifact_paths import (
    ArtifactPathError,
    ArtifactPathOutsideRootError,
    get_stp_nfs_root,
    resolve_device_event_remote_path,
    resolve_local_artifact_path,
)
from backend.core.storage_root import (
    resolve_legacy_shared_storage_root,
    resolve_shared_storage_root as core_resolve,
)
from backend.agent.aee.paths import resolve_shared_storage_root as agent_resolve


def _clear_share_env(monkeypatch):
    for key in (
        "STP_AEE_NFS_ROOT",
        "STP_WATCHER_NFS_BASE_DIR",
        "STP_AEE_CIFS_ROOT",
        "STP_NFS_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("resolver", [core_resolve, agent_resolve])
def test_resolvers_agree_on_primary_and_aliases(monkeypatch, resolver):
    _clear_share_env(monkeypatch)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/stp-aee")
    monkeypatch.setenv("STP_AEE_CIFS_ROOT", "/mnt/cifs")
    assert resolver() == "/mnt/stp-aee"

    _clear_share_env(monkeypatch)
    monkeypatch.setenv("STP_AEE_CIFS_ROOT", "/mnt/cifs")
    assert resolver() == "/mnt/cifs"

    _clear_share_env(monkeypatch)
    monkeypatch.setenv("STP_NFS_ROOT", "/mnt/storage")
    assert resolver() == ""


def test_disk_usage_percent_unknown_is_none():
    assert _disk_usage_percent_from_extra({}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": None}}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": {}}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": "n/a"}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": "bad"}}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": float("nan")}}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": float("inf")}}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": -1}}) is None
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": 101}}) is None


def test_disk_usage_percent_reads_number():
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": 12.5}}) == 12.5
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": 0}}) == 0.0
    assert _disk_usage_percent_from_extra({"disk_usage": {"usage_percent": 100}}) == 100.0


def test_artifact_path_unconfigured_is_explicit(monkeypatch, tmp_path):
    _clear_share_env(monkeypatch)
    with pytest.raises(ArtifactPathError, match="STP_AEE_NFS_ROOT is not configured"):
        get_stp_nfs_root()
    with pytest.raises(ArtifactPathError, match="STP_AEE_NFS_ROOT is not configured"):
        resolve_local_artifact_path(str(tmp_path / "job.bin"))


def test_resolve_device_event_remote_path_scoped_to_plan_run(monkeypatch, tmp_path):
    nfs = tmp_path / "nfs"
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    allowed = nfs / "devices" / "42" / "ke_001"
    allowed.mkdir(parents=True)
    foreign = nfs / "devices" / "99" / "ke_002"
    foreign.mkdir(parents=True)

    resolved = resolve_device_event_remote_path(str(allowed), plan_run_id=42)
    assert resolved == allowed.resolve()

    with pytest.raises(ArtifactPathOutsideRootError):
        resolve_device_event_remote_path(str(foreign), plan_run_id=42)


def test_resolve_legacy_shared_storage_root(monkeypatch):
    monkeypatch.delenv("STP_AEE_NFS_ROOT_LEGACY", raising=False)
    assert resolve_legacy_shared_storage_root() == ""
    monkeypatch.setenv("STP_AEE_NFS_ROOT_LEGACY", "/mnt/legacy-nfs")
    assert resolve_legacy_shared_storage_root() == "/mnt/legacy-nfs"


def test_resolve_extract_event_src_legacy_root(monkeypatch, tmp_path):
    primary = tmp_path / "primary"
    legacy = tmp_path / "legacy"
    event_name = "ke_event_001"
    legacy_src = legacy / "devices" / "42" / event_name
    legacy_src.mkdir(parents=True)
    (legacy_src / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("STP_AEE_NFS_ROOT_LEGACY", str(legacy))

    from backend.core.artifact_paths import resolve_extract_event_src

    located = resolve_extract_event_src(
        f"/old/mount/devices/42/{event_name}",
        nfs_root=str(primary),
        legacy_root=str(legacy),
        plan_run_id=42,
    )
    assert located is not None
    src, devices_root = located
    assert src == legacy_src.resolve()
    assert devices_root == (legacy / "devices" / "42").resolve()


def test_resolve_extract_event_src_rejects_plan_run_symlink_escape(tmp_path):
    nfs = tmp_path / "nfs"
    outside = tmp_path / "outside"
    event_name = "ev1"
    outside_event = outside / event_name
    outside_event.mkdir(parents=True)
    (outside_event / "a.txt").write_text("x", encoding="utf-8")

    devices = nfs / "devices"
    devices.mkdir(parents=True)
    (devices / "42").symlink_to(outside, target_is_directory=True)

    from backend.core.artifact_paths import resolve_extract_event_src

    assert resolve_extract_event_src(
        event_name,
        nfs_root=str(nfs),
        legacy_root="",
        plan_run_id=42,
    ) is None


def test_resolve_extract_event_src_rejects_cross_plan_run_symlink(tmp_path):
    nfs = tmp_path / "nfs"
    event_name = "ev1"
    devices = nfs / "devices"
    real_run = devices / "99" / event_name
    real_run.mkdir(parents=True)
    (real_run / "a.txt").write_text("x", encoding="utf-8")
    (devices / "42").symlink_to(devices / "99", target_is_directory=True)

    from backend.core.artifact_paths import resolve_extract_event_src

    assert resolve_extract_event_src(
        event_name,
        nfs_root=str(nfs),
        legacy_root="",
        plan_run_id=42,
    ) is None


def test_copytree_under_root_rejects_nested_symlink(tmp_path):
    root = tmp_path / "devices" / "42"
    event = root / "ev1"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    event.mkdir(parents=True)
    (event / "nested").symlink_to(outside)

    from backend.core.artifact_paths import ArtifactPathOutsideRootError, copytree_under_root

    with pytest.raises(ArtifactPathOutsideRootError):
        copytree_under_root(event, tmp_path / "dest", root=root)


def test_copytree_under_root_copies_tree(tmp_path):
    root = tmp_path / "devices" / "42"
    event = root / "ev1"
    event.mkdir(parents=True)
    (event / "a.txt").write_text("hello", encoding="utf-8")
    sub = event / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world", encoding="utf-8")

    from backend.core.artifact_paths import copytree_under_root

    dest = tmp_path / "dest"
    copytree_under_root(event, dest, root=root)
    assert (dest / "a.txt").read_text(encoding="utf-8") == "hello"
    assert (dest / "sub" / "b.txt").read_text(encoding="utf-8") == "world"


def test_copytree_under_root_rejects_symlink_source(tmp_path):
    import shutil

    root = tmp_path / "devices" / "42"
    event = root / "ev1"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    event.mkdir(parents=True)
    (event / "safe.txt").write_text("ok", encoding="utf-8")

    real_event = root / "ev1_real"
    shutil.move(event, real_event)
    event.symlink_to(outside, target_is_directory=True)

    from backend.core.artifact_paths import ArtifactPathOutsideRootError, copytree_under_root

    with pytest.raises(ArtifactPathOutsideRootError):
        copytree_under_root(event, tmp_path / "dest", root=root)


def test_resolve_extract_event_src_rejects_symlink_event_dir(tmp_path):
    nfs = tmp_path / "nfs"
    scope = nfs / "devices" / "42"
    ev2 = scope / "ev2"
    ev2.mkdir(parents=True)
    (ev2 / "a.txt").write_text("x", encoding="utf-8")
    (scope / "ev1").symlink_to(ev2, target_is_directory=True)

    from backend.core.artifact_paths import resolve_extract_event_src

    assert resolve_extract_event_src(
        "ev1", nfs_root=str(nfs), legacy_root="", plan_run_id=42,
    ) is None
    located = resolve_extract_event_src(
        "ev2", nfs_root=str(nfs), legacy_root="", plan_run_id=42,
    )
    assert located is not None


def test_resolve_extract_event_src_rejects_nested_relative_symlink(tmp_path):
    nfs = tmp_path / "nfs"
    scope = nfs / "devices" / "42"
    ev1 = scope / "ev1"
    ev2 = scope / "ev2"
    ev1.mkdir(parents=True)
    ev2.mkdir(parents=True)
    (ev1 / "nested").symlink_to("../ev2")

    from backend.core.artifact_paths import resolve_extract_event_src

    assert resolve_extract_event_src(
        "ev1", nfs_root=str(nfs), legacy_root="", plan_run_id=42,
    ) is None
