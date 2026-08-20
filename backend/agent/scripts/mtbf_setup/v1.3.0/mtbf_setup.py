# -*- coding: utf-8 -*-
"""MTBF 部署 + 启动（init 阶段，ADR-0030 D6 P0 / P0 设计 §3.3）。

移植自 stability_MTBF-Test/scripts/deploy.ps1 + lib.ps1
（Install-MtbfApks / Push-MtbfConfig / Set-MtbfPrefs / Set-MtbfDeviceStability /
Start-MtbfTask / Test-MtbfSystemUid）。

可直接在 Agent 上运行：
    STP_DEVICE_SERIAL=... STP_STEP_PARAMS='{...}' python mtbf_setup.py

STP_STEP_PARAMS:
{
    "mtbf_resources_dir": "/opt/stability-test-agent/resources/mtbf",
    "project": "legacy",
    "task_times": 100,        // 覆盖 runtask.xml times；<=0 保持原值
    "tester": "tester",
    "install_apks": true,
    "auto_resume": true
}

输出 (stdout):
    {"success": true/false, "error_message": "...", "metrics": {...}}
metrics: {suite_sha256, apk_sha256: [...], testpoint_count, round_expected, run_dir}
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from _lib import (
    adb,
    adb_shell,
    count_testpoints,
    device_serial,
    output_result,
    param_or_env,
    params,
    patch_runtask_times,
    resources_dir,
    sha256_file,
    suite_dir,
)

# 与 lib.ps1:Install-MtbfApks 一致（精确文件名，勿缩写）
_CASE_APKS = ["ReliabilityUiautomatorTest.apk", "ReliabilityUiautomatorTestTest.apk"]
_OSM_APK = "OfflineScriptManager.apk"
_OSM_PACKAGE = "com.ape.offlinescriptmanager"


def _run(cfg: dict) -> dict:
    project = str(param_or_env(cfg, "project", "STP_MTBF_PROJECT", "legacy"))
    task_times = int(str(param_or_env(cfg, "task_times", "STP_MTBF_TASK_TIMES", "100")) or 0)
    tester = str(param_or_env(cfg, "tester", "STP_MTBF_TESTER", "tester"))
    auto_resume = str(param_or_env(cfg, "auto_resume", "STP_MTBF_AUTO_RESUME", "true")).lower() == "true"
    install_apks = str(param_or_env(cfg, "install_apks", "STP_MTBF_INSTALL_APKS", "true")).lower() == "true"

    sdir = suite_dir(project)
    rdir = resources_dir(cfg)
    suite_file = sdir / "runtask.xml"
    global_file = sdir / "UiAutomatorTestData.xml"

    # 1. 解析资源（fail-fast）
    if not suite_file.is_file():
        raise FileNotFoundError(f"runtask.xml 不存在: {suite_file}")
    if not global_file.is_file():
        raise FileNotFoundError(f"UiAutomatorTestData.xml 不存在: {global_file}")
    for name in _CASE_APKS + [_OSM_APK]:
        if not (rdir / name).is_file():
            raise FileNotFoundError(f"APK 不存在: {rdir / name}")

    # 2. 留痕：清单与 APK sha256（ADR-0029 v2.2 补偿机制）
    suite_sha = sha256_file(suite_file)
    apk_shas = {name: sha256_file(rdir / name) for name in _CASE_APKS + [_OSM_APK]}

    # 3. adb root 前置（prefs 写入 /data/data/... 必须 root；fail-fast，
    #    v1.2.0 曾忽略失败 → user 构建上表现为晦涩的 push rc=1）
    _ensure_adb_root()

    # 4. 安装 APK
    if install_apks:
        for name in _CASE_APKS:
            _install(rdir / name)
        _install(rdir / _OSM_APK, reinstall=True)   # 先 uninstall（签名/uid 变更场景）

    # 5. system uid 校验（经典坑：非 system uid 时用例全 Pass 但每条 <1s）
    info = adb_shell(f"dumpsys package {_OSM_PACKAGE}", timeout=30)
    if "android.uid.system" not in info:
        raise RuntimeError(
            "OfflineScriptManager 不是 system uid（sharedUser 非 android.uid.system），"
            "请使用 apps/OfflineScriptManager 的 platform 签名构建"
        )

    # 6. 推送配置（times patch + appops）
    patched = patch_runtask_times(suite_file.read_bytes(), task_times)
    _push_bytes(patched, "/sdcard/runtask.xml")
    _push_file(global_file, "/sdcard/UiAutomatorTestData.xml")
    adb_shell(f"appops set {_OSM_PACKAGE} MANAGE_EXTERNAL_STORAGE allow", timeout=30)

    # 7. 设备稳定性（7 天长跑：屏幕常亮、禁锁屏）
    adb_shell("svc power stayon true", timeout=30)
    adb_shell("settings put system screen_off_timeout 2147483647", timeout=30)
    adb_shell("settings put global stay_on_while_plugged_in 7", timeout=30)
    adb_shell("locksettings set-disabled true", timeout=30)

    # 8. 写 prefs（task_creator / auto_resume / isUpdating）
    _write_prefs(tester, auto_resume)

    # 9. 启动任务（force-stop → BatteryActivity → RunTaskService → 看门狗广播）
    adb_shell(f"am force-stop {_OSM_PACKAGE}", timeout=30)
    adb_shell(
        f"am start -n {_OSM_PACKAGE}/{_OSM_PACKAGE}.batterytool.BatteryActivity", timeout=30
    )
    time.sleep(2)
    adb_shell(
        f"am start-foreground-service -n {_OSM_PACKAGE}/{_OSM_PACKAGE}.view.RunTaskService "
        f"-a {_OSM_PACKAGE}.view.RunTaskService.action.start",
        timeout=30,
    )
    time.sleep(2)
    adb_shell(
        f"am broadcast -a {_OSM_PACKAGE}.action.MTBF_KEEPALIVE "
        f"-n {_OSM_PACKAGE}/.receiver.MtbfAutoResumeReceiver",
        timeout=30,
    )

    # 10. 验证：服务在跑 + 结果目录出现运行目录（≤60s）
    run_dir = _wait_run_dir(timeout_s=60)
    if not run_dir:
        raise RuntimeError("RunTaskService 未在 60s 内创建结果目录，启动失败")

    testpoint_count = count_testpoints(patched)
    return {
        "suite_sha256": suite_sha,
        "apk_sha256": apk_shas,
        "testpoint_count": testpoint_count,
        "round_expected": task_times if task_times > 0 else None,
        "run_dir": run_dir,
    }


def _ensure_adb_root() -> None:
    """MTBF 前置：prefs 写入 /data/data/... 必须 root 权限。

    ``adb root`` 的退出码不可靠（被拒时也常返回 0，仅打印
    "adbd cannot run as root in production builds"），以 ``id -u`` 为准；
    adbd 重启后 shell 可能短暂不可用，重试数次。user 构建（ro.debuggable=0）
    无法绕过——需 userdebug/eng 工程包，故 fail-fast 并带构建属性诊断。
    """
    _, out, err = adb("root", timeout=15)
    time.sleep(2)
    uid = ""
    for _ in range(5):
        uid = adb_shell("id -u", timeout=15).strip()
        if uid == "0":
            return
        time.sleep(1)
    build_type = adb_shell("getprop ro.build.type", timeout=15).strip()
    debuggable = adb_shell("getprop ro.debuggable", timeout=15).strip()
    detail = f"{out} {err}".strip()[:200]
    raise RuntimeError(
        f"设备 {device_serial()} 不满足 adb root 前置（id -u={uid or '?'}，"
        f"ro.build.type={build_type or '?'}，ro.debuggable={debuggable or '?'}）: "
        f"MTBF prefs 写入需要 root，user 构建请换 userdebug/eng 工程包；"
        f"adb root 输出: {detail or '（无输出）'}"
    )


def _install(apk: Path, reinstall: bool = False) -> None:
    if reinstall:
        adb_shell(f"am force-stop {_OSM_PACKAGE}", timeout=30)
        adb("uninstall", _OSM_PACKAGE, timeout=60)
    rc, out, _ = adb("install", "-r", str(apk), timeout=300)
    if rc != 0 or "Success" not in out:
        raise RuntimeError(f"安装失败: {apk.name}: {out.strip() or 'rc=%d' % rc}")


def _push_bytes(content: bytes, remote: str) -> None:
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        _push_file(Path(tmp_path), remote)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _push_file(local: Path, remote: str) -> None:
    rc, _, err = adb("push", str(local), remote, timeout=120)
    if rc != 0:
        raise RuntimeError(f"push 失败 {local.name} -> {remote}: {err.strip() or 'rc=%d' % rc}")


def _write_prefs(tester: str, auto_resume: bool) -> None:
    pref_dir = f"/data/data/{_OSM_PACKAGE}/shared_prefs"
    adb_shell(f"mkdir -p {pref_dir}", timeout=30)
    resume = "true" if auto_resume else "false"
    prefs = {
        "update_data.xml": f"""<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="isUpdating" value="true"/>
