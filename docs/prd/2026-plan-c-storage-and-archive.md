# PRD：方案 C — 存储与归档（ADR-0025）

- **状态**：Accepted（随 ADR-0025 方案 C，2026-06-20）
- **版本**：1.0
- **日期**：2026-06-21
- **跟踪**：[GitHub #32](https://github.com/DUElost/stability-test-platform/issues/32)
- **关联 ADR**：[ADR-0025](../adr/ADR-0025-phase4-architecture-alignment.md)、[ADR-0018](../adr/ADR-0018-infrastructure-layer-framework-adoption.md)

---

## 1. 背景与问题

平台核心场景是**数小时到数天的 Android 稳定性长跑**。运维与开发需要：

1. **边跑边看**：过程中持续发现崩溃、去重、归档关键事件，而非仅 Job 终态后一次性处理。
2. **存储可预期**：Agent 节点 SSD/HDD 容量有限；15.4 中心存储只承担**汇总与按需共享**，不承担全量运行日志。
3. **无人值守**：Agent 重启、热更新后 Watcher 与归档链路可恢复（ADR-0025 D5，Sprint 1 已落地）。

**原 PR#25 模型痛点**（方案 C 前）：

- 运行日志 tar/目录树上送 15.4 + `run_log_bundle` 注册 → 跨主机路径契约脆弱（#14）。
- AEE 设备日志第一落点在 15.4 CIFS → 与「设备日志在 Agent 本地」的运维习惯不符。
- 控制面 scan 15.4 archives → 与「scan 输入在 Agent HDD」不匹配。

---

## 2. 目标用户

| 角色 | 诉求 |
|------|------|
| **稳定性测试运维（admin）** | PlanRun 详情页查看异常、归档/去重进度，长跑中可手动触发归档 |
| **开发（故障分析）** | 从 15.4 或 Agent 获取 AEE 事件目录、去重报告、运行日志 |
| **平台研发** | Agent 侧闭环 + 控制面聚合，单控制平面可水平扩展推迟 |

---

## 3. 产品目标（方案 C）

### 3.1 存储三级

| 层级 | 内容 | 约束 |
|------|------|------|
| Agent SSD | 运行日志（init/patrol/teardown） | **唯一物理存储**；不上送 15.4 |
| Agent HDD | AEE + mobilelog + bugreport | **第一落点**；按事件目录聚合 |
| 15.4 CIFS | 汇总 xls、按需上送事件、HDD 溢出事件 | **不含**全量运行日志 |

### 3.2 访问方式

- **运行日志**：控制面经 **Agent HTTP** 按需下载（Agent 离线则不可用——可接受）。
- **AEE 事件**：默认读 Agent HDD；溢出或按需上送后在 15.4 `devices/`。
- **去重报告**：Agent 本地 scan → 上送 15.4 `dedup/` → 控制面 merge（Sprint 4）。

### 3.3 归档三阶段（产品语义）

| 阶段 | 名称 | 执行位置 |
|------|------|----------|
| 归档-1 | scan + 按需上送 | Agent |
| 归档-2 | （与 scan 合并为 Agent 本地 scan） | Agent |
| 归档-3 | 分类提取 / JIRA 草稿 | 控制面 |

### 3.4 上送触发（五场景，Sprint 4）

1. PlanRun 终态自动  
2. abort / 失败确认后  
3. （原三场景保留）  
4. 手动「过程中归档」  
5. Plan 配置 **自动归档间隔**

---

## 4. 范围

### 4.1 In Scope（按 Sprint）

| Sprint | 内容 |
|--------|------|
| **Sprint 2** | HDD 第一落点、LogArchiver→SSD prune、HDD 溢出上送、运行日志 HTTP、取消 `run_log_bundle` 注册与 cycle 快照 |
| **Sprint 3** | 控制面/前端对接新模型（Archive 区、report、watcher-summary）；#16/#17/#18 UX |
| **Sprint 4** | Agent scan、upload_manager、五触发、dedup 完成条件改造 |

### 4.2 Out of Scope（本 PRD）

- 多控制平面水平扩展（ADR-0025 D1 推迟）
- Loki 集中日志（D2 不引入）
- 运行日志上送 15.4 的任何形式
- 恢复 tar `run_log_bundle` 或 PR#15 移植路径

---

## 5. 用户故事（摘要）

| ID | 作为… | 我要… | 以便… |
|----|--------|--------|--------|
| US-1 | admin | 在 PlanRun 页看到归档/去重状态 | 长跑中判断是否需要介入 |
| US-2 | admin | 手动触发「过程中归档」 | 未终态也能上送关键事件 |
| US-3 | 开发 | 下载某 Job 运行日志 | 排查 init/patrol 脚本问题 |
| US-4 | 开发 | 在 15.4 拿到去重后的 xls 与对应事件目录 | 提单与二次分析 |
| US-5 | 运维 | Agent HDD 将满时自动溢出最旧事件到 15.4 | 不因单节点磁盘满而丢数 |

---

## 6. 成功标准（可测）

| ID | 标准 | 验收文档 |
|----|------|----------|
| SC-1 | AEE 路径 B 写入 Agent HDD 默认根 | [acceptance Sprint 2](../acceptance/2026-plan-c-sprint2-3.md) |
| SC-2 | 终态 Job 运行日志可通过 Agent HTTP 列出/下载 | 同上 |
| SC-3 | Agent 不再向控制面注册 `run_log_bundle` | 同上 |
| SC-4 | HDD 超阈值时最旧事件目录出现在 15.4 `devices/` | 同上 |
| SC-5 | 控制面 UI 不再依赖空的 bundle 列表误导用户 | 同上 Sprint 3 |
| SC-6 | 终态自动 scan + merge + 可选 extract（Sprint 4） | #30 / 后续 acceptance 增补 |

---

## 7. 非功能需求

- **安全**：Agent HTTP 仅服务运行日志目录，路径遍历防护；生产网络需限制 `:8900` 访问源。
- **可观测**：保留/扩展 Agent heartbeat 归档指标；PlanRun 维度的 pending/failed（#18）。
- **兼容**：`STP_WATCHER_AEE_SUBDIR_LAYOUT=correlated` 可回退旧子目录名。

---

## 8. 关联文档

| 文档 | 链接 |
|------|------|
| 技术设计 | [`design/2026-plan-c-storage-and-access.md`](../design/2026-plan-c-storage-and-access.md) |
| 验收矩阵 | [`acceptance/2026-plan-c-sprint2-3.md`](../acceptance/2026-plan-c-sprint2-3.md) |
| Sprint 2 实施计划 | [`superpowers/plans/2026-06-20-sprint2-watcher-hdd-logarchiver.md`](../superpowers/plans/2026-06-20-sprint2-watcher-hdd-logarchiver.md) |
| 文档地图 | [`DOC-MAP.md`](../DOC-MAP.md) |
