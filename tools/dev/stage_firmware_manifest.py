#!/usr/bin/env python3
"""多 profile 固件上架摘要工具（ADR-0064 D1 / B10 U1）。

对固件版本目录生成（或刷新）唯一的 ``staging_manifest.json``：

- MTK V5/V6 → ``profiles.mtk``
- 展锐目录 → 必有 ``profiles.unisoc_fastboot``；当 ``images/plan.json`` 具备完整
  ``fdl.fdl1`` / ``fdl.fdl2`` 时同步产出 ``profiles.unisoc_bsl``，并以与
  ``flash_firmware_unisoc_bsl.get_sparse_trimmed_size`` 逐字节一致的算法写入
  ``images/bsl/super_trimmed.img``（不改源 ``super.img`` / ``raw/super.img``）

只增不改：不修改或删除既有镜像文件；``staging_manifest.json`` 与 BSL 派生裁剪镜像
均为新增/原子替换。

用法::

    ./scripts/project_python.sh tools/dev/stage_firmware_manifest.py <version_dir>
    ./scripts/project_python.sh tools/dev/stage_firmware_manifest.py <version_dir> --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_CONTRACT = (
    REPO_ROOT / "backend" / "agent" / "contracts" / "firmware_manifest.py"
)


def _load_firmware_manifest():
    spec = importlib.util.spec_from_file_location(
        "stp_firmware_manifest", _MANIFEST_CONTRACT
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load contract: {_MANIFEST_CONTRACT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fm = _load_firmware_manifest()

STAGING_NAME = "staging_manifest.json"
MANIFEST_NAME = "manifest.json"
PLAN_REL = "images/plan.json"
BSL_SUPER_REL = "images/bsl/super_trimmed.img"

_MTK_EXCLUDE_PREFIXES = ("APDB", "MDDB", "DbgInfo")
_MTK_EXCLUDE_SUFFIXES = (".elf",)

# 与 flash_firmware_unisoc_bsl 同口径的受保护分区集合（工具不得 import 脚本包）。
_PROTECTED_NV_PARTITIONS = frozenset(
    {
        "prodnv",
        "calinv",
        "miscdata",
        "persist",
        "spl",
        "splloader",
        "nr_factorynv",
        "nr_downloadnv",
        "nr_deltanv",
        "nr_runtimenv1",
        "nr_runtimenv2",
        "l_fixnv1",
        "l_fixnv2",
        "l_runtimenv1",
        "l_runtimenv2",
    }
)
_PROTECTED_NV_PREFIXES: Tuple[str, ...] = (
    "nr_downloadnv_",
    "nr_deltanv_",
    "nr_factorynv",
    "nr_runtimenv",
    "l_factorynv",
    "l_runtimenv",
    "l_deltanv",
)
_SKIP_PLAN_IDS = frozenset({"FDL", "FDL2"})
_SKIP_PLAN_TYPES = frozenset({"FDL", "NAND_FDL", "EraseFlash2"})
_TAIL_PARTITIONS = frozenset({"cache", "blackbox"})


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _is_mtk_excluded(name: str) -> bool:
    base = os.path.basename(name)
    if any(base.startswith(p) for p in _MTK_EXCLUDE_PREFIXES):
        return True
    if any(base.endswith(s) for s in _MTK_EXCLUDE_SUFFIXES):
        return True
    return False


def _strip_slot_suffix(part: str) -> str:
    if part.endswith("_a") or part.endswith("_b"):
        return part[:-2]
    return part


def _is_protected_partition(part: str) -> bool:
    p = part.strip().lower()
    base = _strip_slot_suffix(p)
    for candidate in (p, base):
        if candidate in _PROTECTED_NV_PARTITIONS or candidate.startswith(
            _PROTECTED_NV_PREFIXES
        ):
            return True
    return False


def _detect_kind(version_dir: Path) -> str:
    """返回 ``mtk`` / ``unisoc``；无法识别则抛错。"""
    manifest_path = version_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SystemExit(f"missing {MANIFEST_NAME} in {version_dir}")
    data = _load_json(manifest_path)
    if not isinstance(data, dict):
        raise SystemExit(f"{manifest_path} must be a JSON object")
    if (version_dir / "images" / "plan.json").is_file():
        return "unisoc"
    gen = str(data.get("tool_generation") or "v5").strip().lower()
    if gen in {"v5", "v6"} or data.get("scatter_file") or data.get("flash_xml_file"):
        return "mtk"
    if data.get("da_file"):
        return "mtk"
    raise SystemExit(
        f"cannot detect firmware kind under {version_dir}: "
        "need images/plan.json (unisoc) or MTK manifest fields"
    )


def _iter_download_agent_files(version_dir: Path) -> List[str]:
    root = version_dir / "download_agent"
    if not root.is_dir():
        return []
    out: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            abs_path = Path(dirpath) / name
            rel = abs_path.relative_to(version_dir).as_posix()
            out.append(rel)
    return sorted(out)


def _parse_scatter_xml_downloads(scatter_path: Path) -> Set[str]:
    root = ET.parse(scatter_path).getroot()
    files: Set[str] = set()
    for part in root.iter("partition_index"):
        dl = (part.findtext("is_download") or "").strip().lower()
        fn = (part.findtext("file_name") or "").strip()
        if dl == "true" and fn and fn.upper() != "NONE":
            files.add(fn)
    return files


def _parse_scatter_txt_downloads(scatter_path: Path) -> Set[str]:
    text = scatter_path.read_text(encoding="utf-8", errors="replace")
    files: Set[str] = set()
    current_file: Optional[str] = None
    current_dl: Optional[str] = None

    def _flush() -> None:
        nonlocal current_file, current_dl
        if (
            current_dl == "true"
            and current_file
            and current_file.upper() != "NONE"
        ):
            files.add(current_file)
        current_file = None
        current_dl = None

    for line in text.splitlines():
        if line.startswith("- partition_index:"):
            _flush()
            continue
        m_file = re.match(r"\s+file_name:\s*(\S+)\s*$", line)
        if m_file:
            current_file = m_file.group(1)
            continue
        m_dl = re.match(r"\s+is_download:\s*(\S+)\s*$", line)
        if m_dl:
            current_dl = m_dl.group(1).strip().lower()
            continue
    _flush()
    return files


def _resolve_mtk_scatter(version_dir: Path, manifest: Mapping[str, Any]) -> Path:
    gen = str(manifest.get("tool_generation") or "v5").strip().lower()
    if gen == "v6":
        flash_xml = str(
            manifest.get("flash_xml_file") or "download_agent/flash.xml"
        ).strip()
        xml_path = version_dir / flash_xml
        if xml_path.is_file():
            try:
                tree = ET.parse(xml_path)
                scatter_rel = (tree.findtext("scatter") or "").strip()
                if scatter_rel:
                    cand = (xml_path.parent / scatter_rel).resolve()
                    if cand.is_file():
                        return cand
            except ET.ParseError as exc:
                raise SystemExit(f"invalid flash.xml: {xml_path}: {exc}") from exc
        # 回退：版本根下常见 XML scatter
        for cand in sorted(version_dir.glob("*_Android_scatter.xml")):
            return cand
    scatter_name = str(manifest.get("scatter_file") or "").strip()
    if scatter_name:
        # 优先同名 XML（is_download 结构更稳），否则用声明的 txt/xml。
        stem = Path(scatter_name).stem
        xml_cand = version_dir / f"{stem}.xml"
        if xml_cand.is_file():
            return xml_cand
        declared = version_dir / scatter_name
        if declared.is_file():
            return declared
    for cand in sorted(version_dir.glob("*_Android_scatter.xml")):
        return cand
    for cand in sorted(version_dir.glob("*_Android_scatter.txt")):
        return cand
    raise SystemExit(f"cannot locate scatter file under {version_dir}")


def _mtk_download_files(version_dir: Path, manifest: Mapping[str, Any]) -> Set[str]:
    scatter = _resolve_mtk_scatter(version_dir, manifest)
    if scatter.suffix.lower() == ".xml":
        names = _parse_scatter_xml_downloads(scatter)
    else:
        names = _parse_scatter_txt_downloads(scatter)
    return {n for n in names if not _is_mtk_excluded(n)}


def _companion_sig_auth_files(version_dir: Path, included: Iterable[str]) -> Set[str]:
    """同目录签名/校验：checksum.ini、*.auth，以及与已收录文件配对的 .sig。"""
    extras: Set[str] = set()
    for path in version_dir.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name == "checksum.ini" or name.endswith(".auth"):
            extras.add(name)
    included_basenames = {os.path.basename(r) for r in included}
    for path in version_dir.rglob("*.sig"):
        if not path.is_file():
            continue
        rel = path.relative_to(version_dir).as_posix()
        if _is_mtk_excluded(rel):
            continue
        base = path.name
        # foo.img.sig / foo.sig 配对已收录镜像；或 download_agent 内签名一律收。
        stem = base[:-4] if base.endswith(".sig") else base
        if (
            rel.startswith("download_agent/")
            or stem in included_basenames
            or any(stem == b or stem.startswith(b + ".") for b in included_basenames)
            or any(b.startswith(stem) for b in included_basenames)
        ):
            extras.add(rel)
    return extras


def collect_mtk_relpaths(version_dir: Path) -> List[str]:
    manifest = _load_json(version_dir / MANIFEST_NAME)
    if not isinstance(manifest, dict):
        raise SystemExit("manifest.json must be an object")
    gen = str(manifest.get("tool_generation") or "v5").strip().lower()
    rels: Set[str] = {MANIFEST_NAME}
    if gen == "v6":
        flash_xml = str(
            manifest.get("flash_xml_file") or "download_agent/flash.xml"
        ).strip()
        rels.add(flash_xml.replace("\\", "/"))
        for da_rel in _iter_download_agent_files(version_dir):
            if not _is_mtk_excluded(da_rel):
                rels.add(da_rel)
    else:
        da = str(manifest.get("da_file") or "").strip()
        scatter = str(manifest.get("scatter_file") or "").strip()
        if not da or not scatter:
            raise SystemExit("MTK v5 manifest requires da_file and scatter_file")
        rels.add(da.replace("\\", "/"))
        rels.add(scatter.replace("\\", "/"))
        # 若存在同名 XML，也收入（与下载解析同源）
        xml_cand = version_dir / f"{Path(scatter).stem}.xml"
        if xml_cand.is_file():
            rels.add(xml_cand.name)

    scatter_path = _resolve_mtk_scatter(version_dir, manifest)
    # 显式收录实际使用的 scatter
    try:
        rels.add(scatter_path.relative_to(version_dir).as_posix())
    except ValueError:
        pass

    for name in _mtk_download_files(version_dir, manifest):
        # scatter 中的 file_name 相对版本根
        candidate = version_dir / name
        if candidate.is_file() and not _is_mtk_excluded(name):
            rels.add(name.replace("\\", "/"))

    rels |= _companion_sig_auth_files(version_dir, rels)
    # 最终再滤一次排除集
    return sorted(r for r in rels if not _is_mtk_excluded(r))


def _image_for_rel(entry: Mapping[str, Any]) -> str:
    """与 ``flash_firmware_unisoc.image_for`` 同口径，返回版本根相对路径。"""
    rel = entry.get("raw") or entry["file"]
    if not isinstance(rel, str) or not rel.strip():
        raise SystemExit(f"plan entry missing file/raw: {entry!r}")
    rel = rel.strip().replace("\\", "/")
    if os.path.isabs(rel):
        raise SystemExit(f"absolute image path forbidden in plan.json: {rel!r}")
    return f"images/{rel}"


def collect_unisoc_fastboot_relpaths(version_dir: Path) -> List[str]:
    plan_path = version_dir / "images" / "plan.json"
    plan = _load_json(plan_path)
    if not isinstance(plan, dict):
        raise SystemExit("images/plan.json must be an object")
    entries = plan.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("images/plan.json entries must be a list")
    rels: Set[str] = {MANIFEST_NAME, PLAN_REL}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("preserve") or not entry.get("partitions"):
            continue
        rels.add(_image_for_rel(entry))
    return sorted(rels)


def _plan_has_complete_fdl(plan: Mapping[str, Any]) -> bool:
    fdl = plan.get("fdl")
    if not isinstance(fdl, dict):
        return False
    fdl1 = fdl.get("fdl1")
    fdl2 = fdl.get("fdl2")
    if not isinstance(fdl1, dict) or not isinstance(fdl2, dict):
        return False
    return all(
        str(fdl1.get(k) or "").strip() and str(fdl2.get(k) or "").strip()
        for k in ("file", "base")
    )


def _load_bsl_flash_spec(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """与 ``flash_firmware_unisoc_bsl._load_flash_spec`` 同口径（输入已是 plan dict）。"""
    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("plan.json entries must be a non-empty list")
    fdl_cfg = plan["fdl"]
    fdl1_cfg = fdl_cfg["fdl1"]
    fdl2_cfg = fdl_cfg["fdl2"]
    fdl1_file = str(fdl1_cfg.get("file") or "").strip()
    fdl2_file = str(fdl2_cfg.get("file") or "").strip()

    userdata_rel = ""
    userdata_count = 0
    super_rel = ""
    super_file = ""
    super_sparse = False
    head_parts: List[Tuple[str, str]] = []
    tail_parts: List[Tuple[str, str]] = []
    seen_parts: Set[str] = set()
    has_super = False

    for item in entries:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or "").strip()
        ftype = str(item.get("flush_type") or "").strip()
        if fid in _SKIP_PLAN_IDS or ftype in _SKIP_PLAN_TYPES:
            continue
        parts = [
            str(p).strip() for p in (item.get("partitions") or []) if str(p).strip()
        ]
        fname = str(item.get("file") or "").strip()
        raw_rel = str(item.get("raw") or "").strip()
        is_sparse = bool(item.get("sparse"))
        if fid == "UserData" and "userdata" in parts:
            ud_rel = raw_rel if (is_sparse and raw_rel) else fname
            if not ud_rel:
                raise SystemExit("UserData entry missing file")
            userdata_count += 1
            userdata_rel = ud_rel
        if not fname and not (raw_rel and "super" in parts):
            continue
        rel_path = raw_rel if (is_sparse and raw_rel) else fname
        if not parts:
            continue
        preserve = bool(item.get("preserve"))
        for part in parts:
            if part == "userdata":
                continue
            if preserve or _is_protected_partition(part):
                continue
            if part == "super":
                has_super = True
                super_file = fname
                super_sparse = is_sparse
                super_rel = raw_rel or fname
                continue
            if part in seen_parts:
                continue
            seen_parts.add(part)
            if part in _TAIL_PARTITIONS:
                tail_parts.append((part, rel_path))
            else:
                head_parts.append((part, rel_path))

    if userdata_count != 1 or not userdata_rel:
        raise SystemExit("plan.json must contain exactly one UserData entry")
    rel_parts = head_parts + tail_parts
    if not rel_parts and not has_super:
        raise SystemExit("plan.json has no writable partitions for BSL")
    return {
        "fdl1": fdl1_file,
        "fdl2": fdl2_file,
        "userdata_rel": userdata_rel,
        "rel_parts": rel_parts,
        "has_super": has_super,
        "super_rel": super_rel,
        "super_file": super_file,
        "super_sparse": super_sparse,
    }


def get_sparse_trimmed_size(sparse_path: Path) -> Optional[int]:
    """与 ``flash_firmware_unisoc_bsl.get_sparse_trimmed_size`` 逐字节一致。"""
    if not sparse_path.is_file():
        return None
    with sparse_path.open("rb") as f:
        hdr = f.read(28)
        if len(hdr) < 28:
            return None
        magic, _, _, _, _, blk_sz, _, total_chunks, _ = struct.unpack(
            "<IHHHHIIII", hdr
        )
        if magic != 0xED26FF3A:
            return None
        off_blk = 0
        last_data_end_blk = 0
        for _ in range(total_chunks):
            chdr = f.read(12)
            if len(chdr) < 12:
                break
            ctype, _, cblks, csz = struct.unpack("<HHII", chdr)
            f.seek(csz - 12, 1)
            if ctype != 0xCAC3:  # not CHUNK_DONT_CARE
                last_data_end_blk = off_blk + cblks
            off_blk += cblks
    align = 16 * 1024 * 1024
    raw_bytes = last_data_end_blk * blk_sz
    return ((raw_bytes + align - 1) // align) * align


def _write_super_trimmed(version_dir: Path, spec: Mapping[str, Any]) -> str:
    """生成 ``images/bsl/super_trimmed.img`` 并返回相对路径。"""
    images = version_dir / "images"
    super_rel = str(spec.get("super_rel") or "").strip()
    super_file = str(spec.get("super_file") or "").strip()
    super_sparse = bool(spec.get("super_sparse"))
    if not super_rel:
        raise SystemExit("BSL super entry missing path")
    src_super = images / super_rel
    if not src_super.is_file():
        raise SystemExit(f"required super image missing: {src_super}")
    sparse_super = (
        (images / super_file) if (super_sparse and super_file) else None
    )
    trimmed_size = (
        get_sparse_trimmed_size(sparse_super) if sparse_super is not None else None
    ) or src_super.stat().st_size
    trimmed_size = min(int(trimmed_size), src_super.stat().st_size)

    out_path = version_dir / BSL_SUPER_REL
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + f".tmp.{os.getpid()}")
    remaining = trimmed_size
    buf_sz = 16 * 1024 * 1024
    try:
        with src_super.open("rb") as fin, tmp.open("wb") as fout:
            while remaining > 0:
                chunk = fin.read(min(buf_sz, remaining))
                if not chunk:
                    break
                fout.write(chunk)
                remaining -= len(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        if tmp.stat().st_size != trimmed_size:
            raise SystemExit(
                f"trimmed super size mismatch: got {tmp.stat().st_size}, "
                f"expected {trimmed_size}"
            )
        os.replace(tmp, out_path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return BSL_SUPER_REL


def collect_unisoc_bsl_relpaths(
    version_dir: Path, *, write_trimmed: bool
) -> List[str]:
    plan = _load_json(version_dir / "images" / "plan.json")
    if not isinstance(plan, dict):
        raise SystemExit("images/plan.json must be an object")
    if not _plan_has_complete_fdl(plan):
        return []
    spec = _load_bsl_flash_spec(plan)
    rels: Set[str] = {MANIFEST_NAME, PLAN_REL}
    for key in ("fdl1", "fdl2", "userdata_rel"):
        rel = str(spec[key]).strip().replace("\\", "/")
        rels.add(f"images/{rel}")
    for _part, rel in spec["rel_parts"]:
        rels.add(f"images/{str(rel).strip().replace(chr(92), '/')}")
    if spec["has_super"]:
        if write_trimmed:
            rels.add(_write_super_trimmed(version_dir, spec))
        else:
            # dry-run：仍声明将产出的派生路径（不落盘）
            rels.add(BSL_SUPER_REL)
    return sorted(rels)


def stage_version_dir(version_dir: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    version_dir = version_dir.resolve()
    if not version_dir.is_dir():
        raise SystemExit(f"not a directory: {version_dir}")
    kind = _detect_kind(version_dir)
    profiles: Dict[str, Any] = {}
    if kind == "mtk":
        profiles[fm.PROFILE_MTK] = fm.build_profile(
            str(version_dir), collect_mtk_relpaths(version_dir)
        )
    else:
        profiles[fm.PROFILE_UNISOC_FASTBOOT] = fm.build_profile(
            str(version_dir), collect_unisoc_fastboot_relpaths(version_dir)
        )
        plan = _load_json(version_dir / "images" / "plan.json")
        if isinstance(plan, dict) and _plan_has_complete_fdl(plan):
            bsl_rels = collect_unisoc_bsl_relpaths(
                version_dir, write_trimmed=not dry_run
            )
            if dry_run:
                # dry-run 下派生镜像可能尚不存在：只校验非派生路径，digest 用占位跳过写入。
                existing = [
                    r
                    for r in bsl_rels
                    if r != BSL_SUPER_REL or (version_dir / r).is_file()
                ]
                if BSL_SUPER_REL in bsl_rels and BSL_SUPER_REL not in existing:
                    # 构造临时 profile 时跳过缺失派生文件——dry-run 仅打印将收录集合。
                    profiles[fm.PROFILE_UNISOC_BSL] = {
                        "content_digest": None,
                        "total_bytes": None,
                        "files": [{"relpath": r, "size": None, "sha256": None} for r in bsl_rels],
                        "dry_run_pending_trimmed": True,
                    }
                else:
                    profiles[fm.PROFILE_UNISOC_BSL] = fm.build_profile(
                        str(version_dir), existing
                    )
            else:
                profiles[fm.PROFILE_UNISOC_BSL] = fm.build_profile(
                    str(version_dir), bsl_rels
                )

    manifest = {"schema_version": fm.SCHEMA_VERSION, "profiles": profiles}
    if dry_run:
        return manifest

    # 正式路径：去掉 dry-run 占位后规范化写入
    if fm.PROFILE_UNISOC_BSL in profiles and profiles[fm.PROFILE_UNISOC_BSL].get(
        "dry_run_pending_trimmed"
    ):
        raise SystemExit("internal error: dry-run placeholder reached write path")
    text = fm.dumps_staging_manifest(manifest)
    out_path = version_dir / STAGING_NAME
    fm.atomic_write_json(str(out_path), text)
    # 回读校验（含越界软链）
    loaded = fm.load_staging_manifest(str(out_path))
    fm.validate_manifest_paths(str(version_dir), loaded)
    return loaded


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate multi-profile staging_manifest.json for a firmware version dir"
    )
    parser.add_argument("version_dir", type=Path, help="firmware version directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print planned profiles without writing staging_manifest.json or trimmed super",
    )
    args = parser.parse_args(argv)
    result = stage_version_dir(args.version_dir, dry_run=args.dry_run)
    summary = {
        "version_dir": str(args.version_dir.resolve()),
        "dry_run": bool(args.dry_run),
        "profiles": {
            name: {
                "content_digest": profile.get("content_digest"),
                "total_bytes": profile.get("total_bytes"),
                "file_count": len(profile.get("files") or []),
                "files": [
                    e.get("relpath") if isinstance(e, dict) else e
                    for e in (profile.get("files") or [])
                ],
            }
            for name, profile in (result.get("profiles") or {}).items()
        },
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
