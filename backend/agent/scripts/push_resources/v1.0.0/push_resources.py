"""Push files or resource bundle from host to device.

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_NFS_ROOT        (required for bundle mode — prepended to relative paths)
    STP_STEP_PARAMS     (required, JSON)

Two modes:
  1. files mode:  {"files": [{"local": "/nfs/...", "remote": "/sdcard/...", "chmod": "755"}]}
  2. bundle mode: {"bundle": "/nfs/bundles/app.tar.gz", "manifest": "/nfs/bundles/manifest.json",
                    "remote_dir": "/sdcard/test_resources", "skip_if_match": true}

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "...", "metrics": {...}}
"""

import hashlib
import json
import os
import subprocess
import sys
from _adb import adb_path, adb_push, adb_shell, device_serial, output_result, params


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nfs_root() -> str:
    return os.environ.get("STP_NFS_ROOT", "")


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    root = _nfs_root()
    if root:
        return os.path.join(root, path)
    return path


def _push_files(serial: str, files: list) -> dict:
    pushed = 0
    errors = []
    for f in files:
        local = _resolve_path(f.get("local", ""))
        remote = f.get("remote", "")
        if not local or not remote:
            continue
        try:
            adb_push(local, remote)
            if f.get("chmod"):
                adb_shell(f"chmod {f['chmod']} {remote}", timeout=10)
            pushed += 1
        except Exception as exc:
            errors.append(f"Failed to push {local}: {exc}")

    if errors:
        return {"success": False, "pushed": pushed, "error": "; ".join(errors)}
    return {"success": True, "pushed": pushed, "error": ""}


def _push_bundle(serial: str, bundle: str, manifest_path: str, remote_dir: str, skip_if_match: bool) -> dict:
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except Exception as exc:
        return {"success": False, "error": f"manifest load failed: {exc}"}

    expected_sha = manifest.get("bundle_sha256", "")
    if not expected_sha:
        return {"success": False, "error": "manifest.bundle_sha256 is required"}

    try:
        actual_sha = _sha256_file(bundle)
    except Exception as exc:
        return {"success": False, "error": f"bundle sha256 failed: {exc}"}

    if actual_sha != expected_sha:
        return {"success": False, "error": f"bundle_sha256 mismatch: manifest={expected_sha} actual={actual_sha}"}

    marker = f"{remote_dir}/.stp_bundle_sha256"

    if skip_if_match:
        try:
            remote_marker = subprocess.run(
                [adb_path(), "-s", serial, "shell", f"cat {marker} 2>/dev/null"],
                capture_output=True, text=True, timeout=10,
            )
            if (remote_marker.stdout or "").strip().splitlines()[0] == expected_sha:
                return {"success": True, "skipped": True, "reason": f"Bundle {manifest.get('name', bundle)} already in sync"}
        except Exception:
            pass

    try:
        adb_shell(f"mkdir -p {remote_dir}", timeout=10)
        remote_bundle = f"{remote_dir}/.stp_tmp_bundle.tar.gz"
        adb_push(bundle, remote_bundle)
        adb_push(manifest_path, f"{remote_dir}/manifest.json")
        adb_shell(
            f"cd {remote_dir} && tar xf .stp_tmp_bundle.tar.gz && "
            f"rm .stp_tmp_bundle.tar.gz && echo {expected_sha} > .stp_bundle_sha256",
            timeout=600,
        )
        return {
            "success": True,
            "bundle_name": manifest.get("name", ""),
            "file_count": manifest.get("file_count", 0),
            "total_size_bytes": manifest.get("total_size_bytes", 0),
        }
    except Exception as exc:
        return {"success": False, "error": f"bundle push failed: {exc}"}


def main() -> None:
    serial = device_serial()
    args = params()

    bundle = args.get("bundle")
    manifest_path = args.get("manifest")

    if bundle and manifest_path:
        bundle = _resolve_path(bundle)
        manifest_path = _resolve_path(manifest_path)
        remote_dir = args.get("remote_dir", "/sdcard/test_resources").rstrip("/")
        skip_if_match = args.get("skip_if_match", True)
        result = _push_bundle(serial, bundle, manifest_path, remote_dir, skip_if_match)
    else:
        files = args.get("files", [])
        if not files:
            output_result(True, skipped=True, skip_reason="No files to push")
            return
        result = _push_files(serial, files)

    if result.get("success"):
        metrics = {k: v for k, v in result.items() if k not in ("success", "error")}
        output_result(True, **metrics)
    else:
        output_result(False, error_message=result.get("error", "push failed"))


if __name__ == "__main__":
    main()
