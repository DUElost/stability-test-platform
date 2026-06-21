# PRD：稳定性测试管理平台（平台级）

- **状态**：Living  
- **版本**：1.0  
- **日期**：2026-06-21  
- **愿景详述**：[`project-vision.md`](../project-vision.md)

---

## 1. 产品陈述

为 Android 稳定性专项提供 **Plan 编排 → 多机多设备执行 → 实时监控 → 崩溃采集 → 去重报告 → JIRA 提单** 的一体化平台，支撑 **数小时至数天** 长跑无人值守。

---

## 2. 目标用户

| 角色 | 核心任务 |
|------|----------|
| 测试运维 | 创建/调度 Plan、监控 PlanRun、处理失败设备 |
| 开发 | 查看崩溃证据、运行日志、去重报告 |
| 平台研发 | 扩展脚本、维护 Agent、对接 NFS/ADB |

---

## 3. 核心能力（当前已实现）

| 能力 | 说明 | 设计参考 |
|------|------|----------|
| Plan 编排 | init/patrol/teardown + Plan 链 | ADR-0020、`design/01-execution-pipeline.md` |
| 派发门禁 | 脚本 sha 对齐、主机可达 | ADR-0021 |
| 多机执行 | Agent + ADB + 设备租约 | ADR-0019 |
| 实时监控 | SocketIO + PlanRun 聚合 API | ADR-0021 C5 |
| Watcher | AEE/信号检测与拉取 | ADR-0018 |
| 脚本目录 | scan 入库、版本化参数 | ADR-0020、ADR-0023 |
| 去重/JIRA | scan/merge/extract + jira runs | ADR-0025 |
| 安全 | Cookie/CSRF、审计、角色 | ADR-0024、ADR-0015 |
| 定时调度 | Cron 触发 Plan | schedules API |

---

## 4. 核心用户旅程

### 4.1 创建并执行专项

1. 脚本 scan 入库 → 创建 Plan（PlanStep 引用 script:version）  
2. 选择设备/主机 → 执行 → 派发门禁通过  
3. PlanRun 详情页：门禁、时间线、设备矩阵、Watcher 异常  
4. 终态：报告、去重、可选 JIRA

### 4.2 长跑无人值守

1. Patrol 周期执行 monkey_check  
2. Watcher 检测崩溃 → log_signal + HDD 拉取  
3. Agent 重启后 RESUME 恢复 Watcher（ADR-0025 D5）  
4. 过程中/终态归档与去重（方案 C 五触发，Sprint 4）

### 4.3 运维

1. 主机管理、热更新、设备状态  
2. 审计日志、通知规则（admin）  
3. 生产部署 checklist + preprod drill

---

## 5. 非目标（平台级）

- 多控制平面水平扩展（ADR-0025 D1 推迟）  
- Loki 式集中日志检索（D2 不引入）  
- 内置 Monkey 以外的通用 CI/CD  
- 移动端 App

---

## 6. 成功标准（平台级）

| ID | 标准 |
|----|------|
| PS-1 | 单 PlanRun 可从创建至终态完成，设备锁释放 |
| PS-2 | CI：backend pytest + frontend tsc/build 通过 |
| PS-3 | 主链集成测试 `test_main_chain_happy_path` 通过 |
| PS-4 | 生产 checklist 冒烟项可执行（见 acceptance） |
| PS-5 | 真机专项 ≥1 次端到端签字（运维流程，非 CI 强制） |

验收详表：[`acceptance/00-platform-smoke.md`](../acceptance/00-platform-smoke.md)

---

## 7. 子域 PRD

| 主题 | 文档 |
|------|------|
| 方案 C 存储与归档 | [`2026-plan-c-storage-and-archive.md`](./2026-plan-c-storage-and-archive.md) |
| 后续 Epic | [GitHub #32](https://github.com/DUElost/stability-test-platform/issues/32) |

---

## 8. 关联文档

- 系统总览：[`design/00-system-overview.md`](../design/00-system-overview.md)  
- ADR 索引：[`adr/README.md`](../adr/README.md)
