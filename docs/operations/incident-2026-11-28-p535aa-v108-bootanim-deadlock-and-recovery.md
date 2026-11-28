# 2026-11-28 P535AA V108 批量卡开机动画死锁（`PFDFAA-1667`）复盘与快速排障指引

> **状态**：根因已定位至 `261117V108` 系统固件自身 `services.jar` 代码缺陷（已在 `261121V116` 官方固件修复）；平台侧恢复阻塞已通过 PR #4223、PR #4229 解除并部署（`53d81e97`），受影响 216 台设备中 199 台已升至 `261121V116`（182 台已 `ONLINE` 归队，17 台在 V116 OOBE 关机态待上电；余 15 台在 `.100` 待当前专项结束并热更后补刷，2 台离线机待现场处置）。
> **影响范围**：`PlanRun #1762`（`P535AA-开关机专项-1D-30s-V552AA-20261126`，`plan_id=258`，332 台 / 23 主机）中全部 **216/216 台 `261117V108` 设备**（覆盖 NX2、NX3、NX3Carrier 三个变体）；同专项内 `261110V94` 与 `261113V100` 设备 0 台触发该开机死锁（91 台完成 649–978 轮重启）。
> **关联**：[#4222](https://github.com/DUElost/stability-test-platform/issues/4222)（平台侧配套缺陷追踪）、PR #4223、PR #4229、[`diagnose-device-stall`](../../.claude/skills/diagnose-device-stall/SKILL.md)、[`host-device-visibility-triage.md`](./host-device-visibility-triage.md)。

---

## 1. 一分钟快速定位信号（遇到“设备 adb 在线但卡开机画面 / 专项零进展”先查这里）

当压测或开关机专项出现大量设备长期卡住、`adb get-state` 返回 `device` 但无测试产出（或 `oobe_skip` / `init_return_check` / `powercycle` 停滞）时，**不得直接归因为平台调度、Agent 主机、USB Hub 或脚本故障**；先执行以下只读四步取证区分“外围链路故障”与“设备端 Android Framework 启动死锁”：

1. **看启动标志与渲染状态**（10 秒定性）：
   ```bash
   adb -s <serial> shell "getprop sys.boot_completed; getprop init.svc.bootanim; getprop sys.boot.reason; uptime; cat /proc/sys/kernel/random/boot_id"
   ```
   - **典型死锁指纹**：`adb get-state == device`，但 `sys.boot_completed` 为空、`init.svc.bootanim == running`、系统负载持续异常偏高（如 `load average: 13.5+`）、`boot_id` 几小时到几十小时不变（说明整机内核没有重启，而是上层反复循环）。
2. **看 `system_server` 是否被 Watchdog 周期性强杀**：
   ```bash
   adb -s <serial> shell "logcat -b events -d | grep -E 'boot_progress|watchdog|system_server'"
   ```
   - **典型死锁指纹**：每隔约 **909 秒（~15 分钟）** 重复出现一次 `boot_progress_start` → `boot_progress_system_run` → `boot_progress_pms_data_scan_start` → `watchdog: Blocked in handler on PackageManager (PackageManager) for 909s`。
3. **抓 `root` 与 ANR 锁链**（确认死锁位置与持有者）：
   ```bash
   adb -s <serial> root
   adb -s <serial> shell "ls -lt /data/anr/ | head -n 5"
   adb -s <serial> shell "head -n 120 \$(ls -t /data/anr/anr_* | head -n 1)"
   ```
   - 同时对照工作线程（如 `package-parsing-thread1..4`）是在等待任务还是忙计算，确认是否属于“生产/消费计数错位”或锁顺序反转。
4. **查死锁前最后业务操作与 `/data/app` 变更**（确认触发前提）：
   ```bash
   adb -s <serial> shell "ls -lt /data/app/ /data/system/shutdown-checkpoints/ && tail -n 30 /data/system/shutdown-checkpoints/*"
   ```
   - `shutdown-checkpoints` 可直接给出死锁前最后一次正常 `reboot` 是由哪个进程发起的（例如 `PowerManagerService$BinderService.reboot From process com.tinno.autotesttool`）。

---

## 2. 本次事故现象与现场实证

在 `PlanRun #1762` 中，332 台设备共 216 台 `261117V108` 在启动 `powercycle_setup` 后于**首次重启**全部卡死；对后来从节点机（`host .90`）取下直连控制面本机保留现场的样机（`device_id=1020`，`MLD-NX3-16-261117V108`）做全栈只读取证，结果与现网 `PlanRun #1762` 采样机完全一致：

1. **未掉线、非硬挂**：USB 枚举正常（`0e8d:201c`），`adb` 为 `device`；`BootAnimation` 以 ~15 fps 持续播放 `/system/media/bootanimation.zip/part1`，用户侧体感就是“一直卡在开机 LOGO / 开机动画进不去桌面”。
2. **启动进度断在 `data` 分区扫描**：
   `logcat -b system,events` 在 `system_server` 拉起后推进至 `boot_progress_pms_data_scan_start`，紧接着输出如下日志后便完全卡死：
   ```text
   PackageManager: PFDFAA-1667: fast boot scan unchanged data app com.tinno.autotesttool
   ```
   909 秒后 `Watchdog` 强杀 `system_server`（`Blocked in handler on PackageManager (PackageManager) for 909s`），`zygote` 重新拉起 `system_server`，再次卡死在同一行。
3. **ANR 线程栈直接指向队列消费多等了一次**（`/data/anr/anr_2026-11-25-15-33-12-113`）：
   - **`main` 线程（tid=1）**：在 `PackageManagerService.<init>`（line 2321）持有 `PackageManagerTracedLock`（`<0x04830f56>`），阻塞于 `InstallPackageHelper.installPackagesFromDir()`（line 4087）→ `ParallelPackageParser.take()`（line 89）→ `ArrayBlockingQueue.take()`。
   - **`package-parsing-thread1..4`（全部 4 个解析线程）**：均空闲停在 `ThreadPoolExecutor.getTask()` → `LinkedBlockingQueue.take()`——**并行解析队列里根本没有任何在途任务**。
   - **`PackageManager` handler 线程（tid=50）**：在 `InstallPackageHelper.handleUpdateOwnerDenyList()`（line 566）等待 `main` 释放同一把 `<0x04830f56>` 锁，触发 Watchdog SWT。

---

## 3. 根因裁决：三代固件 `/system/framework/services.jar` 字节码实证

为解释为何“此前刷 V108 和跑 Monkey 都正常、唯独 V108 在开关机专项 100% 死锁，而同专项内 V100 正常、升级 V116 后恢复”，直接从现场样机及 `/mnt/stp-aee/firmware/MLD/P535AA-HONOR-NX3-16-{261113V100,261117V108,261121V116}/super.img`（`liblp` + EROFS `system_a`）提取 `services.jar`，并用 `baksmali` 反编译 `com.android.server.pm.InstallPackageHelper` 逐版本比对：

### 3.1 直接缺陷（`261117V108` `InstallPackageHelper.installPackagesFromDir`）

```text
遍历 /data/app 目录文件:
  if attemptFastKnownDataAppScan(file, parseFlags, scanFlags, apex):   // 返回 true 时同步 addForInitLI，不入并行队列
      goto :count_inc
  else:
      parallelPackageParser.submit(file, parseFlags)                   // 返回 false 时才入并行队列
:count_inc
  v6 += 1   // <-- Bug：即使走了 fastScan 快速分支，仍把待 take() 的总数 v6 加 1！

后续循环:
  while v6 > 0:
      parallelPackageParser.take()   // <-- 提交 0 个任务却要 take() 1 次，持 mLock 永久阻塞
```

### 3.2 三代固件行为对照

| 固件版本 | `attemptFastKnownDataAppScan()` 实现 | `installPackagesFromDir()` 计数逻辑 | 现网结果 |
|---|---|---|---|
| **`261113V100`** | 若 `/data/system/package_cache` 未命中（`getCachedResult() == null`）直接 `return false` 回退到普通扫描；`/data/app` 后装包无 boot cache，恒回退 | `v6` 计数逻辑同样未拆分，但因前一步永远返回 `false`，从未走入跳过 `submit()` 的分支 | **91/91 台正常跑完数百轮重启** |
| **`261117V108`** | 合入 **`PFDFAA-1667`**：`PackageCacher` 未命中时现场调 `PackageParser2.parsePackage()` 并用 `isFastScanMetadataMatch()` 放行，成功即返回 `true`（打日志 `PFDFAA-1667: fast boot scan unchanged data app ...`） | **未同步修正计数**：仍共享 `v6`，只要 `/data/app` 中存在任意已知应用且处于非首次开机重启，即 100% 死锁 | **216/216 台 100% 死锁** |
| **`261121V116`** | 保留 `PFDFAA-1667` 快扫优化 | **厂商已修复**：拆分为 `v12`（`submitted`）与 `v13`（`fast`）两个独立变量，快扫命中只加 `v13`，`take()` 循环只按 `v12` 消费，并新增摘要日志 `PFDFAA-1667: data scan ... submitted= fast=` | **端到端恢复验证与单机重启通过** |

### 3.3 触发条件与免责边界

- **为什么此前刷 V108 首次开机、跑 Monkey 都没复现**：
  1. 刷机首次开机带有 `SCAN_FIRST_BOOT_OR_UPGRADE`（`0x1000`），`isIncrementalDataAppBootScan()` 直接返回 `false`；且 `/data/app` 此时为空目录（`ArrayUtils.isEmpty()` 直接返回）。
  2. Monkey 专项本身不执行系统重启，且一般不安装新 APK 或未经历完整重启周期。
  3. 直到进入 `PlanRun #1762`，`powercycle_setup` 往 `/data/app` 安装了 `com.tinno.autotesttool` 并由 `PowerManagerService$BinderService.reboot` 发起首次常规重启，才同时集齐 `/data/app` 非空 + 非首次开机标志 + `persist.sys.pms_fast_data_scan`（默认 `true`）三个条件，立刻全量引爆。
- **各侧角色免责**：该故障纯属 `261117V108` ROM Framework 代码 Bug，与平台调度、Agent 主机、USB Hub 及 `powercycle_setup` 脚本逻辑均无关（`isFastDataAppBootScanEnabled()` 虽可通过将 `persist.sys.pms_fast_data_scan` 置 `false` 临时规避，但治本方案是整批升级至 `261121V116`）。

---

## 4. 平台与批量恢复过程中同步沉淀的工程经验

本次对 216 台卡死机群实施恢复（[Issue #4222](https://github.com/DUElost/stability-test-platform/issues/4222)）还沉淀出以下平台与运维经验：

1. **恢复刷机流水线不得要求刷前 `sys.boot_completed=1`（PR #4223 / #4229）**：
   - 卡死在开机动画的设备虽能正常响应 `adb reboot` 并枚举 MTK Preloader（`0e8d:2000`），但因 `sys.boot_completed` 永远为空，此前普通 `adb_reboot` 刷机流水线会在 `init_return_check` 环节直接拦截设备自救。
   - 现已将 `adb_reboot` 前缀 `[flash_preflight, flash_firmware, oobe_skip]` 统一延后到刷机与跳过 OOBE 之后再执行 `init_return_check`（与 `vbus_cycle` 语义对齐）。
2. **高并发 `adb_reboot` 刷机的 OOBE 自动关机陷阱**：
   - 带运营商开机自检关机（`shutdown,provisioning_shutdown_req`）的版本（如 `MLD-NX3-Claro`）在单主机并发超过 `OperationScheduler` 槽位（默认 5）时，先刷完 `flash` 的设备释放槽位后排在后一批 `flash` 队尾等待 `oobe_skip`，往往在排队 15–30 分钟期间因超时未过 OOBE 自动关机。
   - 该后续加固点与 `return-override` 后立即派单撞车在途 `discharge` 引发静默 `PermitDenied`、单固件多 `product`（`MLD-NX3-Claro` / `sys_mssi_64_cn_armv82`）指纹核验等问题均已记入 [#4222](https://github.com/DUElost/stability-test-platform/issues/4222#issuecomment-7237655904) 跟进；其中多 `product` 差异临时通过同目录硬链接 + 独立 `manifest.json`（Plan `#265`）解决。
3. **跨三代固件 `super.img` 只读提取技巧**：
   - 当需要确认未上机固件包内部 `services.jar` 是否含某项 ROM 补丁时，可用 Python 解析 Android sparse header 与 `liblp`（`0x3000`）定位 `system_a`，再配合系统 `/usr/lib/x86_64-linux-gnu/liblz4.so.1`（`LZ4_decompress_safe`）解码 EROFS 压缩块并执行 `baksmali` 对比，无需物理挂载或刷机试错。
