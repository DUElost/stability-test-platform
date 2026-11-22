"""多 profile 固件上架摘要契约（ADR-0064 D1）。

纯标准库、无包内相对导入：上架工具与 Agent 必须以同一实现解析/校验
``staging_manifest.json`` 并计算确定性 ``content_digest``。

Schema（``schema_version = 1``）::

    {
      "schema_version": 1,
      "profiles": {
        "<profile>": {
          "content_digest": "sha256:<hex>",
          "total_bytes": <int>,
          "files": [{"relpath": "...", "size": <int>, "sha256": "<hex>"}, ...]
        }
      }
    }

``content_digest``：将 ``files`` 按 ``relpath``（POSIX ``/``、ASCII 字典序）升序排列后，
对 ``[[relpath, size, sha256], ...]`` 做
``json.dumps(..., ensure_ascii=True, separators=(",", ":"))`` 的 UTF-8 编码，再求
``sha256:<hex>``（不含可执行位）。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Sequence

SCHEMA_VERSION = 1
DIGEST_PREFIX = "sha256:"

PROFILE_MTK = "mtk"
PROFILE_UNISOC_FASTBOOT = "unisoc_fastboot"
PROFILE_UNISOC_BSL = "unisoc_bsl"

KNOWN_PROFILES = frozenset(
    {PROFILE_MTK, PROFILE_UNISOC_FASTBOOT, PROFILE_UNISOC_BSL}
)


class FirmwareManifestError(ValueError):
    """``staging_manifest.json`` 结构或路径校验失败。"""


def normalize_relpath(relpath: str) -> str:
    """规范化相对路径：POSIX ``/`` 分隔、去掉前导 ``./``、禁止空段。"""
    if not isinstance(relpath, str) or not relpath.strip():
        raise FirmwareManifestError("relpath must be a non-empty string")
    raw = relpath.replace("\\", "/").strip()
    if raw.startswith("/"):
        raise FirmwareManifestError(f"absolute relpath forbidden: {relpath!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise FirmwareManifestError(f"empty relpath after normalize: {relpath!r}")
    if any(p == ".." for p in parts):
        raise FirmwareManifestError(f"parent traversal forbidden in relpath: {relpath!r}")
    return "/".join(parts)


def validate_relpath_within_root(version_root: str, relpath: str) -> str:
    """校验 ``relpath`` 解析后仍落在 ``version_root`` 内（含软链越界拒绝）。

    返回规范化后的 ``relpath``。目标文件不必已存在（结构校验阶段可用）；
    若路径组件或最终目标经 ``realpath`` 逃出根目录则拒绝。
    """
    norm = normalize_relpath(relpath)
    root = os.path.realpath(version_root)
    # 逐段 realpath：中间软链越界也要拦。
    cursor = root
    for part in norm.split("/"):
        candidate = os.path.join(cursor, part)
        if os.path.lexists(candidate):
            cursor = os.path.realpath(candidate)
            if cursor != root and not cursor.startswith(root + os.sep):
                raise FirmwareManifestError(
                    f"symlink or path escapes version root: {relpath!r}"
                )
        else:
            # 尚不存在的叶子：按字面拼接后做规范边界检查。
            joined = os.path.normpath(os.path.join(cursor, part))
            if joined != root and not joined.startswith(root + os.sep):
                raise FirmwareManifestError(
                    f"path escapes version root: {relpath!r}"
                )
            cursor = joined
    return norm


def file_sha256(path: str) -> str:
    """流式计算文件内容 SHA-256（十六进制，无前缀）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_file_entry(version_root: str, relpath: str) -> Dict[str, Any]:
    """从版本目录构造单项 ``{relpath, size, sha256}``，并做越界校验。"""
    norm = validate_relpath_within_root(version_root, relpath)
    abs_path = os.path.join(version_root, *norm.split("/"))
    if not os.path.isfile(abs_path):
        raise FirmwareManifestError(f"missing file for relpath: {norm}")
    # 再次确认最终打开的是根内普通文件（拒绝目录/越界软链）。
    real = os.path.realpath(abs_path)
    root = os.path.realpath(version_root)
    if real != root and not real.startswith(root + os.sep):
        raise FirmwareManifestError(f"symlink escapes version root: {norm}")
    if os.path.islink(abs_path):
        # 允许根内软链指向根内文件；上面 realpath 已拦越界。
        pass
    size = os.path.getsize(abs_path)
    return {"relpath": norm, "size": int(size), "sha256": file_sha256(abs_path)}


