"""flash_firmware v1.3.6：族指针 per-model 版本映射。

v1.3.5 及以前由既有用例覆盖；这里验证增量：
latest.json 支持 {"versions": {"MLD_LX2": ..., "MLD_LX3": ...}} 机型级取版本，
旧 {"version": ...} 单键兼容，键匹配支持双拼写（下划线/连字符互转）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.6"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v136", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


class TestReadLatestVersion:
    @pytest.mark.parametrize("model,versions,expected", [
        ("MLD_LX2", {"MLD_LX2": "V62", "MLD_LX3": "V71"}, "V62"),
        ("MLD-LX2", {"MLD_LX2": "V62", "MLD_LX3": "V71"}, "V62"),  # 连字符查下划线键
        ("MLD_LX2", {"MLD-LX2": "V62"}, "V62"),                     # 下划线查连字符键
    ])
    def test_versions_map_lookup(self, model, versions, expected):
        assert ff._read_latest_version(
            {"versions": versions}, model) == expected

    def test_legacy_single_version_fallback(self):
        assert ff._read_latest_version(
            {"version": "V71"}, "MLD_LX3") == "V71"

    def test_model_missing_from_versions_returns_none(self):
        assert ff._read_latest_version(
            {"versions": {"MLD_LX3": "V71"}}, "MLD_LX2") is None

    def test_empty_and_malformed(self):
        assert ff._read_latest_version(None, "MLD_LX2") is None
        assert ff._read_latest_version({}, "MLD_LX2") is None
        assert ff._read_latest_version(
            {"versions": []}, "MLD_LX2") is None


def _make_tree(tmp_path, latest: dict) -> Path:
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True)
    (fw / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
    for ver in ("V62", "V71"):
        vd = fw / ver
        vd.mkdir()
        (vd / "scatter.txt").write_text("s", encoding="utf-8")
        (vd / "da.bin").write_text("d", encoding="utf-8")
        (vd / "manifest.json").write_text(json.dumps({
            "family": "MLD", "version": ver,
            "scatter_file": "scatter.txt", "da_file": "da.bin",
            "models": ["MLD_LX2", "MLD_LX3", "MLD-LX2", "MLD-LX3"],
        }), encoding="utf-8")
    return fw


class TestFingerprintRoutingByModel:
    def test_lx2_and_lx3_route_to_own_versions(self, tmp_path, monkeypatch):
        _make_tree(tmp_path, {"versions": {
            "MLD_LX2": "V62", "MLD_LX3": "V71"}})
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10:
                            "MLD_LX2" if prop == "ro.product.model" else None)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "S1")
        monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
        route, err = ff._resolve_by_fingerprint({}, "S1", "adb")
        assert err is None and route["model"] == "MLD_LX2"
        assert route["version"] == "V62"

        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10:
                            "MLD-LX3" if prop == "ro.product.model" else None)
        route2, err2 = ff._resolve_by_fingerprint({}, "S1", "adb")
        assert err2 is None and route2["version"] == "V71"

    def test_model_missing_versions_key_fails_with_hint(
            self, tmp_path, monkeypatch):
        _make_tree(tmp_path, {"versions": {"MLD_LX3": "V71"}})
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10:
                            "MLD_LX2" if prop == "ro.product.model" else None)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "S1")
        monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
        route, err = ff._resolve_by_fingerprint({}, "S1", "adb")
        assert route is None
        assert "no target version for model MLD_LX2" in err
        assert '"versions"' in err

    def test_legacy_pointer_still_routes(self, tmp_path, monkeypatch):
        _make_tree(tmp_path, {"version": "V62"})
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10:
                            "MLD_LX2" if prop == "ro.product.model" else None)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "S1")
        monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
        route, err = ff._resolve_by_fingerprint({}, "S1", "adb")
        assert err is None and route["version"] == "V62"
