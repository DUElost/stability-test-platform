"""flash_firmware v1.2.0 的指纹路由 / manifest / 版本比对与核验。

单测为主（路由解析、precheck、verify、参数链），外加两个 main() 接线冒烟：
  - 同版本 → skipped 收场（不碰 flash_tool / 锁）
  - flash 成功但核验失败 → success=false
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.2.0"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v120", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


@pytest.fixture
def fw_root(tmp_path: Path) -> Path:
    """MLD 族固件根：latest.json 指针 + 一个版本目录（含 manifest）。"""
    fam = tmp_path / "MLD"
    ver = fam / "8.0.1.100"
    ver.mkdir(parents=True)
    (ver / "scatter.txt").write_text("scatter", encoding="utf-8")
    (ver / "da.bin").write_text("da", encoding="utf-8")
    (ver / "manifest.json").write_text(json.dumps({
        "family": "MLD",
        "version": "8.0.1.100",
        "scatter_file": "scatter.txt",
        "da_file": "da.bin",
        "models": ["MLD_LX2", "MLD_LX3"],
    }), encoding="utf-8")
    (fam / "latest.json").write_text(json.dumps({"version": "8.0.1.100"}),
                                     encoding="utf-8")
    return tmp_path


def _route(args=None, root=None, version=None):
    """调 _resolve_route，隔离 STP_FLASH_* env（root/version 显式传入）。"""
    import os
    overrides = {}
    if root is not None:
        overrides["STP_FLASH_FIRMWARE_ROOT"] = str(root)
    if version is not None:
        overrides["STP_FLASH_FIRMWARE_VERSION"] = str(version)
    saved = {}
    for key in ("STP_FLASH_FIRMWARE_ROOT", "STP_FLASH_FIRMWARE_VERSION"):
        saved[key] = os.environ.pop(key, None)
        if key in overrides:
            os.environ[key] = overrides[key]
    try:
        return ff._resolve_route(args or {})
    finally:
        for key, val in saved.items():
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# 指纹路由
# ---------------------------------------------------------------------------


class TestFingerprintRouting:
    def test_routes_via_latest_pointer(self, fw_root, monkeypatch):
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10:
                            "MLD_LX2" if prop == "ro.product.model" else None)
        route, err = _route(root=fw_root)
        assert err is None
        assert route["decided_by"] == "fingerprint"
        assert route["model"] == "MLD_LX2"
        assert route["family"] == "MLD"
        assert route["version"] == "8.0.1.100"
        assert route["da_file"].endswith("da.bin")
        assert route["scatter_file"].endswith("scatter.txt")

    def test_env_version_overrides_pointer(self, fw_root, monkeypatch):
        (fw_root / "MLD" / "8.0.1.200").mkdir()
        ver = fw_root / "MLD" / "8.0.1.200"
        (ver / "scatter.txt").write_text("s", encoding="utf-8")
        (ver / "da.bin").write_text("d", encoding="utf-8")
        (ver / "manifest.json").write_text(json.dumps({
            "family": "MLD", "version": "8.0.1.200",
            "scatter_file": "scatter.txt", "da_file": "da.bin",
        }), encoding="utf-8")
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "MLD_LX3")
        route, err = _route(version="8.0.1.200", root=fw_root)
        assert err is None
        assert route["version"] == "8.0.1.200"

    def test_unknown_model_fail_fast(self, fw_root, monkeypatch):
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "FOO_X1")
        route, err = _route(root=fw_root)
        assert route is None
        assert "no firmware family route" in err
        assert "MLD_LX2" in err  # 已知机型清单要列出来

    def test_adb_unreachable_fail_fast(self, fw_root, monkeypatch):
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: None)
        route, err = _route(root=fw_root)
        assert route is None
        assert "fingerprint routing failed" in err

    def test_manifest_models_mismatch(self, fw_root, monkeypatch):
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "ELA_LX2")
        # ELA 机型但 manifest models 只允许 MLD：用 family 参数显式路由到 MLD
        route, err = _route({"family": "MLD"}, root=fw_root)
        assert route is None
        assert "not in manifest models" in err

    def test_manifest_version_mismatch_with_dir(self, fw_root, monkeypatch):
        (fw_root / "MLD" / "manifest.json").unlink(missing_ok=True)
        ver = fw_root / "MLD" / "8.0.1.100"
        data = json.loads((ver / "manifest.json").read_text(encoding="utf-8"))
        data["version"] = "9.9.9.999"
        (ver / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "MLD_LX2")
        route, err = _route(root=fw_root)
        assert route is None
        assert "manifest version" in err

    def test_missing_pointer_and_env_fail_fast(self, fw_root, monkeypatch):
        (fw_root / "MLD" / "latest.json").unlink()
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "MLD_LX2")
        route, err = _route(root=fw_root)
        assert route is None
        assert "no target version" in err

    def test_nfs_root_default_firmware_root(self, fw_root, monkeypatch):
        # STP_FLASH_FIRMWARE_ROOT 不设时回落 {STP_NFS_ROOT}/firmware
        import os
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "MLD_LX2")
        monkeypatch.setenv("STP_NFS_ROOT", str(fw_root.parent))
        # fw_root 叫别的名字，造一个 {nfs}/firmware 结构
        (fw_root.parent / "firmware").symlink_to(fw_root, target_is_directory=True)
        route, err = _route()
        os.environ.pop("STP_NFS_ROOT", None)
        assert err is None
        assert route["firmware_dir"].startswith(str(fw_root.parent / "firmware"))


# ---------------------------------------------------------------------------
# 显式 firmware_dir（v1.1.0 回退兼容）
# ---------------------------------------------------------------------------


class TestExplicitDir:
    def test_legacy_params_still_work(self, fw_root):
        ver = fw_root / "MLD" / "8.0.1.100"
        route, err = _route({
            "firmware_dir": str(ver),
            "da_file": "da.bin",
            "scatter_file": "scatter.txt",
        })
        assert err is None
        assert route["decided_by"] == "params"
        assert route["version"] == "8.0.1.100"  # manifest 提供比对基准
        assert route["da_file"].endswith("da.bin")

    def test_manifest_fills_missing_da_scatter(self, fw_root):
        ver = fw_root / "MLD" / "8.0.1.100"
        route, err = _route({"firmware_dir": str(ver)})
        assert err is None
        assert route["da_file"].endswith("da.bin")
        assert route["scatter_file"].endswith("scatter.txt")

    def test_no_da_and_no_manifest_fails(self, tmp_path):
        (tmp_path / "bare").mkdir()
        route, err = _route({"firmware_dir": str(tmp_path / "bare")})
        assert route is None
        assert "da_file is required" in err

    def test_malformed_manifest_fails(self, fw_root):
        ver = fw_root / "MLD" / "8.0.1.100"
        (ver / "manifest.json").write_text("{not json", encoding="utf-8")
        route, err = _route({"firmware_dir": str(ver)})
        assert route is None
        assert "malformed" in err

    def test_manifest_without_scatter_fails(self, tmp_path):
        d = tmp_path / "f"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "version": "1", "da_file": "d.bin",
        }), encoding="utf-8")
        route, err = _route({"firmware_dir": str(d)})
        assert route is None
        assert "must define scatter_file and da_file" in err


# ---------------------------------------------------------------------------
# 刷前比对 / 刷后核验
# ---------------------------------------------------------------------------


class TestPrecheckVersion:
    def _route(self):
        return {"version": "8.0.1.100", "version_prop": "ro.build.version.incremental"}

    def test_same_version_skips(self, monkeypatch):
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "8.0.1.100")
        check = ff._precheck_version(self._route(), "SER", "adb")
        assert check["checked"] is True
        assert check["skip"] is True

    def test_different_version_flashes(self, monkeypatch):
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "7.0.0.1")
        check = ff._precheck_version(self._route(), "SER", "adb")
        assert check["checked"] is True
        assert not check.get("skip")

    def test_adb_unreachable_proceeds(self, monkeypatch):
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: None)
        check = ff._precheck_version(self._route(), "SER", "adb")
        assert check["checked"] is False
        assert "proceeding" in check["reason"]

    def test_no_target_version(self):
        check = ff._precheck_version({"version": None}, "SER", "adb")
        assert check["checked"] is False
        assert "no target version" in check["reason"]


class TestVerifyAfterFlash:
    def _route(self):
        return {"version": "8.0.1.100", "version_prop": "ro.build.version.incremental"}

    def test_match_passes(self, monkeypatch):
        monkeypatch.setattr(ff, "_wait_device_ready",
                            lambda serial, adb, timeout, on_tick: True)
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "8.0.1.100")
        ok, report = ff._verify_after_flash(self._route(), "SER", "adb", 5, lambda: None)
        assert ok is True
        assert report["current"] == "8.0.1.100"

    def test_mismatch_fails(self, monkeypatch):
        monkeypatch.setattr(ff, "_wait_device_ready",
                            lambda serial, adb, timeout, on_tick: True)
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "7.0.0.1")
        ok, report = ff._verify_after_flash(self._route(), "SER", "adb", 5, lambda: None)
        assert ok is False
        assert "mismatch" in report["error"]

    def test_device_never_ready_fails(self, monkeypatch):
        monkeypatch.setattr(ff, "_wait_device_ready",
                            lambda serial, adb, timeout, on_tick: False)
        ok, report = ff._verify_after_flash(self._route(), "SER", "adb", 5, lambda: None)
        assert ok is False
        assert "adb-ready" in report["error"]

    def test_no_target_version_skips_verify(self):
        ok, report = ff._verify_after_flash({"version": None}, "SER", "adb", 5, lambda: None)
        assert ok is True
        assert "no target version" in report["skipped_reason"]


# ---------------------------------------------------------------------------
# 参数链
# ---------------------------------------------------------------------------


class TestParamChain:
    def test_params_over_env(self, monkeypatch):
        monkeypatch.setenv("STP_FLASH_FIRMWARE_VERSION", "from-env")
        assert ff._param_or_env({"version": "from-params"},
                                "version", "STP_FLASH_FIRMWARE_VERSION", "dflt") \
            == "from-params"

    def test_env_over_default(self, monkeypatch):
        monkeypatch.setenv("STP_FLASH_FIRMWARE_VERSION", "from-env")
        assert ff._param_or_env({}, "version", "STP_FLASH_FIRMWARE_VERSION", "dflt") \
            == "from-env"

    def test_default_when_empty_string_param(self, monkeypatch):
        monkeypatch.delenv("STP_FLASH_FIRMWARE_VERSION", raising=False)
        assert ff._param_or_env({"version": ""}, "version",
                                "STP_FLASH_FIRMWARE_VERSION", "dflt") == "dflt"

    def test_as_bool(self):
        assert ff._as_bool("true", False) is True
        assert ff._as_bool("1", False) is True
        assert ff._as_bool("YES", False) is True
        assert ff._as_bool("0", True) is False
        assert ff._as_bool("", True) is True
        assert ff._as_bool(None, True) is True


# ---------------------------------------------------------------------------
# main() 接线冒烟
# ---------------------------------------------------------------------------


class TestMainWiring:
    def test_same_version_short_circuits_to_skipped(
            self, fw_root, monkeypatch, capsys):
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
        monkeypatch.setenv("STP_ADB_PATH", "adb")
        monkeypatch.setenv("STP_FLASH_FIRMWARE_ROOT", str(fw_root))
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10:
                            "MLD_LX2" if prop == "ro.product.model"
                            else "8.0.1.100")
        # 任何对 flash_tool / 锁的触碰都是 bug：skipped 路径必须短路
        def _boom(*a, **kw):
            raise AssertionError("skipped path must not touch flash tool")
        monkeypatch.setattr(ff, "_acquire_host_lock", _boom)
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress", _boom)

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["skipped"] is True
        assert payload["metrics"]["route"]["decided_by"] == "fingerprint"
        assert payload["metrics"]["version_check"]["skip"] is True

    def test_verify_failure_fails_the_step(self, fw_root, monkeypatch, capsys):
        ver = fw_root / "MLD" / "8.0.1.100"
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
        monkeypatch.setenv("STP_ADB_PATH", "adb")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(ver),
            "flash_tool_dir": str(fw_root),  # 不会真的用到
            "verify_wait_seconds": 5,
        }))
        # 刷前是旧版本（不触发 skip），刷后核验由下方 stub 判失败
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "7.0.0.1")
        monkeypatch.setattr(ff, "_acquire_host_lock", lambda on_wait_tick=None: None)
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
        monkeypatch.setattr(ff, "_pick_flash_tool_exe", lambda tool_dir: "/bin/true")
        monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                            lambda serial, target, adb_path, wait_seconds:
                            {"attempted": False})
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress",
                            lambda cmd, cwd, env, timeout, on_stage, on_percent:
                            ("All command exec done", 0))
        monkeypatch.setattr(ff, "_wait_device_back",
                            lambda serial, adb_path, timeout, on_tick: True)
        monkeypatch.setattr(ff, "_wait_device_ready",
                            lambda serial, adb, timeout, on_tick: True)
        # 刷后回读到旧版本 → 整步失败
        monkeypatch.setattr(ff, "_verify_after_flash",
                            lambda route, serial, adb, wait, on_tick:
                            (False, {"error": "post-flash version mismatch: expected 8.0.1.100, got 7.0.0.1"}))

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert "post-flash verify failed" in payload["error_message"]
        assert payload["metrics"]["route"]["decided_by"] == "params"

    def test_skip_disabled_flashes_even_when_current(self, fw_root, monkeypatch, capsys):
        """skip_if_current=false = 强制全量刷：版本相同也不许短路。"""
        ver = fw_root / "MLD" / "8.0.1.100"
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
        monkeypatch.setenv("STP_ADB_PATH", "adb")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(ver),
            "skip_if_current": False,
            "flash_tool_dir": str(fw_root),  # 存在即可，exe 由 stub 提供
        }))
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "8.0.1.100")
        monkeypatch.setattr(ff, "_acquire_host_lock", lambda on_wait_tick=None: None)
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
        monkeypatch.setattr(ff, "_pick_flash_tool_exe", lambda tool_dir: "/bin/true")
        monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                            lambda serial, target, adb_path, wait_seconds:
                            {"attempted": False})
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress",
                            lambda cmd, cwd, env, timeout, on_stage, on_percent:
                            ("All command exec done", 0))
        monkeypatch.setattr(ff, "_wait_device_back",
                            lambda serial, adb_path, timeout, on_tick: True)
        monkeypatch.setattr(ff, "_verify_after_flash",
                            lambda route, serial, adb, wait, on_tick:
                            (True, {"current": "8.0.1.100"}))

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["skipped"] is False
