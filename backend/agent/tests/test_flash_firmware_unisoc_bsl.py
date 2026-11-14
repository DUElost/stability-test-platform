"""Unit tests for backend/agent/scripts/flash_firmware_unisoc_bsl/."""

from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

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


def test_sparse_trimmed_size_ignores_trailing_dont_care(tmp_path: Path) -> None:
    mod = _load_module()
    sparse_file = tmp_path / "super.img"
    # 5000 blocks of FILL/RAW (20,480,000 bytes -> aligns up to 32 MB),
    # followed by 100000 blocks of CHUNK_DONT_CARE (0xCAC3)
    _make_sparse_image(
        sparse_file,
        [
            (0xCAC2, 5000, b"\x00\x00\x00\x00"),
            (0xCAC3, 100000, b""),
        ],
    )
    trimmed = mod.get_sparse_trimmed_size(str(sparse_file))
    assert trimmed == 32 * 1024 * 1024


def test_build_spd_dump_args_protects_nv_and_includes_scheme2_erases(tmp_path: Path) -> None:
    mod = _load_module()
    cmd = mod.build_spd_dump_args("/usr/local/bin/spd_dump", str(tmp_path), str(tmp_path / "super_trimmed.img"))

    joined = " ".join(cmd)
    for protected in ("prodnv", "calinv", "miscdata", "persist", "splloader", "nr_factorynv", "nr_downloadnv_a"):
        assert protected not in joined

    for erased in ("metadata", "sysdumpdb", "misc", "sd_klog", "uboot_log"):
        idx = cmd.index(erased)
        assert cmd[idx - 1] == "erase_part"

    assert "keep_charge" in cmd
    assert "0x65008000" in cmd
    assert "0xBDFFFE00" in cmd
    assert cmd[-1] == "reset"


def test_resolve_firmware_images_dir_accepts_version_or_images_dir(tmp_path: Path) -> None:
    mod = _load_module()
    images = tmp_path / "P532AA" / "v117" / "images"
    images.mkdir(parents=True)
    (images / "fdl1-sign.bin").write_bytes(b"fdl1")

    assert mod.resolve_firmware_images_dir({"firmware_dir": str(images)}, "adb", "SN1") == str(images)
    assert mod.resolve_firmware_images_dir({"firmware_dir": str(images.parent)}, "adb", "SN1") == str(images)


def _patch_advancing_clock(monkeypatch: pytest.MonkeyPatch, mod, *, start: float = 1_000_000.0):
    state = {"now": start}

    def fake_time() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
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


def test_run_acquires_host_lock_before_preparing_local_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    order: list[str] = []

    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda: "/usr/local/bin/spd_dump")
    monkeypatch.setattr(mod, "resolve_firmware_images_dir", lambda *_a: str(tmp_path))
    monkeypatch.setattr(mod, "getprop", lambda *_a: "userdebug")

    lock_file = tmp_path / "host.lock"

    def fake_acquire(progress, timeout_seconds=7200):
        order.append("acquire_host_lock")
        return os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

    def fake_prepare(fw_dir, cache_dir, progress):
        order.append("prepare_local_cache")
        return str(tmp_path), str(tmp_path / "super.img")

    monkeypatch.setattr(mod, "acquire_host_lock", fake_acquire)
    monkeypatch.setattr(mod, "prepare_local_cache", fake_prepare)
    monkeypatch.setattr(mod, "build_spd_dump_args", lambda *_a: ["spd_dump"])
    monkeypatch.setattr(mod, "run_spd_dump", lambda *_a: 1.0)
    monkeypatch.setattr(mod, "wait_boot_completed", lambda *_a: 1.0)

    res = mod.run({"verify_root": False}, "SN1", mod.Progress(), [])
    assert res["metrics"]["duration_seconds"] >= 0
    assert order == ["acquire_host_lock", "prepare_local_cache"]


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

    for protected in ("splloader", "l_fixnv1_a", "l_runtimenv1", "l_deltanv_a", "prodnv", "persist", "miscdata"):
        assert protected not in written_parts


def test_prepare_local_cache_isolates_distinct_firmware_dirs(tmp_path: Path) -> None:
    mod = _load_module()
    cache_root = tmp_path / "ssd_cache"

    def _make_fw(name: str, fdl2_name: str, fdl2_bytes: bytes, boot_bytes: bytes, sparse_blocks: int) -> Path:
        fw = tmp_path / name / "images"
        (fw / "raw").mkdir(parents=True)
        (fw / "fdl1-sign.bin").write_bytes(b"fdl1")
        (fw / fdl2_name).write_bytes(fdl2_bytes)
        (fw / "userdata.img").write_bytes(b"\x00" * 4096)
        (fw / "boot.img").write_bytes(boot_bytes)
        sparse_super = fw / "super.img"
        _make_sparse_image(sparse_super, [(0xCAC2, sparse_blocks, b"\x11\x11\x11\x11"), (0xCAC3, 5000, b"")])
        raw_super = fw / "raw" / "super.img"
        with open(raw_super, "wb") as fh:
            fh.write(b"S" * (sparse_blocks * 4096))
            fh.truncate(20 * 1024 * 1024)
        plan = {
            "fdl": {
                "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
                "fdl2": {"file": fdl2_name, "base": "0x9EFFFE00"},
            },
            "erases": ["misc", "metadata"],
            "entries": [
                {"id": "UserData", "file": "userdata.img", "sparse": False, "partitions": ["userdata"], "flush_type": "YAFFS_IMG2", "preserve": True},
                {"id": "BOOT", "file": "boot.img", "sparse": False, "partitions": ["boot_a"], "flush_type": "CODE2"},
                {"id": "Super", "file": "super.img", "sparse": True, "raw": "raw/super.img", "partitions": ["super"], "flush_type": "YAFFS_IMG2"},
            ],
        }
        (fw / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        return fw

    fw_a = _make_fw("P532AA_v119", "edk2-fdl2-sign.bin", b"A" * 16, b"BOOT_A", 100)
    fw_b = _make_fw("P525AA_v36", "lk-fdl2-sign.bin", b"B" * 24, b"BOOT_B_DIFF", 200)

    local_a, super_a = mod.prepare_local_cache(str(fw_a), str(cache_root), mod.Progress())
    local_b, super_b = mod.prepare_local_cache(str(fw_b), str(cache_root), mod.Progress())

    assert local_a != local_b
    assert Path(super_a).parent != Path(super_b).parent
    assert (Path(local_a) / "boot.img").read_bytes() == b"BOOT_A"
    assert (Path(local_b) / "boot.img").read_bytes() == b"BOOT_B_DIFF"
    assert (Path(local_b) / "lk-fdl2-sign.bin").read_bytes() == b"B" * 24
