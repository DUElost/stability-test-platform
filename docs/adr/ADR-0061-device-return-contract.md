# ADR-0061: 设备归还契约——任何结束路径后设备回到受控状态，以设备侧回读为准

- 状态：Proposed v0.2（2026-10-14 规划复核后原地扩为全部计划的通用契约，待 Owner 裁决 D1–D8；接受前不改任何执行语义）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-10-14
- 决策者：Owner
- 标签：执行引擎, 设备租约, 专项脚本, 中止语义
- 归属域：n/a（Proposed；Owner 接受后登记语义归属索引键，候选 `device-return`）
- 版本记录：
  - v0.1 2026-10-14 起草（[#3714](https://github.com/DUElost/stability-test-platform/issues/3714)：
    `MLD_NX2-开关机专项-18H`（plan 78）两次中止后，5 台设备在 23 小时后仍在自主开关机）。范围限开关机专项。
  - v0.2 2026-10-14 规划复核（[#3718](https://github.com/DUElost/stability-test-platform/issues/3718)）：
    范围扩为全部计划；按生产只读数据与 Agent 日志重建根因（§1）；更正 v0.1 的五处事实（§1.5）；
    决策改为 D1–D8 的目标模型与迁移路径，v0.1 D1–D5 的去向见 §3.9。
    文件由 `ADR-0061-powercycle-terminal-truth.md` 改名为本文件。

## 1. 背景与证据

### 1.1 问题本质

一个 PlanRun 临时接管设备，并改变设备状态：安装工具、修改设置、启动执行体。平台现在把
「job 到达终态 + 租约释放」当作「设备已归还」。设备是否回到受控状态，平台没有任何持久化事实，
派发也不参考它。

五个专项都会在设备上启动脱离 Agent 进程仍持续运行的执行体：

| 专项 | 设备侧执行体 | 负责停止的脚本 | 依据 |
|---|---|---|---|
| 开关机 | AutoTestTool `PowerCycleService`；prefs `auto_resume`，开机广播自启 | `powercycle_finish` | `powercycle_finish.py` 流程注释第 1 步 |
| 休眠 | AutoTestTool 休眠服务；prefs `auto_resume`，开机自启 | `sleep_finish` | `sleep_setup/_lib.py:511-512` |
| MTBF | OSM `RunTaskService` 前台服务；prefs `auto_resume` | `mtbf_finish` | `mtbf_setup.py:125,138` |
| GPU | 设备端 `nohup` 循环脚本；飞行模式、屏幕常亮等设置 | `gpu_finish` | `gpu_setup/_lib.py:585,617` |
| Monkey | 设备端 `nohup MonkeyTest.sh` 与 `aimwd` 看门狗 | `monkey_teardown` | `monkey_launch.py:84,95` |

`teardown` 阶段的脚本一旦没有执行或没有成功，执行体就会继续运行，设备不受平台控制。
开关机专项中止后设备仍自主重启（#3714、#3696）是其中最显眼的一种表现。

### 1.2 平台机制（与计划类型无关）

| 编号 | 机制 | 代码位置 | 生产证据 |
|---|---|---|---|
| M1 | 中止后，`teardown` 步等待 permit 超过 5s 即放弃：脚本不执行，也不写 step_trace | `pipeline_engine.py:1551-1557`（自 2026-08-04 PR #152 `teardown` 接入 permit 起）；permit 默认上限 5（`operation_scheduler.py:28`） | run 920 job 185451（设备 943，`.85`）：`executing teardown` 后恰好 5.0s 报 `failed`，无任何 trace；Monkey run 841 同一特征 |
| M2 | recovery sync 对已请求中止的 job 下发 `ABORT_LOCAL`，Agent `cancel()` 终止执行中的 `teardown` 进程；触发源为 60s 周期 tick 与设备重连钩子 | `agent_recovery.py:423-443`；`recovery_runtime.py:36-39`；`recovery_executor.py:205,247`；`pipeline_engine.py:1078-1087` | run 920 job 185444（设备 933）：设备重启回来（`recovery_reconnect_triggered`）79ms 后 `teardown` 被终止（rc=143）；被终止的开关机 `teardown` 有 126/134 在启动 10s 内 |
| M3 | `teardown` 结论不进终态载荷，终态只看 `termination_reason` | `pipeline_engine.py:2154`；`pipeline_runner.py:101-110`（载荷不含 metadata，后端无 `teardown_status` 消费方） | 34,027 个 COMPLETED job 的 `teardown` 有失败步；其中 940 次是脚本回读到「running 未置 false」（开关机 397、休眠 543） |
| M4 | ABORTED 即释放租约，不看清理结果 | 终态回报后的租约释放路径 | 设备 943 于 10:39:31 释放租约，其 `teardown` 未执行 |
| M5 | manual_exit 跳过 `teardown`（ADR-0022 D7 BO4）。BO4 的缓解前提是「下个 Plan 的 init 兜底」，该前提对设备侧执行体不成立 | `pipeline_engine.py:2126` | #894 的跨专项防御只在 monkey、sleep、powercycle 三个 setup 中；GPU、MTBF 的 setup 没有；其它专项的 setup 都不会停残留的 Monkey 或 GPU 循环 |
| M6 | `teardown` 步骤墙钟小于脚本内部的等待预算 | plan 78：`timeout_seconds=300`，`wait_device_online_seconds=600` | run 906 五台（951、957、976、977、978）`script timeout after 300s`，脚本自己的等待未走完 |

另有一条代码层面的风险，尚无生产实例：Agent 优雅停机时先调用 `operation_scheduler.shutdown()`，
再中止作业（`graceful_shutdown.py:42,49`），此时 `teardown` 步取不到 permit。热更新默认不中止在跑作业
（`host_upgrade_gate.py` 的 `abort_running_jobs=False`），该路径通常不触发。

代码表明设计意图本来是中止后仍执行 `teardown`：`_execute_lifecycle` 的 docstring 写明 teardown 经
try/finally 总会执行，`pipeline_engine.py:1840` 也只对 `teardown` 阶段放行取消后的子进程。M1、M2
是实现与该意图的偏离。

### 1.3 量化（生产只读，`stp_ro`，`step_trace` 保留窗自 2026-08-05 起，查询日 2026-10-14）

中止时 `teardown` 未执行的比例，与同一 Agent host 上同时中止的 job 数强相关：

| 同一 Agent host 同时中止 | job | `teardown` 未执行 | 比例 |
|---|---|---|---|
| ≤5 | 305 | 15 | 4.9% |
| 6–10 | 1,399 | 345 | 24.7% |
| 11–20 | 6,161 | 2,872 | 46.6% |
| >20 | 1,462 | 829 | 56.7% |

中止后执行体已启动、但未被平台停止的 job（候选数）：

| 专项 | 中止 job | 执行体未被停止 | 涉及设备 |
|---|---|---|---|
| Monkey | 4,148 | 1,755 | 603 |
| 开关机 | 2,351 | 1,232 | 557 |
| 休眠 | 1,369 | 831 | 440 |
| GPU | 1,456 | 635 | 382 |
| MTBF | 3 | 0 | 0 |

口径：`init` 中启动执行体的步骤已 COMPLETED，且 `teardown` 未执行或被 SIGTERM 终止。部分执行体会自然结束，
部分设备之后被重刷，或被同专项下一轮 setup 覆盖，因此这是候选数，不等于全部长期失控。

### 1.4 问题为何长期未暴露

- 设备专机专用时，下一轮同专项的 setup 会覆盖残留；设备跨专项复用（如 NX2：开关机 → 刷机 → Monkey）时，问题才显形。
- M1 不写任何 trace，M3 丢弃 `teardown` 结论：平台上没有可查的痕迹，只有设备侧回读能发现。
- 「中止瞬间设备正处于重启窗口」不是主因：中止指令由 Agent host 接收（run 920 的 11 个 job 均记录
  `control_abort requested=True`）；`powercycle_finish` 最长等待设备上线 600s（`powercycle_finish.py:176-190`）。
  开关机专项近 70 天的中止里，约 10 例真正因设备可达性失败，约 2,030 例由 M1、M2 造成。
  设备 933 显示 M2 恰在设备重连时终止了正在等待它的 `teardown`。真正只能归因于设备的是长时间离线（如设备 951）。

### 1.5 对 v0.1 的事实更正

1. v0.1 §1 称 run 920 的设备 939「trace 止于 `powercycle_setup`，`status_reason="lifecycle ended: manual_exit"`」。
   实际：run 920 中 939 的 `powercycle_finish` 于 10:39:30 COMPLETED（rc=0，v1.0.8 含回读校验）；
   v0.1 引用的是 run 906（10-12 manual_exit）中 939 的 trace。939 是否仍有足迹没有证据支持，需现场回读确认。
2. v0.1 把设备 943 归因为「中止时不可达」。实际是 M1：`teardown` 从未执行。
3. v0.1 把 run 906 五台归因为不可达窗口。实际：这五台没有被中止，18H 到点（`termination_reason=timeout`）后
   被 300s 墙钟截断（M6）；其中 951 自 10-12 起长时间离线。
4. v0.1 §2 称 `teardown_status`「只进 metadata 留存」。实际：它不进终态载荷，后端无消费方，生产库查不到（M3）。
5. v0.1 §2 称 18H 自然到点时 `termination_reason="completed"`。实际值为 `timeout`。

## 2. 目标模型（规划者提案）

平台缺少一个概念：**设备归还状态**。它是设备级事实，与测试结论（job 终态）和租约状态正交：

```text
job 终态（测试结论）            设备归还状态（设备事实）
COMPLETED / FAILED / ABORTED    已归还 / 欠账 / 未验证
                                     ▲
专项契约：足迹 → 解除 → 回读探针 ──────┘
派发：只有「已归还」或「未验证」的设备可以被租用
清偿：设备变为可达 → 解除 + 回读 → 已归还 → 释放占用
```

- 归还以设备侧回读为准，不以「步骤跑完了」为准；
- 每个会留下足迹的专项登记足迹、解除动作与回读探针，解除与结果收取分开；
- 平台保证解除在任何结束路径上被尝试，平台自身的机制不得丢弃或终止它；
- 解除未能确认时，义务转为设备欠账：设备不交付，上线时自动清偿；
- 纵深防御（init 跨专项残留清扫、执行体到期）与可观测兜住剩余风险。

新增专项时只要登记契约，就自动获得上述保证，不再需要事故驱动的逐专项排查。

## 3. 决策（待 Owner 裁决；本 ADR 接受前不改任何执行语义）

### D1 归还的判据

1. 设备「已归还」的唯一判据是设备侧回读：该设备上全部已登记专项的回读探针都报告「无足迹」。
   控制面不得用脚本退出码、job 终态或租约状态替代此判据。
2. 每个 job 结束后，控制面必须为该设备记录以下三种归还状态之一。机器值由首批批次方案确定。

   | 状态 | 含义 |
   |---|---|
   | 已归还 | 回读确认无足迹 |
   | 欠账 | 回读发现足迹，或未能确认：解除失败、未执行、被终止、设备不可达 |
   | 未验证 | 涉及的专项尚未登记回读探针（迁移期，见 D8） |

### D2 专项契约

1. 会在设备上留下持续执行体或持久设置的脚本族，实施者必须登记三项：足迹清单、解除动作、回读探针。
2. 实施者必须使解除动作幂等，并使它能在任意时刻对任意设备执行，包括本专项从未在该设备上运行过的情形。
3. 实施者不得让解除动作依赖结果收取。控制面不得因结果收取失败而改变归还判定。
4. 实施者新增或实质修改会产生足迹的脚本族时，必须在同一 PR 中登记或更新其契约（防倒退棘轮）。
5. 登记表的位置与格式由首批批次方案确定。

### D3 解除的执行保证

1. job 在 `init` 阶段执行了会建立足迹的步骤之后，Agent host 必须在该 job 的每一条结束路径上尝试执行解除。
   结束路径包括：自然到点、PlanRun 中止、manual_exit、`init` 或 `patrol` 失败、barrier 超时、失去租约、Agent 服务优雅停机。
2. Agent host 不得因 job 已被取消而放弃排队中的解除步骤（M1）。
3. Agent host 收到 `ABORT_LOCAL`、重复的 `abort` 指令或设备重连事件时，不得终止执行中的解除步骤（M2）。
4. Agent host 处理 manual_exit 时可以跳过结果收取，不得跳过解除。本条修订 ADR-0022 D7 的 BO4。
5. 控制面在中止宽限到期回收 job 时，不得释放设备，必须把设备记为欠账（D5）。本条修订 ADR-0043 回收的后果；
   宽限时长如何覆盖解除耗时，由首批批次方案确定。
6. 控制面在保存 Plan 时必须拒绝步骤墙钟小于该步骤脚本自身等待预算的配置（M6）。
7. 设备已被其它 job 占用时，Agent host 不得对该设备执行解除。冲突的处理方式由首批批次方案确定。
8. 解除未能开始、未能完成或回读未能确认时，控制面必须把该设备记为欠账，不得让该义务随 job 终态消失。

### D4 归还结果落库

1. 控制面必须为每个 job 持久化独立的归还结果字段，不得翻转 COMPLETED、FAILED、ABORTED 的测试结论（保留 v0.1 D2 方案①）。
2. Agent host 的终态回报必须携带解除结果与回读证据。控制面不得从 job 终态推断归还结果。
3. 实施者不得把解除失败写进 `status_reason`：`report_service.py:269` 会把它当作 `error_message` 的回落值。
4. 控制面必须在 job 详情与设备详情中展示归还结果。

### D5 欠账设备不交付，并自动清偿

1. 归还状态为「欠账」的设备，控制面必须保持其不可派发，直到欠账清偿。
2. 实施者建议用 ADR-0019 已定义的 `MAINTENANCE` 租约承载欠账（holder 为平台，reason 写明欠账来源）。
   理由：它复用「每台设备同时只有一个 ACTIVE 租约」的互斥，各条派发路径因此不必另行检查。
   批次方案改用独立表时，规划者必须在方案中证明全部派发与认领路径都会检查该表。
3. 设备变为可达（上线或重连）时，Agent host 必须对欠账设备执行相应专项的解除与回读。
   回读确认后，控制面必须释放占用，并把归还状态改为「已归还」。
4. 控制面不得把人工创建 PlanRun 作为清偿的前提。本条取代 v0.1 D4 的「一台设备一个 run」做法。
5. 控制面必须在设备页与 PlanRun 派发预览中展示欠账及其原因。
6. 运维人员可以人工核销欠账，例如设备已重刷。控制面必须为每次核销写审计记录。
7. 控制面不得因欠账阻止设备退役（ADR-0057）。

### D6 纵深防御

1. Agent host 必须在每个 job 的 `init` 阶段先用已登记的回读探针检查全部专项的残留，对命中项执行对应的解除，
   再建立本专项的足迹。本条取代 #894 分散在三个 setup 中的跨专项防御。
2. 设备侧工具支持时，实施者建议在 setup 中为执行体设置绝对到期时间。
3. 开关机专项的到期键依赖 `AutoTestTool.apk`（仓库外构建）。规划者必须在该项落地前向工具方确认支持情况（即 v0.1 D5）。

### D7 可观测

1. 控制面必须为以下事实提供指标并配置告警：解除未执行、解除被终止、解除失败、欠账设备数、欠账存续时长。
2. 告警阈值由首批批次方案确定。

### D8 迁移与过渡

1. 实施者必须按以下顺序推进：阶段 A（D3、D4、D7）→ 阶段 B（D5）→ 阶段 C（专项接入，开关机先行）→ 阶段 D（D6）。
2. 规划者可以按机会式安排开关机之外的专项接入；接入受 D2 第 4 条约束。
3. 控制面必须把尚未登记探针的专项的设备归还状态记为「未验证」，不得因此阻止派发。
   全部专项接入后，规划者必须提议退役「未验证」状态。
4. 作者必须把过渡措施标注为过渡并写明出口。开关机运维手册
   [`powercycle-chain-disarm-runbook.md`](../operations/powercycle-chain-disarm-runbook.md) 与单台清理 PlanRun 的出口均为 D5 生效。

### 3.9 v0.1 决策的去向

| v0.1 | v0.2 |
|---|---|
| D1 开关机「已解除」以 `running=false` 回读为准 | 推广为 D1；开关机的回读探针（prefs `running` 回读，加两次相隔 10s 以上的 uptime 读数连续增长）在阶段 C 登记 |
| D2 teardown 结果可查询，方案①（新增 `cleanup_status`，不翻转本体） | 保留为 D4 |
| D3 派发拦截（判据 `chain_armed`） | 推广为 D5，判据改为设备欠账 |
| D4 不可达设备「一台一 run」 | 降为过渡措施（D8 第 4 条），终态由 D5 取代 |
| D5 链路绝对到期 | D6 第 3 条（开关机实例） |

## 4. 对既有 ADR 条款的处理

| ADR | 条款 | 处理 |
|---|---|---|
| ADR-0022 | D7 BO4：manual_exit 跳过 teardown | 修订：只跳过结果收取（D3 第 4 条） |
| ADR-0043 | 中止宽限到期后回收为 UNKNOWN，再由 reconciler FAILED 并释放 | 修订回收后果：不释放设备，记为欠账（D3 第 5 条） |
| ADR-0019 | `MAINTENANCE` 租约（已定义，生产未启用） | 建议启用为欠账载体（D5 第 2 条） |
| ADR-0026 | 准入 `queue_reason` | 沿用：欠账作为不可派发原因展示 |
| ADR-0022 | D9：`teardown` 逐步上报 | 保留 |

保留的不变量：`pipeline_def.lifecycle` 结构与 action 格式不变；Plan 不存 lifecycle；测试结论口径不变。

## 5. 放弃的备选

- **把解除失败翻成 FAILED**（v0.1 方案②）：会改变所有专项的成功率与告警口径，且仍然不阻止派发。
- **按专项在引擎里打补丁**（如只对开关机放开 permit）：M1–M4 在通用引擎中，按专项修复会让其余专项继续失控。
- **在 finish 脚本里重试到成功为止**：把不可达问题变成作业长挂，并掩盖真正停不下来的设备。
- **只靠运维手册人工核验**：规模不可扩展；欠账没有持久化事实。
- **提高 `STP_MAX_CONCURRENT_OPERATIONS` 缓解 M1**：不解决 M2、M3，并改变全机并发与带宽假设（#118）。
- **另起新编号承载通用契约**：ADR-0058 D8 不允许同一主题出现第二份权威 ADR；v0.1 仍为 Proposed，按版本记录原地修订。
- **欠账用独立设备状态表**：可行，但每条派发与认领路径都要检查它，遗漏一条即失效；`MAINTENANCE` 租约复用既有互斥（D5 第 2 条）。

## 6. 影响

- Agent host：引擎的 permit、取消与停机路径改动；终态载荷增加解除结果；需要机队热更新。
- 控制面：job 新增归还结果字段（迁移）；欠账占用与清偿；recovery 与回收逻辑改动；需要控制面部署。
- 脚本族：解除与结果收取分开，登记契约；按 ADR-0051 登记新版本，相应计划模板跟进版本 pin。
- 运维：中止耗时会变长（解除必须跑完）；欠账期间可派发设备变少，原因可见。
- 统计：测试结论口径不变。

## 7. 落地（按 ADR-0058）

- 载体：[#3718](https://github.com/DUElost/stability-test-platform/issues/3718)（架构与 Epic）。
  首批 B6 = 阶段 A + 阶段 B + 阶段 C 的开关机；Owner 裁决本 ADR 并批准 Appetite 后，规划者另立批次 issue。
- 激活：控制面部署 + Agent 机队热更新 + 开关机族新版本 + plan 78 等配置修正。
- L2 验收：单个 Agent host 上超过 5 台开关机设备同时中止（含处于重启窗口的设备），全部回读确认已归还；
  离线设备上线后欠账自动清偿。

## 8. 待 Owner 裁决

1. D1–D8 是否接受；可以分条接受，例如先接受 D3 与 D4。
2. D5 欠账载体：`MAINTENANCE` 租约（建议）或独立表。
3. D3 第 5 条与 ADR-0043 的对齐方向：解除期间续期宽限，或解除期间不计宽限。
4. 首批 B6 的 Appetite。

## 9. 复议触发

- 任一专项再次出现「平台记为已归还、设备仍有足迹」。
- 欠账存续时长的告警持续触发，或「未验证」状态长期覆盖大部分设备。
- 设备侧工具普遍支持执行体到期，使 D5 的清偿负担显著下降。

## 10. 关联

- issue：[#3718](https://github.com/DUElost/stability-test-platform/issues/3718)（载体）、
  [#3714](https://github.com/DUElost/stability-test-platform/issues/3714)、
  [#3696](https://github.com/DUElost/stability-test-platform/issues/3696)（开关机证据）、
  [#894](https://github.com/DUElost/stability-test-platform/issues/894)（跨专项残留）、
  [#118](https://github.com/DUElost/stability-test-platform/issues/118)（teardown 接 permit 与结果收取）
- 代码：`backend/agent/pipeline_engine.py`、`backend/agent/operation_scheduler.py`、`backend/agent/recovery_executor.py`、
  `backend/services/agent_recovery.py`、`backend/scheduler/device_lease_reconciler.py:627`、`backend/services/lease_manager.py`
- 文档：[`powercycle-chain-disarm-runbook.md`](../operations/powercycle-chain-disarm-runbook.md)（过渡）、
  [`07-execution-protocol.md`](../design/07-execution-protocol.md)
