"""#172 — 15.4 共享根路径约定统一助手。"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from backend.agent.aee.paths import (
    get_or_create_run_date_stamp,
    resolve_artifact_promote_dir,
    resolve_puller_artifact_dir,
    resolve_spill_devices_dest,
    resolve_upload_devices_dir,
    shanghai_mmdd,
)


def test_shanghai_mmdd_ignores_host_local_tz():
    # 2026-08-09 00:30 CST == 2026-08-08 09:30 PDT / 16:30 UTC previous day.
    utc = datetime(2026, 8, 8, 16, 30, tzinfo=timezone.utc)
    assert shanghai_mmdd(utc) == "0809"
    pdt_naive_as_utc = datetime(2026, 8, 9, 6, 30, tzinfo=timezone.utc)  # 14:30 CST
    assert shanghai_mmdd(pdt_naive_as_utc) == "0809"
    before_cst_midnight = datetime(2026, 8, 8, 15, 59, tzinfo=timezone.utc)  # 23:59 CST
    assert shanghai_mmdd(before_cst_midnight) == "0808"


def test_get_or_create_run_date_stamp_persists_and_uses_shanghai(monkeypatch):
    store = MagicMock()
    store.get_state.return_value = ""
    # 固定时钟，避免测试恰好跨 Asia/Shanghai 午夜时断言漂移。
    monkeypatch.setattr(
        "backend.agent.aee.paths.shanghai_mmdd",
        lambda now=None: "0809",
    )
    stamp = get_or_create_run_date_stamp(store, 42)
    assert stamp == "0809"
    store.set_state.assert_called_once_with("aee:42:run_date_stamp", "0809")
    store.get_state.return_value = "0808"
    assert get_or_create_run_date_stamp(store, 42) == "0808"


def test_get_or_create_run_date_stamp_prefers_authoritative_value():
    """控制面派生 stamp 是权威值：即使无已存值也直接持久化并返回。"""
    store = MagicMock()
    store.get_state.return_value = ""
    assert get_or_create_run_date_stamp(store, 42, run_date_stamp="0810") == "0810"
    store.set_state.assert_called_once_with("aee:42:run_date_stamp", "0810")


def test_get_or_create_run_date_stamp_overwrites_stale_local_fallback():
    """Agent 本地回退先写入的旧 stamp 会被权威值覆盖，避免 MMDD 漂移。"""
    store = MagicMock()
    store.get_state.return_value = "0809"
    assert get_or_create_run_date_stamp(store, 42, run_date_stamp="0810") == "0810"
    assert store.set_state.call_args.args == ("aee:42:run_date_stamp", "0810")


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
