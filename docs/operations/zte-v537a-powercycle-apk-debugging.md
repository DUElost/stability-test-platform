# 中兴 V537A 项目测试 APK 调试与开关机专项排障手册

> **项目与机型**：中兴 V537A（展锐芯片平台 / Mifavor & MyOS 系统）  
> **适用范围**：开关机专项（`power-cycle`）、休眠唤醒等依赖开机自动恢复（`BOOT_COMPLETED` 广播）及系统特权 API（如 `PowerManager.reboot()`）的自动化测试 APK（如 `com.tinno.autotesttool`）。  
> **典型故障现象**：设备进入测试界面后倒计时冻结（如固定显示 `Next重启时间 14:41`，当前时间已远超）、无法重启，或者首轮重启开机后停滞、无法自动进入下一轮测试。

---

## 1. 核心根因与机理分析

### 1.1 根因一：系统签名不匹配导致特权权限被拒
- **机理**：`PowerManager.reboot()` 需要系统级特权权限 `android.permission.REBOOT`。该权限保护级别为 `signature|privileged`，必须满足以下条件之一方可获取：
  1. 放置在 `/system/priv-app/` 且通过 privapp-permissions 白名单声明；
  2. 使用与 ROM 系统底层（`framework-res.apk`）完全一致的签名密钥签名。
- **签名错配证据**：
  - 现场 V537A 设备刷入的 userdebug ROM 底层密钥为 AOSP 官方标准 Platform 测试秘钥：
    `DN: EMAILADDRESS=android@android.com, CN=Android, OU=Android, O=Android, L=Mountain View, ST=California, C=US`，证书哈希为 `b4addb29`；
  - 资源库分发的原始测试 APK（`AutoTestTool.apk`）采用的是 Tinno Release 密钥（证书哈希 `c24808bd`）；
  - 无法通过 `pm grant` 授予（Android 系统拒绝修改该签名权限：`SecurityException: not a changeable permission type`）。
- **假死与漏检表现**：
  - APK 内在倒计时触发后调用 `reboot()` 抛出 `SecurityException`，APK 内部捕获后写入结果日志 `reboot failed` 并直接退出循环，不再更新下一次 `next_action_at`，界面文字因此永久冻结；
  - Agent 端的 `powercycle_check.py` 巡检当时仅通过 `ps` 检查前台服务是否存活，因进程未异常退出（`service_alive() == True`），判定为正常，未能触发告警。

### 1.2 根因二：中兴定制系统自启动策略拦截开机广播
- **机理**：
  - 在设备通过 Platform 密钥重新签名并执行首轮重启后，设备未能自主进入第 2 轮；
  - 深入反编译 `services.jar` 中的 `AutoLaunchManagerService` 与 `BroadcastQueueAutoLaunchHook` 发现：
    第三方非特权应用在开机广播分发时，中兴策略在入队阶段强制过滤：
    ```text
    ZBrPolicy: Delivery state of BroadcastRecord{...BOOT_COMPLETED...} to ...BootCompletedReceiver...
    changed from PENDING to SKIPPED because skipped by policy at enqueue:
    Skipping delivery to com.tinno.autotesttool / <uid> : autolaunch policy
    ```
  - 这导致安装在 `/data/app/` 的测试 APK 的 `BootCompletedReceiver` 无法收到 `BOOT_COMPLETED`，后台循环彻底中断。
- **CTS 逃生通道（关键突破）**：
  - 逆向分析 `AutoLaunchManagerService.isSkipByAutoLaunchInner()`：
    ```smali
    invoke-static {}, Lcom/zte/feature/Feature;->getCtsRunningStatus()Z
    move-result v0
    if-nez v0, :cond_5b5
    # 若为 true 直接跳转到 :cond_5b5，输出：
    # BlockResult = false, callingPkg = com.tinno.autotesttool, reason = BYPASS_FOR_CTS
    ```
  - `Feature.getCtsRunningStatus()` 读取系统属性 `persist.sys.stc`（即 `cts` 倒序字符串）；
  - 当配置 `setprop persist.sys.stc true` 时，中兴系统判定整机处于 CTS 自动化测试模式，完全放行所有第三方测试应用的开机广播与后台服务，且属性由于带 `persist.` 前缀，会在 `/data/property/persistent_properties` 永久固化，跨重启不失效。

---

## 2. 标准排障与恢复 SOP

> 警告：在对生产设备执行批量操作前，操作者必须确认设备处于测试维护状态，不得中断其它正在运行的非开关机测试任务。

按顺序执行以下步骤：

### 步骤 1：检查设备签名匹配性与 REBOOT 权限状态
在 Agent host 上执行：
```bash
adb -s <serial> shell dumpsys package com.tinno.autotesttool | grep -E "signatures|android.permission.REBOOT"
```
预期：
1. 签名包含 `signatures:[b4addb29]`（与 ROM 系统签名匹配）；
2. 权限列表中显示 `android.permission.REBOOT: granted=true`。
如果显示 `granted=false`，转至 §3 执行重新签名与重装流程。

### 步骤 2：写入 CTS 广播放行属性
在 Agent host 上执行：
```bash
adb -s <serial> shell setprop persist.sys.stc true
adb -s <serial> shell getprop persist.sys.stc
```
预期：输出 `true`。

### 步骤 3：拉起前台界面并启动循环服务
> 警告：中兴系统限制未运行后台进程直接通过 `am start-foreground-service` 拉起。操作者必须先启动 Activity，等待 1~2 秒进程启动后再启动 Service。

