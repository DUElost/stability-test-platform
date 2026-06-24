"""UploadManager 单测（ADR-0025 Sprint 4 Task 2）。

覆盖面：
  1. upload_scan_report copies _org.xls to dedup
  2. upload_event_dirs copies event dirs to devices
  3. not configured → None/0
  4. source missing → skip/None
  5. dest already exists → skip
  6. configure env fallback
  7. reconfigure rejected
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agent.upload_manager import UploadManager


@pytest.fixture(autouse=True)
def _reset_upload_manager():
    UploadManager._reset_for_tests()
    yield
    UploadManager._reset_for_tests()


def _make_manager(nfs_root: str) -> UploadManager:
    m = UploadManager.instance()
    m.configure(nfs_root=nfs_root)
    assert m.is_configured()
    return m


def test_upload_scan_report_copies_org_xls_to_dedup(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_dir = tmp_path / "scan_output"
    src_dir.mkdir()
    org_xls = src_dir / "Result_shanghai_org.xls"
    org_xls.write_text("fake-xls-content")

    result = m.upload_scan_report(42, "host-1", str(org_xls))

    assert result is not None
    dest = Path(result)
    assert dest.exists()
    assert dest.read_text() == "fake-xls-content"
    assert dest.name == "host-1_Result_shanghai_org.xls"
    assert "dedup" in str(dest)
    assert "42" in str(dest)


def test_upload_event_dirs_copies_to_devices(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_root = tmp_path / "events"
    src_root.mkdir()
    event_dir = src_root / "aee_db_20260622"
    event_dir.mkdir()
    (event_dir / "main.dbg").write_text("dbg")
    (event_dir / "mobilelog").mkdir()
    (event_dir / "mobilelog" / "log.txt").write_text("log")

    count = m.upload_event_dirs(42, ["aee_db_20260622"], str(src_root))

    assert count == 1
    dest_dir = nfs / "devices" / "42" / "aee_db_20260622"
    assert dest_dir.is_dir()
    assert (dest_dir / "main.dbg").read_text() == "dbg"
    assert (dest_dir / "mobilelog" / "log.txt").read_text() == "log"


def test_upload_manager_not_configured(tmp_path):
    m = UploadManager.instance()
    assert not m.is_configured()

    assert m.upload_scan_report(1, "h", "/fake/path.xls") is None
    assert m.upload_event_dirs(1, ["dir"], "/fake/root") == 0


def test_upload_scan_report_source_missing(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    result = m.upload_scan_report(1, "host-1", "/nonexistent/file.xls")
    assert result is None


def test_upload_event_dirs_dest_already_exists(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_root = tmp_path / "events"
    src_root.mkdir()
    event_dir = src_root / "aee_db_existing"
    event_dir.mkdir()
    (event_dir / "main.dbg").write_text("old")

    dest_dir = nfs / "devices" / "42" / "aee_db_existing"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "placeholder.txt").write_text("already-here")

    count = m.upload_event_dirs(42, ["aee_db_existing"], str(src_root))

    assert count == 0
    assert (dest_dir / "placeholder.txt").read_text() == "already-here"
    assert not (dest_dir / "main.dbg").exists()


def test_upload_event_dirs_source_missing_skipped(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    count = m.upload_event_dirs(1, ["nonexistent_dir"], str(tmp_path / "nope"))
    assert count == 0


def test_configure_env_fallback(monkeypatch, tmp_path):
    env_nfs = str(tmp_path / "env_nfs")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", env_nfs)
    m = UploadManager.instance()
    m.configure()
    assert m.is_configured()
    assert m._nfs_root == env_nfs


def test_configure_rejected_if_already_configured(tmp_path):
    m = _make_manager(str(tmp_path / "first"))
    first_root = m._nfs_root
    m.configure(nfs_root=str(tmp_path / "second"))
    assert m._nfs_root == first_root


def test_upload_event_dirs_multiple_some_fail(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_root = tmp_path / "events"
    src_root.mkdir()
    ok_dir = src_root / "aee_db_ok"
    ok_dir.mkdir()
    (ok_dir / "main.dbg").write_text("ok")
    missing_dir = src_root / "aee_db_missing"

    count = m.upload_event_dirs(42, ["aee_db_ok", "aee_db_missing"], str(src_root))

    assert count == 1
    assert (nfs / "devices" / "42" / "aee_db_ok" / "main.dbg").exists()


def test_upload_scan_report_copies_subdirs(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_dir = tmp_path / "scan_output"
    src_dir.mkdir()
    org_xls = src_dir / "Result_shanghai_org.xls"
    org_xls.write_text("xls")

    result = m.upload_scan_report(99, "host-abc", str(org_xls))
    assert result is not None
    dest = Path(result)
    assert dest.name == "host-abc_Result_shanghai_org.xls"
    assert "dedup" in str(dest) and "99" in str(dest)


def test_upload_event_dirs_auto_discover_ignores_non_timestamp(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_root = tmp_path / "events"
    src_root.mkdir()
    good = src_root / "2026-06-23_14-30-00_db.01"
    good.mkdir()
    (good / "main.dbg").write_text("ok")
    bad_no_ts = src_root / "some_random_dir"
    bad_no_ts.mkdir()
    (bad_no_ts / "file.txt").write_text("bad")
    bad_nested = src_root / "subdir"
    bad_nested.mkdir()
    bad_deep = bad_nested / "2026-06-23_15-00-00_db.02"
    bad_deep.mkdir(parents=True, exist_ok=True)
    (bad_deep / "nested.txt").write_text("nested")

    count = m.upload_event_dirs(42, [], str(src_root))

    assert count == 1
    assert (nfs / "devices" / "42" / "2026-06-23_14-30-00_db.01" / "main.dbg").exists()
    assert not (nfs / "devices" / "42" / "some_random_dir").exists()
    assert not (nfs / "devices" / "42" / "2026-06-23_15-00-00_db.02").exists()


def test_upload_event_dirs_auto_discover_skips_existing(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_root = tmp_path / "events"
    src_root.mkdir()
    event_dir = src_root / "2026-06-23_14-30-00_db.01"
    event_dir.mkdir()
    (event_dir / "main.dbg").write_text("ok")

    dest = nfs / "devices" / "42" / "2026-06-23_14-30-00_db.01"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "placeholder.txt").write_text("here")

    count = m.upload_event_dirs(42, [], str(src_root))

    assert count == 0
    assert (dest / "placeholder.txt").read_text() == "here"
    assert not (dest / "main.dbg").exists()


def test_configure_force_overrides_existing(tmp_path):
    m = _make_manager(str(tmp_path / "first"))
    assert m._nfs_root == str(tmp_path / "first")
    m.configure(nfs_root=str(tmp_path / "second"), force=True)
    assert m._nfs_root == str(tmp_path / "second")
