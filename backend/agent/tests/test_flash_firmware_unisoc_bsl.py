"""Unit tests for backend/agent/scripts/flash_firmware_unisoc_bsl/."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import struct
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "flash_firmware_unisoc_bsl"
    / "flash_firmware_unisoc_bsl.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("flash_firmware_unisoc_bsl_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _identity_firmware_cache(monkeypatch: pytest.MonkeyPatch, request):
    """既有 BSL 用例默认恒等 ensure；U4 反例可标记 ``no_identity_fw_cache`` 关闭。"""
    if request.node.get_closest_marker("no_identity_fw_cache"):
        return
    from backend.agent.tests.firmware_cache_testkit import identity_ensure_firmware

    monkeypatch.setattr(
        "backend.agent.firmware_cache.ensure_firmware",
        identity_ensure_firmware,
    )
    try:
        import agent.firmware_cache as _afc  # type: ignore

        monkeypatch.setattr(_afc, "ensure_firmware", identity_ensure_firmware)
    except Exception:
        pass


def _make_sparse_image(path: Path, chunks: list[tuple[int, int, bytes]]) -> None:
    """Write a minimal Android sparse image with (chunk_type, chunk_blocks, payload)."""
    blk_sz = 4096
    total_blks = sum(c[1] for c in chunks)
    hdr = struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, blk_sz, total_blks, len(chunks), 0)
    with open(path, "wb") as f:
        f.write(hdr)
        for ctype, cblks, payload in chunks:
            chdr = struct.pack("<HHII", ctype, 0, cblks, 12 + len(payload))
            f.write(chdr)
            f.write(payload)


def _p532aa_plan() -> dict:
    """Fixture matching #4006 §3.3 verified facts for P532AA (23 writable partitions excl. super/userdata)."""
    writable_parts = [
        ("UBOOT", "uboot_a", "edk2-sign.bin"),
        ("SML", "sml_a", "sml-sign.bin"),
        ("TOS", "trustos_a", "tos-sign.bin"),
        ("TEECFG", "teecfg_a", "teecfg-sign.bin"),
        ("Modem_NR", "nr_modem_a", "SC9600_QogirN5_PS_modem.bin"),
        ("PHY_NR", "nr_phy_a", "SC9600_QogirN5_PHY_modem.bin"),
        ("AGDSP_LTE", "l_agdsp_a", "ums9632_n5_adsp.bin"),
        ("CH_SYS", "ch_sys_a", "QogirN5_CH.bin"),
        ("PM_SYS", "pm_sys_a", "QogirN5_SP.bin"),
        ("BootLogo", "logo_a", "logo.bin"),
        ("Fastboot_Logo", "fbootlogo_a", "logo.bin"),
        ("BOOT", "boot_a", "boot.img"),
        ("INIT_BOOT", "init_boot_a", "init_boot.img"),
        ("VENDOR_BOOT", "vendor_boot_a", "vendor_boot.img"),
        ("DTBO", "dtbo_a", "dtbo.img"),
        ("VBMETA_SYS", "vbmeta_system_a", "vbmeta_system.img"),
        ("VBMETA_EXT", "vbmeta_system_ext_a", "vbmeta_system_ext.img"),
        ("VBMETA_VEN", "vbmeta_vendor_a", "vbmeta_vendor.img"),
        ("VBMETA_PRD", "vbmeta_product_a", "vbmeta_product.img"),
        ("VBMETA_ODM", "vbmeta_odm_a", "vbmeta_odm.img"),
        ("VBMETA", "vbmeta_a", "vbmeta-sign.img"),
        ("Cache", "cache", "cache.img"),
        ("BlackBox", "blackbox", "blackbox.img"),
    ]
    entries = [
        {"id": "FDL", "file": "fdl1-sign.bin", "sparse": False, "partitions": [], "flush_type": "FDL"},
        {"id": "FDL2", "file": "edk2-fdl2-sign.bin", "sparse": False, "partitions": [], "flush_type": "NAND_FDL"},
        {"id": "SPLLoaderUFS", "file": "u-boot-spl-16k-ufs-sign.bin", "sparse": False, "partitions": ["splloader"], "flush_type": "UBOOT_LOADER2"},
        {"id": "NV_NR", "file": "nr_nv.bin", "sparse": False, "partitions": ["nr_downloadnv_a"], "flush_type": "NV_COMM_SLOT", "preserve": True},
        {"id": "Modem_NR_DELTANV", "file": "nr_deltanv.bin", "sparse": False, "partitions": ["nr_deltanv_a"], "flush_type": "CODE2", "preserve": True},
        {"id": "ProdNV", "file": "prodnv.img", "sparse": False, "partitions": ["prodnv"], "flush_type": "CODE2", "preserve": True},
        {"id": "Persist", "file": "persist.img", "sparse": False, "partitions": ["persist"], "flush_type": "YAFFS_IMG2", "preserve": True},
        {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "flush_type": "YAFFS_IMG2", "preserve": True},
        {"id": "Super", "file": "super.img", "sparse": True, "raw": "raw/super.img", "partitions": ["super"], "flush_type": "YAFFS_IMG2"},
    ]
    for fid, part, fname in writable_parts:
        entry: dict = {"id": fid, "file": fname, "sparse": False, "partitions": [part], "flush_type": "CODE2"}
        if part in ("cache", "blackbox"):
            entry["sparse"] = True
            entry["raw"] = f"raw/{fname}"
        entries.append(entry)
    return {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65008000"},
            "fdl2": {"file": "edk2-fdl2-sign.bin", "base": "0xBDFFFE00"},
        },
        "erases": ["splloader", "uboot_log", "sd_klog", "misc", "sysdumpdb", "metadata"],
        "entries": entries,
    }


def _p525aa_plan() -> dict:
    """Fixture matching #4006 §3.3 verified facts for P525AA / MLY-LX2 (23 writable partitions excl. super/userdata)."""
    writable_parts = [
        ("UBOOTLoader", "uboot_a", "lk-sign.bin"),
        ("SML", "sml_a", "sml-sign.bin"),
        ("TOS", "trustos_a", "tos-sign.bin"),
        ("TEECFG", "teecfg_a", "teecfg-sign.bin"),
        ("Modem_LTE", "l_modem_a", "SC9863A_modem.bin"),
        ("DSP_LTE", "l_ldsp_a", "SC9863A_ldsp.bin"),
        ("GDSP_LTE", "l_gdsp_a", "SC9863A_gdsp.bin"),
        ("AGDSP_LTE", "l_agdsp_a", "SC9863A_agdsp.bin"),
        ("PM_SYS", "pm_sys_a", "pm_sys.bin"),
        ("BootLogo", "logo", "logo.bin"),
        ("Fastboot_Logo", "fbootlogo", "unisoc_HD_720_1600.bmp"),
        ("BOOT", "boot_a", "boot.img"),
        ("INIT_BOOT", "init_boot_a", "init_boot.img"),
        ("VENDOR_BOOT", "vendor_boot_a", "vendor_boot.img"),
        ("DTBO", "dtbo_a", "dtbo.img"),
        ("VBMETA_SYS", "vbmeta_system_a", "vbmeta_system.img"),
        ("VBMETA_EXT", "vbmeta_system_ext_a", "vbmeta_system_ext.img"),
        ("VBMETA_VEN", "vbmeta_vendor_a", "vbmeta_vendor.img"),
        ("VBMETA_PRD", "vbmeta_product_a", "vbmeta_product.img"),
        ("VBMETA_ODM", "vbmeta_odm_a", "vbmeta_odm.img"),
        ("VBMETA", "vbmeta_a", "vbmeta-sign.img"),
        ("Cache", "cache", "cache.img"),
        ("BlackBox", "blackbox", "blackbox.img"),
    ]
    entries = [
        {"id": "FDL", "file": "fdl1-sign.bin", "sparse": False, "partitions": [], "flush_type": "FDL"},
        {"id": "FDL2", "file": "lk-fdl2-sign.bin", "sparse": False, "partitions": [], "flush_type": "NAND_FDL"},
        {"id": "SPLLoaderUFS", "file": "u-boot-spl-16k-ufs-sign.bin", "sparse": False, "partitions": ["splloader"], "flush_type": "UBOOT_LOADER2"},
        {"id": "NV_LTE", "file": "lte_nv.bin", "sparse": False, "partitions": ["l_fixnv1_a"], "flush_type": "NV_COMM_SLOT", "preserve": True},
        {"id": "Modem_LTE_DELTANV", "file": "lte_deltanv.bin", "sparse": False, "partitions": ["l_deltanv_a"], "flush_type": "CODE2", "preserve": True},
        {"id": "ProdNV", "file": "prodnv.img", "sparse": False, "partitions": ["prodnv"], "flush_type": "CODE2", "preserve": True},
        {"id": "Persist", "file": "persist.img", "sparse": False, "partitions": ["persist"], "flush_type": "YAFFS_IMG2", "preserve": True},
        {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "flush_type": "YAFFS_IMG2", "preserve": True},
        {"id": "Super", "file": "super.img", "sparse": True, "raw": "raw/super.img", "partitions": ["super"], "flush_type": "YAFFS_IMG2"},
    ]
    for fid, part, fname in writable_parts:
        entry: dict = {"id": fid, "file": fname, "sparse": False, "partitions": [part], "flush_type": "CODE2"}
        if part in ("cache", "blackbox"):
            entry["sparse"] = True
            entry["raw"] = f"raw/{fname}"
        entries.append(entry)
    return {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["splloader", "uboot_log", "l_runtimenv1", "misc", "sysdumpdb", "metadata"],
        "entries": entries,
    }


def _create_firmware_dir(
    root: Path,
    family: str = "P525AA",
    version: str = "v36",
    *,
    models: Optional[List[str]] = None,
    platform: str = "unisoc",
    version_prop: str = "ro.build.sw_internal_version",
    manifest_version: Optional[str] = None,
    plan: Optional[dict] = None,
    write_files: bool = True,
) -> Path:
    ver_dir = root / family / version
    images = ver_dir / "images"
    (images / "raw").mkdir(parents=True, exist_ok=True)

    manifest = {
        "platform": platform,
        "family": family,
        "version": version if manifest_version is None else manifest_version,
        "version_prop": version_prop,
        "models": ["MLY-LX2", "MLY-LX2-CA"] if models is None else models,
    }
    (ver_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    plan_data = plan if plan is not None else {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["splloader", "uboot_log", "l_runtimenv1", "misc", "sysdumpdb", "metadata"],
        "entries": [
            {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "flush_type": "YAFFS_IMG2", "preserve": True},
            {"id": "BOOT", "file": "boot.img", "sparse": False, "partitions": ["boot_a"], "flush_type": "CODE2"},
            {"id": "Super", "file": "super.img", "sparse": True, "raw": "raw/super.img", "partitions": ["super"], "flush_type": "YAFFS_IMG2"},
        ],
    }
    (images / "plan.json").write_text(json.dumps(plan_data), encoding="utf-8")

    if write_files:
        fdl_cfg = plan_data.get("fdl") if isinstance(plan_data.get("fdl"), dict) else {}
        for fdl_key in ("fdl1", "fdl2"):
            fname = ((fdl_cfg.get(fdl_key) or {}).get("file") or "").strip()
            if fname:
                p = images / fname
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(f"data-{fname}".encode())
        for item in plan_data.get("entries") or []:
            fname = (item.get("file") or "").strip()
            if fname:
                p = images / fname
                p.parent.mkdir(parents=True, exist_ok=True)
                if fname == "super.img" and item.get("sparse"):
                    _make_sparse_image(p, [(0xCAC2, 16, b"\x11\x11\x11\x11"), (0xCAC3, 64, b"")])
                else:
                    p.write_bytes(f"data-{fname}".encode())
            raw_rel = (item.get("raw") or "").strip()
            if raw_rel:
                rp = images / raw_rel
                rp.parent.mkdir(parents=True, exist_ok=True)
                rp.write_bytes(b"R" * (16 * 4096))
        # B10 U4：L1 缓存消费预裁剪 super（images/bsl/super_trimmed.img）
        has_super = any(
            "super" in (item.get("partitions") or [])
            for item in (plan_data.get("entries") or [])
            if isinstance(item, dict)
        )
        if has_super:
            bsl_dir = images / "bsl"
            bsl_dir.mkdir(parents=True, exist_ok=True)
            (bsl_dir / "super_trimmed.img").write_bytes(b"T" * 4096)
    return ver_dir


def _make_executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make_fake_sysfs(
    tmp_path: Path,
    devices: Optional[List[tuple]] = None,
) -> Path:
    base = tmp_path / "sys_usb"
    base.mkdir(parents=True, exist_ok=True)
    entries = [("1-5.2.3", "18d1", "4ee8", "SN1")] if devices is None else devices
    for item in entries:
        port, vid, pid = item[0], item[1], item[2]
        serial = item[3] if len(item) > 3 else None
        d = base / port
        d.mkdir(parents=True, exist_ok=True)
        (d / "idVendor").write_text(vid + "\n", encoding="utf-8")
        (d / "idProduct").write_text(pid + "\n", encoding="utf-8")
        if serial is not None:
            (d / "serial").write_text(serial + "\n", encoding="utf-8")
    return base


def _install_fake_adb(
    monkeypatch: pytest.MonkeyPatch,
    mod,
    *,
    props: Optional[Dict[str, str]] = None,
    post_props: Optional[Dict[str, str]] = None,
    battery_rc: int = 0,
    battery_out: str = "  level: 85\n",
    reboot_rc: int = 0,
    pre_boot_id: str = "boot-uuid-1",
    post_boot_id: str = "boot-uuid-2",
    boot_id_rc: int = 0,
    post_boot_id_rc: Optional[int] = None,
    pre_uptime: str = "120.5 200.0",
    post_uptime: str = "12.3 20.0",
    uptime_rc: int = 0,
    uptime_latency_seconds: float = 0.0,
    setprop_rc: int = 0,
    nonce_read_rc: int = 0,
    clock: Optional[dict] = None,
    boot_advance_seconds: float = 30.0,
    adb_calls: Optional[List[List[str]]] = None,
):
    state = {"rebooted": False, "props": dict(props or {}), "nonce": ""}
    post = dict(post_props or props or {})

    def fake_run_adb(adb_bin: str, serial: str, args: List[str], timeout: float = 30):
        if adb_calls is not None:
            adb_calls.append(list(args))
        if args == ["reboot", "autodloader"]:
            if reboot_rc == 0:
                state["rebooted"] = True
                state["nonce"] = ""
            return reboot_rc, "", ("autodloader failed" if reboot_rc != 0 else "")
        if args == ["root"]:
            return 0, "restarting adbd as root", ""
        if args == ["shell", "id"]:
            return 0, "uid=0(root) gid=0(root)", ""
        if args == ["shell", "dumpsys", "battery"]:
            return battery_rc, battery_out, ""
        if args[:2] == ["shell", "getprop"]:
            prop = args[2]
            if prop == "sys.boot_completed":
                if clock is not None:
                    clock["now"] += boot_advance_seconds
                return 0, "1", ""
            if prop == "debug.stp_bsl_nonce":
                if nonce_read_rc != 0:
                    return nonce_read_rc, "", "getprop failed"
                return 0, state["nonce"], ""
            src = post if state["rebooted"] else state["props"]
            return 0, src.get(prop, ""), ""
        if len(args) == 4 and args[:2] == ["shell", "setprop"] and args[2] == "debug.stp_bsl_nonce":
            if setprop_rc != 0:
                return setprop_rc, "", "setprop failed"
            state["nonce"] = args[3]
            return 0, "", ""
        if args == ["shell", "cat", "/proc/sys/kernel/random/boot_id"]:
            rc = boot_id_rc if post_boot_id_rc is None else (boot_id_rc if not state["rebooted"] else post_boot_id_rc)
            if rc != 0:
                return rc, "", "boot_id unreadable"
            return 0, (post_boot_id if state["rebooted"] else pre_boot_id), ""
        if args == ["shell", "cat", "/proc/uptime"]:
            # Simulate the adb round-trip latency so the post-read window [T0, T1] has width.
            if clock is not None and uptime_latency_seconds > 0:
                clock["now"] += uptime_latency_seconds
            if uptime_rc != 0:
                return uptime_rc, "", "uptime unreadable"
            return 0, (post_uptime if state["rebooted"] else pre_uptime), ""
        return 0, "", ""

    monkeypatch.setattr(mod, "run_adb", fake_run_adb)
    return state


def test_build_spd_dump_args_rejects_without_plan_json(tmp_path: Path) -> None:
    """Rewritten per J1 & #4006 §4 #21: missing plan.json must reject instead of using legacy P532AA defaults."""
    mod = _load_module()
    with pytest.raises(RuntimeError, match="plan\\.json"):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(tmp_path), str(tmp_path / "super_trimmed.img"))


