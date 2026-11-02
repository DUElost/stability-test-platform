"""B8 G0-a counterexamples: distribution/jobs stay on control, old logs only read."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.core.artifact_paths import (
    ArtifactPathError,
    ArtifactPathOutsideRootError,
    get_local_artifact_roots,
    resolve_device_event_remote_path,
    resolve_local_artifact_path,
)
from backend.core.storage_root import resolve_control_plane_storage_root
from backend.scheduler.cron_scheduler import purge_run_storage_dirs, purge_unassigned_event_dirs
from backend.services.device_log_event_download import build_device_log_event_download_response
from backend.services.job_artifact_download import _artifact_download_target
from backend.services.script_catalog import default_packages_root
from backend.services.suite_binding import global_disk_path, runtask_disk_path
from backend.storage_families import JOBS_FAMILY, RUN_FAMILIES


@pytest.fixture
def roots(monkeypatch, tmp_path):
    paths = {"log": tmp_path / "log", "control": tmp_path / "control", "old": tmp_path / "old"}
    for key, name in (
        ("STP_AEE_NFS_ROOT", "log"), ("STP_CONTROL_PLANE_STORAGE_ROOT", "control"),
        ("STP_LEGACY_LOG_STORAGE_ROOT", "old"),
    ):
        monkeypatch.setenv(key, str(paths[name]))
        paths[name].mkdir()
    return SimpleNamespace(**paths)


def _write(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("evidence")
    return path


def test_distribution_and_job_download_use_control_root(roots, monkeypatch):
    monkeypatch.delenv("STP_PACKAGES_ROOT", raising=False)
    assert resolve_control_plane_storage_root() == str(roots.control)
    assert default_packages_root() == roots.control / "packages"
    suite = SimpleNamespace(export_dir="project", project=None)
    assert runtask_disk_path(suite).is_relative_to(roots.control / "mtbf")
    assert global_disk_path(suite).is_relative_to(roots.control / "mtbf")
    job_file = _write(roots.control / "jobs/7/log.txt")
    assert _artifact_download_target(str(job_file)) == {"kind": "local", "path": str(job_file)}
    monkeypatch.setenv("STP_PACKAGES_ROOT", str(roots.old / "explicit-packages"))
    assert default_packages_root() == roots.old / "explicit-packages"


def test_legacy_dle_download_succeeds_but_upload_receipt_rejects_it(roots):
    event_id = uuid4()
    old_file = _write(roots.old / "devices/42" / str(event_id) / "log.txt")
    event = SimpleNamespace(
        id=event_id, plan_run_id=42, state="REMOTE", remote_path=str(old_file),
    )
    db = SimpleNamespace(get=lambda *_args: event)
    response = build_device_log_event_download_response(db, plan_run_id=42, event_id=event_id)
    assert Path(response.path) == old_file
    assert roots.old in get_local_artifact_roots()
    assert resolve_local_artifact_path(str(old_file), must_exist=True) == old_file
    with pytest.raises(ArtifactPathOutsideRootError):
        resolve_device_event_remote_path(str(old_file), plan_run_id=42)
    with pytest.raises(ArtifactPathOutsideRootError):
        resolve_device_event_remote_path(str(old_file), plan_run_id=99, allow_legacy=True)
    with pytest.raises(ArtifactPathError):
        resolve_device_event_remote_path(str(roots.old / "devices/42/../../outside"), allow_legacy=True)
    current = _write(roots.log / "devices/42/new/log.txt")
    assert resolve_device_event_remote_path(str(current), plan_run_id=42) == current


@pytest.mark.parametrize("old_is_control", [False, True])
def test_retention_uses_family_root_and_keeps_old_logs(roots, monkeypatch, old_is_control):
    old = roots.control if old_is_control else roots.old
    monkeypatch.setenv("STP_LEGACY_LOG_STORAGE_ROOT", str(old))
    old_logs = [_write(old / family / "42/old.txt") for family in RUN_FAMILIES]
    new_logs = [_write(roots.log / family / "42/new.txt") for family in RUN_FAMILIES]
    job = _write(roots.control / JOBS_FAMILY / "7/job.txt")
    wrong_log_job = _write(roots.log / JOBS_FAMILY / "7/keep.txt")
    old_unassigned = _write(old / "devices/unassigned/event/log.txt")
    assert purge_run_storage_dirs([42], {42: [7]}) == set()
    assert all(not path.exists() for path in (*new_logs, job))
    assert all(path.read_text() == "evidence" for path in (*old_logs, wrong_log_job))
    assert purge_unassigned_event_dirs({str(old_unassigned): 42}) == set()
    assert old_unassigned.read_text() == "evidence"


def test_only_legacy_root_does_not_enable_any_writer(roots, monkeypatch):
    monkeypatch.delenv("STP_AEE_NFS_ROOT")
    monkeypatch.delenv("STP_CONTROL_PLANE_STORAGE_ROOT")
    sentinel = _write(roots.old / "devices/42/old.txt")
    assert resolve_control_plane_storage_root() == ""
    assert purge_run_storage_dirs([42], {42: [7]}) == set()
    assert sentinel.read_text() == "evidence"
    assert resolve_device_event_remote_path(str(sentinel), plan_run_id=42, allow_legacy=True) == sentinel
    with pytest.raises(ArtifactPathOutsideRootError):
        resolve_device_event_remote_path(str(sentinel), plan_run_id=42)
