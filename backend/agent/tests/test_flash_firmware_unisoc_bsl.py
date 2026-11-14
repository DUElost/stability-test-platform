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


def test_acquire_host_lock_emits_lock_wait_and_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    lock_file = tmp_path / "test.lock"
    progress = mod.Progress()
    stamps: list[dict] = []
    monkeypatch.setattr(progress, "stamp", lambda **kw: stamps.append(kw))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

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
