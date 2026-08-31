# G15 对齐确认：android-tools 三执行包 × MTBF P0/P1 设计去重

- 日期：2026-08-31
- 来源：[issue #462](https://github.com/DUElost/stability-test-platform/issues/462)（方向 5 第一步）
- 上游：`docs/reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md` §2.5（G12–G15）、
  [`docs/design/2026-08-mtbf-p0-runner-design.md`](../design/2026-08-mtbf-p0-runner-design.md)、
  ADR-0030 P1（test_suite/test_case）
- 依据：`/mnt/automation-toolkit/android-tools/` 三执行包逐文件勘察（2026-08-31）+ 平台侧代码核对
- 落地状态：P0a Sleep（7ca82b0）/ P0b PowerCycle（8bc00d8）/ P0c GPU（df9305f）已随
  PR #462 分支合入待验收；**真机冒烟未做**（GPU `-e loop 1`×N 等价性、Sleep 闹钟丢场景、
  PowerCycle reboot 循环均需实机验证后开生产 Plan）
- 真机冒烟（2026-08-31，Sleep P0a，MLD-LX2 userdebug 设备）：setup ✅
  （apk_sha 与资源一致、adb_root=true、服务启动）→ check ⚠️（见下）→ finish ✅
  （final_status=PASS，cycles 2/2，逐条 JSON 落 `{STP_AEE_NFS_ROOT}/sleep/smoke/results/`）。
  **冒烟发现**：① SleepTestService 完成 test_times 后**自停**（设计假设是常驻）——
  sleep_check 必然判死收场，需 v1.0.1 增加「结果文件已 finished」完成检测；② patrol 步骤
  连续失败不触发 job 转 teardown（本冒烟以 abort 手动触发）；③ 真实结果行含 `cpuLock=true`
  等尾随 token，解析器 search 式正则已容错。
- 三专项真机冒烟收尾（2026-08-31）：**Sleep/PowerCycle/GPU 全部 PASS**——
  PowerCycle（2/2 轮 reboot，`final_status=PASS`）；GPU（2/2 轮 instrument，
  `final_status=COMPLETED`，`-e loop 1`×N 等价性实机验证通过）。
  另两个冒烟发现已修复：④ Android `ps -A` 截断 args 匹配不到 instrument
  （gpu v1.0.1 改 pgrep -f + bracket 防自匹配）；⑤ test_log.txt 含二进制
  protobuf 输出，text 解码抛 UnicodeDecodeError（gpu_check v1.0.2 改 bytes 读取）。
  **运维坑**：Agent hot-update 的 rsync `--delete` 会清掉 `resources/` 下
  非 exclude 目录（仅 `resources/mtbf/` 豁免）——带外资源须在最终热更新后放置。
- 综合验收（2026-08-31，3 专项 × 4 设备 × 2 host 并行 10 分钟，12 job）：
  **Sleep ✅ / GPU ✅ / PowerCycle ⚠️**。Sleep 10/100 轮全 wake OK、GPU 各 2 轮
  rc=0（单轮 ~4-5min，10 分钟 2 轮属预期）、PowerCycle 测试执行正常（7+ 轮
  reboot 无设备挂）但 **teardown 0/4 收集失败**（撞 reboot 窗口）。验收发现：
  ⑥ powercycle boot 窗口（adb 在线、服务未起）累计 dead_streak 误判——
  reboot 周期 ~75s > patrol 60s，boot 窗口可跨 2 个 check（含 cycles_done=0
  判死）；⑦ **powercycle teardown 撞 reboot 窗口 → prefs 写入/拉取失败
  （adb device not found）→ 结果 0/4 收集**（需 v1.0.2 容错：设备离线等待
  上线重试）；⑨ **秒级 run_id 并行碰撞**——同秒多个 finish 写同一文件名互相
  覆盖（sleep 4 job → 2 文件、gpu 4 job → 2 文件；run_id 需加设备维度）。
- 收取机制方案落地（用户确认，方法 A/B）：B = finish 等设备上线容错
  （powercycle_finish v1.0.1/1.0.2，#646/#657）；A = 定时收取窗口
  （powercycle_check v1.0.2→v1.0.5：collect_window_start 东八区 +
  collect_window_minutes Per-Plan 自由键可配，窗口内暂停→收取→续跑）。
  **窗口实测通过**（2026-08-31，设备 253）：窗口 17:05-17:15，17:06 收取
  13 轮落盘、任务续跑；手动 finish 收 21 轮全量。实测补强：⑥ boot 判死
  三条件（cycles==0 / result_bytes==0 / 转换清零）、⑪ finish 等
  boot_completed==1（boot 早期 /data 未挂载 run-as stat 失败）、
  主机时区各异（PDT）→ 窗口按东八区固定判定。
- 最终回归验收（2026-08-31，4 设备 × powercycle v1.0.5 + 窗口 + finish v1.0.2）：
  **setup 4/4 ✅、窗口收取 4/4 ✅（18:05 触发，13-15 轮落盘）、teardown 4/4 ✅
  （22-23 轮全量，reboot_fail=0）、run_id 同秒多设备共存 ✅**。-67 两设备
  patrol 全程零判死；-71 两设备各判死 1 次——服务偶发启动失败后自愈（文件
  可读、cycles>0、无转换，v1.0.5 判死**正确触发**但 PowerCycle 重启场景
  判死策略可更宽容——遗留：grace 提高或结果文件 mtime 停滞判定）。
  发现② 关闭（2026-08-31 确认）：patrol 失败不转 teardown 是 **ADR-0022
  best-effort 语义**——单步骤失败不中止周期、不产生 job 级失败，只 trace +
  failure_streak + 指数退避；teardown 由 timeout/abort 触发。check 判死是
  监控信号而非终止机制（设备级故障由平台心跳 UNKNOWN 链路处理），非缺陷。

## 0. 结论摘要

1. **无概念重复**：Sleep/PowerCycle 与 MTBF 执行包是**同构三件套**（`deploy/run/stop.ps1` +
   `lib.ps1` + `test-config.properties`，函数级逐项对应）。不存在「android-tools 版 runner」与
   「平台 MTBF runner」两套概念的并存风险——三包是**同一「专项模板」的首批用户**，
   直接复用 mtbf 三件套的移植骨架（`_lib.py` 契约 / prefs 推送 / 启动·停止序列 / 设备稳定性）。
2. **GPU 是最不规整的一个**：无 ps1/lib/三件套，编排全在 .bat（交互确认、多设备并行窗口、ping
   当 sleep），结果无结构化格式（`/sdcard/Auto/test_log.txt` 文本），且依赖 MTK 专属节点
   （`/proc/mtk_battery_cmd/current_cmd`、`com.debug.loggerui`）。单独按 G12 移植核对处理。
3. **G13 需重新定界**：Sleep/PowerCycle/GPU **均无 suite XML**（只有 `test-config.properties` /
   `gpu_tool_config.ini` / bat 内联），MTBF 的 runtask.xml 导入通道对它们**不适用**——可行性审查
   G13「PowerCycle/Sleep 的 task XML 可走同一导入通道」的假设不成立。见 §4 决策 D5。
4. **结构性差异**：三包都有 **PC 端常驻 watchdog**（wake-watchdog / pc-watchdog / mssv-watchdog，
   `Start-Process -WindowStyle Hidden`），平台无 PC 常驻进程概念——映射为 **patrol 轮询语义**
   （mtbf_check 先例）或设备端机制，这是与 MTBF 移植最大的结构差异。
5. **G14 依赖登记**：AutoTestTool.apk（Sleep/PowerCycle 共用同一 APK，包名
   `com.tinno.autotesttool`）、GPU 三 APK（`scripts-debug*.apk` 外部构建**无源码**，只能二进制
   分发；Antutu 官方下载）、MSSV APK（配置里是 `D:\...` 写死 Windows 路径）。等方向 3 G1
   上传下载 API 解锁后经 `support_files_manifest` 分发（`models/script.py:22` 字段已有）。

## 1. 三包 × 平台已有（MTBF P0/P1）对齐矩阵

| 维度 | 平台 MTBF（已落地 v1.3/v1.4） | Sleep | PowerCycle | GPU |
|------|-------------------------------|-------|------------|-----|
| 执行包形态 | deploy/run/stop.ps1 + lib.ps1 | 同左（同构） | 同左 + 双后端分派 | 无三件套，纯 bat + 设备端 sh |
| 配置 | runtask.xml + UiAutomatorTestData.xml + 3 prefs 文件 | test-config.properties（5 键）+ 单 prefs `sleep_test_runner.xml` | test-config.properties（8 键）+ 单 prefs `powercycle_runner.xml`（+ MSSV `sleep_reboot.xml`） | `gpu_tool_config.ini` 单键（lite_max_gb=8）+ bat 内联 loop |
| 结果 | `/sdcard/results/realresult/{ts}/TESTS-RealResult-TestPoints.xml`（结构化，平台已实现解析） | `/sdcard/Android/data/com.tinno.autotesttool/files/SleepTest/sleep_test_result.txt`（纯文本行） | 同目录族 `powercycle_result.txt`（纯文本）/ MSSV `/data/com.unisoc.mssv/sleepreboot/sleepreboot.log` | `/sdcard/Auto/test_log.txt`（instrument 输出，无结构化） |
| 看门狗 | 设备端 30 分钟 + BootReceiver（PC 无） | PC wake-watchdog（闹钟丢兜底） | PC pc-watchdog（无 REBOOT 权限时）+ PC/设备双端 mssv watchdog | 无（instrument 循环） |
| 后端分派 | 无 | 无 | **auto/mssv/autotesttool 三分派**（auto 检测 REBOOT 权限） | RAM 分版（ro.boot.ddrsize → /proc/meminfo 回退，≤8G 用 Lite/test_id=002） |
| adb root | jar 模式必须 / apk 模式仅 prefs | **非必需**（run-as 兜底；ZTE 写库等需要） | **必需**（MSSV 硬性；AutoTestTool root 写 prefs） | **必需**（清 /data/*、写 proc 节点） |
| 平台对应脚本 | mtbf_setup/check/finish（v1.3.0/v1.4.0） | 无 | 无 | `gpu.json` 模板占位（仅 check_device + ensure_root） |

## 2. 平台侧既有资产（可直接复用）

- **迁移骨架**：`backend/agent/scripts/mtbf_{setup,check,finish}/v1.3.0/` 的 `_lib.py`
  （STP_DEVICE_SERIAL / STP_STEP_PARAMS / stdout JSON / PROGRESS 打戳 / adb 封装 /
  prefs 推送 root + run-as 兜底 / 设备稳定性逐条移植）。Sleep/PowerCycle 的 lib.ps1 函数
  与 MTBF lib.ps1 一一对应（`Install-*Apk` / `Set-*DeviceStability` / `Set-*Prefs` /
  `Start-*Task` / `Stop-*Task`）。
- **已有 init 脚本**：`check_device`、`ensure_root`、`install_apk`、`push_resources`。
- **pipeline 模板**：`backend/schemas/pipeline_templates/` 的 `mtbf.json`（init/patrol/teardown
  三件套范式）；`gpu.json` 已占位；sleep/powercycle 需新建。
- **专项字典表**：`specialty`（`backend/models/project.py:93`，key + sort_order，ADR-0029 D6）——
  新专项 = 新增 specialty 行 + Plan 绑定。

## 3. 移植要点（G12 逐项核对结论）

### 3.1 Sleep（建议首个移植——最同构、无后端分派、adb root 非硬性必需）

- 三件套映射：`deploy.ps1` → `sleep_setup`（Install-SleepTestApk → 设备稳定性 → 写 prefs
  `-ResetCount` → 启动序列）；`run.ps1` 语义并入 setup（平台 Plan 步骤本身就是「一次启动」，
  断点续跑由 patrol + auto_resume 承担——**与 MTBF 相同**，run.ps1 的 `-ResetCount` 区分
  deploy/run 在平台表现为 setup 参数 `reset_count`）；`stop.ps1` → `sleep_finish`（停
  watchdog 语义 → prefs `auto_resume=false` → STOP action → force-stop → 拉结果 → 解析）。
- **watchdog 映射**：wake-watchdog（PC 轮询 prefs `phase=sleep` 超时亮屏）→ patrol 周期内
  `dumpsys`/prefs 轮询 + 必要时机 `am start WakeUpActivity`；或先验真机是否需要（OEM 闹钟
  丢失是特定机型现象）——**P0 建议先不做 watchdog 兜底，记录为已知缺口**。
- 结果解析：纯文本行 `cycle N/M wake OK|FAIL` + 尾部 `finished result=PASS|FAIL`；
  join 键 = `cycle (\d+)/(\d+)` 分子分母。新写 `parse_sleep_result`（文本解析器，与
  `parse_realresult` 并列放 `_lib.py`）。

### 3.2 PowerCycle（建议第二个——同 AutoTestTool 基础，但有三处额外复杂度）

- **后端分派**：`backend=auto` 检测 REBOOT 权限选 MSSV / AutoTestTool。**建议 P0 固定
  `backend=autotesttool`**（平台工程机假设 `ro.debuggable=1` + REBOOT 权限），MSSV 延后
  （展锐相关，见决策 D3）。
- **pc-watchdog**：AutoTestTool 无 REBOOT 权限时 PC 代 reboot + 开机后续跑——平台 patrol
  无法代 reboot（Agent 在主机侧，`adb reboot` 等价可做）；设备重启后心跳断链走既有
  UNKNOWN/恢复链路，boot 后 patrol 恢复轮询即可续跑——**建议不移植 PC watchdog，
  靠平台既有离线/恢复语义**。
- **poweroff 模式**：真关机 + RTC 闹钟唤醒，依赖平台（设备能力/充电保护）——**P0 只做
  `reboot` 模式**，poweroff 记录为配置校验失败（明确 error_message）。
- 结果解析：`powercycle_result.txt` 行格式 `cycle N/M start` / `reboot failed: ...` /
  `finished result=PASS`；join 键同 `cycle N/M`。

### 3.3 GPU（建议最后——最不规整 + 依赖 G14）

- 编排移植：bat 的 RAM 分版（`ro.boot.ddrsize` → `/proc/meminfo` 回退，`lite_max_gb` 阈值，
  test_id 001/002）是确定性逻辑，直接移植为 setup 的参数分支；`am instrument` 主命令本身
  与设备端 `run_stress_gpu.sh`（LF，可直接 push）在 Linux 侧等价可构造。
- 交互确认（`set /p` 输入 loop 次数）→ 参数化 `loop`（default_params/STP_STEP_PARAMS）。
- 多设备并行窗口（`%temp%\adb_run_<serial>.bat` + `start cmd /c`）→ 平台多设备由 Plan
  多 Job 天然承担，**无需移植**。
- **结果缺口**：test_log.txt 无结构化格式、无现成 join 键——**需在设备端追加结构化标记行**
  （每轮 `GPU_ROUND <n> PASS|FAIL <score>`，由 `am instrument` 的 listener 或 sh 追加），
  这是 GPU 包移植的最大新增工作（决策 D1）。
- MTK 专属依赖核对：`/proc/mtk_battery_cmd/current_cmd`、`com.debug.loggerui`、aee_exp
  清理——非 MTK 设备跳过（平台 #220 已定调生产只扫 MTK），脚本按 `getprop ro.board.platform`
  分支或跳过告警。

## 4. 决策点（需人工拍板，P0 实施前定）

| # | 决策 | 建议 |
|---|------|------|
| D1 | GPU 结果无结构化格式 | **设备端追加结构化标记行**（`GPU_ROUND ...`），test_log.txt 只留原文备查 |
| D2 | Sleep/PowerCycle 共用包名 `com.tinno.autotesttool`（部署互覆盖） | 专项层面互斥（同设备不同时跑两专项）；文档明示 + precheck 阶段说明 |
| D3 | MSSV 后端（展锐设备 PowerCycle） | **P0 不做**，固定 autotesttool 后端；MSSV 与展锐专项（G7 / #220）合并推进 |
| D4 | PowerCycle poweroff 模式（RTC 唤醒） | **P0 只做 reboot 模式**，poweroff 配置直接校验失败 |
| D5 | G13 重新定界：test_suite/test_case 模型是否覆盖三包 | **P0 不进 suite 模型**（三包无 testpoint 概念，只有循环参数）；可行性审查 G13 修正为「通道不适用，顺路验证改期」 |
| D6 | 新专项登记 | specialty 表新增 `gpu`/`sleep`/`powercycle` 三条 + pipeline 模板（gpu.json 已有 init 占位；sleep/powercycle 新建） |

## 5. 实施计划（G12 顺序）

```
P0a  Sleep 三件套（sleep_setup / sleep_check / sleep_finish + pipeline 模板 + specialty 行 + 结果解析器 + agent 测试）
P0b  PowerCycle 三件套（powercycle_setup / powercycle_check / powercycle_finish，固定 autotesttool 后端）
P0c  GPU（gpu_setup / gpu_check / gpu_finish + RAM 分版 + 设备端标记行改造）——依赖 G14 或先带外分发 APK
G14  APK/资产经 support_files_manifest 分发（方向 3 G1 落地后解锁；在此之前 resources 目录带外部署，参照 mtbf_resources_dir 先例）
```

每包形态对齐 mtbf 三件套：init（setup）/ patrol（check，`capabilities.json` 声明
`progress_stamps`）/ teardown（finish）；配置 `{STP_AEE_NFS_ROOT}/<specialty>/{project}/`
（或先 env 注入），结果逐条/摘要写回中心存储，stdout 只带摘要（64KiB 截断约束同 MTBF）。

## 6. 验证方式与重议时机

- 验证：`backend/agent/tests/` 单元测试（解析器 golden + 编排参数化）+ 真机冒烟（MTK 工程机）。
- 重议：方向 3 G1 上传下载 API 落地（G14 解锁）；展锐专项推进（MSSV 后端）；方向 7 runbook
  写作时以本三件套为「新专项模板」实例。