def sort_file_entries(
    files: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """按 ``relpath`` ASCII 字典序稳定排序并浅拷贝为普通 dict。"""
    normalized: List[Dict[str, Any]] = []
    for item in files:
        if not isinstance(item, Mapping):
            raise FirmwareManifestError("files entries must be objects")
        relpath = normalize_relpath(str(item.get("relpath", "")))
        try:
            size = int(item["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FirmwareManifestError(
                f"files[].size must be int for {relpath!r}"
            ) from exc
        sha = item.get("sha256")
        if not isinstance(sha, str) or not sha:
            raise FirmwareManifestError(
                f"files[].sha256 must be non-empty string for {relpath!r}"
            )
        normalized.append({"relpath": relpath, "size": size, "sha256": sha})
    normalized.sort(key=lambda e: e["relpath"])
    return normalized


def compute_content_digest(files: Sequence[Mapping[str, Any]]) -> str:
    """确定性 ``content_digest``（不含可执行位）。"""
    ordered = sort_file_entries(files)
    payload = [[e["relpath"], e["size"], e["sha256"]] for e in ordered]
    blob = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return DIGEST_PREFIX + hashlib.sha256(blob).hexdigest()


def build_profile(
    version_root: str,
    relpaths: Iterable[str],
) -> Dict[str, Any]:
    """由相对路径集合构造完整 profile 对象（含 digest 与 total_bytes）。"""
    seen: set[str] = set()
    entries: List[Dict[str, Any]] = []
    for rel in relpaths:
        norm = normalize_relpath(rel)
        if norm in seen:
            continue
        seen.add(norm)
        entries.append(build_file_entry(version_root, norm))
    ordered = sort_file_entries(entries)
    total = sum(int(e["size"]) for e in ordered)
    return {
        "content_digest": compute_content_digest(ordered),
        "total_bytes": total,
        "files": ordered,
    }


def parse_staging_manifest(data: Any) -> Dict[str, Any]:
    """解析并校验 ``staging_manifest.json`` 结构，返回规范化副本。"""
    if not isinstance(data, Mapping):
        raise FirmwareManifestError("staging_manifest root must be an object")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise FirmwareManifestError(
            f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}"
        )
    profiles_raw = data.get("profiles")
    if not isinstance(profiles_raw, Mapping) or not profiles_raw:
        raise FirmwareManifestError("profiles must be a non-empty object")
    profiles: Dict[str, Any] = {}
    for name, profile in profiles_raw.items():
        if not isinstance(name, str) or not name:
            raise FirmwareManifestError("profile name must be a non-empty string")
        if not isinstance(profile, Mapping):
            raise FirmwareManifestError(f"profile {name!r} must be an object")
        files = profile.get("files")
        if not isinstance(files, list) or not files:
            raise FirmwareManifestError(
                f"profile {name!r} files must be a non-empty list"
            )
        ordered = sort_file_entries(files)
        digest = compute_content_digest(ordered)
        declared = profile.get("content_digest")
        if declared is not None and declared != digest:
            raise FirmwareManifestError(
                f"profile {name!r} content_digest mismatch: "
                f"declared={declared!r} computed={digest!r}"
            )
        total = sum(int(e["size"]) for e in ordered)
        declared_total = profile.get("total_bytes")
        if declared_total is not None and int(declared_total) != total:
            raise FirmwareManifestError(
                f"profile {name!r} total_bytes mismatch: "
                f"declared={declared_total!r} computed={total}"
            )
        profiles[name] = {
            "content_digest": digest,
            "total_bytes": total,
            "files": ordered,
        }
    return {"schema_version": SCHEMA_VERSION, "profiles": profiles}


def load_staging_manifest(path: str) -> Dict[str, Any]:
    """从路径加载并校验 ``staging_manifest.json``。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return parse_staging_manifest(data)


def validate_manifest_paths(
    version_root: str,
    manifest: Mapping[str, Any],
) -> None:
    """校验清单内全部 ``relpath`` 无 ``..`` / 绝对路径 / 越界软链，且文件存在。"""
    profiles = manifest.get("profiles")
    if not isinstance(profiles, Mapping):
        raise FirmwareManifestError("profiles missing")
    for name, profile in profiles.items():
        files = profile.get("files") if isinstance(profile, Mapping) else None
        if not isinstance(files, list):
            raise FirmwareManifestError(f"profile {name!r} files missing")
        for item in files:
            rel = item.get("relpath") if isinstance(item, Mapping) else None
            if not isinstance(rel, str):
                raise FirmwareManifestError(f"profile {name!r} has invalid relpath")
            entry = build_file_entry(version_root, rel)
            if entry["size"] != int(item["size"]) or entry["sha256"] != item["sha256"]:
                raise FirmwareManifestError(
                    f"profile {name!r} file digest/size drift: {rel}"
                )


def dumps_staging_manifest(manifest: Mapping[str, Any]) -> str:
    """规范化 JSON 文本（UTF-8，缩进 2，末尾换行），供原子写入。"""
    parsed = parse_staging_manifest(manifest)
    return json.dumps(parsed, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def atomic_write_json(path: str, text: str) -> None:
    """原子写入文本文件（同目录临时文件 + ``os.replace``）。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