在 Agent host 上执行：
```bash
# 1. 先启动界面
adb -s <serial> shell am start -n com.tinno.autotesttool/com.mediatek.schpwronoff.powercycle.PowerCycleActivity
sleep 2

# 2. 启动开关机服务
adb -s <serial> shell am start-foreground-service \
  -n com.tinno.autotesttool/com.mediatek.schpwronoff.powercycle.PowerCycleService \
  -a com.tinno.autotesttool.action.POWER_CYCLE_START
sleep 1

# 3. 发送心跳保活广播
adb -s <serial> shell am broadcast \
  -a com.tinno.autotesttool.action.POWER_CYCLE_KEEPALIVE \
  -n com.tinno.autotesttool/com.mediatek.schpwronoff.powercycle.PowerCycleAutoResumeReceiver
```
预期：系统返回 `Starting service`，无 `Blocked by AutoLaunch` 报错。

### 步骤 4：验证自动重启与跨轮次推进
等待 60 秒（或设定时间），并在设备完成重启后检查本地测试日志：
```bash
adb -s <serial> wait-for-device
adb -s <serial> shell cat /sdcard/Android/data/com.tinno.autotesttool/files/PowerCycle/powercycle_result.txt
```
预期：日志连续记录两轮及以上记录，如：
```text
2026-08-13 16:51:02 cycle 1/100 start
2026-08-13 16:52:48 cycle 2/100 start
```

---

## 3. 测试 APK 的 Platform 密钥重新签名标准流程

在控制面或构建机上操作：

### 步骤 1：获取与 ROM 匹配的 AOSP Platform 密钥对
从官方 AOSP 构建树或凭据中心获取：
- `platform.pk8`（PKCS#8 私钥）
- `platform.x509.pem`（X.509 证书公钥）

### 步骤 2：重签与对齐
```bash
# 1. 确保安装 apksigner 与 zipalign
sudo apt-get install -y apksigner zipalign

# 2. 对未对齐/新生成的包进行 4 字节对齐
zipalign -v -p 4 AutoTestTool_raw.apk AutoTestTool_aligned.apk

# 3. 使用 platform 密钥进行 v1/v2/v3 签名
apksigner sign --key platform.pk8 --cert platform.x509.pem AutoTestTool_aligned.apk

# 4. 核验签名证书指纹
apksigner verify --verbose --print-certs AutoTestTool_aligned.apk
```
预期：输出显示 `Signer #1 certificate DN: EMAILADDRESS=android@android.com, CN=Android...`，SHA-256 为 `e88d013db3276b11ffff58fb64ac20f7592ec283a610bc329215fa7f45c9e7ef`。

### 步骤 3：同步控制面资源目录
将签名后的 APK 拷贝至所有关联版本路径：
- `/mnt/stp-aee/resources/power-cycle/chain/AutoTestTool.apk`
- `/mnt/stp-aee/resources/power-cycle/legacy/AutoTestTool.apk`
- `/mnt/stp-aee/resources/power-cycle/verify/AutoTestTool.apk`
- `/mnt/stp-aee/resources/power-cycle/AutoTestTool.apk`

---

## 4. 批量处理脚本设计（多主机并发）

在控制面上，针对批量集群运行以下多线程并发脚本（示例逻辑见下）：

```python
#!/usr/bin/env python3
"""
针对中兴 V537A 项目 87 台测试机开关机专项批量恢复脚本
"""
import subprocess
from concurrent.futures import ThreadPoolExecutor

def fix_device(host: str, serial: str):
    # 1. 写入 CTS 全局放行属性
    cmd_stc = f"adb -s {serial} shell setprop persist.sys.stc true"
    subprocess.run(["ssh", f"android@{host}", cmd_stc], timeout=20)
    
    # 2. 顺序拉起 Activity 与 Service
    cmd_launch = (
        f"adb -s {serial} shell am start -n com.tinno.autotesttool/com.mediatek.schpwronoff.powercycle.PowerCycleActivity; "
        f"sleep 2; "
        f"adb -s {serial} shell am start-foreground-service -n com.tinno.autotesttool/com.mediatek.schpwronoff.powercycle.PowerCycleService -a com.tinno.autotesttool.action.POWER_CYCLE_START; "
        f"sleep 1; "
        f"adb -s {serial} shell am broadcast -a com.tinno.autotesttool.action.POWER_CYCLE_KEEPALIVE -n com.tinno.autotesttool/com.mediatek.schpwronoff.powercycle.PowerCycleAutoResumeReceiver"
    )
    res = subprocess.run(["ssh", f"android@{host}", cmd_launch], capture_output=True, text=True, timeout=30)
    return serial, "Success" if "Starting service" in res.stdout else "Check needed"
```

---

## 5. 平台代码与长效防御建议

1. **`powercycle_setup.py` 初始化加固**：
   - 增加设备机型识别：针对中兴机型（`ro.product.brand=ZTE` 或 `ro.product.manufacturer=ZTE`），在安装 APK 后必须自动执行 `setprop persist.sys.stc true`；
   - 权限检查收紧：废弃 `which su` 的误判兜底逻辑，强制检查 `dumpsys package <pkg>` 中的 `android.permission.REBOOT: granted=true`，若未授予必须直接 FAIL，禁止静默通过。
2. **`powercycle_check.py` 巡检死锁兜底**：
   - 读取设备本地 `/sdcard/Android/data/com.tinno.autotesttool/files/PowerCycle/powercycle_result.txt`，如果包含 `reboot failed` 关键字，立即判定为执行异常；
   - 检测到设备 `uptime` 增长超过设定阈值且轮次未推进时，增加自愈唤醒尝试（重发保活广播或拉起 Activity）。
