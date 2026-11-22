"""B10 U1 / ADR-0064 D1：firmware_manifest 契约与上架摘要工具。"""

from __future__ import annotations

import importlib.util
import json
import os
import struct
import sys
from pathlib import Path

import pytest

from backend.agent.contracts import firmware_manifest as fm

REPO_ROOT = Path(__file__).resolve().parents[3]
STAGE_TOOL = REPO_ROOT / "tools" / "dev" / "stage_firmware_manifest.py"


def _load_stage():
    spec = importlib.util.spec_from_file_location("stage_firmware_manifest_u1", STAGE_TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


def _sha(path: Path) -> str:
    return fm.file_sha256(str(path))


def test_content_digest_independent_of_executable_bit(tmp_path: Path):
    root = tmp_path / "fw"
    root.mkdir()
    f = root / "a.bin"
    _write(f, b"payload")
    entry = fm.build_file_entry(str(root), "a.bin")
    digest_before = fm.compute_content_digest([entry])
    os.chmod(f, 0o755)
    entry_exec = fm.build_file_entry(str(root), "a.bin")
    digest_after = fm.compute_content_digest([entry_exec])
    assert digest_before == digest_after
    assert entry["sha256"] == entry_exec["sha256"]


def test_reject_parent_traversal_and_escaping_symlink(tmp_path: Path):
    root = tmp_path / "fw"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    _write(outside, b"secret")
    with pytest.raises(fm.FirmwareManifestError):
        fm.normalize_relpath("../outside.bin")
    with pytest.raises(fm.FirmwareManifestError):
        fm.validate_relpath_within_root(str(root), "../outside.bin")
    link = root / "escape.bin"
    link.symlink_to(outside)
    with pytest.raises(fm.FirmwareManifestError):
        fm.build_file_entry(str(root), "escape.bin")


def _make_mtk_v5(root: Path) -> None:
    _write(
        root / "manifest.json",
        json.dumps(
            {
                "family": "MOD",
                "version": "v5-test",
                "version_prop": "ro.build.version.incremental",
                "tool_generation": "v5",
                "da_file": "DA_TEST.bin",
                "scatter_file": "MT_TEST_scatter.txt",
            }
        ),
    )
    _write(root / "DA_TEST.bin", b"da")
    _write(
        root / "MT_TEST_scatter.txt",
        """
- partition_index: SYS0
  partition_name: preloader
  file_name: preloader.bin
  is_download: true

- partition_index: SYS1
  partition_name: boot
  file_name: boot.img
  is_download: true

- partition_index: SYS2
  partition_name: unused
  file_name: skip.img
  is_download: false
""".lstrip(),
    )
    _write(root / "preloader.bin", b"pl")
    _write(root / "boot.img", b"boot")
    _write(root / "skip.img", b"skip")
    _write(root / "APDB_MT_TEST", b"apdb")
    _write(root / "debug.elf", b"elf")
    _write(root / "boot.img.sig", b"sig")
    _write(root / "checksum.ini", b"[checksum]\n")


def _make_mtk_v6(root: Path) -> None:
    _write(
        root / "manifest.json",
        json.dumps(
            {
                "family": "NX3",
                "version": "v6-test",
                "version_prop": "ro.product.build.fingerprint",
                "tool_generation": "v6",
                "flash_xml_file": "download_agent/flash.xml",
            }
        ),
    )
    da = root / "download_agent"
    _write(da / "DA_BR.bin", b"dabr")
    _write(da / "MT6835_loadregion0.bin", b"lr0")
    _write(
        da / "flash.xml",
        """<?xml version="1.0"?>
<flash-mode>
  <dagent>DA_BR.bin</dagent>
  <scatter>../MT6835_Android_scatter.xml</scatter>
</flash-mode>
""",
    )
    _write(
        root / "MT6835_Android_scatter.xml",
        """<?xml version="1.0"?>
<root>
  <storage_type name="UFS">
    <partition_index>
      <partition_name>boot_a</partition_name>
      <file_name>boot.img</file_name>
      <is_download>true</is_download>
    </partition_index>
    <partition_index>
      <partition_name>dbg</partition_name>
      <file_name>APDB_MT6835</file_name>
      <is_download>true</is_download>
    </partition_index>
    <partition_index>
      <partition_name>sym</partition_name>
      <file_name>P535AA.elf</file_name>
      <is_download>true</is_download>
    </partition_index>
    <partition_index>
      <partition_name>skip</partition_name>
      <file_name>unused.img</file_name>
      <is_download>false</is_download>
    </partition_index>
  </storage_type>
</root>
""",
    )
    _write(root / "boot.img", b"bootv6")
    _write(root / "unused.img", b"unused")
    _write(root / "APDB_MT6835", b"apdb")
    _write(root / "P535AA.elf", b"elf")
    _write(root / "boot.img.sig", b"sigv6")


def _make_sparse(path: Path, *, data_blocks: int = 2, dont_care_blocks: int = 4, blk: int = 4096) -> None:
    """最小合法 sparse：RAW 数据块 + DONT_CARE 尾块。"""
    chunks = []
    # RAW chunk
    raw = b"A" * (data_blocks * blk)
    chunks.append((0xCAC1, data_blocks, 12 + len(raw), raw))
    # DONT_CARE
    chunks.append((0xCAC3, dont_care_blocks, 12, b""))
    total_chunks = len(chunks)
    total_blocks = data_blocks + dont_care_blocks
    hdr = struct.pack(
        "<IHHHHIIII",
        0xED26FF3A,
        1,
        0,
        28,
        12,
        blk,
        total_blocks,
        total_chunks,
        0,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(hdr)
        for ctype, cblks, csz, payload in chunks:
            fh.write(struct.pack("<HHII", ctype, 0, cblks, csz))
            fh.write(payload)


def _make_unisoc_fastboot_only(root: Path) -> None:
    _write(
        root / "manifest.json",
        json.dumps(
            {
                "family": "A57",
                "version": "B19",
                "version_prop": "ro.build.sw_internal_version",
            }
        ),
    )
    plan = {
        "entries": [
            {
                "id": "SPLLoaderEMMC",
                "file": "u-boot-spl-16k-emmc-sign.bin",
                "partitions": ["splloader"],
            },
            {
                "id": "Boot",
                "file": "boot.img",
                "partitions": ["boot_a"],
            },
            {
                "id": "Super",
                "file": "super.img",
                "raw": "raw/super.img",
                "sparse": True,
                "partitions": ["super"],
            },
            {
                "id": "UserData",
                "file": "userdata.img",
                "preserve": True,
                "partitions": ["userdata"],
            },
            {
                "id": "FDL",
                "file": "fdl1-sign.bin",
                "flush_type": "FDL",
                "partitions": [],
            },
        ]
    }
    _write(root / "images" / "plan.json", json.dumps(plan))
    _write(root / "images" / "u-boot-spl-16k-emmc-sign.bin", b"spl")
    _write(root / "images" / "boot.img", b"boot")
    _make_sparse(root / "images" / "super.img")
    # 展开态 raw（比 sparse 大）
    _write(root / "images" / "raw" / "super.img", b"R" * (2 * 1024 * 1024))
    _write(root / "images" / "userdata.img", b"ud")
    _write(root / "images" / "fdl1-sign.bin", b"fdl1")
    _write(root / "images" / "orphan.img", b"orphan")


def _make_unisoc_fastboot_bsl(root: Path) -> None:
    _write(
        root / "manifest.json",
        json.dumps(
            {
                "family": "P525AA",
                "version": "V36",
                "version_prop": "ro.build.sw_internal_version",
            }
        ),
    )
    plan = {
        "fdl": {
            "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
            "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
        },
        "erases": ["metadata"],
        "entries": [
            {
                "id": "FDL",
                "file": "fdl1-sign.bin",
                "flush_type": "FDL",
                "partitions": [],
            },
            {
                "id": "FDL2",
                "file": "lk-fdl2-sign.bin",
                "flush_type": "NAND_FDL",
                "partitions": [],
            },
            {
                "id": "SPLLoaderEMMC",
                "file": "u-boot-spl-16k-emmc-sign.bin",
                "partitions": ["splloader"],
            },
            {
                "id": "Boot",
                "file": "boot.img",
                "partitions": ["boot_a"],
            },
            {
                "id": "Cache",
                "file": "cache.img",
                "raw": "raw/cache.img",
                "sparse": True,
                "partitions": ["cache"],
            },
            {
                "id": "Super",
                "file": "super.img",
                "raw": "raw/super.img",
                "sparse": True,
                "partitions": ["super"],
            },
            {
                "id": "UserData",
                "file": "userdata.img",
                "preserve": True,
                "partitions": ["userdata"],
            },
            {
                "id": "ProdNV",
                "file": "prodnv.img",
                "preserve": True,
                "partitions": ["prodnv"],
            },
        ],
    }
    _write(root / "images" / "plan.json", json.dumps(plan))
    for name, blob in [
        ("fdl1-sign.bin", b"fdl1"),
        ("lk-fdl2-sign.bin", b"fdl2"),
        ("u-boot-spl-16k-emmc-sign.bin", b"spl"),
        ("boot.img", b"boot"),
        ("cache.img", b"cache-sparse"),
        ("userdata.img", b"ud"),
        ("prodnv.img", b"nv"),
    ]:
        _write(root / "images" / name, blob)
    _make_sparse(root / "images" / "super.img", data_blocks=2, dont_care_blocks=8, blk=4096)
    # raw/super：前若干字节有数据，后部为大尾
    raw = (b"S" * (2 * 4096)) + (b"\x00" * (16 * 1024 * 1024))
    _write(root / "images" / "raw" / "super.img", raw)
    _write(root / "images" / "raw" / "cache.img", b"cache-raw")


def test_stage_mtk_v5_and_v6(tmp_path: Path):
    stage = _load_stage()
    v5 = tmp_path / "mtk-v5"
    v5.mkdir()
    _make_mtk_v5(v5)
    result = stage.stage_version_dir(v5)
    files = {e["relpath"] for e in result["profiles"]["mtk"]["files"]}
    assert "manifest.json" in files
    assert "DA_TEST.bin" in files
    assert "boot.img" in files
    assert "boot.img.sig" in files
    assert "checksum.ini" in files
    assert "skip.img" not in files
    assert "APDB_MT_TEST" not in files
    assert "debug.elf" not in files

    v6 = tmp_path / "mtk-v6"
    v6.mkdir()
    _make_mtk_v6(v6)
    result6 = stage.stage_version_dir(v6)
    files6 = {e["relpath"] for e in result6["profiles"]["mtk"]["files"]}
    assert "download_agent/DA_BR.bin" in files6
    assert "download_agent/MT6835_loadregion0.bin" in files6
    assert "download_agent/flash.xml" in files6
    assert "boot.img.sig" in files6
    assert "boot.img" in files6
    assert "APDB_MT6835" not in files6
    assert "P535AA.elf" not in files6
    assert "unused.img" not in files6


def test_stage_unisoc_fastboot_only_excludes_sparse_and_fdl(tmp_path: Path):
    stage = _load_stage()
    root = tmp_path / "a57"
    root.mkdir()
    _make_unisoc_fastboot_only(root)
    result = stage.stage_version_dir(root)
    assert set(result["profiles"]) == {"unisoc_fastboot"}
    files = {e["relpath"] for e in result["profiles"]["unisoc_fastboot"]["files"]}
    assert "images/raw/super.img" in files
    assert "images/super.img" not in files
    assert "images/fdl1-sign.bin" not in files
    assert "images/userdata.img" not in files  # preserve
    assert "images/orphan.img" not in files
    assert "images/boot.img" in files
    assert "images/u-boot-spl-16k-emmc-sign.bin" in files
    assert not (root / "images" / "bsl" / "super_trimmed.img").exists()


def test_stage_unisoc_fastboot_and_bsl_trimmed_super(tmp_path: Path):
    stage = _load_stage()
    root = tmp_path / "p525"
    root.mkdir()
    _make_unisoc_fastboot_bsl(root)
    raw_super = root / "images" / "raw" / "super.img"
    sparse_super = root / "images" / "super.img"
    raw_before = raw_super.read_bytes()
    sparse_before = sparse_super.read_bytes()
    raw_mtime = raw_super.stat().st_mtime_ns
    sparse_mtime = sparse_super.stat().st_mtime_ns

    result = stage.stage_version_dir(root)
    assert set(result["profiles"]) == {"unisoc_fastboot", "unisoc_bsl"}

    fb = {e["relpath"] for e in result["profiles"]["unisoc_fastboot"]["files"]}
    assert "images/raw/super.img" in fb
    assert "images/super.img" not in fb
    assert "images/fdl1-sign.bin" not in fb
    assert "images/bsl/super_trimmed.img" not in fb

    bsl = {e["relpath"] for e in result["profiles"]["unisoc_bsl"]["files"]}
    assert "images/bsl/super_trimmed.img" in bsl
    assert "images/fdl1-sign.bin" in bsl
    assert "images/lk-fdl2-sign.bin" in bsl
    assert "images/userdata.img" in bsl
    assert "images/raw/super.img" not in bsl
    assert "images/super.img" not in bsl
    # cache：BSL 口径 raw if (sparse and raw) else file
    assert "images/raw/cache.img" in bsl
    assert "images/cache.img" not in bsl
    # prodnv preserve → 不进 BSL 可写集
    assert "images/prodnv.img" not in bsl

    trimmed = root / "images" / "bsl" / "super_trimmed.img"
    assert trimmed.is_file()
    expected = stage.get_sparse_trimmed_size(sparse_super)
    assert expected is not None
    expected = min(expected, raw_super.stat().st_size)
    assert trimmed.stat().st_size == expected
    assert trimmed.read_bytes() == raw_before[:expected]

    # 源镜像未被修改
    assert raw_super.read_bytes() == raw_before
    assert sparse_super.read_bytes() == sparse_before
    assert raw_super.stat().st_mtime_ns == raw_mtime
    assert sparse_super.stat().st_mtime_ns == sparse_mtime

    # 原子写入 staging_manifest.json
    staging = root / "staging_manifest.json"
    assert staging.is_file()
    loaded = fm.load_staging_manifest(str(staging))
    assert loaded["profiles"]["unisoc_bsl"]["content_digest"].startswith("sha256:")


def test_first_seal_is_additive_only(tmp_path: Path):
    stage = _load_stage()
    root = tmp_path / "seal"
    root.mkdir()
    _make_unisoc_fastboot_only(root)
    before = {p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    stage.stage_version_dir(root)
    after_files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    new_files = after_files - set(before)
    assert new_files == {"staging_manifest.json"}
    for rel, mtime in before.items():
        assert (root / rel).stat().st_mtime_ns == mtime
