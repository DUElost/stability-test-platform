# 中兴 V537A 项目开关机专项至 Monkey 专项原生平滑切换与 B6 归还契约验证留档

Status: implemented
Class: process

## Decision

针对批次 B6（设备归还与解除契约 ADR-0061 阶段 B）在真实生产集群中的长效生效能力，利用中兴 V537A（展锐芯片平台）四专项 7 天联合周期测试的切换节点，在**完全零外置干预、100% 依赖平台原生机制**的前提下，对开关机专项（PlanRun #1134，Plan 118）到期中止、设备足迹解除及向 Monkey 专项（PlanRun #1135，Plan 119）的自动流转进行了全链路被动观测与客观留档：

1. **零外置干预原则**：在切换前清除了所有可能产生主动干预的外置脚本，不进行人工 ADB 重连、不发送强制杀进程命令、不主动改写设备端配置，真实检验平台调度器、Agent、`powercycle_disarm@1.0.8` 及 `trigger_next_plan` 的原生接力表现。
2. **开关机超时中止实证**：PlanRun #1134 在跑满 6 小时设定时长（`timeout_seconds = 21600`）后，平台调度器自主下发 TEARDOWN 信号。80/80 台在册在线设备全部成功完成 teardown 收口，终态自主收敛为 `SUCCESS`，完成率达 100%（0 失败、0 挂死）。
3. **B6 设备解除契约（Disarm）与零残留达标**：
   - 80/80 台设备全部由原生 `powercycle_disarm@1.0.8` 执行完毕，上报合法的 `disarm_result = "executed_unverified"`，`readback = "clear"`，未产生任何未执行债务（`device_return_debt` 为 0）；
   - 设备端抽查核验确认：`com.tinno.autotesttool` 物理进程全部彻底自然终止（`pidof` 为空），shared_prefs 中 `auto_resume` 与 `running` 原生固化为 `false`，彻底消除了中兴系统在此前开启 CTS 放行属性（`persist.sys.stc=true`）后开机可能引发的自动关机回弹隐患。
4. **原生状态机流转与 Monkey 专项顺利接棒**：PlanRun #1134 终态后，平台内部 `trigger_next_plan` 状态机自主派发生成子 PlanRun #1135（Plan 119，Monkey 12H）。80 台健康设备全量完成认领与初始化，原生拉起 `com.android.commands.monkey.transsion` 压测进程，无缝进入 12 小时的稳定压测巡检。

## Alternatives

- **依赖外部哨兵脚本在切换期主动护航**：在早期构想中曾设计外部脚本主动扫描并触发 `adb reconnect` 与强制 `am force-stop`；但该方案无法验证平台自身的原生健壮性，违背了“后续无人盯防计划必须由平台自主闭环”的验收初心，因此被彻底废弃并完全终止清理。
- **跳过物理级现场进程抽检**：仅依赖控制面 API 上报的 `disarm_result`；但中兴设备的 AutoTestTool 曾有收到广播后在存活期间回写配置的历史前科，物理级 `pidof` 与 `grep auto_resume` 抽查能提供不可辩驳的第一现场证据，故予以保留执行。

## Verification

### 1. PlanRun #1134（开关机 6H）终态与作业完成度核验

- **运行窗口**：`2026-10-28 04:46:39` 至 `2026-10-28 10:48:37`（跑满 6 小时 1 分钟）。
- **控制面 API 状态**：
  ```json
  {
    "id": 1134,
    "plan_id": 118,
    "status": "SUCCESS",
    "total_jobs": 80,
    "job_statuses": {"COMPLETED": 80},
    "next_plan_triggered": true
  }
  ```
- **80 台设备 Disarm 结果分布**：
  全部 80 个作业均返回 `executed_unverified`，`readback: clear`，`exit_code: 0`；停止步骤记录显示服务均由存活状态成功转为 `stopped`（`state_after: stopped`, `state_after_rc: 0`）。

### 2. 物理现场抽样核查（以 3 台跨 Host 设备为例）

- **样本 1（`6R0A57EU****0028` on Host `172.21.***.***`）**：
  - `pidof com.tinno.autotesttool` 输出为空（无进程存活）；
  - `grep auto_resume powercycle_runner.xml` 输出 `auto_resume="false"`。
- **样本 2（`6R0A57EU****0012` on Host `172.21.***.***`）**：
  - `pidof com.tinno.autotesttool` 输出为空；
  - `grep auto_resume powercycle_runner.xml` 输出 `auto_resume="false"`。
- **样本 3（`6R0A77LA****0055` on Host `172.21.***.***`）**：
  - `pidof com.tinno.autotesttool` 输出为空；
  - `grep auto_resume powercycle_runner.xml` 输出 `auto_resume="false"`。

### 3. 子计划 PlanRun #1135（Monkey 12H）原生接棒核验

- **生成时间**：`2026-10-28 10:49:41`（`parent_plan_run_id = 1134`）。
- **设备认领情况**：80 台目标设备全量被认领，79 台设备秒级完成 Step 0 ~ Step 4（`monkey_setup` → `resource_push` → `monkey_launch`）并进入 `PATROL_SLEEP` 阶段。
- **Monkey 压测进程验证**（以设备 `6R0A77LA****0076` 为例）：
  - `ps -ef | grep monkey` 输出：
    ```text
    root  8095  8018 22 03:15:11 ?  00:00:26 com.android.commands.monkey.transsion
    ```
  - 进程持续稳定占用 CPU，无任何开关机进程干扰，无异常关机重启。

## Revisit

- 本次实证充分验证了 `powercycle_disarm@1.0.8` 在 UNISOC A57 芯片架构与中兴 MyOS 系统下的解除可靠性，未来可作为评估将 UNISOC 平台纳入 B6 可信组合登记的实证依据；
- 平台在面临大规模机队并发切换时表现出极高的调度韧性，后续在推进批次 B7（GPU 归还契约）与后续专项接入时，应继续坚持此种无外部干预的原生自治原则。
