"""#172 — 15.4 共享根路径约定统一助手。"""

from pathlib import Path

from backend.agent.aee.paths import (
    resolve_artifact_promote_dir,
    resolve_puller_artifact_dir,
    resolve_spill_devices_dest,
    resolve_upload_devices_dir,
)


def test_artifact_promote_dir_is_jobs_job_id():
    assert resolve_artifact_promote_dir("/mnt/nfs", 42) == Path("/mnt/nfs/jobs/42")


def test_puller_artifact_dir_is_jobs_job_id_category():
    assert resolve_puller_artifact_dir("/mnt/nfs", 42, "AEE") == Path(
        "/mnt/nfs/jobs/42/AEE"
    )


def test_upload_devices_dir_is_devices_run_id():
    assert resolve_upload_devices_dir("/mnt/nfs", 7) == Path("/mnt/nfs/devices/7")


def test_spill_devices_dest_preserves_hdd_relative_path(tmp_path: Path):
    hdd = tmp_path / "hdd"
    local_dir = hdd / "folder" / "SERIAL" / "aee_exp" / "2026-08-06_10-00-00_db.0"
    local_dir.mkdir(parents=True)

    dest = resolve_spill_devices_dest("/mnt/nfs", hdd, local_dir)
    assert dest == Path(
        "/mnt/nfs/devices/folder/SERIAL/aee_exp/2026-08-06_10-00-00_db.0"
    )


def test_spill_devices_dest_rejects_path_outside_hdd(tmp_path: Path):
    hdd = tmp_path / "hdd"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    try:
        resolve_spill_devices_dest("/mnt/nfs", hdd, outside)
    except ValueError:
        return
    raise AssertionError("expected ValueError for path outside hdd root")