def test_resolve_firmware_images_dir_accepts_version_or_images_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Updated per J1/J2 & #4006 §4 #21: requires manifest.json + plan.json and validates device identity."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path, family="P532AA", version="v117", models=["p532a_64only"], plan=_p532aa_plan())
    images = ver_dir / "images"
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "p532a_64only",
            "ro.product.device": "p532a",
            "ro.product.name": "p532a_64only",
        },
    )

    assert mod.resolve_firmware_images_dir({"firmware_dir": str(images)}, "adb", "SN1") == str(images)
    assert mod.resolve_firmware_images_dir({"firmware_dir": str(ver_dir)}, "adb", "SN1") == str(images)


def _patch_advancing_clock(monkeypatch: pytest.MonkeyPatch, mod, *, start: float = 1_000_000.0):
    state = {"now": start}

    def fake_time() -> float:
        return state["now"]

    def fake_monotonic() -> float:
        # J8/J9 (#4006 v1.2) read the monotonic clock for stage duration and R/T0/T1;
        # the fake clock advances it together with time.time().
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["now"] += float(seconds)

    monkeypatch.setattr(mod, "time", SimpleNamespace(time=fake_time, monotonic=fake_monotonic, sleep=fake_sleep))
    return state


def test_acquire_host_lock_emits_lock_wait_and_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    lock_file = tmp_path / "test.lock"
    progress = mod.Progress()
    stamps: list[dict] = []
    monkeypatch.setattr(progress, "stamp", lambda **kw: stamps.append(kw))
    _patch_advancing_clock(monkeypatch, mod)

    calls = {"n": 0}
    real_flock = mod.fcntl.flock

    def fake_flock(fd: int, op: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise BlockingIOError("busy")
        real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", fake_flock)
    fd = mod.acquire_host_lock(progress, timeout_seconds=30, lock_path=str(lock_file))
    try:
        assert [s["stage"] for s in stamps] == ["lock-wait", "lock-held"]
    finally:
        mod.fcntl.flock(fd, mod.fcntl.LOCK_UN)
        os.close(fd)


def test_main_missing_serial_outputs_contract_json() -> None:
    env = dict(os.environ)
    env.pop("STP_DEVICE_SERIAL", None)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout.strip())
    assert data["success"] is False
    assert "STP_DEVICE_SERIAL" in data["error_message"]


def test_run_ensures_firmware_before_host_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B10 U4：ensure_firmware（锁外）必须先于 acquire_host_lock。"""
    mod = _load_module()
    order: list[str] = []
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path)

    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "100",
            "ro.build.sw_internal_version": "old",
        },
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "100",
            "ro.build.sw_internal_version": "v36",
        },
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/local/bin/spd_dump")

    lock_file = tmp_path / "host.lock"
    from backend.agent.tests.firmware_cache_testkit import identity_ensure_firmware
    from contextlib import contextmanager

    @contextmanager
    def tracking_ensure(source_dir, profile, **kwargs):
        order.append(("ensure_firmware", profile, str(source_dir)))
        with identity_ensure_firmware(source_dir, profile, **kwargs) as fw:
            yield fw

    def fake_acquire(progress, timeout_seconds=7200, lock_path=None):
        order.append("acquire_host_lock")
        return os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

    clock = _patch_advancing_clock(monkeypatch, mod)

    def fake_spd_dump(*_a, **_kw):
        adb_state["rebooted"] = True
        adb_state["nonce"] = ""
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "_load_firmware_cache_api", lambda: (tracking_ensure, Exception, None))
    monkeypatch.setattr(mod, "acquire_host_lock", fake_acquire)
    monkeypatch.setattr(mod, "run_spd_dump", fake_spd_dump)
    monkeypatch.setattr(mod, "wait_boot_completed", lambda *_a: 1.0)

    res = mod.run({"firmware_dir": str(ver_dir), "verify_root": False}, "SN1", mod.Progress(), [])
    assert res["metrics"]["duration_seconds"] >= 0
    assert res["metrics"]["firmware_cache"]["hit"] is True
    assert order[0][0] == "ensure_firmware"
    assert order[0][1] == "unisoc_bsl"
    assert order[1] == "acquire_host_lock"
    assert "prepare_local_cache" not in order
    assert not hasattr(mod, "prepare_local_cache")

def test_is_protected_partition_blocks_lte_and_nr_nv() -> None:
    mod = _load_module()
    for protected in (
        "prodnv",
        "calinv",
        "miscdata",
        "persist",
        "splloader",
        "nr_factorynv",
        "nr_runtimenv1",
        "nr_downloadnv_a",
        "nr_deltanv_a",
        "l_fixnv1_a",
        "l_fixnv2_a",
        "l_fixnv1_b",
        "l_runtimenv1",
        "l_runtimenv2",
        "l_deltanv_a",
        "l_deltanv_b",
    ):
        assert mod.is_protected_partition(protected) is True, protected

    for writable in ("boot_a", "userdata", "super", "logo", "fbootlogo", "uboot_log", "metadata"):
        assert mod.is_protected_partition(writable) is False, writable


def test_build_spd_dump_args_reads_p525aa_plan_json(tmp_path: Path) -> None:
    mod = _load_module()
    fw_dir = tmp_path / "P525AA" / "v36" / "images"
    fw_dir.mkdir(parents=True)
    plan = {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["splloader", "uboot_log", "l_runtimenv1", "misc", "sysdumpdb", "metadata"],
        "entries": [
            {"id": "FDL", "file": "fdl1-sign.bin", "sparse": False, "partitions": [], "flush_type": "FDL"},
            {"id": "FDL2", "file": "lk-fdl2-sign.bin", "sparse": False, "partitions": [], "flush_type": "NAND_FDL"},
            {"id": "SPLLoaderUFS", "file": "u-boot-spl-16k-ufs-sign.bin", "sparse": False, "partitions": ["splloader"], "flush_type": "UBOOT_LOADER2"},
            {"id": "NV_LTE", "file": "lte_nv.bin", "sparse": False, "partitions": ["l_fixnv1_a"], "flush_type": "NV_COMM_SLOT", "preserve": True},
            {"id": "Modem_LTE_DELTANV", "file": "lte_deltanv.bin", "sparse": False, "partitions": ["l_deltanv_a"], "flush_type": "CODE2", "preserve": True},
            {"id": "ProdNV", "file": "prodnv.img", "sparse": False, "partitions": ["prodnv"], "flush_type": "CODE2", "preserve": True},
            {"id": "Persist", "file": "persist.img", "sparse": False, "partitions": ["persist"], "flush_type": "YAFFS_IMG2", "preserve": True},
            {"id": "PhaseCheck", "file": "miscdata.bin", "sparse": False, "partitions": ["miscdata"], "flush_type": "CODE2"},
            {"id": "Super", "file": "super.img", "sparse": True, "raw": "raw/super.img", "partitions": ["super"], "flush_type": "YAFFS_IMG2"},
            {"id": "Cache", "file": "cache.img", "sparse": True, "raw": "raw/cache.img", "partitions": ["cache"], "flush_type": "YAFFS_IMG2"},
            {"id": "BlackBox", "file": "blackbox.img", "sparse": True, "raw": "raw/blackbox.img", "partitions": ["blackbox"], "flush_type": "YAFFS_IMG2"},
            {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "flush_type": "YAFFS_IMG2", "preserve": True},
            {"id": "UBOOTLoader", "file": "lk-sign.bin", "sparse": False, "partitions": ["uboot_a"], "flush_type": "UBOOT_LOADER2"},
            {"id": "BootLogo", "file": "logo.bin", "sparse": False, "partitions": ["logo"], "flush_type": "CODE2"},
            {"id": "Fastboot_Logo", "file": "unisoc_HD_720_1600.bmp", "sparse": False, "partitions": ["fbootlogo"], "flush_type": "CODE2"},
            {"id": "BOOT", "file": "boot.img", "sparse": False, "partitions": ["boot_a"], "flush_type": "CODE2"},
        ],
    }
    (fw_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    super_trimmed = tmp_path / "super_trimmed.img"
    cmd = mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(fw_dir), str(super_trimmed))

    assert "0x65000800" in cmd
    assert "0x9EFFFE00" in cmd
    assert str(fw_dir / "lk-fdl2-sign.bin") in cmd
    assert str(fw_dir / "unisoc_HD_720_1600.bmp") in cmd

    erased = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "erase_part"]
    assert erased == ["uboot_log", "misc", "sysdumpdb", "metadata"]

    written = [(cmd[i + 1], cmd[i + 2]) for i, tok in enumerate(cmd) if tok == "write_part"]
    written_parts = [p for p, _ in written]
    assert written_parts[0] == "userdata"
    assert written_parts[-3:] == ["cache", "blackbox", "super"]
    assert ("logo", str(fw_dir / "logo.bin")) in written
    assert ("fbootlogo", str(fw_dir / "unisoc_HD_720_1600.bmp")) in written
    assert ("uboot_a", str(fw_dir / "lk-sign.bin")) in written
    assert ("super", str(super_trimmed)) in written

    assert "keep_charge" in cmd
    assert cmd[-1] == "reset"
    for protected in ("splloader", "l_fixnv1_a", "l_runtimenv1", "l_deltanv_a", "prodnv", "persist", "miscdata"):
        assert protected not in written_parts



# ── B9-U2 Finite Acceptance Checklist (#4006 §4) ──────────────────────────


def test_u2_01_rejects_plan_without_fdl_key(tmp_path: Path) -> None:
    mod = _load_module()
    plan = _p525aa_plan()
    del plan["fdl"]
    ver_dir = _create_firmware_dir(tmp_path, plan=plan)
    with pytest.raises(RuntimeError, match="fdl"):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), str(tmp_path / "s.img"))


def test_u2_02_rejects_missing_plan_json(tmp_path: Path) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path)
    (ver_dir / "images" / "plan.json").unlink()
    with pytest.raises(RuntimeError, match="plan\\.json"):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), str(tmp_path / "s.img"))


def test_u2_03_rejects_empty_entries_plan(tmp_path: Path) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path, plan={"entries": []})
    with pytest.raises(RuntimeError):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), str(tmp_path / "s.img"))


def test_u2_04_rejects_plan_without_erases_key(tmp_path: Path) -> None:
    mod = _load_module()
    plan = _p525aa_plan()
    del plan["erases"]
    ver_dir = _create_firmware_dir(tmp_path, plan=plan)
    with pytest.raises(RuntimeError, match="erases"):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), str(tmp_path / "s.img"))


def test_u2_01b_rejects_plan_without_userdata_or_writable_parts(tmp_path: Path) -> None:
    mod = _load_module()
    plan_no_ud = _p525aa_plan()
    plan_no_ud["entries"] = [e for e in plan_no_ud["entries"] if e["id"] != "UserData"]
    d1 = _create_firmware_dir(tmp_path / "no_ud", plan=plan_no_ud)
    with pytest.raises(RuntimeError, match="UserData"):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(d1 / "images"), str(tmp_path / "s.img"))

    plan_no_writable = {
        "fdl": {"fdl1": {"file": "fdl1.bin", "base": "0x1"}, "fdl2": {"file": "fdl2.bin", "base": "0x2"}},
        "erases": ["misc"],
        "entries": [
            {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "preserve": True},
        ],
    }
    d2 = _create_firmware_dir(tmp_path / "no_writable", plan=plan_no_writable)
    with pytest.raises(RuntimeError, match="writable"):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(d2 / "images"), str(tmp_path / "s.img"))


@pytest.mark.parametrize("route_mode", ["firmware_dir", "family_version", "family_latest"])
def test_u2_05_rejects_identity_mismatch_across_all_three_routes_without_flash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route_mode: str
) -> None:
    mod = _load_module()
    fw_root = tmp_path / "firmware"
    ver_dir = _create_firmware_dir(fw_root, family="P525AA", version="v36", models=["MLY-LX2", "MLY-LX2-CA"])
    (fw_root / "P525AA" / "latest.json").write_text(
        json.dumps({"versions": {"OTHER-MODEL": "v36"}}), encoding="utf-8"
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "OTHER-MODEL",
            "ro.product.device": "OTHER-DEV",
            "ro.product.name": "OTHER-NAME",
            "ro.build.type": "userdebug",
        },
        adb_calls=adb_calls,
    )
    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))
    monkeypatch.setenv("STP_FLASH_FIRMWARE_ROOT", str(fw_root))
    monkeypatch.delenv("STP_FLASH_FIRMWARE_VERSION", raising=False)

    if route_mode == "firmware_dir":
        args = {"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache")}
    elif route_mode == "family_version":
        args = {"family": "P525AA", "version": "v36", "cache_dir": str(tmp_path / "cache")}
    else:
        args = {"family": "P525AA", "cache_dir": str(tmp_path / "cache")}

    with pytest.raises(RuntimeError) as exc_info:
        mod.run(args, "SN1", mod.Progress(), [])

    assert "OTHER-MODEL" in str(exc_info.value)
    assert spd_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls
    route = getattr(exc_info.value, "route", None)
    assert route is not None
    assert route["model"] == "OTHER-MODEL"
    assert route["device"] == "OTHER-DEV"
    assert route["product_name"] == "OTHER-NAME"


def test_u2_05b_identity_normalizes_hyphen_and_underscore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path, family="P532AA", version="v119", models=["p532a_64only"])
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "p532a-64only",
            "ro.product.device": "other",
            "ro.product.name": "other",
        },
    )
    res_dir = mod.resolve_firmware_images_dir({"firmware_dir": str(ver_dir)}, "adb", "SN1")
    assert res_dir == str(ver_dir / "images")


def test_u2_06_rejects_missing_manifest_empty_models_or_wrong_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )

    d_missing = _create_firmware_dir(tmp_path / "m_missing")
    (d_missing / "manifest.json").unlink()
    with pytest.raises(RuntimeError, match="manifest"):
        mod.resolve_firmware_images_dir({"firmware_dir": str(d_missing)}, "adb", "SN1")

    d_empty_models = _create_firmware_dir(tmp_path / "m_empty", models=[])
    with pytest.raises(RuntimeError, match="models"):
        mod.resolve_firmware_images_dir({"firmware_dir": str(d_empty_models)}, "adb", "SN1")

    d_wrong_plat = _create_firmware_dir(tmp_path / "m_plat", platform="mtk")
    with pytest.raises(RuntimeError, match="unisoc"):
        mod.resolve_firmware_images_dir({"firmware_dir": str(d_wrong_plat)}, "adb", "SN1")


def test_u2_07_rejects_latest_versions_map_without_fallback_to_family_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #7 (v1.1): family-level version target passes J2 so rejection can only come from J3."""
    mod = _load_module()
    fw_root = tmp_path / "firmware"
    _create_firmware_dir(fw_root, family="P525AA", version="v36", models=["MLY-LX2"])
    _create_firmware_dir(fw_root, family="P525AA", version="v37_fallback", models=["MLY-LX2-NEW"])
    (fw_root / "P525AA" / "latest.json").write_text(
        json.dumps({"version": "v37_fallback", "versions": {"MLY-LX2": "v36"}}), encoding="utf-8"
    )
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2-NEW", "ro.product.device": "MLY-NEW", "ro.product.name": "MLY-LX2-NEW"},
    )
    monkeypatch.setenv("STP_FLASH_FIRMWARE_ROOT", str(fw_root))
    monkeypatch.delenv("STP_FLASH_FIRMWARE_VERSION", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        mod.resolve_firmware_images_dir({"family": "P525AA"}, "adb", "SN1")
    assert "MLY-LX2-NEW" in str(exc_info.value)
    assert "versions" in str(exc_info.value)


def test_u2_08_erase_whitelist_excludes_non_whitelist_and_reports_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    plan = _p525aa_plan()
    plan["erases"] = ["splloader", "l_runtimenv1", "uboot_log", "misc", "sysdumpdb", "metadata"]
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"], plan=plan)
    sysfs_base = _make_fake_sysfs(tmp_path)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/local/bin/spd_dump")

    cmd = mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), str(tmp_path / "s.img"))
    erased = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "erase_part"]
    assert "splloader" not in erased
    assert "l_runtimenv1" not in erased
    assert erased == ["uboot_log", "misc", "sysdumpdb", "metadata"]

    res = mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])
    assert res["metrics"]["skipped_erases"] == ["splloader", "l_runtimenv1"]


