"""Install an APK (or a split APK set) on the device via adb.

v1.2.0（2026-11-02）：支持 split APK 集。`apk_paths`（显式列表）或 `apk_dir`
（目录内 `*.apk`，`base.apk` 优先）走 `adb install-multiple -r`；单 `apk_path`
仍走原 `adb install -r`。新增 opt-in `verify_after_install`：为 true 且给出
`pkg_name` + `required_version` 时，安装后回读 `versionName` 并与目标精确比较，
不一致即失败（默认关闭，保持既有调用方行为）。

v1.0.2（#816）：已装版本判定改 `versionName=` 字段级精确比较——子串匹配下
required=1.0.1 会命中已装的 1.0.10，误判 skipped 导致所需 apk 永不安装。

v1.1.0（#1690）：adb install 段接入 PROGRESS 心跳——停滞钟只认 PROGRESS
戳（#115），大 apk 安装的长静默段会被误判停滞。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_NFS_ROOT        (prepended to relative apk paths)
    STP_STEP_PARAMS     (required, JSON:
                         {apk_path?, apk_paths?, apk_dir?, pkg_name?,
                          required_version?, verify_after_install?,
                          reinstall?, timeout_seconds?})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "...",
     "metrics": {"apk_paths": [...], "install_mode": "single|multiple", ...}}
"""

import glob
import os
import subprocess
from _adb import adb_path, device_serial, output_result, params, progress_heartbeat


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    root = os.environ.get("STP_NFS_ROOT", "")
    if root:
        return os.path.join(root, path)
    return path


def _resolve_apk_paths(args: dict) -> list[str]:
    """返回要安装的 APK 路径列表；参数缺失/文件不存在抛 ValueError。"""
    raw_paths = args.get("apk_paths")
    if raw_paths:
        if not isinstance(raw_paths, list) or not all(isinstance(p, str) and p for p in raw_paths):
            raise ValueError("apk_paths must be a non-empty list of paths")
        paths = [_resolve_path(p) for p in raw_paths]
    elif args.get("apk_dir"):
        directory = _resolve_path(str(args["apk_dir"]))
        if not os.path.isdir(directory):
            raise ValueError(f"apk_dir is not a directory: {directory}")
        paths = sorted(glob.glob(os.path.join(directory, "*.apk")))
        if not paths:
            raise ValueError(f"no .apk files found in apk_dir: {directory}")
        # base 优先，便于人工按命令回放；install-multiple 本身不要求顺序。
        paths.sort(key=lambda p: (os.path.basename(p) != "base.apk", os.path.basename(p)))
    elif args.get("apk_path"):
        paths = [_resolve_path(str(args["apk_path"]))]
    else:
        raise ValueError("No apk_path/apk_paths/apk_dir specified")

    # 显式路径不预检存在性：保持 v1.1.0 行为，由 adb install 报出具体错误；
    # apk_dir 走 glob，天然只返回存在的文件。
    return paths


def _installed_version(serial: str, pkg_name: str) -> str:
    """读已装 versionName；读不到返回空串（不阻断，交由安装/回读判定）。"""
    try:
        result = subprocess.run(
            [adb_path(), "-s", serial, "shell", f"dumpsys package {pkg_name} | grep versionName"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    for raw in (result.stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("versionName="):
            return line.split("=", 1)[1].strip()
    return ""


def main() -> None:
    serial = device_serial()
    args = params()

    pkg_name = args.get("pkg_name", "")
    required_version = args.get("required_version", "")
    verify_after_install = bool(args.get("verify_after_install", False))
    reinstall = args.get("reinstall", True)

    installed_version = ""
    if pkg_name and required_version:
        installed_version = _installed_version(serial, pkg_name)
        # #816：字段级精确比较；skip 语义只认 ==。skip 判定先于路径解析，
        # 保持旧版本「已同版本时即使 apk 不存在也不报错」的行为。
        if installed_version == required_version:
            skip_metrics = {"pkg_name": pkg_name, "installed_version": installed_version}
            for key in ("apk_path", "apk_paths", "apk_dir"):
                if args.get(key):
                    skip_metrics[key] = args[key]
            output_result(
                True,
                skipped=True,
                skip_reason=f"{pkg_name}=={required_version} already installed",
                metrics=skip_metrics,
            )
            return

    try:
        apk_paths = _resolve_apk_paths(args)
    except ValueError as exc:
        output_result(False, error_message=str(exc))
        return

    timeout = int(args.get("timeout_seconds") or 300)
    install_mode = "multiple" if len(apk_paths) > 1 else "single"
    base_metrics = {
        "apk_paths": apk_paths,
        "install_mode": install_mode,
        "pkg_name": pkg_name,
        "verify_after_install": verify_after_install,
    }

    try:
        flags = ["-r"] if reinstall else []
        adb_subcommand = "install-multiple" if install_mode == "multiple" else "install"
        cmd = [adb_path(), "-s", serial, adb_subcommand] + flags + apk_paths
        if install_mode == "multiple":
            phase = f"apk_install_multiple:{len(apk_paths)}"
        else:
            phase = f"apk_install:{os.path.basename(apk_paths[0])}"
        with progress_heartbeat(phase):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = (result.stdout or "").strip()

        if result.returncode != 0 or "Failure" in output:
            output_result(
                False,
                error_message=f"APK install failed: {output}",
                metrics={**base_metrics, "output": output[:500]},
            )
            return

        if verify_after_install and pkg_name and required_version:
            installed_version = _installed_version(serial, pkg_name)
            if installed_version != required_version:
                output_result(
                    False,
                    error_message=(
                        "post-install version mismatch: "
                        f"expected {required_version}, got {installed_version or '<empty>'}"
                    ),
                    metrics={
                        **base_metrics,
                        "installed_version": installed_version,
                        "output": output[:500],
                    },
                )
                return

        output_result(
            True,
            metrics={
                **base_metrics,
                "installed_version": installed_version if verify_after_install else None,
                "output": output[:500],
            },
        )
    except subprocess.TimeoutExpired:
        output_result(False, error_message=f"APK install timed out after {timeout}s", metrics=base_metrics)
    except Exception as exc:
        output_result(False, error_message=f"APK install failed: {exc}", metrics=base_metrics)


if __name__ == "__main__":
    main()
