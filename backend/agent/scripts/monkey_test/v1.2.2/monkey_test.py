"""Monkey 一站式脚本：推送资源 + 启动 AIMonkey 测试。

替代 monkey_setup + monkey_launch，将 AIMonkeyTest 二进制（aim/aimwd/aimonkey.apk）
推送至设备，启动 MonkeyTest.sh 看门狗和 aimwd 守护进程。

可直接在节点上运行：
    STP_DEVICE_SERIAL=... python monkey_test.py

环境变量:
    STP_DEVICE_SERIAL      (required)
    STP_ADB_PATH           (default: adb)
    STP_STEP_PARAMS        (optional JSON)

STP_STEP_PARAMS:
{
    "push_resources": true,       // 推送 /sdcard/resource/ 媒体资源
    "blacklist": true,            // 使用黑名单
    "need_nohup": true,           // nohup 启动
    "aimonkey_dir": "/opt/stability-test-agent/agent/resources/aimonkey/AIMonkeyTest_20260317"
}

输出 (stdout):
    {"success": true/false, "error_message": "...", "metrics": {...}}

#507 三要素（v1.2.0）：机型路由决策（AD11 例外分支 vs 通用）写入
metrics.route（decided_by/model/branch），step_trace 可审计；AD11 专属
脚本缺失 **fail-fast** 不静默回退。

v1.2.1（#808）：stdout 只允许最终 JSON——资源推送日志改 stderr（普通行，
不污染引擎对整份 stdout 的 json.loads）；资源目录缺失不再静默 no-op，
成功 metrics 增 `resource_push`（disabled / missing / pushed:N）。

v1.2.2（#809）：aimwd 按 is_file 推送并回验（旧 is_dir 守卫让它永远推不上
设备，双看门狗只剩单层）；启动后轮询 ps 确认真有 MonkeyWatchdog 进程，
未见计入 errors；黑名单/看门狗/媒体/可选资源推送 rc 全部计入 errors、
chmod rc 检查；monkey_running 在 ≥15s 窗口复查（排除 MonkeyWatchdog——
其 cmdline 也含 com.android.commands.monkey，不排除会让门禁失真），仍未见
计入 errors 作为成功门禁。
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, device_serial, output_result, params

_AGENT_ROOT = Path(__file__).resolve().parents[3]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from aimonkey_paths import resolve_aimonkey_bundle_dir  # noqa: E402


# ── 资源解析 ──

def _resolve_aimonkey_dir(cfg: dict) -> Path:
    return resolve_aimonkey_bundle_dir(cfg)


def _run_adb(serial: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """执行 adb 命令并返回 (returncode, stdout, stderr)。"""
    result = subprocess.run(
        [adb_path(), "-s", serial] + args,
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _push_file(serial: str, local: str, remote: str, timeout: int = 60) -> bool:
    """推送单个文件到设备，返回是否成功。"""
    rc, out, err = _run_adb(serial, ["push", local, remote], timeout=timeout)
    return rc == 0


def _shell(serial: str, cmd: str, timeout: int = 30) -> tuple[int, str]:
    """执行 adb shell 命令，返回 (returncode, stdout)。"""
    rc, out, err = _run_adb(serial, ["shell", cmd], timeout=timeout)
    return rc, out


def _wait_ps_process(
    serial: str,
    pattern: str,
    timeout_s: float,
    interval_s: float,
    exclude: str = "",
) -> bool:
    """在窗口内轮询 ps，见到 pattern 进程即 True；窗口耗尽返回 False。

    #809：启动是异步的——瞬时不见 ≠ 没有；反过来 nohup 的 shell rc 恒 0，
    也不能把"adb 通"当"进程起来了"。exclude 用于排除 cmdline 同样命中
    pattern 的伴生进程（如 MonkeyWatchdog）。
    """
    cmd = f"ps -ef | grep '{pattern}' | grep -v grep"
    if exclude:
        cmd += f" | grep -v '{exclude}'"
    cmd += " | head -5"
    deadline = time.monotonic() + timeout_s
    while True:
        _, ps_out = _shell(serial, cmd, timeout=15)
        if pattern.lower() in ps_out.lower():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_s)


def _get_device_model(serial: str) -> str:
    """获取设备型号。"""
    _, model = _shell(serial, "getprop ro.product.model", timeout=10)
    return model.strip()


# ── 主流程 ──

def main():
    serial = device_serial()
    args = params()

    aimonkey_dir = _resolve_aimonkey_dir(args)
    if not aimonkey_dir.is_dir():
        output_result(False, error_message=f"AIMonkeyTest dir not found: {aimonkey_dir}")
        return

    push_resources = bool(args.get("push_resources", True))
    need_nohup = bool(args.get("need_nohup", True))
    blacklist = bool(args.get("blacklist", True))
    is_sleep = bool(args.get("sleep_mode", False))
    play_video = bool(args.get("play_video", False))
    memory_rw = bool(args.get("memory_rw", False))

    t0 = time.time()
    errors = []

    # ── 0. 设备预检 ──
    rc, _ = _shell(serial, "echo ready", timeout=10)
    if rc != 0:
        output_result(False, error_message=f"Device {serial} not reachable")
        return

    # ── 1. 获取设备型号 ──
    device_model = _get_device_model(serial)

    # ── 2. 推送 monkey 二进制 (aim / aimwd / aim.jar / arm64-v8a / armeabi-v7a) ──
    for name in ["aim", "aim.jar"]:
        local = str(aimonkey_dir / name)
        if os.path.exists(local):
            if not _push_file(serial, local, f"/data/local/tmp/{name}"):
                errors.append(f"push failed: {name}")

    # 推送架构库目录
    for arch_dir in ["arm64-v8a", "armeabi-v7a"]:
        local_arch = aimonkey_dir / arch_dir
        if local_arch.is_dir():
            if not _push_file(serial, str(local_arch), "/data/local/tmp/"):
                errors.append(f"push failed: {arch_dir}")

    # 推送 aimwd 守护进程（#809：仓库 bundle 中 aimwd 是普通脚本文件——旧
    # is_dir() 守卫让它永远推不上设备，全新设备双看门狗只剩单层）
    local_aimwd = aimonkey_dir / "aimwd"
    if local_aimwd.is_file():
        if not _push_file(serial, str(local_aimwd), "/data/local/tmp/aimwd"):
            errors.append("push failed: aimwd")
        else:
            _, verify_out = _shell(
                serial, "test -s /data/local/tmp/aimwd && echo OK || echo MISSING", timeout=10
            )
            if "OK" not in verify_out:
                errors.append("aimwd verification failed on device (test -s)")
    else:
        errors.append(f"aimwd bundle file missing: {local_aimwd}")

    # 推送 aimonkey.apk → monkey.apk
    local_apk = str(aimonkey_dir / "aimonkey.apk")
    if os.path.exists(local_apk):
        if not _push_file(serial, local_apk, "/data/local/tmp/monkey.apk"):
            errors.append("push failed: aimonkey.apk")

    # 设置权限（#809：rc 非零计入 errors——对未推成功的文件 chmod 失败可见）
    for f in ["aim", "aimwd"]:
        rc_chmod, _ = _shell(serial, f"chmod 777 /data/local/tmp/{f}", timeout=10)
        if rc_chmod != 0:
            errors.append(f"chmod failed: {f}")

    # ── 3. 推送 MonkeyTest 看门狗脚本 ──
    _shell(serial, "mkdir -p /sdcard/systeminfo", timeout=10)

    # 机型路由决策（#507 三要素：指纹 → 分支 → 决策进 metrics.route，
    # step_trace 可审计）。unknown 机型走通用脚本（MonkeyTestAi.sh 是
    # 合理默认，AD11 是例外分支——非闭集白名单）。
    route = {"decided_by": "fingerprint", "model": device_model, "branch": "generic"}

    if is_sleep:
        if not _push_file(serial, str(aimonkey_dir / "blacklist.txt"), "/sdcard/blacklist.txt"):
            errors.append("push failed: blacklist.txt")
        if not _push_file(serial, str(aimonkey_dir / "offlinemonkey.sh"), "/data/local/tmp/MonkeyTest.sh"):
            errors.append("push failed: offlinemonkey.sh")
    else:
        if not _push_file(serial, str(aimonkey_dir / "blacklist.txt"), "/sdcard/blacklist.txt"):
            errors.append("push failed: blacklist.txt")

        if play_video or memory_rw:
            if not _push_file(serial, str(aimonkey_dir / "MonkeyTestAi_PlayVideo_MemoryRW.sh"),
                              "/data/local/tmp/MonkeyTest.sh"):
                errors.append("push failed: MonkeyTestAi_PlayVideo_MemoryRW.sh")
        elif "AD11" in device_model:
            ad11_script = aimonkey_dir / "MonkeyTestAiAD11.sh"
            if not ad11_script.is_file():
                # #507：AD11 专属脚本缺失**不静默回退**——回退会让
                # 「AD11 设备跑了通用脚本」事后无从归因（asset 缺失是
                # 配置错误，不是路由决策）
                output_result(
                    False,
                    error_message=(
                        f"AD11 MonkeyTest script missing: {ad11_script}; "
                        "refusing silent fallback to generic script"
                    ),
                )
                return
            route["branch"] = "AD11"
            if not _push_file(serial, str(ad11_script), "/data/local/tmp/MonkeyTest.sh"):
                errors.append("push failed: MonkeyTestAiAD11.sh")
        else:
            if not _push_file(serial, str(aimonkey_dir / "MonkeyTestAi.sh"),
                              "/data/local/tmp/MonkeyTest.sh"):
                errors.append("push failed: MonkeyTestAi.sh")

    # ── 4. 推送媒体资源 ──
    resource_push_status = "disabled"
    if push_resources:
        resource_dir = aimonkey_dir / "resource"
        if resource_dir.is_dir():
            _shell(serial, "mkdir -p /sdcard/resource", timeout=10)
            pushed_count = 0
            failed_count = 0
            for root, _dirs, files in os.walk(resource_dir):
                for f in files:
                    local = os.path.join(root, f)
                    if _push_file(serial, local, f"/sdcard/resource/{f}", timeout=300):
                        pushed_count += 1
                    else:
                        failed_count += 1
            resource_push_status = f"pushed:{pushed_count}"
            # #809：媒体推送失败不再静默丢弃（rc 计入 errors）
            if failed_count:
                resource_push_status = f"pushed:{pushed_count}/failed:{failed_count}"
                errors.append(f"resource push failed: {failed_count} file(s)")
            # #808：stdout 只允许最终 JSON——日志必须走 stderr
            print(
                f"[monkey_test] 已推送 {pushed_count} 个媒体资源",
                file=sys.stderr,
                flush=True,
            )
        else:
            # #808：资源目录缺失不再是静默 no-op——metrics 留痕可见
            resource_push_status = "missing"

    # ── 5. 推送 play_video / memory_rw 相关文件 ──
    if play_video:
        if not _push_file(serial, str(aimonkey_dir / "monkey_video.3gpp"), "/sdcard/monkey_video.3gpp"):
            errors.append("push failed: monkey_video.3gpp")
    if memory_rw:
        if not _push_file(serial, str(aimonkey_dir / "monkey_rw_test_64bit"), "/data/local/tmp/monkey_rw_test_64bit"):
            errors.append("push failed: monkey_rw_test_64bit")
        _shell(serial, "chmod 777 /data/local/tmp/monkey_rw_test_64bit", timeout=10)

    # ── 6. 启动 Monkey 测试 ──
    if need_nohup:
        cmd = "cd /data/local/tmp; nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"
        rc, _ = _shell(serial, cmd, timeout=60)
    else:
        cmd = "nohup sh /data/local/tmp/MonkeyTest.sh >/dev/null 2>&1 &"
        rc, _ = _shell(serial, cmd, timeout=60)

    if rc != 0:
        errors.append(f"MonkeyTest.sh start failed (rc={rc})")

    # 启动 aimwd 守护进程并回验（#809：aimwd 是普通脚本文件，推不上/起不来
    # 以前都静默——nohup rc 恒 0；必须 ps 见到 MonkeyWatchdog 才算第二层就位）
    _shell(serial, "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &", timeout=15)
    if not _wait_ps_process(serial, "MonkeyWatchdog", timeout_s=15, interval_s=2):
        errors.append("aimwd (MonkeyWatchdog) not observed within 15s after start")

    # ── 7. 确认 monkey 进程已启动（#809：瞬时未见 → ≥15s 窗口复查；
    #     仍未见计入 errors 作为成功门禁，对齐 monkey_launch 的 post-check。
    #     排除 MonkeyWatchdog——其 cmdline 也含 com.android.commands.monkey）
    monkey_running = _wait_ps_process(
        serial, "monkey", timeout_s=15, interval_s=3, exclude="MonkeyWatchdog"
    )
    if not monkey_running:
        errors.append("monkey process not observed within 15s after start")

    elapsed = round(time.time() - t0, 1)

    if errors:
        output_result(
            False,
            error_message="; ".join(errors),
            metrics={
                "serial": serial,
                "device_model": device_model,
                "route": route,
                "aimonkey_dir": str(aimonkey_dir),
                "duration_s": elapsed,
                "monkey_running": monkey_running,
                "resource_push": resource_push_status,
            },
        )
    else:
        output_result(
            True,
            metrics={
                "serial": serial,
                "device_model": device_model,
                "route": route,
                "aimonkey_dir": str(aimonkey_dir),
                "duration_s": elapsed,
                "monkey_running": monkey_running,
                "push_resources": push_resources,
                "resource_push": resource_push_status,
                "blacklist": blacklist,
            },
        )


if __name__ == "__main__":
    main()