def test_u2_09_protected_partition_strips_slot_suffix_and_blocks_spl_a(tmp_path: Path) -> None:
    mod = _load_module()
    assert mod.is_protected_partition("spl") is True
    assert mod.is_protected_partition("spl_a") is True
    assert mod.is_protected_partition("spl_b") is True
    assert mod.is_protected_partition("splloader_a") is True
    assert mod.is_protected_partition("splloader_b") is True
    assert mod.is_protected_partition("prodnv_a") is True

    plan = _p525aa_plan()
    plan["erases"].append("spl_a")
    plan["entries"].append(
        {"id": "SPL_A", "file": "spl_a.bin", "sparse": False, "partitions": ["spl_a"], "flush_type": "CODE2"}
    )
    ver_dir = _create_firmware_dir(tmp_path, plan=plan)
    cmd = mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), str(tmp_path / "s.img"))
    erased = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "erase_part"]
    written = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "write_part"]
    assert "spl_a" not in erased
    assert "spl_a" not in written


def test_u2_10_battery_gate_rejects_low_or_unreadable_and_allows_skip_battery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path)
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/local/bin/spd_dump")

    base_props = {"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"}

    _install_fake_adb(monkeypatch, mod, props=base_props, battery_out="  level: 4\n")
    with pytest.raises(RuntimeError, match="4%"):
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])

    _install_fake_adb(monkeypatch, mod, props=base_props, battery_rc=1, battery_out="")
    with pytest.raises(RuntimeError, match="battery"):
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])

    res = mod.run({"firmware_dir": str(ver_dir), "dry_run": True, "skip_battery": True}, "SN1", mod.Progress(), [])
    assert res["metrics"]["dry_run"] is True


def test_u2_11_rejects_when_unisoc_download_port_already_on_usb_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-3.2", "1782", "4d00")])
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(RuntimeError, match="1782:4d00"):
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])
    assert spd_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls


def _assert_pid_reaped(pid: int) -> None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    # If somehow zombie, waitpid should not return 0.
    res_pid, _ = os.waitpid(pid, os.WNOHANG)
    assert res_pid == pid


def test_u2_12_adb_reboot_autodloader_failure_terminates_and_reaps_spd_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    pid_file = tmp_path / "dummy_spd.pid"
    dummy_spd = _make_executable(
        tmp_path / "dummy_spd_dump.py",
        f"""
        import os, time
        with open({str(pid_file)!r}, "w") as f:
            f.write(str(os.getpid()))
            f.flush()
        while True:
            time.sleep(0.2)
        """,
    )
    import time as _real_time

    def wait_then_fail_adb(adb_bin: str, serial: str, args: List[str], timeout: int = 30):
        if args == ["reboot", "autodloader"]:
            deadline = _real_time.time() + 3.0
            while not pid_file.exists() and _real_time.time() < deadline:
                _real_time.sleep(0.05)
            return 1, "", "autodloader failed"
        return 0, "", ""

    monkeypatch.setattr(mod, "run_adb", wait_then_fail_adb)

    with pytest.raises(RuntimeError, match="autodloader"):
        mod.run_spd_dump([sys.executable, str(dummy_spd)], "adb", "SN1", mod.Progress(), [], timeout_seconds=5)

    assert pid_file.exists()
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    _assert_pid_reaped(pid)


def test_u2_13_silent_hanging_spd_dump_times_out_and_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    pid_file = tmp_path / "hang_spd.pid"
    dummy_spd = _make_executable(
        tmp_path / "hang_spd_dump.py",
        f"""
        import os, time
        with open({str(pid_file)!r}, "w") as f:
            f.write(str(os.getpid()))
        while True:
            time.sleep(0.2)
        """,
    )
    _install_fake_adb(monkeypatch, mod, reboot_rc=0)

    with pytest.raises(RuntimeError, match="timed out"):
        mod.run_spd_dump([sys.executable, str(dummy_spd)], "adb", "SN1", mod.Progress(), [], timeout_seconds=1.8)

    assert pid_file.exists()
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    _assert_pid_reaped(pid)


def test_u2_14_rejects_when_spd_dump_exits_0_but_device_did_not_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"

    # Device stays on same boot_id (veto), post uptime window cannot be after the reboot (veto)
    def fake_run_adb(adb_bin: str, serial: str, args: List[str], timeout: int = 30):
        if args == ["reboot", "autodloader"]:
            return 0, "", ""
        if args == ["shell", "dumpsys", "battery"]:
            return 0, "  level: 90\n", ""
        if args == ["shell", "getprop", "ro.product.model"]:
            return 0, "MLY-LX2", ""
        if args == ["shell", "getprop", "ro.product.device"]:
            return 0, "MLY", ""
        if args == ["shell", "getprop", "ro.product.name"]:
            return 0, "MLY-LX2", ""
        if args == ["shell", "getprop", "ro.build.type"]:
            return 0, "userdebug", ""
        if args == ["shell", "getprop", "ro.build.sw_internal_version"]:
            return 0, "v36", ""
        if args == ["shell", "getprop", "sys.boot_completed"]:
            return 0, "1", ""
        if args == ["shell", "cat", "/proc/sys/kernel/random/boot_id"]:
            return 0, "same-boot-id", ""
        if args == ["shell", "cat", "/proc/uptime"]:
            return 0, "500.0 800.0", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "run_adb", fake_run_adb)
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )
    _patch_advancing_clock(monkeypatch, mod)

    with pytest.raises(RuntimeError, match="did not reboot"):
        mod.run(
            {"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False},
            "SN1",
            mod.Progress(),
            [],
        )


def test_u2_15_rejects_when_post_flash_version_mismatch_manifest_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(
        tmp_path / "fw", family="P525AA", version="v36", manifest_version="TARGET_V36", models=["MLY-LX2"]
    )
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"

    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "OLD_V35",
        },
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "STILL_OLD_V35",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    with pytest.raises(RuntimeError, match="STILL_OLD_V35"):
        mod.run(
            {"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False},
            "SN1",
            mod.Progress(),
            [],
        )



def test_u2_17_same_length_plan_json_replacement_uses_new_fdl_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    plan_1 = {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["misc"],
        "entries": [
            {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "preserve": True},
            {"id": "BOOT", "file": "boot.img", "sparse": False, "partitions": ["boot_a"]},
        ],
    }
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"], plan=plan_1)
    images = ver_dir / "images"
    cache_dir = tmp_path / "cache"
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"

    seen_cmds: List[List[str]] = []
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
        },
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/local/bin/spd_dump")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    clock = _patch_advancing_clock(monkeypatch, mod)

    def fake_spd_dump(cmd, *_a, **_kw):
        seen_cmds.append(list(cmd))
        adb_state["rebooted"] = True
        adb_state["nonce"] = ""
        clock["now"] += 30.0
        return 0.5

    monkeypatch.setattr(mod, "run_spd_dump", fake_spd_dump)

    mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(cache_dir), "verify_root": False}, "SN1", mod.Progress(), [])
    assert "0x9EFFFE00" in seen_cmds[-1]

    # Reset rebooted state for second run and replace plan.json with exact same length, changing fdl2.base
    adb_state["rebooted"] = False
    old_text = (images / "plan.json").read_text(encoding="utf-8")
    new_text = old_text.replace("0x9EFFFE00", "0x8AAA0000")
    assert len(old_text) == len(new_text)
    (images / "plan.json").write_text(new_text, encoding="utf-8")

    mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(cache_dir), "verify_root": False}, "SN1", mod.Progress(), [])
    assert "0x8AAA0000" in seen_cmds[-1]
    assert "0x9EFFFE00" not in seen_cmds[-1]



