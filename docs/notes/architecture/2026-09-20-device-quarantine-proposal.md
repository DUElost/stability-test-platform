# 提案：device 级隔离位（#2753 第 3 项，「device 级维护位」的语义收敛）

Status: proposed
Class: architecture

> **本文是提案，不是裁决**。所有结论需 owner 裁定后才落地；未裁定的项在 §Revisit 列成清单。
> 触发来源：#2753（10 台 MTK 刷了非调试固件 → `ensure_root` 6/6 窗全败），该单把
> 「device 级维护位」标为**另立**，本文即那一步的输入。

## Decision

（本节给结论；需求与现状作为结论的依据并列在此。）

### 需求：为什么 host 级不够

#2753 现场：`.89`×9 + `.87`×1 是**同一条坏批次**（`ro.debuggable=0` 的产线构建，adbd 拒绝
`adb root`）。host 级维护位（`host.maintenance_until`）在这里的代价是：

- `.87`：坏批只有 1 台，host 上还有 6 台正常设备 → host 级维护**误伤 6 台**；
- `.89`：恰好 9 台全是坏批，host 级看起来「干净」——**但那是运气**，不是判据。

而现状（不做任何隔离）的代价是每 4–6h 一个全量窗、每窗烧 **10 个 init job**，并污染 run
统计（r431 的 44 台 `ensure_root` 失败里有 10 台是本批）；失败原因埋在 job 里，是审计翻出来
的，不是调度面看出来的。

### 现状：host 侧那套是什么语义

| 项 | 实现 |
|---|---|
| 字段 | `host.maintenance_until`（截止时刻）+ `host.maintenance_holder`（持有者），迁移 `m8n9o0p1q2r3`（#960） |
| 入口 | `services/host_maintenance.py`：`acquire_maintenance_window`（TTL + 持有者，冲突即拒）/ `release_maintenance_window`（**只认持有者**）/ `in_maintenance_window`（判据） |
| 执行点 | ① 派发：`services/plan_dispatcher_sync.py:193`（`reason="host_maintenance"`，可重试）；② 认领：`services/agent_claim.py:188`（与派发同一判据，防「检查完活跃 Job → 重启」之间认领进新作业） |
| 共用 | 热更新/升级门禁把它当作**唯一能同时挡住派发与 claim 的互斥面**（`services/host_upgrade_gate.py:6`） |

**关键观察**：host 那套是**短窗互斥**——「我正占着这台机器做维护（几分钟到几小时）」，
所以 TTL + 持有者是对的。**#2753 需要的不是这个**：那 10 台在**重刷之前**都不该进链，
可能是几天，没人愿意每隔几小时续一次 TTL。

### 提案：device 级「隔离位」（quarantine），而不是 device 级「维护窗」

**语义**：`device ∈ quarantine` = 「这台设备在**已验证修复**之前不得被派发、不得被认领」。
与 host 维护窗的三点差异：

1. **无 TTL**：只由显式动作解除（重刷后核验通过）；到期自动过期会让坏设备悄悄回到链上——
   那正是本仓反复出现的「绿而空」形态；
2. **带理由与凭据**：`quarantine_reason`（枚举，如 `non_debuggable_build`）+ `quarantine_ref`
   （关联的 issue/批次标识），让「为什么它不跑」在设备页可见、在审计里可回溯；
3. **不占坑语义**：它不是「我正在用」，而是「它不可用」——所以**不需要 holder 概念**
   （holder 的目的是防两个操作者互擦窗口；这里的出口是核验，不是窗口）。

**字段建议**（需 owner 定稿）：`device.quarantined_at TIMESTAMPTZ NULL`、
`device.quarantine_reason VARCHAR NULL`、`device.quarantine_ref VARCHAR NULL`。

**执行点**（对照 host 的两处，保持「派发 + 认领同判据」这条既有纪律）：

- `services/plan_dispatcher_sync.py`：设备不可用清单里加 `reason="device_quarantined"`；
- `services/agent_claim.py`：与 host 检查并列，加设备级检查（同判据，防竞态）；
- **展示面**（本条是 #2753 的直接教训：现状失败埋在 job 里）：设备页/主机页标记 + 可按
  `quarantine_reason` 筛选；仪表盘「为什么少了几台」不再需要人肉翻 job。

