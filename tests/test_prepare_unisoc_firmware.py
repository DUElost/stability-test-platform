"""Regression tests for tools/dev/prepare_unisoc_firmware.py.

The sparse expander is the risky part: a FILL chunk written with the wrong
length corrupts super.img and the device falls back to fastboot (observed on
2026-10-17). These tests pin the chunk semantics.
"""

from __future__ import annotations

import importlib.util
import struct
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