def test_u2_19_dry_run_returns_fdl_erases_skipped_partitions_and_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"], plan=_p525aa_plan())
    sysfs_base = _make_fake_sysfs(tmp_path)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/local/bin/spd_dump")

    res = mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])
    m = res["metrics"]
    assert m["dry_run"] is True
    assert m["fdl"]["fdl1"] == {"file": "fdl1-sign.bin", "base": "0x65000800"}
    assert m["fdl"]["fdl2"] == {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"}
    assert m["erases"] == ["uboot_log", "misc", "sysdumpdb", "metadata"]
    assert m["skipped_erases"] == ["splloader", "l_runtimenv1"]
    assert len(m["partitions"]) == 25  # userdata + 23 writable + super
    assert {"partition": "super", "image": "bsl/super_trimmed.img"} in m["partitions"]
    assert m["route"]["decided_by"] == "params"
    assert m["route"]["model"] == "MLY-LX2"
    assert m["route"]["family"] == "P525AA"
    assert m["route"]["version"] == "v36"
    assert m["route"]["manifest"] == str(ver_dir / "manifest.json")


_P532AA_WRITABLE_PARTITIONS = frozenset({
    "blackbox", "boot_a", "cache", "ch_sys_a", "dtbo_a", "fbootlogo_a",
    "init_boot_a", "l_agdsp_a", "logo_a", "nr_modem_a", "nr_phy_a", "pm_sys_a",
    "sml_a", "teecfg_a", "trustos_a", "uboot_a", "vbmeta_a", "vbmeta_odm_a",
    "vbmeta_product_a", "vbmeta_system_a", "vbmeta_system_ext_a",
    "vbmeta_vendor_a", "vendor_boot_a",
})

_P525AA_WRITABLE_PARTITIONS = frozenset({
    "blackbox", "boot_a", "cache", "dtbo_a", "fbootlogo", "init_boot_a",
    "l_agdsp_a", "l_gdsp_a", "l_ldsp_a", "l_modem_a", "logo", "pm_sys_a",
    "sml_a", "teecfg_a", "trustos_a", "uboot_a", "vbmeta_a", "vbmeta_odm_a",
    "vbmeta_product_a", "vbmeta_system_a", "vbmeta_system_ext_a",
    "vbmeta_vendor_a", "vendor_boot_a",
})


def test_u2_20_regression_p532aa_and_p525aa_match_section_3_3_facts(tmp_path: Path) -> None:
    """Regression (#4006 §4 #20/#21): exact partition sets (with slot suffixes), FDL, erases, keep_charge, reset."""
    mod = _load_module()

    p532 = _create_firmware_dir(tmp_path / "fw1", family="P532AA", version="v119", models=["p532a_64only"], plan=_p532aa_plan())
    cmd_532 = mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(p532 / "images"), str(tmp_path / "s1.img"))
    assert str(p532 / "images" / "fdl1-sign.bin") in cmd_532
    assert "0x65008000" in cmd_532
    assert str(p532 / "images" / "edk2-fdl2-sign.bin") in cmd_532
    assert "0xBDFFFE00" in cmd_532
    assert "keep_charge" in cmd_532
    assert cmd_532[-1] == "reset"
    erased_532 = [cmd_532[i + 1] for i, t in enumerate(cmd_532) if t == "erase_part"]
    # Erase command order assertion restored per #4006 §4 #21 (v1.2, B3 ruling).
    assert erased_532 == ["uboot_log", "sd_klog", "misc", "sysdumpdb", "metadata"]
    assert set(erased_532) == {"uboot_log", "sd_klog", "misc", "sysdumpdb", "metadata"}
    assert len(erased_532) == 5
    written_532 = [cmd_532[i + 1] for i, t in enumerate(cmd_532) if t == "write_part"]
    assert written_532[0] == "userdata"
    assert written_532[-1] == "super"
    assert set(written_532) == _P532AA_WRITABLE_PARTITIONS | {"userdata", "super"}
    assert len(written_532) == 25

    p525 = _create_firmware_dir(tmp_path / "fw2", family="P525AA", version="v36", models=["MLY-LX2", "MLY-LX2-CA"], plan=_p525aa_plan())
    cmd_525 = mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(p525 / "images"), str(tmp_path / "s2.img"))
    assert str(p525 / "images" / "fdl1-sign.bin") in cmd_525
    assert "0x65000800" in cmd_525
    assert str(p525 / "images" / "lk-fdl2-sign.bin") in cmd_525
    assert "0x9EFFFE00" in cmd_525
    assert "keep_charge" in cmd_525
    assert cmd_525[-1] == "reset"
    erased_525 = [cmd_525[i + 1] for i, t in enumerate(cmd_525) if t == "erase_part"]
    assert erased_525 == ["uboot_log", "misc", "sysdumpdb", "metadata"]
    assert set(erased_525) == {"uboot_log", "misc", "sysdumpdb", "metadata"}
    assert len(erased_525) == 4
    written_525 = [cmd_525[i + 1] for i, t in enumerate(cmd_525) if t == "write_part"]
    assert written_525[0] == "userdata"
    assert written_525[-1] == "super"
    assert set(written_525) == _P525AA_WRITABLE_PARTITIONS | {"userdata", "super"}
    assert len(written_525) == 25


def test_u2_j9_version_readback_cannot_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(
        tmp_path / "fw", family="P525AA", version="v36", manifest_version="TARGET_V36", models=["MLY-LX2"]
    )
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "WRONG"},
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="WRONG"):
        mod.run(
            {"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False, "verify_version": False},
            "SN1",
            mod.Progress(),
            [],
        )


def test_u2_j11_main_emits_route_on_rejection_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(
        tmp_path / "fw", family="P525AA", version="v36", manifest_version="TARGET_V36", models=["MLY-LX2"]
    )
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"

    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))
    clock = _patch_advancing_clock(monkeypatch, mod)
    monkeypatch.setattr(mod.sys, "argv", ["flash_firmware_unisoc_bsl.py", "--serial", "SN1", "--firmware-dir", str(ver_dir)])

    # 1. Rejection output includes route decision
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "WRONG-M", "ro.product.device": "WRONG-D", "ro.product.name": "WRONG-N"},
        clock=clock,
    )
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"verify_root": False}))
    mod.main()
    rej_out = json.loads(capsys.readouterr().out.strip())
    assert rej_out["success"] is False
    assert rej_out["metrics"]["route"]["model"] == "WRONG-M"
    assert rej_out["metrics"]["route"]["device"] == "WRONG-D"
    assert rej_out["metrics"]["route"]["product_name"] == "WRONG-N"
    assert rej_out["metrics"]["route"]["manifest"] == str(ver_dir / "manifest.json")

    # 2. Success output includes route decision and verified version
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "TARGET_V36"},
        clock=clock,
    )
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"cache_dir": str(tmp_path / "cache"), "verify_root": False}))
    mod.main()
    ok_out = json.loads(capsys.readouterr().out.strip())
    assert ok_out["success"] is True
    assert ok_out["metrics"]["route"]["decided_by"] == "params"
    assert ok_out["metrics"]["post_version"] == "TARGET_V36"
    assert ok_out["metrics"]["reboot_evidence"]["boot_id_changed"] is True


# ── B9-U2 Finite Acceptance Checklist (#4006 §4 v1.1 #23–#35) ─────────────


def test_u2_23_super_uses_bsl_trimmed_not_legacy_raw_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B10 U4：BSL 固定消费 images/bsl/super_trimmed.img，不读 plan 内 raw/super 路径。"""
    mod = _load_module()
    plan = {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["misc"],
        "entries": [
            {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "preserve": True},
            {"id": "BOOT", "file": "boot.img", "sparse": False, "partitions": ["boot_a"]},
            {
                "id": "Super",
                "file": "alt_super_sparse.img",
                "sparse": True,
                "raw": "custom/alt_super.img",
                "partitions": ["super"],
                "flush_type": "YAFFS_IMG2",
            },
        ],
    }
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"], plan=plan)
    images = ver_dir / "images"
    _make_sparse_image(images / "alt_super_sparse.img", [(0xCAC2, 16, b"\x11\x11\x11\x11"), (0xCAC3, 64, b"")])
    alt_raw = images / "custom" / "alt_super.img"
    alt_raw.parent.mkdir(parents=True, exist_ok=True)
    alt_raw.write_bytes(b"ALT_SUPER_BYTES_" + b"X" * (16 * 4096 - 16))
    (images / "super.img").write_bytes(b"WRONG_SPARSE")
    (images / "raw" / "super.img").write_bytes(b"WRONG_RAW" * 1024)
    trimmed = images / "bsl" / "super_trimmed.img"
    trimmed.write_bytes(b"TRIMMED_SUPER_PAYLOAD")

    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    dry_res = mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])
    assert {"partition": "super", "image": "bsl/super_trimmed.img"} in dry_res["metrics"]["partitions"]
    assert {"partition": "super", "image": "custom/alt_super.img"} not in dry_res["metrics"]["partitions"]

    captured: dict = {}

    def fake_spd_dump(cmd, *_a, **_kw):
        written = {cmd[i + 1]: cmd[i + 2] for i, t in enumerate(cmd) if t == "write_part"}
        captured["super_path"] = written["super"]
        captured["super_bytes"] = Path(written["super"]).read_bytes()
        adb_state["rebooted"] = True
        adb_state["nonce"] = ""
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_spd_dump)
    run_res = mod.run(
        {"firmware_dir": str(ver_dir), "verify_root": False},
        "SN1",
        mod.Progress(),
        [],
    )
    assert captured["super_bytes"] == b"TRIMMED_SUPER_PAYLOAD"
    assert captured["super_path"].endswith("bsl/super_trimmed.img")
    assert {"partition": "super", "image": "bsl/super_trimmed.img"} in run_res["metrics"]["partitions"]

def test_u2_24_rejects_non_userdata_id_targeting_userdata(tmp_path: Path) -> None:
    mod = _load_module()
    plan = {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["misc"],
        "entries": [
            {"id": "Unrelated", "file": "other_userdata.img", "sparse": False, "partitions": ["userdata"], "flush_type": "CODE2"},
            {"id": "BOOT", "file": "boot.img", "sparse": False, "partitions": ["boot_a"]},
        ],
    }
    ver_dir = _create_firmware_dir(tmp_path, plan=plan)
    with pytest.raises(RuntimeError, match="UserData"):
        mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), str(tmp_path / "s.img"))


def test_u2_25_regression_userdata_written_with_preserve_while_other_preserve_skipped(tmp_path: Path) -> None:
    mod = _load_module()
    plan = {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["misc"],
        "entries": [
            {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "preserve": True},
            {"id": "BOOT", "file": "boot.img", "sparse": False, "partitions": ["boot_a"], "preserve": True},
            {"id": "INIT_BOOT", "file": "init_boot.img", "sparse": False, "partitions": ["init_boot_a"]},
        ],
    }
    ver_dir = _create_firmware_dir(tmp_path, plan=plan)
    cmd = mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(ver_dir / "images"), "")
    written = [cmd[i + 1] for i, t in enumerate(cmd) if t == "write_part"]
    assert "userdata" in written
    assert "boot_a" not in written
    assert "init_boot_a" in written


def test_u2_26_rejects_download_port_appearing_after_ensure_before_spd_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B10 U4：下载口闸门仍在锁内、spd_dump 之前；ensure 之后插入 1782:4d00 必须拒绝。"""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    spd_called = {"n": 0}

    def acquire_then_plug(progress, timeout_seconds=7200, lock_path=None):
        d = sysfs_base / "1-4.1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "idVendor").write_text("1782\n", encoding="utf-8")
        (d / "idProduct").write_text("4d00\n", encoding="utf-8")
        return os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))
    monkeypatch.setattr(mod, "acquire_host_lock", acquire_then_plug)
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(RuntimeError, match="1782:4d00"):
        mod.run(
            {"firmware_dir": str(ver_dir), "require_port_binding": False, "download_port_clear_wait_seconds": 0},
            "SN1",
            mod.Progress(),
            [],
        )
    assert spd_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls


def test_u2_27_rejects_when_usb_bus_directory_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    missing_sysfs = tmp_path / "no_such_sysfs_usb"
    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(missing_sysfs), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(RuntimeError, match="no_such_sysfs_usb"):
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])
    assert spd_called["n"] == 0


