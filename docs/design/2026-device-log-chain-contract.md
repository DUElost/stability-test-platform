# 设备日志链路契约（Device Log Chain Contract）

- **状态**：Living Draft（#2546；ChatGPT 分享定型结论落地）
- **类型**：Living Design / Contract——**不是**新 ADR
- **目的**：把已分散在 ADR-0018 / 0025 / 0028 / 0032 与
  [`2026-scan-upload-merge-contract.md`](./2026-scan-upload-merge-contract.md)
  中的现有事实，收敛成一张**系统级地图 + 逻辑坐标**；回答「日志从哪到哪、谁定义什么、
  现在在哪」。
- **非目标**：
  - 不推倒现有采集 / Watcher / DLE / HDD→中心 / MTK·UNISOC 双链；
  - 不新增日志状态机、不新立存储 ADR、不取代上述 ADR 的内容权威；
  - **暂不大改物理目录**；Contract 定型前不推动目录迁移或 merge 执行位置改造；
  - 不实现代码适配器 / 不改运行时行为。
- **细节展开**：阶段编号（A1–A3 / B1–B6）、B2≠B5、泳道图与易混词表见
  [`2026-log-chain-global-semantics.md`](./2026-log-chain-global-semantics.md)
  （#2783；**被本文引用的展开文**，不是第二份地图权威）。
