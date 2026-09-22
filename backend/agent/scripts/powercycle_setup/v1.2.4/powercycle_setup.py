# -*- coding: utf-8 -*-
"""PowerCycle 部署 + 启动（init 阶段，issue #462 P0b；G15 对齐 §3.2）。

v1.2.4（#2802，2026-09-22）：把 v1.2.2 的「等就绪 + 有界重试」补到**服务启动**与
**uid 解析**两条裸等待分支——r503（首个含 v1.2.2 的窗）取证：残余 14 条里 2 条
`PowerCycleService 未在 30s 内启动` + 1 条 `无法解析 uid`，失败全落 T+0~30s，
设备仍在上一窗的重启循环里（~75s 周期 > 单次 30s 等待）；两条分支此前既无就绪门
也无证据字段（归类为「未升级报文」）。现：
- `_start_service_with_retry`：首试快速路径；失败后等系统就绪再重试，最多
  `STP_PCS_RETRY_MAX_ATTEMPTS`（默认 3）次、预算 `STP_PCS_RETRY_WAIT_BUDGET_SECONDS`
  （默认 90s）封顶；失败报文带 `attempts=` / `history=[` / `rc=` / `dumpsys=` 片段。
- `get_app_uid`（_lib.py）：同款重试（就绪门可覆盖 `STP_PCS_RETRY_READY_SECONDS`，
  默认 60s），失败报文带 `attempts=` / `history=[` / dumpsys 片段。

v1.2.3（#2979，2026-09-21）：`repair_prefs_ownership` 的删除判据再收紧——
v1.2.1/v1.2.2 里 `get_prefs_xml`（cat）与 `_prefs_file_state`（test -f）是**两次
独立 adb 调用**：cat 撞瞬时失败（超时 rc=-1 / 设备下线）而 test -f 成功回
present，仍会 `rm -f` 掉健康 prefs（#2846 数据丢失家族的另一条交错路径，
上一版修复只去掉了 run-as 恒空这个必然成因）。现存在性与内容共用**同一次**
root 探测（`_root_read_prefs`），且删除只认「文件存在、读取成功、内容为空」
这一种确定性损坏证据；rc≠0 / 超时 / 被拒（denied）/ 不存在（absent）一律
未知——保留文件，等下一轮读取恢复。

v1.2.2（#2802，2026-09-21）：`install_apk` 重试前等**系统就绪**（`get-state==device`
且 `sys.boot_completed==1`，与 `check_device v1.0.2` / `powercycle_finish` 同判定），
替代只等 adbd 可见的 `wait-for-device`；尝试上限默认 3 次、等待总预算默认 90s
（`STP_ATT_INSTALL_MAX_ATTEMPTS` / `STP_ATT_INSTALL_READY_SECONDS` /
`STP_ATT_INSTALL_WAIT_BUDGET_SECONDS` 可覆盖）；失败报文保留原文并追加
`attempts=` / `history=[`。r477 取证：残差 59 条里 21 条 `device is still booting`
+ 19 条 `push: device not found`——`wait-for-device` 只等 adbd、10s 退避又短于
~75s 重启周期，重试仍落在重启窗内。判定语义不变（仍要求 `Success`）。

v1.2.1（#2846，2026-09-20）：`repair_prefs_ownership` 不再把健康 prefs 误删——
判据从「run-as 读空 + 可 root ⇒ rm -f」改为「root 下能读就不删；读空必须
`test -f` 核验存在才删」。根因：AutoTestTool 是 platform 签名 system app
（shared uid `android.uid.system`），AOSP 对 non-debuggable/shared-uid 包**恒拒绝
run-as** ⇒ 旧读路径恒空 ⇒ 每次 set_prefs / set_stop_flags 前删真文件
（续跑 current_count 归零、auto_resume 断链、stop_flags 把完整 prefs 降成最小图）。

v1.2.0（#2756，2026-09-19）：install_apk 保留 push/pm 失败输出（v1.1.0 及
之前丢弃致只剩 rc=N 的诊断盲区）+ 重试前 wait-for-device 与短退避（默认 10s，
STP_ATT_INSTALL_RETRY_BACKOFF_SECONDS）——链交接瞬时 adb 风暴吸收
（run 431：67/537 台安装失败，随后全部自愈）。

v1.1.0（#1690）：长耗时段打 PROGRESS 戳——AutoTestTool 的 push/pm install
全程心跳 + 起止戳，启用 stall_seconds 的 Plan 不再把长步骤当停滞误杀；
capabilities.json 声明 progress_stamps。

移植自 stability_PowerCycle-Test/scripts/deploy.ps1 + lib.ps1（AutoTestTool 后端）
（Install-PowerCycleApk / Test-PowerCycleSystemUid / Test-PowerCycleRebootPermission /
Set-PowerCycleDeviceStability / Grant-PowerCycleStorage / Set-PowerCyclePrefs /
Start-PowerCycleTask）。run.ps1 的续跑语义并入：``reset_count=false`` 保留 current_count。

P0 边界（G15 D3/D4）：固定 autotesttool 后端；只做 reboot 模式（poweroff 配置校验失败）；
PC pc-watchdog 不移植（设备离线由平台心跳 UNKNOWN/恢复链路兜底）。

配置解析：STP_STEP_PARAMS > STP_POWER_CYCLE_* env >
``{STP_AEE_NFS_ROOT}/power-cycle/{project}/test-config.properties``（可选）> 代码默认。

STP_STEP_PARAMS:
{
    "powercycle_resources_dir": "/opt/stability-test-agent/agent/resources/power-cycle",
    "project": "legacy",
    "test_times": 100,          // 循环次数（对应 test.times）
    "mode": "reboot",           // P0 只支持 reboot；poweroff 直接校验失败
    "power_off_minutes": 1,     // 仅 poweroff 模式有效（保留字段，reboot 模式不读）
    "wait_seconds": 3,          // 每次开机后、重启前的等待秒
    "tester": "tester",
    "auto_resume": true,        // 开机自动续跑
    "install_apks": true,
    "reset_count": true         // false = 从 prefs current_count 续跑
}

输出 (stdout): {"success": true/false, "metrics": {...}}
metrics: {apk_sha256, test_times, mode, wait_seconds, tester, auto_resume,
          adb_root, reboot_method, current_count, service_started}
reboot_method: granted | su（REBOOT 权限或 su 兜底；两者皆无 → fail-fast）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from _lib import (
    check_reboot_permission,
    device_serial,
    grant_storage,
    clear_cross_prefs,
    install_apk,
    output_result,
    params,
    pcs_retry_max_attempts,
    pcs_retry_ready_seconds,
    pcs_retry_wait_budget_seconds,
    powercycle_config,
    resources_dir,
    service_alive,
    service_probe,
    set_device_stability,
    set_prefs,
    sha256_file,
    start_task,
    try_adb_root,
    wait_system_ready,
)

_APK_NAME = "AutoTestTool.apk"


def _run(cfg_raw: dict) -> dict:
    cfg = powercycle_config(cfg_raw)

    apk = resources_dir(cfg) / _APK_NAME
    if not apk.is_file():
        raise FileNotFoundError(
            f"APK 不存在: {apk}（G14 分发未解锁前按 mtbf_resources_dir 先例带外部署）"
        )

    apk_sha = sha256_file(apk)
    root_ok = try_adb_root()

    if cfg["install_apks"]:
        install_apk(apk)
        clear_cross_prefs()  # #894：清另一专项 prefs 防 boot 自启叠加

    reboot_method = check_reboot_permission()
    if reboot_method is None:
        raise RuntimeError(
            f"REBOOT 权限未授予且无 su：PowerCycle 无法重启设备。"
            f"请安装匹配该机型的 platform 签名 system APK（dumpsys package 确认 "
            f"android.permission.REBOOT: granted=true），userdebug 构建可用 su 兜底"
        )

    set_device_stability()
    grant_storage()

    current_count = set_prefs(cfg)

    ok, history, waited_ready, last_probe = _start_service_with_retry()
    if not ok:
        raise RuntimeError(_service_failure_message(history, waited_ready, last_probe))

    return {
        "apk_sha256": apk_sha,
        "test_times": cfg["test_times"],
        "mode": cfg["mode"],
        "wait_seconds": cfg["wait_seconds"],
        "tester": cfg["tester"],
        "auto_resume": cfg["auto_resume"],
        "adb_root": root_ok,
        "reboot_method": reboot_method,
        "current_count": current_count,
        "service_started": True,
    }


def _service_failure_message(
    history: list[str], waited_ready: bool, last_probe: tuple[bool, int, str]
) -> str:
    """服务启动失败报文（v1.2.4：带 attempts/history/rc/dumpsys 证据，可跨窗聚合）。"""
    return (
        f"PowerCycleService 未在预算内启动（attempts={len(history)}/{pcs_retry_max_attempts()} "
        f"history=[{','.join(history)}] waited_ready={waited_ready} "
        f"rc={last_probe[1]} dumpsys={last_probe[2]!r}）"
    )


def _wait_service(timeout_s: float = 30) -> bool:
    """前台服务启动是异步的，轮询 dumpsys 直到可见。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if service_alive():
            return True
        time.sleep(2)
    return False