def test_u2_28_rejects_when_total_phase_duration_exceeds_timeout_even_if_spd_dump_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #28 (v1.2): the stub spd_dump returns 0 after the reboot command was sent,
    with the stage duration already over the limit. The test first observes the reboot
    command being called (stub waits for its marker), then observes the stub returning 0
    on its own (exit marker written before any kill), and the run must still fail."""
    import time as _real_time

    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"

    reboot_marker = tmp_path / "reboot_called.json"
    exit_marker = tmp_path / "spd_exit.json"
    dummy_spd = _make_executable(
        tmp_path / "late_ok_spd.py",
        f"""
        import json, sys, time
        start = time.time()
        marker = {str(reboot_marker)!r}
        deadline = time.time() + 10.0
        seen = None
        while time.time() < deadline:
            try:
                seen = json.load(open(marker))
                break
            except (OSError, ValueError):
                time.sleep(0.02)
        if seen is None:
            json.dump({{"rc": 3, "start": start, "exit": time.time()}}, open({str(exit_marker)!r}, "w"))
            sys.exit(3)
        time.sleep(1.0)
        exit_ts = time.time()
        json.dump({{"rc": 0, "start": start, "seen_reboot_at": seen["at"], "exit": exit_ts}},
                  open({str(exit_marker)!r}, "w"))
        sys.exit(0)
        """,
    )

    def slow_adb(adb_bin: str, serial: str, args: List[str], timeout: float = 30):
        if args == ["reboot", "autodloader"]:
            with open(reboot_marker, "w", encoding="utf-8") as fh:
                json.dump({"at": _real_time.time()}, fh)
            _real_time.sleep(1.6)
            return 0, "", ""
        if args == ["shell", "dumpsys", "battery"]:
            return 0, "  level: 90\n", ""
        if args[:2] == ["shell", "getprop"]:
            if args[2] == "sys.boot_completed":
                return 0, "1", ""
            return 0, {"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"}.get(args[2], ""), ""
        if args == ["shell", "cat", "/proc/sys/kernel/random/boot_id"]:
            return 0, "boot-before", ""
        if args == ["shell", "id"]:
            return 0, "uid=0(root) gid=0(root)", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "run_adb", slow_adb)
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="timed out"):
        mod.run(
            {
                "firmware_dir": str(ver_dir),
                "cache_dir": str(tmp_path / "cache"),
                "spd_dump_timeout_seconds": 2.0,
                "verify_root": False,
            },
            "SN1",
            mod.Progress(),
            [],
        )

    assert reboot_marker.exists()
    assert exit_marker.exists()
    stub = json.loads(exit_marker.read_text(encoding="utf-8"))
    # The stub completed on its own (not killed) with rc 0, after the reboot was sent,
    # and its lifespan (the stage duration) was already over the 2.0s limit.
    assert stub["rc"] == 0
    assert stub["exit"] > stub["seen_reboot_at"]
    assert stub["exit"] - stub["start"] > 2.0


@pytest.mark.parametrize("bad_timeout", [0, -5, float("nan"), float("inf")])
def test_u2_29_rejects_non_positive_or_non_finite_spd_dump_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_timeout: float
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path)
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(RuntimeError, match="spd_dump_timeout_seconds"):
        mod.run({"firmware_dir": str(ver_dir), "spd_dump_timeout_seconds": bad_timeout}, "SN1", mod.Progress(), [])
    assert spd_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls


def test_u2_30_rejects_when_spd_dump_exits_before_adb_reboot_without_calling_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #30 (a): the stub exits during the startup wait — no reboot is sent."""
    mod = _load_module()
    dummy_spd = _make_executable(tmp_path / "early_exit_spd.py", "import sys; sys.exit(0)")
    adb_calls: List[List[str]] = []
    _install_fake_adb(monkeypatch, mod, adb_calls=adb_calls)

    with pytest.raises(RuntimeError, match="before adb reboot autodloader"):
        mod.run_spd_dump([sys.executable, str(dummy_spd)], "adb", "SN1", mod.Progress(), [], timeout_seconds=5)
    assert ["reboot", "autodloader"] not in adb_calls


def test_u2_30b_rejects_when_spd_dump_exits_during_last_progress_stamp_before_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #30 (b) (v1.2): the stub exits during the last PROGRESS output before the
    reboot is sent. The liveness check must be the last step before `adb reboot
    autodloader`, so an exit during the stamp must be caught and no reboot may be sent."""
    import time as _real_time

    mod = _load_module()
    exit_marker = tmp_path / "stub_exit.marker"
    dummy_spd = _make_executable(
        tmp_path / "exit_during_stamp_spd.py",
        f"""
        import sys, time
        time.sleep(1.9)
        open({str(exit_marker)!r}, "w").close()
        sys.exit(0)
        """,
    )
    adb_calls: List[List[str]] = []
    _install_fake_adb(monkeypatch, mod, adb_calls=adb_calls)

    stamp_state = {"entered_autodloader_stamp": False, "saw_exit_during_stamp": False}

    class StampBlockingProgress:
        """Progress double whose 'autodloader' stamp blocks until the stub has exited,
        so the stub provably terminates during the last PROGRESS output."""

        def stamp(self, **fields) -> None:
            if fields.get("stage") != "autodloader":
                return
            stamp_state["entered_autodloader_stamp"] = True
            deadline = _real_time.time() + 5.0
            while not exit_marker.exists() and _real_time.time() < deadline:
                _real_time.sleep(0.02)
            if exit_marker.exists():
                stamp_state["saw_exit_during_stamp"] = True
                _real_time.sleep(0.05)

    with pytest.raises(RuntimeError, match="before adb reboot autodloader"):
        mod.run_spd_dump(
            [sys.executable, str(dummy_spd)], "adb", "SN1", StampBlockingProgress(), [], timeout_seconds=8
        )

    assert stamp_state["entered_autodloader_stamp"] is True
    assert stamp_state["saw_exit_during_stamp"] is True
    assert ["reboot", "autodloader"] not in adb_calls


def test_u2_31a_rejects_when_boot_id_unreadable_and_uptime_window_straddles_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #31 (a) v1.2: boot_id unreadable both times; the device did not reboot but
    adb return latency widens the post-read window so T1-u is later than R while T0-u is
    not — uptime provides no evidence and no positive evidence exists -> failure."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        boot_id_rc=1,
        pre_uptime="30.0 60.0",
        post_uptime="30.0 60.0",
        uptime_latency_seconds=2.0,
        boot_advance_seconds=30.0,
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="no positive evidence"):
        # boot_stabilize_seconds=0: fixture uses static post_uptime="30.0" that does not advance with clock.
        mod.run(
            {
                "firmware_dir": str(ver_dir),
                "cache_dir": str(tmp_path / "cache"),
                "verify_root": False,
                "boot_stabilize_seconds": 0,
            },
            "SN1",
            mod.Progress(),
            [],
        )


def test_u2_31b_passes_when_boot_id_unreadable_and_uptime_window_starts_after_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #31 (b) v1.2: boot_id unreadable; T0-u later than R (fresh boot inside the
    window start); the fixture's post uptime is larger than its pre uptime, so a decrease
    heuristic would fail — the window rule passes. Green on 1.0.2 (regression)."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        boot_id_rc=1,
        pre_uptime="2.0 4.0",
        post_uptime="5.0 10.0",
        boot_advance_seconds=30.0,
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    res = mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False}, "SN1", mod.Progress(), [])
    assert res.get("skipped") is not True
    assert res["metrics"]["duration_seconds"] >= 0


def test_u2_31c_rejects_when_boot_id_and_uptime_all_reads_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #31 (c) v1.2: boot_id and uptime reads all fail -> no evidence -> failure."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        boot_id_rc=1,
        uptime_rc=1,
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="no positive evidence"):
        mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False}, "SN1", mod.Progress(), [])


def test_u2_31d_passes_when_boot_id_changes_and_uptime_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #31 (d) v1.2: boot_id differs before/after (positive) although the uptime
    read fails -> pass. Green on 1.0.2 (regression)."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        pre_boot_id="boot-1",
        post_boot_id="boot-2",
        uptime_rc=1,
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    res = mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False}, "SN1", mod.Progress(), [])
    assert res.get("skipped") is not True
    assert res["metrics"]["duration_seconds"] >= 0


def test_u2_31e_rejects_when_boot_id_same_even_if_uptime_window_starts_after_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #31 (e) v1.2: boot_id identical (veto) while the uptime window would give
    positive evidence — a veto must win over positive evidence -> failure."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        pre_boot_id="boot-same",
        post_boot_id="boot-same",
        pre_uptime="5.0 10.0",
        post_uptime="5.0 10.0",
        boot_advance_seconds=30.0,
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="did not reboot"):
        mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False}, "SN1", mod.Progress(), [])


def test_u2_31f_rejects_when_boot_id_changes_but_uptime_window_not_after_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #31 (f) v1.2: boot_id differs (positive) but T1-u is not later than R —
    the uptime veto must win over the boot_id positive evidence -> failure."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        pre_boot_id="boot-1",
        post_boot_id="boot-2",
        pre_uptime="50.0 80.0",
        post_uptime="50.0 80.0",
        uptime_latency_seconds=2.0,
        boot_advance_seconds=30.0,
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="did not reboot"):
        mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False}, "SN1", mod.Progress(), [])


def test_u2_31g_rejects_when_only_post_boot_id_readable_and_uptime_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #31 (g) v1.2: boot_id readable only after the flash (one side) and the
    uptime read fails — one-sided boot_id is not evidence -> failure."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(
        tmp_path / "ok_spd.py",
        "import sys, time; print('Opened 1782:4d00 on usb-port 1-5.2.3', file=sys.stderr, flush=True); time.sleep(1.7); sys.exit(0)",
    )
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug"},
        post_props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2", "ro.build.type": "userdebug", "ro.build.sw_internal_version": "v36"},
        boot_id_rc=1,
        post_boot_id_rc=0,
        post_boot_id="boot-post-only",
        uptime_rc=1,
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="no positive evidence"):
        mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache"), "verify_root": False}, "SN1", mod.Progress(), [])


def test_u2_36_wall_clock_rollback_inside_stage_still_times_out_on_monotonic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4006 §4 #36 (v1.2): the host wall clock is rolled back while the spd_dump stage is
    running; the monotonic clock shows the stage duration over the limit -> failure.
    Stage enforcement and R/T0/T1 use time.monotonic() (J8/J9 v1.2), so the rollback of
    time.time() cannot extend the deadline."""
    import time as _real_time

    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    dummy_spd = _make_executable(tmp_path / "slow_ok_spd.py", "import sys, time; time.sleep(2.6); sys.exit(0)")
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"

    wall_state = {"rolled_back": False}

    def fake_wall_time() -> float:
        return _real_time.time() - (12.0 if wall_state["rolled_back"] else 0.0)

    def fake_run_adb(adb_bin: str, serial: str, args: List[str], timeout: float = 30):
        if args == ["reboot", "autodloader"]:
            wall_state["rolled_back"] = True
            return 0, "", ""
        if args == ["shell", "dumpsys", "battery"]:
            return 0, "  level: 90\n", ""
        if args[:2] == ["shell", "getprop"]:
            return 0, {
                "ro.product.model": "MLY-LX2",
                "ro.product.device": "MLY",
                "ro.product.name": "MLY-LX2",
                "ro.build.type": "userdebug",
                "sys.boot_completed": "1",
            }.get(args[2], ""), ""
        if args == ["shell", "cat", "/proc/sys/kernel/random/boot_id"]:
            return 0, "boot-before", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "time", SimpleNamespace(time=fake_wall_time, monotonic=_real_time.monotonic, sleep=_real_time.sleep))
    monkeypatch.setattr(mod, "run_adb", fake_run_adb)
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(dummy_spd))
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666))

    with pytest.raises(RuntimeError, match="timed out"):
        mod.run(
            {
                "firmware_dir": str(ver_dir),
                "cache_dir": str(tmp_path / "cache"),
                "spd_dump_timeout_seconds": 2.0,
                "verify_root": False,
            },
            "SN1",
            mod.Progress(),
            [],
        )



def test_u2_33_route_records_selected_dir_not_manifest_family_or_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(
        tmp_path / "fw",
        family="ACTUAL_FAM",
        version="v_actual",
        manifest_version="DISPLAY_VER_99",
        models=["MLY-LX2"],
    )
    manifest_path = ver_dir / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["family"] = "WRONG_FAM_IN_MANIFEST"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    sysfs_base = _make_fake_sysfs(tmp_path)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))

    res = mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])
    m = res["metrics"]
    assert m["route"]["family"] == "ACTUAL_FAM"
    assert m["route"]["version"] == "v_actual"
    assert m["expected_version"] == "DISPLAY_VER_99"
    assert "WRONG_FAM_IN_MANIFEST" not in m["route"].values()
    assert "DISPLAY_VER_99" not in m["route"].values()


def test_u2_34_identity_non_utf8_adb_output_returns_false_with_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    fake_adb = _make_executable(
        tmp_path / "fake_adb.py",
        """
        import sys
        args = sys.argv[1:]
        if "ro.product.model" in args:
            sys.stdout.write("MLY-LX2\\n")
        elif "ro.product.device" in args:
            sys.stdout.buffer.write(b"bad-\\xff\\xfe\\n")
        else:
            sys.stdout.write("MLY-LX2\\n")
        """,
    )
    stub_spd = _make_executable(tmp_path / "stub_spd.py", "import sys; sys.exit(99)")
    monkeypatch.setattr(mod, "resolve_adb", lambda: str(fake_adb))
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(stub_spd))
    monkeypatch.setattr(mod.sys, "argv", ["flash_firmware_unisoc_bsl.py", "--serial", "SN1", "--firmware-dir", str(ver_dir)])
    monkeypatch.setenv("STP_STEP_PARAMS", "{}")

    mod.main()
    out = json.loads(capsys.readouterr().out.strip())
    assert out["success"] is False
    assert "route" in out["metrics"]
    route = out["metrics"]["route"]
    assert route["decided_by"] == "params"
    assert route["model"] == "MLY-LX2"
    assert route["device"] == ""
    assert route["product_name"] == ""
    assert route["family"] == "P525AA"
    assert route["version"] == "v36"
    assert route["manifest"] == str(ver_dir / "manifest.json")


def test_u2_35_dry_run_takes_precedence_over_skip_if_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"], plan=_p525aa_plan())
    sysfs_base = _make_fake_sysfs(tmp_path)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "36",
        },
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base), raising=False)
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump_stub"))

    res = mod.run(
        {
            "firmware_dir": str(ver_dir),
            "dry_run": True,
            "skip_if_current": True,
            "verify_incremental": "36",
            "verify_build_type": "userdebug",
        },
        "SN1",
        mod.Progress(),
        [],
    )
    assert res.get("skipped") is not True
    m = res["metrics"]
    assert m["dry_run"] is True
    assert m["fdl"]["fdl1"] == {"file": "fdl1-sign.bin", "base": "0x65000800"}
    assert m["erases"] == ["uboot_log", "misc", "sysdumpdb", "metadata"]
    assert m["skipped_erases"] == ["splloader", "l_runtimenv1"]
    assert len(m["partitions"]) == 25
    assert m["route"]["version"] == "v36"


