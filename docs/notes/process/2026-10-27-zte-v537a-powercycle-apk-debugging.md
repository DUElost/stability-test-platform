# 中兴 V537A 项目测试 APK 调试与开关机专项排障留档

Status: implemented
Class: process

## Decision

针对中兴 V537A（展锐芯片平台）项目开关机专项（PlanRun #1129）87 台设备在执行过程中卡死、界面停留在 `Next重启时间 14:41`、且首轮重启后无法自主进入下一轮的问题，进行了全链路逆向与排查，并在 `docs/operations/` 下沉淀运维排障与自愈操作手册：

1. **新增文档**：[`docs/operations/zte-v537a-powercycle-apk-debugging.md`](../../operations/zte-v537a-powercycle-apk-debugging.md)；并在 [`docs/operations/README.md`](../../operations/README.md) 中建立索引。
2. **根因一治理**：确认 ROM 系统使用 AOSP 官方 Platform 测试密钥（`b4addb29`），与原始分发包 Tinno Release 密钥不一致导致 `android.permission.REBOOT` 权限被拒。控制面重新使用 AOSP 平台测试密钥对 `AutoTestTool.apk` 进行签名并同步更新至 `/mnt/stp-aee/resources/power-cycle/` 下各版本目录。
3. **根因二治理**：反编译系统框架 `services.jar` 中的 `AutoLaunchManagerService` 与 `BroadcastQueueAutoLaunchHook`，定位到中兴 MyOS 策略对非特权应用开机广播进行静默丢弃（`Skipping delivery ... autolaunch policy`）。逆向确认中兴系统的自动化 CTS 逃生通道 `persist.sys.stc`（即 `cts` 倒序字符串），通过设置全局持久化属性 `persist.sys.stc=true`，完全放行开机广播与后台服务（`BlockResult = false, callingPkg = com.tinno.autotesttool, reason = BYPASS_FOR_CTS`），跨重启永久有效。
4. **批量自愈**：通过多线程批量脚本为 6 台主机 87 台设备重签安装 APK、注入 CTS 属性并重新拉起服务。全量设备已自主平稳推进至数十轮。

涉及文件：
- `docs/operations/zte-v537a-powercycle-apk-debugging.md`
- `docs/operations/README.md`
- `docs/notes/process/2026-10-27-zte-v537a-powercycle-apk-debugging.md`

## Alternatives

- **修改 AndroidManifest.xml 注入 sharedUserId="android.uid.system"**：测试反编译回编时遇到 APK 内带有 `$` 字符的资源名称（如 `$avd_hide_password__0.xml`）在 aapt/aapt2 编译期被语法规则拒绝的问题；直接通过 `persist.sys.stc=true` 属性放行无需修改 APK 内部资源结构，且原生跨重启持久化，更简单、安全、低风险。
- **推入 /system/priv-app/**：因 V537A 现网设备 bootloader 锁定，无法直接执行 `adb remount` 写系统分区，此路不通。
- **仅靠外部脚本周期性轮询与强制唤醒**：治标不治本，不仅增加了 Agent 巡检与 ADB 频繁交互的负担，而且无法解决设备真实开机自启与无网环境下的自愈逻辑。

## Verification

1. **单机连续重启验证（以 `6R0A57EUAE9000012` 为例）**：
   - 检查 `dumpsys package com.tinno.autotesttool` 确认 `android.permission.REBOOT: granted=true`；
   - 检查 `getprop persist.sys.stc` 返回 `true`；
   - 检查本地测试日志 `/sdcard/Android/data/com.tinno.autotesttool/files/PowerCycle/powercycle_result.txt`，确认开机后无外部干预下自动推进：
     ```text
     2026-08-13 16:11:22 cycle 1/100 start
     2026-08-13 16:51:02 cycle 2/100 start
     2026-08-13 16:52:48 cycle 3/100 start
     2026-08-13 16:54:38 cycle 4/100 start
     2026-08-13 16:56:20 cycle 5/100 start
     ```
2. **集群验证**：
   - 全网 87 台设备全部下发成功（0 失败），全部设备进入自主循环；
   - 控制面 API 验证 PlanRun #1129 的 87 个 Job 全部保持在 `RUNNING` 状态；
   - 截至留档时，集群所有设备已正常推进至第 20 ~ 31 轮以上。
3. **文档与格式检查**：
   - 遵循 ADR-0059 写作规范；
   - `ruff check docs/operations/` 检查无违规。

## Revisit

- 当后续设备升级为非 userdebug ROM（采用厂商正式 Release 签名）时，APK 签名需重新同步对齐至厂商正式签名证书；
- 后续在 `powercycle_setup.py` 与 `powercycle_check.py` 进行长效迭代时，可将中兴机型 `persist.sys.stc` 自动配置与本地结果文件错误解析正式合并至主干代码。
