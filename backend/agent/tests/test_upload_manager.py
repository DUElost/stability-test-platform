"""UploadManager 单测（ADR-0025 Sprint 4 Task 2）。

覆盖面：
  1. upload_scan_report copies _org.xls to dedup
  2. not configured → None
  3. source missing → skip/None
  4. configure env fallback
  5. reconfigure rejected
  6. 写侧登记分片（#2188 D 步单2）：成功写 / 幂等合并 / 失败 raise / 坏分片重建
"""

from __future__ import annotations

import json
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


# ── #2188 D 步单2（#2474）：写侧登记分片 ──────────────────────────────


def _shard(nfs: Path, run_id: int, host_id: str) -> Path:
    return nfs / "_meta" / str(run_id) / f"{host_id}.json"


def test_upload_scan_report_writes_manifest_shard(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))
    src_dir = tmp_path / "scan_output"
    src_dir.mkdir()
    org_xls = src_dir / "Result_shanghai_org.xls"
    org_xls.write_text("fake-xls-content")

    assert m.upload_scan_report(42, "host-1", str(org_xls)) is not None

    data = json.loads(_shard(nfs, 42, "host-1").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["host_id"] == "host-1"
    assert data["plan_run_id"] == 42
    assert data["updated_at"]
    assert data["artifacts"] == [
        {
            "file_key": "host-1_Result_shanghai_org.xls",
            "platform": "",
            "size_bytes": len("fake-xls-content"),
            "registerable": True,
        }
    ]


def test_shard_merges_idempotent_and_platform_subdir(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))
    src_dir = tmp_path / "scan_output"
    src_dir.mkdir()
    org_xls = src_dir / "Result_shanghai_org.xls"
    org_xls.write_text("xls-org")
    dedup_xls = src_dir / "Result_shanghai_dedup.xls"
    dedup_xls.write_text("xls-dedup")

    assert m.upload_scan_report(7, "host-9", str(org_xls)) is not None
    assert m.upload_scan_report(7, "host-9", str(dedup_xls), platform_subdir="mtk") is not None
    # 同文件重传 → 幂等合并不翻倍
    assert m.upload_scan_report(7, "host-9", str(org_xls)) is not None

    data = json.loads(_shard(nfs, 7, "host-9").read_text(encoding="utf-8"))
    keys = [a["file_key"] for a in data["artifacts"]]
    assert keys == ["host-9_Result_shanghai_org.xls", "mtk/host-9_Result_shanghai_dedup.xls"]
    by_key = {a["file_key"]: a for a in data["artifacts"]}
    assert by_key["host-9_Result_shanghai_org.xls"]["registerable"] is True
    mtk_entry = by_key["mtk/host-9_Result_shanghai_dedup.xls"]
    assert mtk_entry["platform"] == "mtk"
    # dedup 变体名不匹配 legacy 谓词 → registerable False（分片注册面 ≡ legacy）
    assert mtk_entry["registerable"] is False


def test_shard_write_failure_raises_not_silent(tmp_path):
    """#2474: 登记失败必须 raise（ScanRunner 不接返回值，吞 None = 静默半交付）。"""
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))
    src_dir = tmp_path / "scan_output"
    src_dir.mkdir()
    org_xls = src_dir / "Result_shanghai_org.xls"
    org_xls.write_text("xls")

    # tmp 兄弟路径被目录占用 → 分片重写必失败
    tmp_blocker = nfs / "_meta" / "42" / "host-1.json.tmp"
    tmp_blocker.parent.mkdir(parents=True)
    tmp_blocker.mkdir()

    with pytest.raises(OSError):
        m.upload_scan_report(42, "host-1", str(org_xls))
    # copy 本身已成功（半交付形态存在）——raise 让它沿 scan_now 暴露为步失败
    assert (nfs / "dedup" / "42" / "host-1_Result_shanghai_org.xls").exists()


def test_shard_corrupt_existing_is_rebuilt(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))
    shard = _shard(nfs, 3, "host-x")
    shard.parent.mkdir(parents=True)
    shard.write_text("{not json", encoding="utf-8")

    src_dir = tmp_path / "scan_output"
    src_dir.mkdir()
    org_xls = src_dir / "Result_a_org.xls"
    org_xls.write_text("xls")
    assert m.upload_scan_report(3, "host-x", str(org_xls)) is not None

    data = json.loads(shard.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert [a["file_key"] for a in data["artifacts"]] == ["host-x_Result_a_org.xls"]
