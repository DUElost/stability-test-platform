# ADR-0061: 开关机链路的"已解除"必须是设备侧事实——teardown 结果不得被"跑完了"替代

- 状态：Proposed
- 版本记录：
  - v0.1 2026-10-14 起草（[#3714](https://github.com/DUElost/stability-test-platform/issues/3714)：
    `MLD_NX2-开关机专项-18H`（plan 78）两次中止后，5 台设备在 23 小时后仍在自主开关机，
    而平台账面上它们是"已完成/已中止"）。

## 1. 背景与可复核事实

中止开关机专项后，设备侧的续跑链路（`com.tinno.autotesttool` 的
`shared_prefs/powercycle_runner.xml`，键 `running` / `auto_resume`）必须被写回
`running=false` 才算真停。写回由 `powercycle_finish`（teardown 阶段）经 adb 完成，
因此**依赖设备当时可达**。2026-10-14 实测（`172.21.x.x`，末段 `.83–.86`）：

| 事实 | 证据 |
|---|---|
| 5 台仍在自主循环，`running=true` | 设备侧 prefs 回读：id 957（`current_count` 491/1000）、978（187）、976（101）、977（42）、943；发现时 uptime 34–104s |
| 中止对**当时可达**的设备有效 | 同 host 另 10 台 `running=false`、计数停在 10–12、uptime ≈ 84,200s（对齐 10-13 10:40 中止时刻） |
| teardown 失败被记成作业成功 | run 906 `step_traces`：5 条 `powercycle_finish=FAILED` 而 `__job__=COMPLETED`、`error_message=None`（951/957/976/977/978） |
| manual_exit 直接跳过 teardown | run 920 dev 939：trace 止于 `powercycle_setup COMPLETED`，`status_reason="lifecycle ended: manual_exit"`，无 finish 记录（ADR-0022 BO4，同族 #3696） |
| 链路无时间兜底 | prefs 实测 `mode=reboot`、`next_power_on_at=0`；`powercycle_setup` P0 只支持 reboot（poweroff 配置校验失败）。唯一上限是 `test_times=1000` 轮 |
| "关机 + 仍武装"存量 | 951（10-12 19:09 掉线，run 906 窗口内）、939（10-13 10:46 掉线，run 920 中止那一分钟）；两者均不在任何 host 总线上 |
| 准入是整 run 原子的 | `backend/services/admission_pump.py` 模块 docstring：*"one PlanRun admits as a unit … there is no partial per-host admission"* |
| host 归属会随上线自愈 | 四台 host sysfs 全量枚举 vs 平台 `host_id`：48 台可见设备，**不符 0 台**；`device_host_map` 在每次准入评估时从 Device 表现取（`plan_dispatcher_sync.py:95-123`） |

## 2. 现行语义为什么"骗人"

`backend/agent/pipeline_engine.py:2155` 起：

```python
success = termination_reason in ("completed", "timeout")   # 与 teardown 结果无关
...
final_metadata["teardown_status"] = "SKIPPED"              # manual_exit 只进 metadata
... teardown_status ∈ {SUCCESS, DEGRADED, FAILED}          # :2595-2599，同样只进 metadata
```

即 `teardown_status` **只作为元数据留存，不参与终态判定**。18H 窗口自然到点时
`termination_reason="completed"`，于是清理失败的作业仍记 `COMPLETED`；`manual_exit` 时记
`ABORTED` 但 teardown 根本没跑。两种情况在列表页与报表里都与"已清理干净"不可区分。

顺带一条实现约束：`backend/services/report_service.py:269` 把 `job.status_reason` 当
`error_message` 的回落来源使用，**不得**为了"留个痕迹"就往 `status_reason` 里写非错误文本，
否则成功作业会在报表里长出假错误。

## 3. 决策（待 Owner 裁决；本 ADR 接受前不改任何执行语义）

- **D1｜"已解除"是设备侧回读事实，不是步骤执行记录。** 判据固定为：回读
  `powercycle_runner.xml` 得 `running=false`（或 prefs 文件确证不存在）。`powercycle_finish`
  的步级结论必须以该回读为准；回读不到（设备不可达）即步失败——现状已如此，本条只是把它写成契约。
- **D2｜teardown 结果必须可从作业上查询，且不得伪装成"已清理"。** 建议做法（择一，倾向 ①）：
  ① 新增独立可查询字段（如 `cleanup_status ∈ {OK, FAILED, SKIPPED, UNKNOWN}`）并由终态回报持久化，
  **不翻转** `COMPLETED/ABORTED` 本体语义（避免污染成功率与告警口径）；
  ② 直接把 teardown 失败翻成 `FAILED`——代价是所有专项的清理失败都会改判历史口径，
  且 `MTBF`/成功率统计与 #3679 类告警规则都要重评，**不建议**在本单顺手做。
- **D3｜未确认解除的设备不得被当作"可交付"。** 由派发侧拦截：设备带 `cleanup_status∈{FAILED,SKIPPED,UNKNOWN}`
  且 `chain_armed=true` 时，准入以显式 reason 排队或拒绝（与 ADR-0026 的 queue_reason 同机制）。
  这条是本单真正的止血点：一台随时会自己重启的设备被派去跑 Monkey/GPU/MTBF，损失远大于一次清理失败。
- **D4｜中止/超时对不可达设备登记"上线即解除"。** 复用现有 `powercycle_finish`，但**必须一台一个 run**
  （受"准入整 run 原子"约束，见 §1）。若嫌派发路径别扭，改为独立 pending-disarm 登记表并由设备上线事件驱动。
- **D5｜链路必须有绝对到期，不依赖计数上限。** `auto_resume` 应带 deadline（如
  `last_action_time + 计划窗口 + 宽限`）过期自弃。⚠ **实现受限于 out-of-band 资源**：
  续跑逻辑在 `AutoTestTool.apk` 内（`agent/resources/power-cycle/`，非本仓构建物），
  平台侧只能改写入的 prefs 键，能否生效取决于 APK 是否支持该键。故 D5 落地前必须先向工具方确认；
  确认前的替代是平台侧 sweep：周期回读 armed 设备并解除。

## 4. 放弃的备选

- **"让现场长按电源直到计数跑满 1000 轮"**：否。一轮 60s，最长 16 小时以上，且期间设备随时可被误派。
- **每次中止后人工逐台回读 prefs**：作为本次应急手段可以，作为长期机制否——规模不可扩展，且依赖 root 与
  root 后重连时序（实测会把"root 重启 adbd 瞬间读不到文件"误报成"无 prefs"）。
- **在 `powercycle_finish` 里加重试直到成功**：否。它只是把不可达问题变成作业长挂，且掩盖真实停不下来的设备。
- **把 teardown 失败写进 `status_reason` 了事**：否，见 §2 末的 `report_service` 回落约束。

## 5. 实施边界

- 本 ADR 接受前：只允许做 D2/D4 的**只读查询与运维手册**部分（已在
  [`operations/powercycle-chain-disarm-runbook.md`](../operations/powercycle-chain-disarm-runbook.md) 落地），
  不改终态语义。
- 接受后：D2/D3 涉及回报协议与 job 列（迁移需按 ADR-0051/单表迁移纪律）、D4 涉及派发路径，
  按 ADR-0058 判据决定是否成批；D5 依赖工具方，单列。
- 复议触发器：任一专项再次出现"账面已清理、设备仍 armed"；或 `test_times` 上限不足以让
  armed 设备自弃（实测已跑到 491/1000）；或 out-of-band APK 具备 deadline 键能力。
