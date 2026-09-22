"""ScanRunner × tool_cache 集成（ADR-0033 Phase B 第一切片，#3075 / C6）。

只测切接语义：显式传参 > 包面 > env 路径；包面任何失败必须原样保留 env 路径。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from backend.agent.scan_runner import ScanRunner
from backend.agent.unisoc_scan_runner import UnisocScanRunner

NAME = "Start-Log-Scan"
VERSION = "2026.09.22"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for key in (
        "STP_DEDUP_SCAN_PYTHON", "STP_DEDUP_SCAN_SCRIPT", "STP_DEDUP_SCAN_PACKAGE_REF",
        "STP_PACKAGES_ROOT", "STP_TOOLS_CACHE_ROOT", "STP_AEE_NFS_ROOT", "AGENT_INSTALL_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    ScanRunner._reset_for_tests()
    UnisocScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()
    UnisocScanRunner._reset_for_tests()


def _publish_pkg(tmp_path: Path, monkeypatch, *, corrupt: bool = False) -> Path:
    pkg_root = tmp_path / "packages"
    tar = pkg_root / NAME / f"{VERSION}.tar.gz"
    tar.parent.mkdir(parents=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar_f:
            for name in ("start_log_scan.py", "venv/bin/python"):
                data = b"print(1)"
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar_f.addfile(info, io.BytesIO(data))
    payload = buf.getvalue()
    sha = hashlib.sha256(payload).hexdigest()
    tar.write_bytes(b"tampered" if corrupt else payload)
    (pkg_root / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "tools": {NAME: {"versions": [{
            "version": VERSION, "package_sha256": sha,
            "artifact": f"packages/{NAME}/{VERSION}.tar.gz",
            "python": "venv/bin/python", "script": "start_log_scan.py", "retired": False,
        }]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STP_PACKAGES_ROOT", str(pkg_root))
    monkeypatch.setenv("STP_TOOLS_CACHE_ROOT", str(tmp_path / "cache"))
    return pkg_root


def _configure_from_env():
    ScanRunner.instance().configure(force=True)
    r = ScanRunner.instance()
    return r._scan_tool_python, r._scan_tool_script


class TestScanRunnerPackageSwitch:
    def test_no_ref_keeps_env_paths(self, monkeypatch):
        monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/site/python")
        monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py")
        py, sc = _configure_from_env()
        assert (py, sc) == ("/site/python", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py")

    def test_ref_switches_both_paths_to_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/site/python")
        monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py")
        monkeypatch.setenv("STP_DEDUP_SCAN_PACKAGE_REF", f"{NAME}/{VERSION}")
        _publish_pkg(tmp_path, monkeypatch)
        py, sc = _configure_from_env()
        cache = str(tmp_path / "cache" / NAME / VERSION)
        assert py == f"{cache}/venv/bin/python"
        assert sc == f"{cache}/start_log_scan.py"

    def test_sha_mismatch_falls_back_to_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/site/python")
        monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py")
        monkeypatch.setenv("STP_DEDUP_SCAN_PACKAGE_REF", f"{NAME}/{VERSION}")
        _publish_pkg(tmp_path, monkeypatch, corrupt=True)
        py, sc = _configure_from_env()
        assert (py, sc) == ("/site/python", "/mnt/stp-aee/tools/Start-Log-Scan/start_log_scan.py")

    def test_explicit_args_beat_package(self, tmp_path, monkeypatch):
        """受控/测试调用显式传参时包面不得改写（优先级：显式 > 包 > env）。"""
        monkeypatch.setenv("STP_DEDUP_SCAN_PACKAGE_REF", f"{NAME}/{VERSION}")
        _publish_pkg(tmp_path, monkeypatch)
        ScanRunner.instance().configure(
            scan_tool_python="/explicit/python", scan_tool_script="/explicit/run.py", force=True
        )
        r = ScanRunner.instance()
        assert r._scan_tool_python == "/explicit/python"
        assert r._scan_tool_script == "/explicit/run.py"
