"""复合设备初始化脚本：按序执行 WiFi / Root / 推送 / 安装 / 填充 / 清理。

v2.1.0 相对 v2.0.0 的唯一差异：**缺省步骤不再含 `fill`**（跟随 v1.3.0 的判断）。
填充存储到 60% 在百 GB 级 /data 上要写十几 GB，而 `step_fill` 里 dd 的硬超时是
300s —— 183 台实跑时它是绝大多数失败的唯一原因。需要存储压力时显式传 `steps`
把 "fill" 加回来，并同时放宽 dd 超时。

v2.0.0 相对 v1.0.0 的差异：**WiFi 变成可选**。没有 SSID 时 `wifi`
步骤返回 skipped 而不是失败，因此计划可以在「不连接 WiFi」下正常跑完；
需要连接时由平台在执行前按所选 WiFi 资源池注入 `params.wifi.{ssid,password}`。
注入后若连接失败，仍然是硬失败。

可通过平台 script: 动作调用，也可直接在节点上运行：
    STP_WIFI_SSID=... STP_WIFI_PASSWORD=... python monkey_setup.py

环境变量:
    STP_DEVICE_SERIAL    (required)
    STP_ADB_PATH         (default: adb)
    STP_NFS_ROOT         (default: /mnt/nfs)
    STP_WIFI_SSID        (WiFi 凭据——由平台 ResourcePool 注入；留空=不连接)
    STP_WIFI_PASSWORD
    STP_STEP_PARAMS      (optional, JSON——覆盖步骤配置)

STP_STEP_PARAMS 结构:
{
    "steps": ["wifi", "root", "push", "install", "fill", "clean"],   // 要执行的步骤，默认全部
    "wifi": {"ssid": "...", "password": "...", "timeout_seconds": 30},
    "root": {"max_attempts": 3},
    "push": {"bundle": "/nfs/bundles/app.tar.gz", "manifest": "/nfs/bundles/manifest.json",
             "remote_dir": "/sdcard/test_resources"},
    "install": {"apk_path": "/nfs/apks/app.apk", "pkg_name": "com.example.app",
                "required_version": "1.0.0"},
    "fill": {"target_percentage": 60},
    "clean": {"uninstall_packages": [], "clear_logs": true}
}

输出 (stdout):
    {"success": true/false, "error_message": "...", "metrics": {"steps": {...}, "total_duration_s": 123}}
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from _adb import adb_path, adb_shell, adb_shell_quiet, adb_push, device_serial, output_result, params


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    root = os.environ.get("STP_NFS_ROOT", "/mnt/nfs")
    return os.path.join(root, path)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── step implementations ────────────────────────────────────────────

def step_wifi(serial: str, cfg: dict) -> dict:
    ssid = cfg.get("ssid") or os.environ.get("STP_WIFI_SSID", "")
    password = cfg.get("password") or os.environ.get("STP_WIFI_PASSWORD", "")
    if not ssid:
        # v2.0.0 起：没配 SSID = 本次执行没勾选「连接 WiFi」，跳过而不是失败。
        # v1.0.0 在这里返回 success=False，导致只要平台没注入凭据，整个
        # lifecycle init 就挂在这一步——而平台的 ResourcePool 注入只认
        # connect_wifi 动作，从不注入 monkey_setup，于是必然失败。
        # 「勾了却没连上」仍然是硬失败（下面的 connect 分支），只有「没勾」才跳过。
        return {"success": True, "skipped": True, "reason": "No SSID configured"}

    try:
        status = adb_shell_quiet("cmd -w wifi status", timeout=10)
        if ssid in (status.stdout or ""):
            return {"success": True, "skipped": True, "reason": f"Already connected to {ssid}"}
    except Exception:
        pass

    adb_shell("svc wifi enable", timeout=10)
    time.sleep(1)
    result = adb_shell(f'cmd -w wifi connect-network "{ssid}" wpa2 "{password}"', timeout=cfg.get("timeout_seconds", 30))
    if "Error" in (result or ""):
        return {"success": False, "error": f"WiFi connect failed: {result.strip()}"}
    return {"success": True, "ssid": ssid}


def step_root(serial: str, cfg: dict) -> dict:
    max_attempts = cfg.get("max_attempts", 3)

    try:
        result = adb_shell_quiet("id -u", timeout=10)
        if (result.stdout or "").strip() == "0":
            return {"success": True, "skipped": True, "reason": "Already root"}
    except Exception:
        pass

    for attempt in range(1, max_attempts + 1):
        try:
            subprocess.run(
                [adb_path(), "-s", serial, "root"],
                capture_output=True, text=True, timeout=10,
            )
            time.sleep(3)
            result = adb_shell_quiet("id -u", timeout=10)
            if (result.stdout or "").strip() == "0":
                return {"success": True, "attempts": attempt}
        except Exception as exc:
            if attempt == max_attempts:
                return {"success": False, "error": f"Root failed after {max_attempts} attempts: {exc}"}
            time.sleep(2)

    return {"success": False, "error": f"Root not granted after {max_attempts} attempts"}


def step_push(serial: str, cfg: dict) -> dict:
    bundle = cfg.get("bundle")
    manifest_path = cfg.get("manifest")

    if bundle and manifest_path:
        bundle = _resolve_path(bundle)
        manifest_path = _resolve_path(manifest_path)
        remote_dir = cfg.get("remote_dir", "/sdcard/test_resources").rstrip("/")
        skip_if_match = cfg.get("skip_if_match", True)

        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as exc:
            return {"success": False, "error": f"manifest load failed: {exc}"}

        expected_sha = manifest.get("bundle_sha256", "")
        if not expected_sha:
            return {"success": False, "error": "manifest.bundle_sha256 required"}
        actual_sha = _sha256_file(bundle)
        if actual_sha != expected_sha:
            return {"success": False, "error": f"sha256 mismatch: {expected_sha} vs {actual_sha}"}

        marker = f"{remote_dir}/.stp_bundle_sha256"
        if skip_if_match:
            try:
                remote = adb_shell(f"cat {marker} 2>/dev/null", timeout=10)
                if remote.strip().splitlines()[0] == expected_sha:
                    return {"success": True, "skipped": True, "reason": "Bundle already in sync"}
            except Exception:
                pass

        adb_shell(f"mkdir -p {remote_dir}", timeout=10)
        adb_push(bundle, f"{remote_dir}/.stp_tmp_bundle.tar.gz")
        adb_push(manifest_path, f"{remote_dir}/manifest.json")
        adb_shell(
            f"cd {remote_dir} && tar xf .stp_tmp_bundle.tar.gz && "
            f"rm .stp_tmp_bundle.tar.gz && echo {expected_sha} > .stp_bundle_sha256",
            timeout=600,
        )
        return {"success": True, "bundle": manifest.get("name", ""), "files": manifest.get("file_count", 0)}

    files = cfg.get("files", [])
    if not files:
        return {"success": True, "skipped": True, "reason": "No files/bundle configured"}
    pushed = 0
    for f in files:
        local = _resolve_path(f.get("local", ""))
        remote = f.get("remote", "")
        if not local or not remote:
            continue
        adb_push(local, remote)
        if f.get("chmod"):
            adb_shell(f"chmod {f['chmod']} {remote}", timeout=10)
        pushed += 1
    return {"success": True, "files_pushed": pushed}


def step_install(serial: str, cfg: dict) -> dict:
    apk_path = cfg.get("apk_path", "")
    if not apk_path:
        return {"success": True, "skipped": True, "reason": "No apk_path configured"}
    apk_path = _resolve_path(apk_path)

    pkg_name = cfg.get("pkg_name", "")
    required_version = cfg.get("required_version", "")

    if pkg_name and required_version:
        try:
            result = subprocess.run(
                [adb_path(), "-s", serial, "shell", f"dumpsys package {pkg_name} | grep versionName"],
                capture_output=True, text=True, timeout=10,
            )
            if required_version in (result.stdout or ""):
                return {"success": True, "skipped": True, "reason": f"{pkg_name}=={required_version} installed"}
        except Exception:
            pass

    flags = ["-r"] if cfg.get("reinstall", True) else []
    result = subprocess.run(
        [adb_path(), "-s", serial, "install"] + flags + [apk_path],
        capture_output=True, text=True, timeout=cfg.get("timeout_seconds", 120),
    )
    output = (result.stdout or "").strip()
    if result.returncode != 0 or "Failure" in output:
        return {"success": False, "error": f"Install failed: {output}"}
    return {"success": True, "apk": apk_path}


def step_fill(serial: str, cfg: dict) -> dict:
    target_pct = cfg.get("target_percentage", 60)
    result = adb_shell_quiet("df /data", timeout=10)
    lines = (result.stdout or "").strip().splitlines()
    if len(lines) < 2:
        return {"success": False, "error": "Cannot parse df"}
    parts = lines[1].split()
    if len(parts) < 4:
        return {"success": False, "error": "Cannot parse df columns"}

    total_kb = int(parts[1])
    used_kb = int(parts[2])
    need_kb = total_kb * target_pct // 100 - used_kb

    if need_kb <= 0:
        return {"success": True, "skipped": True, "reason": f"Already at {used_kb * 100 // total_kb}%"}

    block_size = cfg.get("block_size_kb", 1024)
    blocks = max(need_kb // block_size, 1)
    fill_path = cfg.get("fill_path", "/data/local/tmp/fill.bin")
    subprocess.run(
        [adb_path(), "-s", serial, "shell",
         f"dd if=/dev/zero of={fill_path} bs={block_size}k count={blocks}"],
        capture_output=True, text=True, timeout=300,
    )
    return {"success": True, "filled_kb": need_kb}


def step_clean(serial: str, cfg: dict) -> dict:
    errors = []
    for pkg in cfg.get("uninstall_packages", []):
        try:
            adb_shell(f"pm uninstall {pkg}", timeout=30)
        except Exception as exc:
            if "not installed" not in str(exc).lower():
                errors.append(f"Uninstall {pkg}: {exc}")

    if cfg.get("clear_logs", False):
        for d in cfg.get("log_dirs", ["/data/aee_exp", "/data/vendor/aee_exp", "/data/debuglogger/mobilelog"]):
            try:
                adb_shell(f"rm -rf {d}/*", timeout=30)
                adb_shell(f"mkdir -p {d}", timeout=10)
            except Exception as exc:
                errors.append(f"Clear {d}: {exc}")

    for key, value in cfg.get("set_properties", {}).items():
        try:
            adb_shell(f"setprop {key} {value}", timeout=10)
        except Exception as exc:
            errors.append(f"setprop {key}: {exc}")

    if errors:
        return {"success": False, "error": "; ".join(errors)}
    return {"success": True}


# ── step registry ───────────────────────────────────────────────────

STEPS = {
    "wifi":     step_wifi,
    "root":     step_root,
    "push":     step_push,
    "install":  step_install,
    "fill":     step_fill,
    "clean":    step_clean,
}


def main() -> None:
    serial = device_serial()
    args = params()

    # v2.1.0 起缺省不再跑 `fill`（跟随 v1.3.0 的判断）。
    # 实测：填到 60% 在 101.7 GB 的 /data 上要写 ~19.5 GB，而 step_fill 里 dd 的
    # 硬超时是 300s —— 需要持续 67 MB/s，`adb shell dd` 达不到，一台 host 挂十几台
    # 设备时更不可能。183 台实跑时它是 40/42 次失败的唯一原因，还把 3 台 host 卡在
    # BARRIER_WAIT（设备困在 init，barrier 凑不齐），连累同 host 的设备进不了 PATROL。
    # 需要存储压力时显式传 steps 把 "fill" 加回来，并同时放宽 dd 超时。
    step_names = args.get("steps", ["wifi", "root", "push", "install", "clean"])
    unknown = [s for s in step_names if s not in STEPS]
    if unknown:
        output_result(False, error_message=f"Unknown steps: {unknown}")
        sys.exit(1)

    t0 = time.time()
    results = {}
    overall_success = True

    for name in step_names:
        cfg = args.get(name, {})
        try:
            result = STEPS[name](serial, cfg)
        except subprocess.TimeoutExpired:
            result = {"success": False, "error": f"Step '{name}' timed out"}
        except Exception as exc:
            result = {"success": False, "error": f"Step '{name}' exception: {exc}"}

        results[name] = result
        if not result.get("success"):
            overall_success = False
            break

    duration = round(time.time() - t0, 1)
    if overall_success:
        output_result(True, steps=results, total_duration_s=duration)
    else:
        failed_step = [k for k, v in results.items() if not v.get("success")][0]
        output_result(
            False,
            error_message=f"Step '{failed_step}' failed: {results[failed_step].get('error', 'unknown')}",
            steps=results,
            total_duration_s=duration,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