def _start_service_with_retry() -> tuple[bool, list[str], bool, tuple[bool, int, str]]:
    """``start_task`` + 等服务的**有界重试**（v1.2.4，#2802 撞峰同族缺口）。

    r503 取证：``PowerCycleService 未在 30s 内启动`` 每窗 0–2 条地板级存在，失败全落
    T+0~30s——设备仍在上一窗的重启循环里（~75s 周期 > 单次 30s 等待），而 v1.2.2 只把
    就绪门接到了 install 路径。本版：首试仍走快速路径（正常设备耗时不变）；失败后等
    **系统就绪**（``get_state==device`` 且 ``sys.boot_completed==1``，同 ``install_apk``
    v1.2.2 判定）再重试，最多 ``pcs_retry_max_attempts()`` 次、预算
    ``pcs_retry_wait_budget_seconds()`` 封顶。

    返回 (是否成功, history, 是否发生过就绪等待, 最后一次 dumpsys 探测)。
    """
    max_attempts = pcs_retry_max_attempts()
    deadline = time.time() + pcs_retry_wait_budget_seconds()
    history: list[str] = []
    waited_ready = False
    last_probe: tuple[bool, int, str] = (False, -1, "no observation")
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            remaining = deadline - time.time()
            if remaining <= 1:
                break
            ready, observed = wait_system_ready(
                time.time() + min(pcs_retry_ready_seconds(), remaining)
            )
            waited_ready = waited_ready or ready
            if not ready:
                history.append(f"{attempt}:not_ready({observed})")
                break
        start_task()
        wait_s = max(1.0, min(30.0, deadline - time.time()))
        if _wait_service(timeout_s=wait_s):
            return True, history, waited_ready, last_probe
        last_probe = service_probe()
        history.append(f"{attempt}:no_service(rc={last_probe[1]})")
    return False, history, waited_ready, last_probe


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