# ── U4: spd_dump 工具包（STP_SPD_DUMP_DIR，#4006 §3.2 L1–L4 / §4 U4 第 1–3、6 条）──


def _u4_firmware_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Route-establishable firmware + fake adb env for U4 tests (identity OK, battery 85%)."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path)
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    return mod, ver_dir


def test_u4_1_6_rejects_without_stp_spd_dump_dir_and_carries_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U4 第 1 条（1.0.3 上为红）+ 第 6 条（v1.2）：未设置 STP_SPD_DUMP_DIR 必须拒绝，
    # 且拒绝结果带路由（1.0.3 在路由建立前解析工具路径，失败结果无路由）。
    mod, ver_dir = _u4_firmware_env(tmp_path, monkeypatch)
    monkeypatch.delenv("STP_SPD_DUMP_DIR", raising=False)
    monkeypatch.delenv("STP_SPD_DUMP_PATH", raising=False)
    with pytest.raises(mod.FlashRejectedError, match="STP_SPD_DUMP_DIR") as exc_info:
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])
    route = exc_info.value.route
    assert route, "U4 item 6: rejection must carry route"
    assert route.get("family") == "P525AA"
    assert route.get("version") == "v36"


def test_u4_2_rejects_when_tool_dir_lacks_executable_spd_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U4 第 2 条：键指向的目录里没有可执行的 spd_dump（缺文件 / 不可执行）→ 拒绝。
    mod, ver_dir = _u4_firmware_env(tmp_path, monkeypatch)
    empty_dir = tmp_path / "toolpkg-empty"
    empty_dir.mkdir()
    monkeypatch.setenv("STP_SPD_DUMP_DIR", str(empty_dir))
    with pytest.raises(mod.FlashRejectedError, match="spd_dump"):
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])

    noexec_dir = tmp_path / "toolpkg-noexec"
    noexec_dir.mkdir()
    (noexec_dir / "spd_dump").write_bytes(b"not really an elf")
    os.chmod(noexec_dir / "spd_dump", 0o644)
    monkeypatch.setenv("STP_SPD_DUMP_DIR", str(noexec_dir))
    with pytest.raises(mod.FlashRejectedError, match="spd_dump"):
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])


def test_u4_3_uses_tool_dir_even_when_path_has_another_spd_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U4 第 3 条：STP_SPD_DUMP_DIR 正确时使用该目录下的文件，即使 PATH 中另有 spd_dump。
    mod, _ver_dir = _u4_firmware_env(tmp_path, monkeypatch)
    tool_dir = tmp_path / "toolpkg"
    tool_dir.mkdir()
    tool_bin = tool_dir / "spd_dump"
    tool_bin.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(tool_bin, 0o755)
    path_dir = tmp_path / "pathbin"
    path_dir.mkdir()
    (path_dir / "spd_dump").write_text("#!/bin/sh\nexit 0\n")
    os.chmod(path_dir / "spd_dump", 0o755)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("STP_SPD_DUMP_DIR", str(tool_dir))
    assert mod.resolve_spd_dump({"family": "P525AA", "version": "v36"}) == str(tool_bin)


@pytest.mark.parametrize("dir_state", ["unset", "missing_file", "not_executable"])
@pytest.mark.parametrize("legacy_candidate", ["env_path", "path", "usr_local_bin", "usr_bin"])
def test_u4_7_rejects_all_legacy_sources_via_main_when_tool_dir_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dir_state: str,
    legacy_candidate: str,
) -> None:
    """U4 #7 (#4006 v1.4): when STP_SPD_DUMP_DIR is unset/missing/non-executable and a single
    legacy candidate (STP_SPD_DUMP_PATH, PATH, /usr/local/bin/spd_dump, /usr/bin/spd_dump)
    is available, main() must emit success=false with real metrics.route and invoke neither
    the flasher nor adb reboot autodloader, without depending on host spd_dump."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path)
    lock_file = tmp_path / "host.lock"
    cache_dir = tmp_path / "cache"
    adb_calls: List[List[str]] = []

    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
        },
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        post_uptime="0.01 0.01",
        adb_calls=adb_calls,
    )

    if dir_state == "unset":
        monkeypatch.delenv("STP_SPD_DUMP_DIR", raising=False)
    elif dir_state == "missing_file":
        empty_dir = tmp_path / "toolpkg-missing"
        empty_dir.mkdir()
        monkeypatch.setenv("STP_SPD_DUMP_DIR", str(empty_dir))
    else:
        noexec_dir = tmp_path / "toolpkg-noexec"
        noexec_dir.mkdir()
        noexec_bin = noexec_dir / "spd_dump"
        noexec_bin.write_bytes(b"#!/bin/sh\nexit 0\n")
        os.chmod(noexec_bin, 0o644)
        monkeypatch.setenv("STP_SPD_DUMP_DIR", str(noexec_dir))

    marker_file = tmp_path / "legacy_spd_invoked.txt"
    stub_body = (
        f"#!{sys.executable}\n"
        "import sys, time\n"
        "from pathlib import Path\n"
        f"Path({str(marker_file)!r}).write_text(' '.join(sys.argv), encoding='utf-8')\n"
        "time.sleep(1.8)\n"
        "sys.exit(0)\n"
    )

    empty_path_dir = tmp_path / "empty_path"
    empty_path_dir.mkdir()
    monkeypatch.delenv("STP_SPD_DUMP_PATH", raising=False)
    monkeypatch.setenv("PATH", str(empty_path_dir))

    fixed_probe_targets: Dict[str, Optional[str]] = {
        "/usr/local/bin/spd_dump": None,
        "/usr/bin/spd_dump": None,
    }
    if legacy_candidate == "env_path":
        legacy_bin = tmp_path / "legacy_env_spd_dump"
        legacy_bin.write_text(stub_body, encoding="utf-8")
        os.chmod(legacy_bin, 0o755)
        monkeypatch.setenv("STP_SPD_DUMP_PATH", str(legacy_bin))
    elif legacy_candidate == "path":
        legacy_path_dir = tmp_path / "legacy_path"
        legacy_path_dir.mkdir()
        legacy_bin = legacy_path_dir / "spd_dump"
        legacy_bin.write_text(stub_body, encoding="utf-8")
        os.chmod(legacy_bin, 0o755)
        monkeypatch.setenv("PATH", str(legacy_path_dir))
    elif legacy_candidate == "usr_local_bin":
        legacy_bin = tmp_path / "fake_usr_local_bin_spd_dump"
        legacy_bin.write_text(stub_body, encoding="utf-8")
        os.chmod(legacy_bin, 0o755)
        fixed_probe_targets["/usr/local/bin/spd_dump"] = str(legacy_bin)
    elif legacy_candidate == "usr_bin":
        legacy_bin = tmp_path / "fake_usr_bin_spd_dump"
        legacy_bin.write_text(stub_body, encoding="utf-8")
        os.chmod(legacy_bin, 0o755)
        fixed_probe_targets["/usr/bin/spd_dump"] = str(legacy_bin)

    real_isfile = os.path.isfile
    real_exists = os.path.exists
    real_access = os.access

    def _probe_key(path) -> Optional[str]:
        if isinstance(path, (str, bytes, os.PathLike)):
            raw = os.fspath(path)
            return os.fsdecode(raw) if isinstance(raw, bytes) else raw
        return None

    def fake_isfile(path) -> bool:
        key = _probe_key(path)
        if key in fixed_probe_targets:
            target = fixed_probe_targets[key]
            return target is not None and real_isfile(target)
        return real_isfile(path)

    def fake_exists(path) -> bool:
        key = _probe_key(path)
        if key in fixed_probe_targets:
            target = fixed_probe_targets[key]
            return target is not None and real_exists(target)
        return real_exists(path)

    def fake_access(path, mode: int, *args, **kwargs) -> bool:
        key = _probe_key(path)
        if key in fixed_probe_targets:
            target = fixed_probe_targets[key]
            return target is not None and real_access(target, mode, *args, **kwargs)
        return real_access(path, mode, *args, **kwargs)

    monkeypatch.setattr(mod.os.path, "isfile", fake_isfile)
    monkeypatch.setattr(mod.os.path, "exists", fake_exists)
    monkeypatch.setattr(mod.os, "access", fake_access)

    popen_calls: List[List[str]] = []
    real_popen = mod.subprocess.Popen

    def fake_popen(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        popen_calls.append(cmd_list)
        mapped_cmd = list(cmd_list)
        if mapped_cmd and mapped_cmd[0] in fixed_probe_targets and fixed_probe_targets[mapped_cmd[0]] is not None:
            mapped_cmd[0] = str(fixed_probe_targets[mapped_cmd[0]])
        return real_popen(mapped_cmd, *args, **kwargs)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SN1")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"verify_root": False}))
    monkeypatch.setattr(
        mod.sys,
        "argv",
        [
            "flash_firmware_unisoc_bsl.py",
            "--serial",
            "SN1",
            "--firmware-dir",
            str(ver_dir),
            "--cache-dir",
            str(cache_dir),
        ],
    )

    mod.main()
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())
    assert out["success"] is False, (
        f"expected rejection for dir_state={dir_state}, legacy={legacy_candidate} "
        f"(popen_calls={popen_calls}, adb_reboot_sent={['reboot', 'autodloader'] in adb_calls}), got {out}"
    )
    assert popen_calls == [], f"legacy flasher must not be launched: {popen_calls}"
    assert not marker_file.exists(), "legacy spd_dump stub must not execute"
    assert ["reboot", "autodloader"] not in adb_calls, "adb reboot autodloader must not be called"
    assert out.get("metrics", {}).get("route") == {
        "decided_by": "params",
        "model": "MLY-LX2",
        "device": "MLY",
        "product_name": "MLY-LX2",
        "family": "P525AA",
        "version": "v36",
        "manifest": str(ver_dir / "manifest.json"),
    }


# ── 1.0.5: spd_dump --usb-port 口级绑定与回显校验（#3995 §5 第 3 项）──


def test_v105_build_spd_dump_args_places_usb_port_and_rejects_invalid(tmp_path: Path) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    fw_dir = str(ver_dir / "images")
    cmd = mod.build_spd_dump_args(
        "/tmp/spd_dump",
        fw_dir,
        str(tmp_path / "super.img"),
        usb_port="1-5.2.3",
    )
    assert cmd[:3] == ["/tmp/spd_dump", "--usb-port", "1-5.2.3"]

    for bad in ("", "1", "01-5.1", "1-5.0", "../1-5.1", "1-5.2.3:1.0"):
        with pytest.raises(ValueError, match="invalid usb_port"):
            mod.build_spd_dump_args(
                "/tmp/spd_dump",
                fw_dir,
                str(tmp_path / "super.img"),
                usb_port=bad,
            )


def test_v105_resolve_device_usb_port_cases(tmp_path: Path) -> None:
    mod = _load_module()
    route = {"family": "P525AA"}

    sysfs_ok = _make_fake_sysfs(tmp_path / "ok", [("1-5.2.3", "18d1", "4ee8", "SN1"), ("1-5.2.3:1.0", "", "")])
    assert mod.resolve_device_usb_port("SN1", base=str(sysfs_ok), route=route) == "1-5.2.3"

    sysfs_empty = _make_fake_sysfs(tmp_path / "empty", [])
    with pytest.raises(mod.FlashRejectedError, match="cannot resolve target USB port") as exc_missing:
        mod.resolve_device_usb_port("SN1", base=str(sysfs_empty), route=route)
    assert exc_missing.value.route == route
    assert mod.resolve_device_usb_port("SN1", require_port_binding=False, base=str(sysfs_empty), route=route) is None

    sysfs_dup = _make_fake_sysfs(
        tmp_path / "dup",
        [("1-5.1.1", "18d1", "4ee8", "SN1"), ("1-5.2.3", "18d1", "4ee8", "SN1")],
    )
    with pytest.raises(mod.FlashRejectedError, match="ambiguous"):
        mod.resolve_device_usb_port("SN1", base=str(sysfs_dup), route=route)

    sysfs_bad_name = _make_fake_sysfs(tmp_path / "bad", [("01-5.2", "18d1", "4ee8", "SN1")])
    with pytest.raises(mod.FlashRejectedError, match="invalid sysfs USB port"):
        mod.resolve_device_usb_port("SN1", base=str(sysfs_bad_name), route=route)


def test_v105_run_rejects_missing_sysfs_port_before_lock_and_reboot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_empty = _make_fake_sysfs(tmp_path, [])
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_empty))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump"))

    lock_called = {"n": 0}
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda *_a, **_kw: lock_called.__setitem__("n", lock_called["n"] + 1),
    )

    with pytest.raises(mod.FlashRejectedError, match="cannot resolve target USB port") as exc_info:
        mod.run({"firmware_dir": str(ver_dir), "dry_run": False}, "SN1", mod.Progress(), [])
    assert exc_info.value.route and exc_info.value.route["version"] == "v36"
    assert lock_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls


def test_v105_run_rejects_when_usb_port_changes_while_waiting_for_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-5.2.3", "18d1", "4ee8", "SN1")])
    lock_file = tmp_path / "host.lock"
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: str(tmp_path / "spd_dump"))

    def fake_acquire(progress, timeout_seconds=7200):
        # Simulate device re-plugging onto a different physical port during lock wait.
        old_dir = sysfs_base / "1-5.2.3"
        for child in old_dir.iterdir():
            child.unlink()
        old_dir.rmdir()
        new_dir = sysfs_base / "1-5.2.4"
        new_dir.mkdir()
        (new_dir / "idVendor").write_text("18d1\n", encoding="utf-8")
        (new_dir / "idProduct").write_text("4ee8\n", encoding="utf-8")
        (new_dir / "serial").write_text("SN1\n", encoding="utf-8")
        return os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

    monkeypatch.setattr(mod, "acquire_host_lock", fake_acquire)

    with pytest.raises(mod.FlashRejectedError, match="changed while waiting for host lock"):
        mod.run({"firmware_dir": str(ver_dir), "cache_dir": str(tmp_path / "cache")}, "SN1", mod.Progress(), [])
    assert ["reboot", "autodloader"] not in adb_calls


@pytest.mark.parametrize(
    ("echo_line", "expect_msg"),
    [
        ("", "without USB port binding echo"),
        ("Opened 1782:4d00 on usb-port 1-5.2.4", "unexpected USB port"),
    ],
)
def test_v105_run_spd_dump_rejects_missing_or_wrong_port_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    echo_line: str,
    expect_msg: str,
) -> None:
    mod = _load_module()
    dummy_spd = _make_executable(
        tmp_path / "spd_echo.py",
        f"""
        import sys, time
        if {echo_line!r}:
            print({echo_line!r}, file=sys.stderr, flush=True)
        time.sleep(1.7)
        sys.exit(0)
        """,
    )
    _install_fake_adb(monkeypatch, mod)
    with pytest.raises(RuntimeError, match=expect_msg):
        mod.run_spd_dump(
            [sys.executable, str(dummy_spd)],
            "adb",
            "SN1",
            mod.Progress(),
            [],
            timeout_seconds=10,
            expected_usb_port="1-5.2.3",
        )


def test_v105_run_passes_usb_port_and_supports_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [])
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    seen_cmds: List[List[str]] = []

    def fake_run_spd_dump(cmd, *_a, **kwargs):
        seen_cmds.append(list(cmd))
        assert kwargs.get("expected_usb_port") is None
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    res = mod.run(
        {
            "firmware_dir": str(ver_dir),
            "cache_dir": str(tmp_path / "cache"),
            "verify_root": False,
            "require_port_binding": False,
        },
        "SN1",
        mod.Progress(),
        [],
    )
    assert res["metrics"]["require_port_binding"] is False
    assert res["metrics"]["usb_port"] is None
    assert "--usb-port" not in seen_cmds[0]


def test_v108_queued_device_waits_for_host_lock_while_peer_holds_download_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4066: peer holding host lock + 1782:4d00 must NOT kill queued sibling before lock."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    # Peer device on 1-5.3.1 is actively flashing (1782:4d00); target SN1 is idle in ADB on 1-5.2.3.
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-5.3.1", "1782", "4d00"),
            ("1-5.2.3", "18d1", "4ee8", "SN1"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "old",
        },
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    lock_acquired = {"called": False}

    def fake_acquire(progress, timeout_seconds=7200):
        # Peer finishes flashing and exits 1782:4d00 before releasing the host lock.
        lock_acquired["called"] = True
        peer_dir = sysfs_base / "1-5.3.1"
        for child in peer_dir.iterdir():
            child.unlink()
        peer_dir.rmdir()
        return os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

    def fake_run_spd_dump(*_a, **_kw):
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "acquire_host_lock", fake_acquire)
    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    res = mod.run(
        {"firmware_dir": str(ver_dir), "verify_root": False},
        "SN1",
        mod.Progress(),
        [],
    )
    assert lock_acquired["called"] is True
    assert res["metrics"]["usb_port"] == "1-5.2.3"
    assert res["metrics"]["post_version"] == "v36"


def test_v108_orphan_download_port_still_rejected_inside_host_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4066: a genuine orphan 1782:4d00 still present AFTER acquiring host lock must fail-closed."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-5.3.1", "1782", "4d00"),
            ("1-5.2.3", "18d1", "4ee8", "SN1"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    lock_acquired = {"called": False}

    def fake_acquire(progress, timeout_seconds=7200):
        lock_acquired["called"] = True
        return os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "acquire_host_lock", fake_acquire)
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(mod.FlashRejectedError, match="1782:4d00") as exc_info:
        mod.run(
            {"firmware_dir": str(ver_dir), "verify_root": False, "require_port_binding": False},
            "SN1",
            mod.Progress(),
            [],
        )

    assert lock_acquired["called"] is True
    assert exc_info.value.route and exc_info.value.route["version"] == "v36"
    assert spd_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls


def test_v108_early_exit_spd_dump_captures_stderr_in_log_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4066: spd_dump exiting before adb reboot autodloader must preserve stderr in log_tail."""
    mod = _load_module()
    dummy_spd = _make_executable(
        tmp_path / "missing_lib_spd.py",
        "import sys; print('error while loading shared libraries: libusb-1.0.so.0', file=sys.stderr, flush=True); sys.exit(127)",
    )
    adb_calls: List[List[str]] = []
    _install_fake_adb(monkeypatch, mod, adb_calls=adb_calls)
    log: List[str] = []

    with pytest.raises(RuntimeError, match="before adb reboot autodloader") as exc_info:
        mod.run_spd_dump([sys.executable, str(dummy_spd)], "adb", "SN1", mod.Progress(), log, timeout_seconds=5)

    assert ["reboot", "autodloader"] not in adb_calls
    assert any("libusb-1.0.so.0" in line for line in log), f"expected stderr in log_tail, got {log!r}"
    assert "libusb-1.0.so.0" in str(exc_info.value)