**出口**：重刷 → 核验（`ensure_root` 通过，或读回 `ro.debuggable=1`）→ **显式解除**。
建议把核验做成解除的前置（解除接口要求带核验证据字符串），避免「手滑解除」。

**过渡**：#2753 现存 10 台由运维按同一入口批量隔离即可（**不需要数据迁移**——新列默认
NULL = 未隔离）。

## Alternatives

1. **复用 `device.status`（如加一个 `QUARANTINED`）**：**否决**——`device.status` 由**心跳**
   写入（`api/routes/heartbeat.py:141`、`:496-505` 按设备真实状态写 ONLINE/OFFLINE/BUSY/
   ERROR），操作员设的值会被下一跳**覆盖**；且那是「设备自报的业务状态」，与「运维意图」
   是两层。
2. **plan 级设备排除名单**：否决。只对单个 plan 生效，没有单一事实源；新 plan（或新一批
   周期回归）要重新配置——#2753 的复发形态恰恰是「没人记得上次排除了什么」。
3. **host 级维护**：对 `.87` 误伤 6 台正常设备（见 §Decision）；对 `.89` 只是运气好。
4. **维持现状（靠 ensure_root 失败兜底）**：现状就是它。每窗 10 个 job + 统计污染 + 失败
   不可发现；#2753 是审计人肉翻出来的。
5. **在 dispatch 侧按构建指纹自动识别并隔离**（读到 `ro.debuggable=0` 自动隔离）：
   **部分可行但代价大**——`ro.*` 只能设备侧读，要 agent 上报新字段（心跳扩面 + 迁移 +
   fleet 铺开）；且「产线构建」不等于「坏」（有些场景就是要测非调试固件）。建议作为
   §Revisit 的方向：先做人工隔离位，让自动识别成为它的生产者之一。

## Verification

本文是提案，核验的是**依据本身**（都按「先证伪再采信」读过代码，非凭记忆）：

- host 侧三处引用逐条打开确认：`acquire_maintenance_window` / `release_maintenance_window` /
  `in_maintenance_window` 三个函数的实际名字与语义（起草时曾把 acquire 误写成
  `set_maintenance_window`，核对时改正）；`plan_dispatcher_sync.py:193` 确为「不可用清单 +
  reason」形态；`agent_claim.py:188` 确为 claim 前拒；升级门禁确以它为互斥面。
- **否决方案 1 的关键依据是实测的代码路径**，不是推断：`device.status` 在
  `api/routes/heartbeat.py:141` 与 `:496-505` 被心跳写成 ONLINE/OFFLINE/BUSY/ERROR。
- #2753 的现场数字（6/6 窗、10 台、`.67` 对照台 `ro.debuggable=1`）引自该单的只读取证，
  本文不重复取证；本文的增量是**「host 级 vs device 级」的语义辨析**。

## Revisit

### 待裁决（owner 最小清单）

1. **概念与命名**：`quarantine`（隔离）还是沿用 `maintenance`（维护）？本文建议前者——
   语义不同（无 TTL、无持有者、出口是核验），沿用旧名会让读者按 host 那套理解。
2. **是否允许 TTL 自动过期**：本文建议**不允许**（坏设备静默回链是最坏形态）。
3. **执行点是否含 claim**：本文建议**含**（与 host 同纪律：「派发 + 认领同判据」）。
4. **权限**：谁能设/解？（host 维护位目前走 admin 面。）
5. **展示面范围**：设备页标记 + 筛选是必须项；是否进仪表盘「不可用原因」分桶需定。
6. **是否同批做「按批次批量隔离」接口**（#2753 是 10 台同批）：建议提供按 serial 列表批量
   设置的入口，避免一次点 10 次。

### 裁决通过后的落地切片（供排期参考）

① 迁移（三列 NULL）+ 服务助手（acquire/release/is_quarantined，无需 holder）；② 两处执行点
+ 各自回归用例（派发不可用 reason、claim 拒绝）；③ 设备页标记与筛选 + API；④ 批量入口 +
#2753 的 10 台实际隔离（运维动作）。

### 更远的一步

`ro.debuggable` 之类的**构建指纹自动识别**（方案 5）——先有人工隔离位作为它的落点，
再决定 agent 是否上报、以及「产线构建是否一律隔离」（那是个产品问题，不是调度问题）。