- **关联**：[#2546](https://github.com/DUElost/stability-test-platform/issues/2546)；
  [`2026-semantic-ownership.md`](./2026-semantic-ownership.md) X2；
  ADR-0033 Phase 2 **选项 A 已落地**（原阻塞笔记出口见
  [`2026-09-19-adr0033-phase2-option-a.md`](../notes/architecture/2026-09-19-adr0033-phase2-option-a.md)）。
- **日期**：2026-09-19

**冲突处理**：代码与测试为准；散文冲突时回写权威 ADR / 本 Contract / 展开文。

---

## 1. 本文只承担六件事

| # | 承担 | 不承担 |
|---|------|--------|
| 1 | 完整链路图（Device → … → JIRA） | 逐步 CLI argv / 环境变量百科 |
| 2 | 每阶段唯一 owner（四层 + 后半段契约） | 成为第五个内容权威 |
| 3 | 统一「日志在哪里」逻辑坐标 | UI/API 实现 |
| 4 | Central Storage **逻辑** namespace | 立刻搬迁物理目录 |
| 5 | MTK / UNISOC 真实分叉点 | 混用工具或改 D3 |
| 6 | raw event vs derived artifact（含 HddSpill） | 新状态机 |

`2026-scan-upload-merge-contract` 只覆盖后半段 `scan → upload → merge → extract`，
**不能**替代全链地图。

---

## 2. 完整链路图

### 2.1 主视图（逻辑）

```text
Device
  │
  ├─(运行中)─► Watcher / Reconciler ──► log_signal ──► Pull ──► Agent HDD
  │                                                      │
  │                                                      ▼
  │                                              DeviceLogEvent (DLE)
  │                                                      │
  └─(终态)──── scan_now ──► Scan(B1) ──► Host汇总(B2) ──┤
                                                         │
                    ┌────────────────────────────────────┘
                    │
                    ▼
              Upload xls(B3) + EventUploader(B4)
                    │
                    ▼
           Central Storage（物理现态：dedup/ · devices/ · …）
                    │
                    ▼
              Merge(B5) ──► Extract(B6) ──► jira/ ──► 人工 / JIRA
```

**压力逃生（非正常 scan→upload）**：

```text
Agent HDD ≥95% ──► HddSpill ──► EventUploader(force=True) ──► devices/（spill 路径）
```

运行日志 `logs/runs/{job_id}/`（Agent SSD）**永不**上中心（ADR-0025）。

### 2.2 两层互补（ADR-0032 D2）

| 层 | 何时 | 止于 |
|----|------|------|
| **A · Watcher 实时** | PlanRun / Job RUNNING | Agent HDD + signal/DLE；**不出**汇总 xls、**不做** merge |
| **B · 归档终态** | 终态 / 手动 / `auto_archive` → `scan_now` | 中心 dedup/devices → merge → extract → jira |

编号流水线与 **B2（Agent 主机汇总）≠ B5（控制面多 host merge）** 的展开见
[`2026-log-chain-global-semantics.md`](./2026-log-chain-global-semantics.md) §2。

### 2.3 读图约定

- 文件夹**只做定位投影**，不重复承担「谁 / 何时 / 平台 / 状态 / 下一步」；
  那些语义已在 DLE / PlanRun / platform / `plan_run_artifact`。
- `01-execution-pipeline.md` 是主流程概览；日志链细节以**本 Contract + 展开文 + ADR**
  为准（例如 FAILED 亦走 scan/upload 的现态以 ADR-0028 为准，勿回读旧「仅 SUCCESS
  触发 dedup」句）。

---

## 3. 阶段 owner 表（四层 + 后半段）

与 #2546 X2 一致：**四层都对**，不是竞争 owner。本 Contract 只做 **chain map /
路由入口**，不是第五层内容权威。

| 概念层 | 回答什么 | Owner（内容权威） | 本图位置 |
|--------|----------|-------------------|----------|
| realtime signal | 异常事件**流** | ADR-0018 → `log-signal-stream` | Watcher → `job_log_signal` |
| event lifecycle | 事件在哪、什么状态 | ADR-0028 → `dle-record` | DLE 台账 |
| physical storage | 搬运 / 中心布局 / HDD 模型 | ADR-0025 → `center-storage-model` | HDD、CIFS 角色与三阶段归档 |
| platform / merge 行为 | 并列流水线、禁止混工具、分区 | ADR-0032 → `dedup-pipeline-behavior` | MTK/UNISOC 分叉与 merge 分区 |
| scan/upload/merge **执行契约** | SAQ、完备性、跨进程 I/O | [`2026-scan-upload-merge-contract.md`](./2026-scan-upload-merge-contract.md) → `R-merge-consumes-log` 等 | 层 B 后半段 |
| **chain map（本文）** | 端到端怎么串、坐标与 namespace | Living Contract（路由） | 全图 |

```text
 Semantic Ownership（#2546 表）
        │
        ▼
 ┌──────────────────────┐
 │ Log Chain Contract   │  ← 路由入口 / 地图（本文）
 └──────────┬───────────┘
            │
   ┌────────┼────────┬────────────┐
   ▼        ▼        ▼            ▼
 ADR-0018  ADR-0028  ADR-0025   ADR-0032
 Signal    DLE       Storage    Platform/Merge
            \          |         /
             \         |        /
              ▼        ▼       ▼
         scan-upload-merge contract（后半段执行）
```

---

## 4. Log Event 逻辑坐标（「日志在哪里」）

**同一事实只有一个定位坐标**；目录只是该坐标的物理投影。

查询入口应是 Event（或 PlanRun → Events），而不是「猜 `dedup/` / `devices/` /
`merge/` / HddSpill」。

```text
LogEvent
├── event_id          # DLE 主键
├── serial
├── platform          # device.platform → mtk | unisoc | …
├── event_type
├── plan_run_id / job_id
├── lifecycle_state   # DETECTED → LOCAL → UPLOAD_PENDING → REMOTE → ARCHIVED | …
├── local_location    # Agent HDD 路径（可空 / PRUNED）
└── remote_location   # 中心路径（upload 或 HddSpill 后；可空）
```

派生产物**挂在** Event / PlanRun 上，不另立「第二套身份」：

| 产物 | 逻辑归属 | 现态如何找到（投影） |
|------|----------|----------------------|
| 原始事件目录 | `events/{event_id}` | DLE `remote_path` → 物理 `devices/…` |
| host 汇总 xls | `artifacts/{plan_run_id}/scan` | `dedup/{run}/{platform}/{host}_*` + artifact |
| merge 总表 | `artifacts/{plan_run_id}/merge` | `dedup/{run}/merge/{platform}/` |
| jira 束 | `reports/{plan_run_id}` | `jira/{run}/` |

DB **不是**日志存储；DLE **不是**文件本体（ADR-0028）。

---

## 5. Central Storage：逻辑 namespace vs 现态物理映射

### 5.1 目标逻辑模型（**只定坐标，不搬迁**）

```text
Central Storage
├── events/      # 原始设备日志事件（按 event_id）
├── artifacts/   # scan / merge / extract 等派生产物（按 plan_run_id）
└── reports/     # 面向人的最终报告 / JIRA bundle（按 plan_run_id）
```

可选细化（仍属逻辑层）：

```text
events/<event_id>/
artifacts/<plan_run_id>/{scan,merge,extract}/
reports/<plan_run_id>/
```

### 5.2 现态物理兼容层（映射表；**禁止本轮迁移**）

| 逻辑 namespace | 现态物理路径（权威行为见 ADR-0025/0032） | 写入方 |
|---------------|------------------------------------------|--------|
| events（正常上送） | `devices/{plan_run_id}/…` | Agent `EventUploader` |
| events（HddSpill） | `devices/{folder}/{serial}/…`（legacy 第二根，ADR-0028 D9） | HddSpill → EventUploader(force) |
| artifacts / scan | `dedup/{plan_run_id}/{mtk\|unisoc}/{host_id}_*.xls` | Agent `UploadManager` |
| artifacts / merge | `dedup/{plan_run_id}/merge/{mtk\|unisoc}/` | 控制面 `dedup_scan` |
| reports | `jira/{plan_run_id}/` | 控制面 `dedup_extract` |

其它中心树（`jobs/` `mtbf/` `tools/` `_meta/` 等）见
[`2026-storage-roles-and-aliases.md`](./2026-storage-roles-and-aliases.md)；
**不属于**本日志事件坐标。

P2 再评估「逻辑收敛后物理是否仍需 `devices/`/`dedup/`/`jira/` 字面」——默认答案可以是
**逻辑收敛、物理兼容路径暂不动**。

---

## 6. MTK / UNISOC 分叉点

路由键唯一：`device.platform`。两层**共享路由键、不共享工具**（ADR-0032）。

| 分叉点 | MTK | UNISOC | 是否分叉 |
|--------|-----|--------|----------|
| Watcher / Reconciler | AEE / ZZ_INTERNAL → MTK Reconciler | uniview / dropbox / ylog → UNISOC Reconciler | **是** |
| 归档采集 B1 | `start_log_scan -m 0` | `scan_log_gt -m sprd` | **是** |
| 主机汇总 B2 | `start_log_scan -dedup_org` | `scan_result.py -d`（Scan-Result-GT） | **是** |
| 上送执行 | 同：UploadManager + EventUploader | 同（触发源实现差见 ADR-0032 D10，**非**状态机分叉） | 路径分区是 |
| 控制面 merge B5 | **同一** `start_log_scan -merge_files_list` | **同一**（D3） | 输入/输出**分区**；工具不分叉 |
| extract / ARCHIVED | 同链、同判（D10） | 同 | **否** |

产品层可一张总表；底层 artifact **必须**平台隔离（`dedup/…/{platform}/`、
`merge/{platform}/`）。

QCOM：无采集实现；`reconciler_supported=false`（ADR-0032 D9）。

---

## 7. raw event vs derived artifact；HddSpill

| 类别 | 是什么 | 例子 | 权威索引 |
|------|--------|------|----------|
| **raw event** | 设备上发生的一次日志事件实体（目录 + DLE 行） | HDD / `devices/…` 事件目录 | DLE + `remote_path` |
| **derived artifact** | 对事件集或 run 的加工结果 | `*_org.xls`、merge 总表、jira 束 | `plan_run_artifact` + 中心路径 |
| **signal** | 实时异常流（非文件本体） | `job_log_signal` | ADR-0018 |
| **运行日志** | Job 过程日志 | Agent SSD `logs/runs/…` | **不上中心** |

**HddSpill**：磁盘压力兜底，把仍为 `LOCAL` 的事件 **force** 上送中心；
**不是** scan 精选路径的替代品，也不单独构成「已 ARCHIVED」。
extract 可读 spill 第二根路径（ADR-0028 D9）；旧 spill 数据自然淘汰后可收第二根。

**过滤模型**（ADR-0025/0028）：中心只收 scan 引用的有效子集 + spill；
不存在「连续全量上送」。`upload_task` **只**做 `LOCAL → UPLOAD_PENDING`；
唯一拷贝者是 Agent `EventUploader`。

---

## 8. 与 #2546 / ADR-0033 Phase 2 的关系

| 议题 | 本 Contract 的立场 |
|------|-------------------|
| #2546 X2 | 四层登记 + **本文作 chain map 路由**；S15 不得把四层并存判成冲突 |
| 目录「过多」 | 根因是物理路径叠了生命周期/平台/产物维度；先钉逻辑坐标，再谈是否收敛目录 |
| ADR-0033 Phase 2 A/B/C | **Contract 定型前**：不推动 merge 位置改造、不落 Adapter、不改 `run_merge_sync`；拍板语义前提见展开文 §6 |
| ownership 表 | 可增「路由」说明指向本文；**不**把本文写成新的 `owner_anchor` 内容源 |

---

## 9. 推进顺序（P0–P3）

| 阶段 | 做什么 | 明确不做 |
|------|--------|----------|
| **P0（本轮）** | 定型本 Contract：图、owner、坐标、逻辑 namespace、分叉、raw/derived | 改物理目录、改行为、新 ADR |
| **P1** | 现有 ADR / scan-upload-merge / DOC-MAP / ownership **指向**本文；展开文降为细节 | 让 Contract 反过来取代 ADR 条文 |
| **P2** | 评估物理目录是否仍需字面收敛；默认可「逻辑收敛 + 物理兼容不动」 | 为「目录好看」做高风险存储迁移 |
| **P3** | UI/API：PlanRun → Device → Log Event → Open（经 DLE / artifact index） | 训练用户记文件夹树 |

---

## 10. 权威索引（速查）

| 主题 | 文档 |
|------|------|
| 本地图 / 坐标 | **本文** |
| 阶段展开 · B2≠B5 · 泳道 | [`2026-log-chain-global-semantics.md`](./2026-log-chain-global-semantics.md) |
| signal | [ADR-0018](../adr/ADR-0018-infrastructure-layer-framework-adoption.md) |
| 存储角色与三阶段 | [ADR-0025](../adr/ADR-0025-phase4-architecture-alignment.md)、[`2026-storage-roles-and-aliases.md`](./2026-storage-roles-and-aliases.md) |
| DLE / 上送 / HddSpill | [ADR-0028](../adr/ADR-0028-device-log-event-and-continuous-upload.md) |
| 双平台流水线 | [ADR-0032](../adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md) |
| 后半段跨进程 | [`2026-scan-upload-merge-contract.md`](./2026-scan-upload-merge-contract.md) |
| ownership X2 | [`2026-semantic-ownership.md`](./2026-semantic-ownership.md) |
| 上送时序图 | [`2026-adr-0025-log-flow-sequence.md`](./2026-adr-0025-log-flow-sequence.md) |
