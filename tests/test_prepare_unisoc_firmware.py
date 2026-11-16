"""Regression tests for tools/dev/prepare_unisoc_firmware.py.

The sparse expander is the risky part: a FILL chunk written with the wrong
length corrupts super.img and the device falls back to fastboot (observed on
2026-10-17). These tests pin the chunk semantics.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest


_TOOL = Path(__file__).resolve().parent.parent / "tools" / "dev" / "prepare_unisoc_firmware.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("prepare_unisoc_under_test", _TOOL)
    assert spec and spec.loader, f"cannot locate {_TOOL}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sparse(chunks, blk=4096, total_blks=None):
    total = sum(c[2] for c in chunks) if total_blks is None else total_blks
    header = struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, blk, total, len(chunks), 0)
    body = b""
    for ctype, payload, nblk in chunks:
        body += struct.pack("<HHII", ctype, 0, nblk, 12 + len(payload)) + payload
    return header + body


def test_unsparse_handles_fill_dont_care_and_raw(tmp_path, tool):
    blk = 4096
    chunks = [
        (tool.CHUNK_FILL, b"\xab\xab\xab\xab", 1),
        (tool.CHUNK_DONT_CARE, b"", 1),
        (tool.CHUNK_RAW, b"R" * blk, 1),
    ]
    src = tmp_path / "super.sparse"
    src.write_bytes(_sparse(chunks))
    dst = tmp_path / "super.raw"

    tool.unsparse(str(src), str(dst))
    data = dst.read_bytes()

    assert len(data) == 3 * blk
    assert data[:blk] == b"\xab" * blk          # FILL covers blk_sz * chunk_sz bytes
    assert data[blk:2 * blk] == b"\x00" * blk   # DONT_CARE stays a hole
    assert data[2 * blk:] == b"R" * blk         # RAW copies payload verbatim


def test_parse_scheme_maps_file_id_to_partition(tmp_path, tool):
    xml = tmp_path / "scheme.xml"
    xml.write_text(
        "<BMAConfig><SchemeList><Scheme name='s'>"
        "<File><ID>BOOT</ID><Type>CODE2</Type><Block id='boot_a'/></File>"
        "<File><ID>Super</ID><Type>YAFFS_IMG2</Type><Block id='super'/></File>"
        "</Scheme></SchemeList></BMAConfig>",
        encoding="utf-8",
    )
    mapping = tool.parse_scheme(str(xml))
    assert mapping["BOOT"]["partitions"] == ["boot_a"]
    assert mapping["Super"]["type"] == "YAFFS_IMG2"


def test_build_plan_marks_device_unique_entries_preserve(tmp_path, tool):
    images = tmp_path / "images"
    images.mkdir()
    names = {
        "NV_LTE": "lte_nv.bin",
        "Modem_LTE_DELTANV": "lte_deltanv.bin",
        "NV_NR": "nr_nv.bin",
        "Modem_NR_DELTANV": "nr_deltanv.bin",
        "NV_CALI_NR": "nr_cali.bin",
        "BOOT": "boot.img",
    }
    for name in names.values():
        (images / name).write_bytes(b"\x00\x00\x00\x00")
    blocks = {
        "NV_LTE": ("l_fixnv1_a", "NV_COMM_SLOT"),
        "Modem_LTE_DELTANV": ("l_deltanv_a", "CODE2"),
        "NV_NR": ("nr_downloadnv_a", "NV_COMM"),
        "Modem_NR_DELTANV": ("nr_deltanv_a", "CODE2"),
        "NV_CALI_NR": ("calinv", "CODE2"),
        "BOOT": ("boot_a", "CODE2"),
    }
    pac = {"entries": [
        {"id": fid, "file": names[fid], "size": 4, "type": 1} for fid in names
    ]}
    scheme = {fid: {"partitions": [blocks[fid][0]], "type": blocks[fid][1]}
              for fid in names}

    plan = tool.build_plan(pac, str(images), scheme)
    by_id = {entry["id"]: entry for entry in plan["entries"]}

    for fid in ("NV_LTE", "Modem_LTE_DELTANV", "NV_NR", "Modem_NR_DELTANV", "NV_CALI_NR"):
        assert by_id[fid]["preserve"] is True, fid
        assert by_id[fid]["partitions"] == [blocks[fid][0]], fid
    assert "preserve" not in by_id["BOOT"]
    assert by_id["BOOT"]["partitions"] == ["boot_a"]


def test_parse_scheme_and_build_plan_capture_fdl_base_and_erases(tmp_path, tool):
    xml = tmp_path / "P525AAN.xml"
    xml.write_text(
        "<BMAConfig><SchemeList><Scheme name='P525AAN'>"
        "<File><ID>FDL</ID><Type>FDL</Type><Block><Base>0x65000800</Base><Size>0x0</Size></Block></File>"
        "<File><ID>FDL2</ID><Type>NAND_FDL</Type><Block><Base>0x9EFFFE00</Base><Size>0x0</Size></Block></File>"
        "<File><ID>EraseSPL</ID><Type>EraseFlash2</Type><Block id='splloader'><Base>0x0</Base></Block></File>"
        "<File><ID>EraseMisc</ID><Type>EraseFlash2</Type><Block id='misc'><Base>0x0</Base></Block></File>"
        "<File><ID>EraseMetadata</ID><Type>EraseFlash2</Type><Block id='metadata'/></File>"
        "<File><ID>BOOT</ID><Type>CODE2</Type><Block id='boot_a'/></File>"
        "</Scheme></SchemeList></BMAConfig>",
        encoding="utf-8",
    )
    images = tmp_path / "images"
    images.mkdir()
    for fname in ("fdl1-sign.bin", "lk-fdl2-sign.bin", "boot.img"):
        (images / fname).write_bytes(b"\x01\x02\x03\x04")

    scheme = tool.parse_scheme(str(xml))
    assert scheme["FDL"]["base"] == "0x65000800"
    assert scheme["FDL2"]["base"] == "0x9EFFFE00"
    assert "base" not in scheme["EraseMisc"]  # 0x0 ignored

    pac = {
        "entries": [
            {"id": "FDL", "file": "fdl1-sign.bin", "size": 4, "type": 1},
            {"id": "FDL2", "file": "lk-fdl2-sign.bin", "size": 4, "type": 1},
            {"id": "BOOT", "file": "boot.img", "size": 4, "type": 1},
        ]
    }
    plan = tool.build_plan(pac, str(images), scheme)
    assert plan["fdl"] == {
        "fdl1": {"file": "fdl1-sign.bin", "base": "0x65000800"},
        "fdl2": {"file": "lk-fdl2-sign.bin", "base": "0x9EFFFE00"},
    }
    assert plan["erases"] == ["splloader", "misc", "metadata"]


def _pack_u16(text: str, n_chars: int = 256) -> bytes:
    encoded = text.encode("utf-16-le")
    return encoded[: 2 * n_chars].ljust(2 * n_chars, b"\x00")


def _build_pac(
    path: Path,
    entries: list[tuple[str, str, int, bytes]],
    *,
    product: str = "P525AAN",
    firmware: str = "P525AAN_V1.0",
) -> Path:
    """Create a minimal valid Unisoc PAC archive for CLI tests."""
    header_size = 2124
    entry_size = 2580
    magic = ~0x50005 & 0xFFFFFFFF

    hdr = bytearray(header_size)
    hdr[52 : 52 + 512] = _pack_u16(product)
    hdr[564 : 564 + 512] = _pack_u16(firmware)
    struct.pack_into("<II", hdr, 1076, len(entries), header_size)
    struct.pack_into("<I", hdr, 2116, magic)

    data_offset = header_size + len(entries) * entry_size
    directory = bytearray()
    payloads = bytearray()
    for eid, fname, typ, data in entries:
        rec = bytearray(entry_size)
        rec[4 : 4 + 512] = _pack_u16(eid)
        rec[516 : 516 + 512] = _pack_u16(fname)
        struct.pack_into("<IIIIII", rec, 1532, 0, 0, len(data), typ, 1, data_offset)
        directory.extend(rec)
        payloads.extend(data)
        data_offset += len(data)

    path.write_bytes(bytes(hdr) + bytes(directory) + bytes(payloads))
    return path


def _run_main(tool, monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["prepare_unisoc_firmware.py", *argv])
    tool.main()


def test_main_fails_and_writes_no_plan_when_fdl2_base_missing(tmp_path, tool, monkeypatch):
    xml = (
        "<BMAConfig><SchemeList><Scheme name='P525AAN'>"
        "<File><ID>FDL</ID><Type>FDL</Type><Block><Base>0x65000800</Base></Block></File>"
        "<File><ID>FDL2</ID><Type>NAND_FDL</Type><Block><Base>0x0</Base></Block></File>"
        "<File><ID>BOOT</ID><Type>CODE2</Type><Block id='boot_a'/></File>"
        "</Scheme></SchemeList></BMAConfig>"
    ).encode("utf-8")
    pac_path = _build_pac(
        tmp_path / "missing_fdl2_base.pac",
        [
            ("FDL", "fdl1-sign.bin", 1, b"fdl1"),
            ("FDL2", "lk-fdl2-sign.bin", 1, b"fdl2"),
            ("BOOT", "boot.img", 1, b"boot"),
            ("SCHEME", "P525AAN.xml", 2, xml),
        ],
    )
    outdir = tmp_path / "out"

    with pytest.raises(SystemExit) as exc_info:
        _run_main(tool, monkeypatch, [str(pac_path), str(outdir), "--models", "MLY-LX2"])

    assert exc_info.value.code not in (0, None)
    assert not (outdir / "images" / "plan.json").exists()
    assert not (outdir / "manifest.json").exists()


def test_main_fails_and_writes_no_plan_when_fdl1_missing(tmp_path, tool, monkeypatch):
    xml = (
        "<BMAConfig><SchemeList><Scheme name='P525AAN'>"
        "<File><ID>FDL2</ID><Type>NAND_FDL</Type><Block><Base>0x9EFFFE00</Base></Block></File>"
        "<File><ID>BOOT</ID><Type>CODE2</Type><Block id='boot_a'/></File>"
        "</Scheme></SchemeList></BMAConfig>"
    ).encode("utf-8")
    pac_path = _build_pac(
        tmp_path / "missing_fdl1.pac",
        [
            ("FDL2", "lk-fdl2-sign.bin", 1, b"fdl2"),
            ("BOOT", "boot.img", 1, b"boot"),
            ("SCHEME", "P525AAN.xml", 2, xml),
        ],
    )
    outdir = tmp_path / "out"

    with pytest.raises(SystemExit) as exc_info:
        _run_main(tool, monkeypatch, [str(pac_path), str(outdir), "--models", "MLY-LX2"])

    assert exc_info.value.code not in (0, None)
    assert not (outdir / "images" / "plan.json").exists()


def test_build_plan_preserves_nv_comm_types_even_when_id_unknown(tmp_path, tool):
    images = tmp_path / "images"
    images.mkdir()
    for fname in ("custom_slot_nv.bin", "custom_nv.bin", "boot.img"):
        (images / fname).write_bytes(b"\x01\x02\x03\x04")

    pac = {
        "entries": [
            {"id": "UNKNOWN_NV_SLOT", "file": "custom_slot_nv.bin", "size": 4, "type": 1},
            {"id": "UNKNOWN_NV_PLAIN", "file": "custom_nv.bin", "size": 4, "type": 1},
            {"id": "BOOT", "file": "boot.img", "size": 4, "type": 1},
        ]
    }
    scheme = {
        "UNKNOWN_NV_SLOT": {"partitions": ["l_fixnv1_a"], "type": "NV_COMM_SLOT"},
        "UNKNOWN_NV_PLAIN": {"partitions": ["nr_downloadnv_a"], "type": "NV_COMM"},
        "BOOT": {"partitions": ["boot_a"], "type": "CODE2"},
    }

    plan = tool.build_plan(pac, str(images), scheme)
    by_id = {entry["id"]: entry for entry in plan["entries"]}

    assert by_id["UNKNOWN_NV_SLOT"]["preserve"] is True
    assert by_id["UNKNOWN_NV_PLAIN"]["preserve"] is True
    assert "preserve" not in by_id["BOOT"]


def test_build_plan_keeps_splloader_in_erases(tmp_path, tool):
    images = tmp_path / "images"
    images.mkdir()
    for fname in ("fdl1-sign.bin", "edk2-fdl2-sign.bin", "boot.img"):
        (images / fname).write_bytes(b"\x01\x02\x03\x04")

    xml = tmp_path / "p532a.xml"
    xml.write_text(
        "<BMAConfig><SchemeList><Scheme name='p532a'>"
        "<File><ID>FDL</ID><Type>FDL</Type><Block><Base>0x65008000</Base></Block></File>"
        "<File><ID>FDL2</ID><Type>NAND_FDL</Type><Block><Base>0xBDFFFE00</Base></Block></File>"
        "<File><ID>EraseSPL</ID><Type>EraseFlash2</Type><Block id='splloader'/></File>"
        "<File><ID>EraseUbootLog</ID><Type>EraseFlash2</Type><Block id='uboot_log'/></File>"
        "<File><ID>EraseSdklog</ID><Type>EraseFlash2</Type><Block id='sd_klog'/></File>"
        "<File><ID>EraseMisc</ID><Type>EraseFlash2</Type><Block id='misc'/></File>"
        "<File><ID>EraseSysdump</ID><Type>EraseFlash2</Type><Block id='sysdumpdb'/></File>"
        "<File><ID>EraseMetadata</ID><Type>EraseFlash2</Type><Block id='metadata'/></File>"
        "<File><ID>BOOT</ID><Type>CODE2</Type><Block id='boot_a'/></File>"
        "</Scheme></SchemeList></BMAConfig>",
        encoding="utf-8",
    )
    scheme = tool.parse_scheme(str(xml))
    pac = {
        "entries": [
            {"id": "FDL", "file": "fdl1-sign.bin", "size": 4, "type": 1},
            {"id": "FDL2", "file": "edk2-fdl2-sign.bin", "size": 4, "type": 1},
            {"id": "BOOT", "file": "boot.img", "size": 4, "type": 1},
        ]
    }

    plan = tool.build_plan(pac, str(images), scheme)
    assert "splloader" in plan["erases"]
    assert plan["erases"] == ["splloader", "uboot_log", "sd_klog", "misc", "sysdumpdb", "metadata"]


def test_build_plan_always_emits_erases_even_when_empty(tmp_path, tool):
    images = tmp_path / "images"
    images.mkdir()
    for fname in ("fdl1-sign.bin", "edk2-fdl2-sign.bin", "boot.img"):
        (images / fname).write_bytes(b"\x01\x02\x03\x04")

    pac = {
        "entries": [
            {"id": "FDL", "file": "fdl1-sign.bin", "size": 4, "type": 1},
            {"id": "FDL2", "file": "edk2-fdl2-sign.bin", "size": 4, "type": 1},
            {"id": "BOOT", "file": "boot.img", "size": 4, "type": 1},
        ]
    }
    scheme_no_erase = {
        "FDL": {"partitions": [], "type": "FDL", "base": "0x65008000"},
        "FDL2": {"partitions": [], "type": "NAND_FDL", "base": "0xBDFFFE00"},
        "BOOT": {"partitions": ["boot_a"], "type": "CODE2"},
    }
    plan_no_erase = tool.build_plan(pac, str(images), scheme_no_erase)
    assert plan_no_erase["erases"] == []


def test_main_version_prop_option_and_default(tmp_path, tool, monkeypatch):
    xml = (
        "<BMAConfig><SchemeList><Scheme name='p532a'>"
        "<File><ID>FDL</ID><Type>FDL</Type><Block><Base>0x65008000</Base></Block></File>"
        "<File><ID>FDL2</ID><Type>NAND_FDL</Type><Block><Base>0xBDFFFE00</Base></Block></File>"
        "<File><ID>EraseSPL</ID><Type>EraseFlash2</Type><Block id='splloader'/></File>"
        "<File><ID>BOOT</ID><Type>CODE2</Type><Block id='boot_a'/></File>"
        "</Scheme></SchemeList></BMAConfig>"
    ).encode("utf-8")
    pac_path = _build_pac(
        tmp_path / "valid.pac",
        [
            ("FDL", "fdl1-sign.bin", 1, b"fdl1"),
            ("FDL2", "edk2-fdl2-sign.bin", 1, b"fdl2"),
            ("BOOT", "boot.img", 1, b"boot"),
            ("SCHEME", "p532a.xml", 2, xml),
        ],
        product="p532a_64only",
        firmware="P532AA_V1.0",
    )

    out_default = tmp_path / "out_default"
    _run_main(tool, monkeypatch, [str(pac_path), str(out_default), "--models", "p532a_64only"])
    manifest_default = json.loads((out_default / "manifest.json").read_text(encoding="utf-8"))
    plan_default = json.loads((out_default / "images" / "plan.json").read_text(encoding="utf-8"))
    assert manifest_default["version_prop"] == "ro.build.sw_internal_version"
    assert manifest_default["platform"] == "unisoc"
    assert manifest_default["models"] == ["p532a_64only"]
    assert plan_default["fdl"] == {
        "fdl1": {"file": "fdl1-sign.bin", "base": "0x65008000"},
        "fdl2": {"file": "edk2-fdl2-sign.bin", "base": "0xBDFFFE00"},
    }
    assert plan_default["erases"] == ["splloader"]

    out_custom = tmp_path / "out_custom"
    _run_main(
        tool,
        monkeypatch,
        [
            str(pac_path),
            str(out_custom),
            "--models",
            "p532a_64only",
            "--version-prop",
            "ro.build.version.incremental",
        ],
    )
    manifest_custom = json.loads((out_custom / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_custom["version_prop"] == "ro.build.version.incremental"

    with pytest.raises(SystemExit):
        _run_main(
            tool,
            monkeypatch,
            [str(pac_path), str(tmp_path / "out_empty"), "--version-prop", "   "],
        )
