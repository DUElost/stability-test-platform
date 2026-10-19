#!/usr/bin/env python3
"""prepare_unisoc_firmware.py -- Unisoc .pac -> flash plan + raw images (one-time, per firmware).

Why this exists: pacextractor reads PAC sizes/offsets as 32-bit, so any entry
> 4 GiB (super.img) is truncated and any entry stored above 4 GiB is read from
the wrong offset.  The PAC directory uses 64-bit size_high / pac_offset_high
fields; this tool reads them correctly.

Output layout (meant to live on NFS, one copy per firmware version):

    <out>/manifest.json
    <out>/images/<original files>
    <out>/images/raw/<expanded sparse images>
    <out>/images/plan.json

Usage:
    python tools/dev/prepare_unisoc_firmware.py <firmware.pac> <outdir> \
        --family A57 --models Z2581,P615F03 --latest
    # 已有解包产物时加 --no-extract；--latest 合并写 ../latest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
import xml.etree.ElementTree as ET

PAC_HEADER_SIZE = 2124
PAC_ENTRY_SIZE = 2580
SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4

# PAC file IDs that carry device-unique data: a version switch must NOT touch
# them, otherwise IMEI / RF calibration are gone ("format all" behaviour).
PRESERVE_IDS = {
    "ProdNV", "Persist", "NV_LTE", "Modem_LTE_DELTANV", "NV_CALI_LTE",
    "UserData",
}

# Erase entries in the scheme carry no image, only a target block.
ERASE_IDS_PREFIX = "Erase"


def _u16s(raw: bytes, off: int, n: int) -> str:
    s = raw[off:off + 2 * n].decode("utf-16-le", "ignore")
    return s.split("\x00")[0]


def parse_pac(path: str) -> dict:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        hdr = f.read(PAC_HEADER_SIZE)
        if len(hdr) < PAC_HEADER_SIZE:
            raise SystemExit(f"not a PAC (header too short): {path}")
        count, dir_off = struct.unpack_from("<II", hdr, 1076)
        magic = struct.unpack_from("<I", hdr, 2116)[0]
        if magic != ~0x50005 & 0xFFFFFFFF:
            raise SystemExit(f"bad PAC magic 0x{magic:08x}: {path}")
        if dir_off != PAC_HEADER_SIZE:
            raise SystemExit(f"unexpected PAC dir offset {dir_off}")
        entries = []
        for _ in range(count):
            e = f.read(PAC_ENTRY_SIZE)
            if len(e) < PAC_ENTRY_SIZE:
                raise SystemExit("truncated PAC directory")
            eid = _u16s(e, 4, 256)
            name = _u16s(e, 516, 256)
            size_high, off_high, sz, typ, flash_use, off = struct.unpack_from("<IIIIII", e, 1532)
            entries.append({
                "id": eid,
                "file": name,
                "size": (size_high << 32) | sz,
                "offset": (off_high << 32) | off,
                "type": typ,
                "flash_use": flash_use,
            })
    return {
        "path": path,
        "bytes": size,
        "product": _u16s(hdr, 52, 256),
        "firmware": _u16s(hdr, 564, 256),
        "entries": entries,
    }


def extract_entry(pac_path: str, entry: dict, dest: str) -> None:
    if not entry["file"] or not entry["size"]:
        return
    tmp = dest + ".part"
    with open(pac_path, "rb") as src, open(tmp, "wb") as out:
        src.seek(entry["offset"])
        left = entry["size"]
        while left:
            buf = src.read(min(left, 8 << 20))
            if not buf:
                raise SystemExit(f"short read on {entry['file']}")
            out.write(buf)
            left -= len(buf)
    os.replace(tmp, dest)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_sparse(path: str) -> bool:
    with open(path, "rb") as f:
        return struct.unpack("<I", f.read(4))[0] == SPARSE_MAGIC


def unsparse(src: str, dst: str) -> None:
    """Expand an Android sparse image. Mirrors simg2img without the dependency."""
    with open(src, "rb") as f, open(dst + ".part", "wb") as out:
        magic, _major, _minor, _fh, _ch, blk, total_blk, _chunks, image_crc = struct.unpack(
            "<IHHHHIIII", f.read(28))
        if magic != SPARSE_MAGIC:
            raise SystemExit(f"not sparse: {src}")
        written = 0
        while True:
            head = f.read(12)
            if len(head) < 12:
                break
            ctype, _res, cblk, total = struct.unpack("<HHII", head)
            if ctype == CHUNK_RAW:
                left = cblk * blk
                while left:
                    buf = f.read(min(left, 8 << 20))
                    if not buf:
                        raise SystemExit("truncated sparse data")
                    out.write(buf)
                    left -= len(buf)
            elif ctype == CHUNK_FILL:
                pattern = f.read(4)
                out.write(pattern * (cblk * blk // 4))
            elif ctype == CHUNK_DONT_CARE:
                out.seek(cblk * blk, os.SEEK_CUR)
            elif ctype == CHUNK_CRC32:
                f.read(4)
                continue
            else:
                raise SystemExit(f"unknown sparse chunk 0x{ctype:04x}")
            written += cblk
        out.truncate(total_blk * blk)
    if written != total_blk:
        raise SystemExit(f"sparse block count mismatch: {written} != {total_blk}")
    os.replace(dst + ".part", dst)


def parse_scheme(xml_path: str) -> dict:
    """PAC file id -> list of target block (partition) ids, from the PAC config XML."""
    tree = ET.parse(xml_path)
    mapping = {}
    for f in tree.iter("File"):
        fid = (f.findtext("ID") or "").strip()
        if not fid:
            continue
        blocks = [b.get("id") for b in f.iter("Block") if b.get("id")]
        ftype = (f.findtext("Type") or "").strip()
        mapping[fid] = {"partitions": blocks, "type": ftype}
    return mapping


def build_plan(pac: dict, images_dir: str, scheme: dict) -> dict:
    plan = []
    for e in pac["entries"]:
        fid, fname, size = e["id"], e["file"], e["size"]
        if not fname or not size:
            continue
        path = os.path.join(images_dir, fname)
        if not os.path.isfile(path):
            continue
        info = scheme.get(fid) or {}
        parts = list(info.get("partitions") or [])
        item = {
            "id": fid,
            "file": fname,
            "size": size,
            "sparse": is_sparse(path),
            "partitions": parts,
            "flush_type": info.get("type") or "",
        }
        if fid in PRESERVE_IDS:
            item["preserve"] = True
        if not parts:
            item["partitions"] = []
        if item["sparse"]:
            item["raw"] = os.path.join("raw", fname)
            item["raw_size"] = os.path.getsize(os.path.join(images_dir, item["raw"])) \
                if os.path.isfile(os.path.join(images_dir, item["raw"])) else 0
        plan.append(item)
    return {"entries": plan}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pac")
    ap.add_argument("outdir")
    ap.add_argument("--family", default="")
    ap.add_argument("--version", default="")
    ap.add_argument("--no-extract", action="store_true", help="reuse images/ if present")
    ap.add_argument("--models", default="", help="comma separated model ids for manifest models[] and latest.json")
    ap.add_argument("--latest", action="store_true",
                    help="create/merge <parent>/latest.json pointer ({versions:{model:version}})")
    args = ap.parse_args()

    t0 = time.time()
    pac = parse_pac(args.pac)
    images = os.path.join(args.outdir, "images")
    raw = os.path.join(images, "raw")
    os.makedirs(raw, exist_ok=True)

    print(f"PAC    : {pac['firmware']} ({pac['product']}) {pac['bytes']/2**30:.2f} GiB", flush=True)
    print(f"images : {len(pac['entries'])} entries -> {images}", flush=True)

    for e in pac["entries"]:
        if not e["file"] or not e["size"]:
            continue
        dest = os.path.join(images, e["file"])
        if args.no_extract and os.path.isfile(dest) and os.path.getsize(dest) == e["size"]:
            continue
        if os.path.isfile(dest) and os.path.getsize(dest) == e["size"]:
            continue
        print(f"  extract {e['file']} ({e['size']/2**20:.1f} MiB)", flush=True)
        extract_entry(args.pac, e, dest)

    # scheme XML is the PAC entry with type 2
    xml_name = next((e["file"] for e in pac["entries"] if e["type"] == 2 and e["file"]), "")
    scheme = {}
    if xml_name and os.path.isfile(os.path.join(images, xml_name)):
        scheme = parse_scheme(os.path.join(images, xml_name))
        print(f"scheme : {len(scheme)} file ids from {xml_name}", flush=True)

    for e in pac["entries"]:
        if not e["file"] or not e["size"]:
            continue
        src = os.path.join(images, e["file"])
        if not is_sparse(src):
            continue
        dst = os.path.join(raw, e["file"])
        if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            continue
        print(f"  unsparse {e['file']} -> raw/{e['file']}", flush=True)
        unsparse(src, dst)

    plan = build_plan(pac, images, scheme)
    with open(os.path.join(images, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    version = args.version or pac["firmware"]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    manifest = {
        "platform": "unisoc",
        "family": args.family or pac["product"],
        "version": version,
        "version_prop": "ro.build.sw_internal_version",
        "models": models,
        "pac_file": os.path.basename(args.pac),
        "pac_path": os.path.abspath(args.pac),
        "pac_sha256": sha256_file(args.pac),
        "product": pac["product"],
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "images_dir": "images",
        "plan": "images/plan.json",
    }
    with open(os.path.join(args.outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 族级版本指针，和 MTK 固件同一套约定：{root}/{family}/latest.json
    #   {"version": "...", "versions": {"<ro.product.model>": "..."}}
    if args.latest:
        pointer_path = os.path.join(os.path.dirname(os.path.abspath(args.outdir)), "latest.json")
        pointer = {}
        if os.path.isfile(pointer_path):
            try:
                with open(pointer_path, encoding="utf-8") as f:
                    pointer = json.load(f) or {}
            except ValueError:
                pointer = {}
        pointer["version"] = version
        versions = pointer.get("versions")
        if not isinstance(versions, dict):
            versions = {}
        for m in models:
            versions[m] = version
        if versions:
            pointer["versions"] = versions
        with open(pointer_path, "w", encoding="utf-8") as f:
            json.dump(pointer, f, ensure_ascii=False, indent=2)
        print(f"latest : {pointer_path}", flush=True)

    n_flash = sum(1 for i in plan["entries"] if i["partitions"] and not i.get("preserve"))
    n_pres = sum(1 for i in plan["entries"] if i.get("preserve"))
    print(f"done   : {n_flash} flashable entries, {n_pres} preserved, {time.time()-t0:.0f}s", flush=True)
    print(f"manifest: {os.path.join(args.outdir, 'manifest.json')}", flush=True)


if __name__ == "__main__":
    main()