def test_v109_post_flash_waits_for_own_download_port_to_clear_before_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3995: after spd_dump reset returns, wait for own 1782:4d00 to leave sysfs before releasing host lock."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-5.4.4", "18d1", "4ee8", "SN1")])
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(mod, "_DOWNLOAD_PORT_POLL_INTERVAL", 0.01)

    events: List[str] = []
    real_flock = mod.fcntl.flock

    def spy_flock(fd, op):
        if op == mod.fcntl.LOCK_UN:
            # Unlock must happen only AFTER 1-5.4.4 is no longer 1782:4d00.
            remaining = mod.find_unisoc_download_ports(str(sysfs_base))
            events.append(f"unlock:remaining={remaining}")
        return real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", spy_flock)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    real_find = mod.find_unisoc_download_ports
    checks = {"n": 0}

    def transient_after_flash(base=None):
        hits = real_find(base)
        if adb_state["rebooted"] and "1-5.4.4" in hits:
            checks["n"] += 1
            if checks["n"] >= 3:
                # Simulate USB disconnect clearing 1782:4d00 after 2 polls.
                (sysfs_base / "1-5.4.4" / "idVendor").write_text("18d1\n", encoding="utf-8")
                (sysfs_base / "1-5.4.4" / "idProduct").write_text("4ee8\n", encoding="utf-8")
                return []
        return hits

    monkeypatch.setattr(mod, "find_unisoc_download_ports", transient_after_flash)

    def fake_run_spd_dump(*_a, **_kw):
        # Leave 1-5.4.4 momentarily in 1782:4d00 right as spd_dump reset returns.
        (sysfs_base / "1-5.4.4" / "idVendor").write_text("1782\n", encoding="utf-8")
        (sysfs_base / "1-5.4.4" / "idProduct").write_text("4d00\n", encoding="utf-8")
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    res = mod.run({"firmware_dir": str(ver_dir), "verify_root": False}, "SN1", mod.Progress(), [])
    assert res["metrics"]["post_version"] == "v36"
    assert checks["n"] >= 3
    assert "unlock:remaining=[]" in events


def test_v109_in_lock_gate_tolerates_transient_peer_reset_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3995 (Run 1656 job 209896): a peer's 1782:4d00 tail clearing within grace window must pass."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-5.4.4", "1782", "4d00"),
            ("1-5.2.3", "18d1", "4ee8", "SN1"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(mod, "_DOWNLOAD_PORT_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    real_find = mod.find_unisoc_download_ports
    polls = {"n": 0}

    def clearing_find(base=None):
        polls["n"] += 1
        if polls["n"] >= 3:
            peer_dir = sysfs_base / "1-5.4.4"
            if peer_dir.exists():
                for child in peer_dir.iterdir():
                    child.unlink()
                peer_dir.rmdir()
        return real_find(base)

    monkeypatch.setattr(mod, "find_unisoc_download_ports", clearing_find)

    def fake_run_spd_dump(*_a, **_kw):
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    res = mod.run(
        {
            "firmware_dir": str(ver_dir),
            "verify_root": False,
            "download_port_clear_wait_seconds": 1.0,
            "require_port_binding": False,
        },
        "SN1",
        mod.Progress(),
        [],
    )
    assert polls["n"] >= 3
    assert res["metrics"]["post_version"] == "v36"


def test_v109_in_lock_gate_still_rejects_persistent_orphan_port_after_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3995: in unbound mode, a genuine orphan 1782:4d00 persisting beyond grace must still fail-closed."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-5.4.4", "1782", "4d00"),
            ("1-5.2.3", "18d1", "4ee8", "SN1"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(mod, "_DOWNLOAD_PORT_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(mod.FlashRejectedError, match="1782:4d00") as exc_info:
        mod.run(
            {
                "firmware_dir": str(ver_dir),
                "verify_root": False,
                "download_port_clear_wait_seconds": 0.05,
                "require_port_binding": False,
            },
            "SN1",
            mod.Progress(),
            [],
        )
    assert exc_info.value.route and exc_info.value.route["version"] == "v36"
    assert spd_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls


def test_v1010_bound_mode_ignores_non_target_orphan_download_port_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3995 v1.0.10: with --usb-port binding enabled, a non-target orphan 1782:4d00 logs warning and proceeds."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-4.2.4", "1782", "4d00"),
            ("1-4.1.2", "18d1", "4ee8", "SN1"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    seen_ports: List[Optional[str]] = []

    def fake_run_spd_dump(cmd, *_a, expected_usb_port=None, **_kw):
        seen_ports.append(expected_usb_port)
        assert "--usb-port" in cmd and "1-4.1.2" in cmd
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)
    log: List[str] = []
    res = mod.run(
        {"firmware_dir": str(ver_dir), "verify_root": False, "boot_stabilize_seconds": 0},
        "SN1",
        mod.Progress(),
        log,
    )
    assert seen_ports == ["1-4.1.2"]
    assert res["metrics"]["post_version"] == "v36"
    assert res["metrics"]["non_target_download_ports_ignored"] == ["1-4.2.4"]
    assert any("non-target" in line and "1-4.2.4" in line for line in log)


def test_v1010_bound_mode_still_rejects_target_port_in_download_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3995 v1.0.10: even with port binding, target port itself in 1782:4d00 must fail-closed."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-4.1.2", "18d1", "4ee8", "SN1")])
    lock_file = tmp_path / "host.lock"
    adb_calls: List[List[str]] = []
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        adb_calls=adb_calls,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(mod, "_DOWNLOAD_PORT_POLL_INTERVAL", 0.01)

    def flip_target_to_download(progress, timeout_seconds=7200):
        (sysfs_base / "1-4.1.2" / "idVendor").write_text("1782\n", encoding="utf-8")
        (sysfs_base / "1-4.1.2" / "idProduct").write_text("4d00\n", encoding="utf-8")
        return os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

    monkeypatch.setattr(mod, "acquire_host_lock", flip_target_to_download)
    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(mod.FlashRejectedError, match="target Unisoc download port 1782:4d00"):
        mod.run(
            {"firmware_dir": str(ver_dir), "verify_root": False, "download_port_clear_wait_seconds": 0.03},
            "SN1",
            mod.Progress(),
            [],
        )
    assert spd_called["n"] == 0
    assert ["reboot", "autodloader"] not in adb_calls


def test_v1010_dry_run_bound_vs_unbound_download_port_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3995 v1.0.10: dry_run mirrors runtime gate — bound ignores sibling orphan, unbound rejects."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-4.2.4", "1782", "4d00"),
            ("1-4.1.2", "18d1", "4ee8", "SN1"),
        ],
    )
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    log: List[str] = []
    res = mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), log)
    assert res["metrics"]["dry_run"] is True
    assert res["metrics"]["non_target_download_ports_ignored"] == ["1-4.2.4"]
    assert res["metrics"]["hold_lock_until_boot"] is True
    assert res["metrics"]["boot_stabilize_seconds"] == 5.0
    assert any("1-4.2.4" in line for line in log)

    with pytest.raises(mod.FlashRejectedError, match="1782:4d00"):
        mod.run(
            {"firmware_dir": str(ver_dir), "dry_run": True, "require_port_binding": False},
            "SN1",
            mod.Progress(),
            [],
        )


def test_v1010_holds_host_lock_until_boot_completed_and_stabilize_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1.0.10: default hold_lock_until_boot=True keeps host lock until boot_completed=1 + stabilize settle."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-5.4.4", "18d1", "4ee8", "SN1")])
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    events: List[str] = []
    real_flock = mod.fcntl.flock

    def spy_flock(fd, op):
        if op == mod.fcntl.LOCK_UN:
            events.append("unlock")
        return real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", spy_flock)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: (
            events.append("lock") or os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
        ),
    )

    real_wait_boot = mod.wait_boot_completed

    def spy_wait_boot(*a, **kw):
        res_s = real_wait_boot(*a, **kw)
        events.append("boot_completed")
        return res_s

    monkeypatch.setattr(mod, "wait_boot_completed", spy_wait_boot)

    real_stabilize = mod._wait_boot_stabilize

    def spy_stabilize(sec, progress):
        out = real_stabilize(sec, progress)
        events.append(f"stabilized:{out}")
        return out

    monkeypatch.setattr(mod, "_wait_boot_stabilize", spy_stabilize)

    def fake_run_spd_dump(*_a, **_kw):
        events.append("spd_dump")
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    res = mod.run(
        {"firmware_dir": str(ver_dir), "verify_root": False, "boot_stabilize_seconds": 3.0},
        "SN1",
        mod.Progress(),
        [],
    )
    assert res["metrics"]["lock_held_until_boot"] is True
    assert res["metrics"]["boot_stabilize_seconds"] == 3.0
    assert events.index("lock") < events.index("spd_dump") < events.index("boot_completed")
    assert events.index("boot_completed") < events.index("stabilized:3.0") < events.index("unlock")


