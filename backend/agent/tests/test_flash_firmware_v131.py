"""flash_firmware v1.3.1 的路由表连字符机型。

v1.3.0 全部行为由 test_flash_firmware_v130.py 覆盖；这里只验证增量：
getprop 返回连字符型号（MLD-LX3 实测）时默认参数可路由，下划线键保留。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.1"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v131", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


@pytest.fixture
def fw_root(tmp_path: Path) -> Path:
    """MLD + ELA 双族固件根：latest.json 指针 + 版本目录（含 manifest）。"""
    for fam, ver, models in (
        ("MLD", "8.0.1.100", ["MLD_LX2", "MLD_LX3", "MLD-LX2", "MLD-LX3"]),
        ("ELA", "9.9.0.1", ["ELA_LX2", "ELA_LX3", "ELA-LX2", "ELA-LX3"]),
    ):
        ver_dir = tmp_path / fam / ver
        ver_dir.mkdir(parents=True)
        (ver_dir / "scatter.txt").write_text("s", encoding="utf-8")
        (ver_dir / "da.bin").write_text("d", encoding="utf-8")
        (ver_dir / "manifest.json").write_text(json.dumps({
            "family": fam, "version": ver,
            "scatter_file": "scatter.txt", "da_file": "da.bin",
            "models": models,
        }), encoding="utf-8")
        (tmp_path / fam / "latest.json").write_text(
            json.dumps({"version": ver}), encoding="utf-8")
    return tmp_path


def _route(monkeypatch, fw_root: Path, model: str):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
    monkeypatch.setattr(ff, "_adb_getprop",
                        lambda prop, adb, serial, timeout=10:
                        model if prop == "ro.product.model" else None)
    saved = {k: None for k in ("STP_FLASH_FIRMWARE_ROOT",
                               "STP_FLASH_FIRMWARE_VERSION")}
    for k in saved:
        saved[k] = None
    import os
    for k in saved:
        saved[k] = os.environ.pop(k, None)
    os.environ["STP_FLASH_FIRMWARE_ROOT"] = str(fw_root)
    try:
        return ff._resolve_route({})
    finally:
        del os.environ["STP_FLASH_FIRMWARE_ROOT"]
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


class TestHyphenRoutes:
    @pytest.mark.parametrize("model,family", [
        ("MLD-LX3", "MLD"), ("MLD-LX2", "MLD"),
        ("ELA-LX3", "ELA"), ("ELA-LX2", "ELA"),
        # 下划线键保留：不同批次固件两种拼写并存
        ("MLD_LX3", "MLD"), ("ELA_LX2", "ELA"),
    ])
    def test_routes_without_family_param(
            self, fw_root, monkeypatch, model, family):
        route, err = _route(monkeypatch, fw_root, model)
        assert err is None
        assert route["decided_by"] == "fingerprint"
        assert route["model"] == model
        assert route["family"] == family

    def test_unknown_model_fail_fast_lists_both_spellings(
            self, fw_root, monkeypatch):
        route, err = _route(monkeypatch, fw_root, "FOO-X1")
        assert route is None
        assert "no firmware family route" in err
        assert "MLD-LX3" in err and "MLD_LX3" in err

    def test_hyphen_model_passes_manifest_models_gate(
            self, fw_root, monkeypatch):
        """生产 manifest 曾只写连字符/下划线单拼写导致 fail-fast；
        白名单与路由表独立，双拼写任一命中即可。"""
        route, err = _route(monkeypatch, fw_root, "MLD-LX3")
        assert err is None  # fixture 的 MLD manifest 同时含两种拼写
        assert route["da_file"].endswith("da.bin")


class TestDefaultRoutingEndToEnd:
    def test_same_version_skips_via_default_route(
            self, fw_root, monkeypatch, capsys):
        """无 family 参数、连字符型号：main() 默认路由直达 skipped 收场。"""
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10:
                            "MLD-LX3" if prop == "ro.product.model"
                            else "8.0.1.100")
        # firmware_root 走参数而非 {STP_NFS_ROOT}/firmware 布局：
        # 后者需要在会话级目录建固定名 symlink，跨文件合跑会撞名
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
            {"firmware_root": str(fw_root)}))
        for key in ("STP_FLASH_FIRMWARE_VERSION",
                    "STP_FLASH_FIRMWARE_ROOT"):
            monkeypatch.delenv(key, raising=False)

        def _boom(*a, **kw):
            raise AssertionError("skipped path must not touch flash tool")

        monkeypatch.setattr(ff, "_acquire_host_lock", _boom)
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress", _boom)

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["skipped"] is True
        assert payload["metrics"]["route"]["decided_by"] == "fingerprint"
        assert payload["metrics"]["route"]["model"] == "MLD-LX3"
