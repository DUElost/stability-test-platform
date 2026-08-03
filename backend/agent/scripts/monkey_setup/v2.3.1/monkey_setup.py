"""复合设备初始化脚本：按序执行 WiFi / Root / 推送 / 安装 / 填充 / 清理。

v2.3.0 相对 v2.2.0 的差异：**长耗时步骤打 PROGRESS 戳**（#115 阶段 2）。
停滞判据只认 PROGRESS 戳（普通输出不算活），所以 push 的传输与 fill 的填盘
在打开 stall_seconds 后必须自己打戳，否则会被误判停滞：
  - push：解析 adb push 的 `[ NN%]` 进度行，百分比变化打戳
  - fill：dd status=progress 解析已写字节，增长打戳
打戳走 stderr（stdout 是结果契约）。seq 单调递增是唯一判据。

v2.2.0 相对 v2.1.0 的差异：**内层超时不再写死**。原本 `push` 的 tar 解包(600s)、
`fill` 的 dd(300s)、以及 `_adb.adb_push` 的传输(120s 缺省)都硬编码在代码里 ——
对外不可见、不可配，运维在 UI 上看到 PlanStep 的 `timeout_seconds` 会以为"还有
余量"，实际被一个看不见的内层常数掐死。超时文案也标注了是**脚本内层**限制。

`push` 有两个独立的钟，别混：搬数据的是 `adb_push`，解包的是后面那条 tar。
大 bundle 撞的是前者的 120s，不是后者的 600s。两者互不回落，各自保持原缺省。

    "push":    {"timeout_seconds": 600,      # tar 解包
                "push_timeout_seconds": 120} # adb push 传输
    "install": {"timeout_seconds": 120}      # pm install（v2.1.0 起就已可配）
    "fill":    {"timeout_seconds": 300}      # dd 填充

v2.1.0 相对 v2.0.0：**缺省步骤不再含 `fill`**（跟随 v1.3.0 的判断）。填充存储到
60% 在百 GB 级 /data 上要写十几 GB，300s 根本不够；183 台实跑时它是绝大多数失败
的唯一原因。需要存储压力时显式传 `steps` 加回来，并同时放宽 `fill.timeout_seconds`。

v2.0.0 相对 v1.0.0：**WiFi 变成可选**。没有 SSID 时 `wifi` 步骤返回 skipped
而不是失败，因此计划可以在「不连接 WiFi」下正常跑完；需要连接时由平台在执行前
按所选 WiFi 资源池注入 `params.wifi.{ssid,password}`。注入后若连接失败，仍然是硬失败。

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
from _adb import (
    _progress_stamp,
    adb_path,
    adb_push_progress,
    adb_shell,
    adb_shell_quiet,
    device_serial,
    output_result,
    params,
)


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


def _push_or_timeout(local: str, remote: str, timeout: int,
                    progress: "callable | None" = None) -> dict | None:
    """adb push，带进度戳；超时时返回指名**传输钟**的失败结果。

    不能让它冒泡到 main 的通用 TimeoutExpired 处理 —— 那里只会说
    "override via STP_STEP_PARAMS.push.timeout_seconds"，而 push 有两个独立
    的钟：`timeout_seconds` 管 tar 解包，`push_timeout_seconds` 才管传输。
    报错指错旋钮会让人调半天没反应。

    progress 回调在传输期间打 PROGRESS 戳（#115 阶段 2）——停滞判据只认
    戳，传输阶段没有戳会在 stall_seconds 后被杀。
    """
    try:
        adb_push_progress(local, remote, timeout=timeout, on_progress=progress)
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": (
                f"adb push timed out after {timeout}s: {local} -> {remote} "
                f"(script-internal transfer limit; override via "
                f"STP_STEP_PARAMS.push.push_timeout_seconds)"
            ),
        }
    return None


def _make_progress(step: str) -> "callable":
    """返回打戳回调：seq 单调递增，语义字段仅供人读。"""
    state = {"seq": 0}

    def _emit(**fields) -> None:
        state["seq"] += 1
        sys.stderr.write(_progress_stamp(state["seq"], step=step, **fields) + "\n")
        sys.stderr.flush()

    return _emit


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
        # 真正搬数据的是 adb push，不是后面的 tar 解包。大 bundle 超过
        # _adb.adb_push 的 120s 缺省很容易发生，所以它必须可配 —— 否则就是
        # fill 的 300s 问题换个数字继续存在。
        push_timeout = cfg.get("push_timeout_seconds", 120)
        progress = _make_progress("push")
        for src, dst in (
            (bundle, f"{remote_dir}/.stp_tmp_bundle.tar.gz"),
            (manifest_path, f"{remote_dir}/manifest.json"),
        ):
            failure = _push_or_timeout(src, dst, push_timeout, progress=progress)
            if failure:
                return failure
        adb_shell(
            f"cd {remote_dir} && tar xf .stp_tmp_bundle.tar.gz && "
            f"rm .stp_tmp_bundle.tar.gz && echo {expected_sha} > .stp_bundle_sha256",
            timeout=cfg.get("timeout_seconds", 600),
        )
        return {"success": True, "bundle": manifest.get("name", ""), "files": manifest.get("file_count", 0)}

    files = cfg.get("files", [])
    if not files:
        return {"success": True, "skipped": True, "reason": "No files/bundle configured"}
    push_timeout = cfg.get("push_timeout_seconds", 120)
    progress = _make_progress("push")
    pushed = 0
    for f in files:
        local = _resolve_path(f.get("local", ""))
        remote = f.get("remote", "")
        if not local or not remote:
            continue
        failure = _push_or_timeout(local, remote, push_timeout, progress=progress)
        if failure:
            return failure
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
    _dd_with_progress(
        serial, fill_path, block_size, blocks,
        timeout=cfg.get("timeout_seconds", 300),
    )
    return {"success": True, "filled_kb": need_kb}


# 每块 512MB：单次 dd 在 USB 上 ~5-30s，块级超时 120s 安全；
# 39 块 × ~1s adb 开销 ≈ 40s 附加开销，可接受。测试会 monkeypatch 成小块。
_FILL_CHUNK_KB = 512 * 1024


def _dd_with_progress(
    serial: str,
    fill_path: str,
    block_size: int,
    blocks: int,
    timeout: int,
) -> None:
    """dd 填盘，带 PROGRESS 戳（#115 阶段 2）。

    不用 dd status=progress：toybox dd 不一定支持该选项（review 指出，
    测试用假 adb 没覆盖真机）。改为**分块 dd 追加写 + stat 轮询**：

      - 每块 dd 是独立 adb shell 调用，`>>` 追加，`2>/dev/null` 关掉统计输出
      - 每块都检查 returncode，失败立即返回失败 —— 绝不"没填盘但报成功"
      - 每块完成后 stat 实际文件大小打戳（written_bytes 单调递增）

    adb_shell_quiet 返回 CompletedProcess，能拿到 returncode。
    """
    progress = _make_progress("fill")
    need_kb = blocks * block_size
    written_kb = 0
    while written_kb < need_kb:
        n = min(_FILL_CHUNK_KB, need_kb - written_kb)
        # **不要写 of=**：of= 让 dd 自己以截断方式打开目标，`>>` 只重定向 dd
        # 的 stdout，对 of= 无效 —— 每块都会"清空再写"而不是追加（实测两轮
        # 后文件还是 512KB）。必须让 dd 写到 stdout，由 shell 的 `>>` 追加。
        result = adb_shell_quiet(
            f"dd if=/dev/zero bs=1024 count={n} >> {fill_path} 2>/dev/null",
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"dd fill failed rc={result.returncode} at {written_kb}KiB: "
                f"{fill_path} ({result.stderr[:200]})"
            )
        written_kb += n
        # stat 实际大小作为进度（诚实：dd 报成功但文件没涨也能被看见）
        try:
            st = adb_shell_quiet(f"stat -c %s {fill_path}", timeout=10)
            if st.returncode == 0 and (st.stdout or "").strip().isdigit():
                progress(written_bytes=int(st.stdout.strip()))
        except Exception:
            progress(written_kb=written_kb)


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
        except subprocess.TimeoutExpired as exc:
            # 明确标注这是**脚本内层**超时，不是引擎的 PlanStep.timeout_seconds。
            # 两者是嵌套关系，内层先到期时外层根本不会触发；不写清楚会让人去调
            # 一个永远不生效的旋钮（2026-08-01 的 fill 故障就卡在这个误导上）。
            limit = getattr(exc, "timeout", None)
            hint = f" after {limit:g}s" if isinstance(limit, (int, float)) else ""
            result = {
                "success": False,
                "error": (
                    f"Step '{name}' timed out{hint} (script-internal limit; "
                    f"override via STP_STEP_PARAMS.{name}.timeout_seconds)"
                ),
            }
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
