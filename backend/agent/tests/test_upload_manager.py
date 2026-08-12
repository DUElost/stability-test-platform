"""UploadManager 单测（ADR-0025 Sprint 4 Task 2）。

覆盖面：
  1. upload_scan_report copies _org.xls to dedup
  2. not configured → None
  3. source missing → skip/None
  4. configure env fallback
  5. reconfigure rejected
"""

from __future__ import annotations

from pathlib import Path

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


def test_upload_manager_not_configured(tmp_path):
    m = UploadManager.instance()
    assert not m.is_configured()

    assert m.upload_scan_report(1, "h", "/fake/path.xls") is None


def test_upload_scan_report_source_missing(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    result = m.upload_scan_report(1, "host-1", "/nonexistent/file.xls")
    assert result is None


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


def test_configure_force_overrides_existing(tmp_path):
    m = _make_manager(str(tmp_path / "first"))
    assert m._nfs_root == str(tmp_path / "first")
    m.configure(nfs_root=str(tmp_path / "second"), force=True)
    assert m._nfs_root == str(tmp_path / "second")