def test_v1010_releases_host_lock_when_wait_boot_completed_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1.0.10: host lock must always be released in finally if wait_boot_completed times out."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-5.4.4", "18d1", "4ee8", "SN1")])
    lock_file = tmp_path / "host.lock"
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    unlocked = {"count": 0}
    real_flock = mod.fcntl.flock

    def spy_flock(fd, op):
        if op == mod.fcntl.LOCK_UN:
            unlocked["count"] += 1
        return real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", spy_flock)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: 1.0)
    monkeypatch.setattr(
        mod,
        "wait_boot_completed",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boot timeout")),
    )

    with pytest.raises(RuntimeError, match="boot timeout"):
        mod.run({"firmware_dir": str(ver_dir), "verify_root": False}, "SN1", mod.Progress(), [])
    assert unlocked["count"] == 1


def test_v1010_hold_lock_until_boot_false_unlocks_before_boot_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1.0.10: hold_lock_until_boot=False opt-out releases host lock prior to wait_boot_completed."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-5.4.4", "18d1", "4ee8", "SN1")])
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    events: List[str] = []
    real_flock = mod.fcntl.flock

    def spy_flock(fd, op):
        if op == mod.fcntl.LOCK_UN:
            events.append("unlock")
        return real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", spy_flock)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    real_wait_boot = mod.wait_boot_completed

    def spy_wait_boot(*a, **kw):
        events.append("boot_wait")
        return real_wait_boot(*a, **kw)

    monkeypatch.setattr(mod, "wait_boot_completed", spy_wait_boot)

    def fake_run_spd_dump(*_a, **_kw):
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    res = mod.run(
        {"firmware_dir": str(ver_dir), "verify_root": False, "hold_lock_until_boot": False},
        "SN1",
        mod.Progress(),
        [],
    )
    assert res["metrics"]["lock_held_until_boot"] is False
    assert res["metrics"]["boot_stabilize_seconds"] == 0.0
    assert events.index("unlock") < events.index("boot_wait")


@pytest.mark.parametrize("bad_val", [-1, -0.5, "nan", "inf", True, "abc"])
def test_v1010_rejects_invalid_boot_stabilize_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_val: object
) -> None:
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-5.4.4", "18d1", "4ee8", "SN1")])
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    with pytest.raises(mod.FlashRejectedError, match="boot_stabilize_seconds"):
        mod.run(
            {"firmware_dir": str(ver_dir), "boot_stabilize_seconds": bad_val},
            "SN1",
            mod.Progress(),
            [],
        )


def test_v1010_acquire_host_lock_releases_and_closes_fd_when_stamp_raises(
    tmp_path: Path,
) -> None:
    """PR #4177 R1: BrokenPipeError on lock-held stamp must unlock & close fd so peers can lock."""
    import fcntl as _fcntl

    mod = _load_module()
    lock_file = tmp_path / "host.lock"

    class BrokenStampProgress(mod.Progress):
        def stamp(self, **fields: object) -> None:
            if fields.get("stage") == "lock-held":
                raise BrokenPipeError("stderr pipe closed")

    with pytest.raises(BrokenPipeError):
        mod.acquire_host_lock(BrokenStampProgress(), timeout_seconds=5, lock_path=str(lock_file))

    probe_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        _fcntl.flock(probe_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        _fcntl.flock(probe_fd, _fcntl.LOCK_UN)
    finally:
        os.close(probe_fd)


def test_v1010_run_closes_lock_fd_even_when_lock_un_raises_eio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #4177 R1: LOCK_UN raising EIO must not skip os.close(lock_fd); second fd must acquire lock."""
    import errno as _errno
    import fcntl as _fcntl

    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(tmp_path, [("1-5.4.4", "18d1", "4ee8", "SN1")])
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: mod.acquire_host_lock.__wrapped__(progress, timeout_seconds, str(lock_file))
        if hasattr(mod.acquire_host_lock, "__wrapped__")
        else (
            lambda fd: (_fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB) or fd)
        )(os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)),
    )

    real_flock = _fcntl.flock

    def flaky_unlock(fd, op):
        if op == _fcntl.LOCK_UN:
            raise OSError(_errno.EIO, "simulated unlock EIO")
        return real_flock(fd, op)

    monkeypatch.setattr(mod.fcntl, "flock", flaky_unlock)

    def fake_run_spd_dump(*_a, **_kw):
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    res = mod.run(
        {"firmware_dir": str(ver_dir), "verify_root": False, "boot_stabilize_seconds": 0},
        "SN1",
        mod.Progress(),
        [],
    )
    assert res["metrics"]["post_version"] == "v36"

    probe_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        real_flock(probe_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        real_flock(probe_fd, _fcntl.LOCK_UN)
    finally:
        os.close(probe_fd)


@pytest.mark.parametrize("require_port_binding", [True, False])
@pytest.mark.parametrize("dry_run", [True, False])
def test_v1010_unreadable_idproduct_eacces_fails_closed_all_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_port_binding: bool,
    dry_run: bool,
) -> None:
    """PR #4177 R1: EACCES reading idProduct must fail-closed (not silently treated as non-matching)."""
    import builtins
    import errno as _errno

    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-4.1.2", "18d1", "4ee8", "SN1"),
            ("1-4.2.4", "1782", "4d00"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )
    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    blocked_path = str(sysfs_base / "1-4.2.4" / "idProduct")
    real_open = builtins.open

    def guarded_open(path, *a, **kw):
        if os.fspath(path) == blocked_path:
            raise PermissionError(_errno.EACCES, "Permission denied", blocked_path)
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", guarded_open)

    with pytest.raises(mod.FlashRejectedError, match="unreadable"):
        mod.run(
            {
                "firmware_dir": str(ver_dir),
                "dry_run": dry_run,
                "require_port_binding": require_port_binding,
                "verify_root": False,
            },
            "SN1",
            mod.Progress(),
            [],
        )
    assert spd_called["n"] == 0


def test_v1010_disappearing_sysfs_node_enoent_is_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #4177 R1: transient ENOENT on a disappearing sibling USB node must not falsely fail-closed."""
    import builtins

    mod = _load_module()
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-4.1.2", "18d1", "4ee8", "SN1"),
            ("1-4.9.9", "18d1", "4ee8"),
        ],
    )
    gone_path = str(sysfs_base / "1-4.9.9" / "idProduct")
    real_open = builtins.open

    def flaky_open(path, *a, **kw):
        if os.fspath(path) == gone_path:
            raise FileNotFoundError("device unplugged mid-scan")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", flaky_open)
    assert mod.find_unisoc_download_ports(str(sysfs_base)) == []


def test_v1010_non_target_orphan_warning_visible_on_stderr_and_metrics_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PR #4177 R1: both dry_run and run success paths emit collectible stderr PROGRESS warning and metrics.warnings."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-4.2.4", "1782", "4d00"),
            ("1-4.1.2", "18d1", "4ee8", "SN1"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    def fake_run_spd_dump(*_a, **_kw):
        adb_state["rebooted"] = True
        clock["now"] += 30.0
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_run_spd_dump)

    # 1) dry_run via main()
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SN1")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"firmware_dir": str(ver_dir), "dry_run": True}))
    monkeypatch.setattr(mod.sys, "argv", ["flash_firmware_unisoc_bsl.py"])
    mod.main()
    cap_dry = capsys.readouterr()
    dry_out = json.loads(cap_dry.out.strip())
    assert dry_out["success"] is True
    assert dry_out["metrics"]["non_target_download_ports_ignored"] == ["1-4.2.4"]
    assert any("1-4.2.4" in w for w in dry_out["metrics"]["warnings"])
    assert "download-port-warning" in cap_dry.err and "1-4.2.4" in cap_dry.err

    # 2) run via main()
    monkeypatch.setenv(
        "STP_STEP_PARAMS",
        json.dumps({"firmware_dir": str(ver_dir), "verify_root": False, "boot_stabilize_seconds": 0}),
    )
    mod.main()
    cap_run = capsys.readouterr()
    run_out = json.loads(cap_run.out.strip())
    assert run_out["success"] is True
    assert run_out["metrics"]["non_target_download_ports_ignored"] == ["1-4.2.4"]
    assert any("1-4.2.4" in w for w in run_out["metrics"]["warnings"])
    assert "download-port-warning" in cap_run.err and "1-4.2.4" in cap_run.err


def test_v1010_dry_run_rejects_usb_port_drift_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #4177 R1: dry_run must never return a usb_port that was ignored as non-target 1782:4d00."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-2", "18d1", "4ee8", "SN1"),
            ("1-3", "1782", "4d00"),
        ],
    )
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")

    real_check = mod.check_no_unisoc_download_port

    def drift_during_gate(*a, **kw):
        res = real_check(*a, **kw)
        # Simulate SN1 moving from 1-2 to 1-3 right after gate ignored 1-3
        (sysfs_base / "1-2" / "serial").unlink()
        (sysfs_base / "1-3" / "serial").write_text("SN1\n", encoding="utf-8")
        return res

    monkeypatch.setattr(mod, "check_no_unisoc_download_port", drift_during_gate)

    with pytest.raises(mod.FlashRejectedError, match="target USB port changed during dry-run"):
        mod.run({"firmware_dir": str(ver_dir), "dry_run": True}, "SN1", mod.Progress(), [])


@pytest.mark.parametrize("drift_phase", ["boot_id_read", "download_port_gate"])
def test_v1010_runtime_rejects_usb_port_drift_after_lock_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift_phase: str
) -> None:
    """PR #4177 R1: USB port drift during boot_id read or download-port gate must fail-closed before spd_dump."""
    mod = _load_module()
    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    sysfs_base = _make_fake_sysfs(
        tmp_path,
        [
            ("1-2", "18d1", "4ee8", "SN1"),
            ("1-4", "18d1", "4ee8"),
        ],
    )
    lock_file = tmp_path / "host.lock"
    _install_fake_adb(
        monkeypatch,
        mod,
        props={"ro.product.model": "MLY-LX2", "ro.product.device": "MLY", "ro.product.name": "MLY-LX2"},
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs_base))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/tmp/spd_dump")
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200: os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666),
    )

    def _move_sn1_to_1_4() -> None:
        (sysfs_base / "1-2" / "serial").unlink()
        (sysfs_base / "1-4" / "serial").write_text("SN1\n", encoding="utf-8")

    if drift_phase == "boot_id_read":
        real_capture = mod._capture_pre_reboot_evidence

        def drift_capture(*a, **kw):
            out = real_capture(*a, **kw)
            _move_sn1_to_1_4()
            return out

        monkeypatch.setattr(mod, "_capture_pre_reboot_evidence", drift_capture)
    else:
        real_gate = mod.check_no_unisoc_download_port

        def drift_gate(*a, **kw):
            out = real_gate(*a, **kw)
            _move_sn1_to_1_4()
            return out

        monkeypatch.setattr(mod, "check_no_unisoc_download_port", drift_gate)

    spd_called = {"n": 0}
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a, **_kw: spd_called.__setitem__("n", spd_called["n"] + 1))

    with pytest.raises(mod.FlashRejectedError, match="target USB port changed"):
        mod.run({"firmware_dir": str(ver_dir), "verify_root": False}, "SN1", mod.Progress(), [])
    assert spd_called["n"] == 0


@pytest.mark.parametrize("wall_jump_seconds", [-120.0, +120.0])
def test_v1010_wait_boot_completed_uses_monotonic_clock_and_bounds_adb_timeout(
    monkeypatch: pytest.MonkeyPatch, wall_jump_seconds: float
) -> None:
    """PR #4177 R1: wait_boot_completed must follow monotonic clock (immune to wall jumps) and bound run_adb timeout."""
    mod = _load_module()
    state = {"mono": 100.0, "wall": 1_700_000_000.0, "calls": 0}
    adb_timeouts: List[float] = []

    def fake_monotonic() -> float:
        return state["mono"]

    def fake_wall() -> float:
        return state["wall"]

    def fake_sleep(sec: float) -> None:
        state["mono"] += sec
        state["wall"] += sec

    def fake_run_adb(adb_bin, serial, args, timeout=30):
        adb_timeouts.append(float(timeout))
        state["calls"] += 1
        if state["calls"] == 1:
            # Trigger wall-clock rollback or forward jump after first poll
            state["wall"] += wall_jump_seconds
        state["mono"] += 0.5
        return 1, "", "offline"

    monkeypatch.setattr(mod, "time", SimpleNamespace(time=fake_wall, monotonic=fake_monotonic, sleep=fake_sleep))
    monkeypatch.setattr(mod, "run_adb", fake_run_adb)

    t0 = state["mono"]
    with pytest.raises(RuntimeError, match="did not reach sys.boot_completed=1 within 4s"):
        mod.wait_boot_completed("adb", "SN1", mod.Progress(), 4)
    elapsed_mono = round(state["mono"] - t0, 3)
    assert elapsed_mono == 4.0
    assert adb_timeouts
    assert all(0 < t <= 4.0 for t in adb_timeouts)
    assert adb_timeouts[-1] <= 1.5