</map>
""",
        "test_task_data.xml": f"""<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="task_creator">{tester}</string>
</map>
""",
        "mtbf_runner.xml": f"""<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="auto_resume" value="{resume}"/>
</map>
""",
    }
    for name, content in prefs.items():
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
            tmp.write(content.encode("utf-8"))
            tmp_path = tmp.name
        try:
            _push_file(Path(tmp_path), f"{pref_dir}/{name}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    adb_shell(
        f"chown system:system {pref_dir}/update_data.xml {pref_dir}/test_task_data.xml {pref_dir}/mtbf_runner.xml",
        timeout=30,
    )
    adb_shell(
        f"chmod 660 {pref_dir}/update_data.xml {pref_dir}/test_task_data.xml {pref_dir}/mtbf_runner.xml",
        timeout=30,
    )


def _wait_run_dir(timeout_s: int = 60) -> str:
    """轮询 /sdcard/results/realresult/ 直到出现运行目录，返回目录名。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = adb_shell("ls /sdcard/results/realresult/", timeout=30).strip()
        names = [line for line in out.splitlines() if line.strip() and not line.startswith("total")]
        if names:
            return names[-1]   # 时间戳目录名按字典序即时间序，取最新
        time.sleep(5)
    return ""


def main() -> None:
    cfg = params()
    try:
        metrics = _run(cfg)
    except Exception as exc:  # noqa: BLE001 — 脚本顶层统一输出错误
        output_result(False, error_message=str(exc))
        sys.exit(1)
    output_result(True, metrics=metrics)


if __name__ == "__main__":
    main()
