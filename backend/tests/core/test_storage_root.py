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
from backend.core.storage_root import resolve_shared_storage_root as core_resolve
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
