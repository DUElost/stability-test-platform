# 稳定性测试平台 — 文档中心

> **最后更新**：2026-09-05
> 本目录为项目**权威文档**入口。冲突时以**代码与测试**为准，并回写此处。  
> 根目录 [`README.md`](../README.md) 只保留产品概述与快速入口；细则在本树子文档。

---

## 快速导航

| 我想… | 去看 |
|--------|------|
| 了解文档分层与权威来源 | [`DOC-MAP.md`](./DOC-MAP.md) |
| 跑起开发环境 | [`development/local-development.md`](./development/local-development.md) |
| 查环境变量 | [`development/environment-variables.md`](./development/environment-variables.md) |
| 查测试怎么跑 / 生产机禁区 | [`development/testing.md`](./development/testing.md) |
| 查依赖、lock 与本地门禁 | [`development/dependencies-and-quality.md`](./development/dependencies-and-quality.md) |
| 查 PR、CI、Agent Note 与并行 worktree | [`development/repository-workflow.md`](./development/repository-workflow.md) |
| 理解系统架构 | [`design/00-system-overview.md`](./design/00-system-overview.md) |
| 查存储角色 / CIFS / NFS / 文件服务器页别称 | [`design/2026-storage-roles-and-aliases.md`](./design/2026-storage-roles-and-aliases.md) |
| 查设备日志上送时序（ADR-0025） | [`design/2026-adr-0025-log-flow-sequence.md`](./design/2026-adr-0025-log-flow-sequence.md) |
| 理解 Plan 执行主链路 | [`design/01-execution-pipeline.md`](./design/01-execution-pipeline.md) |
| 查执行协议硬契约 | [`design/07-execution-protocol.md`](./design/07-execution-protocol.md) |
| 查后端 / 前端 / Agent | [`design/02`](./design/02-backend.md) · [`03`](./design/03-frontend.md) · [`04`](./design/04-agent.md) |
| 查数据模型 · 实时与后台 | [`design/05`](./design/05-data-model.md) · [`06`](./design/06-realtime-and-background.md) |
| 查 Agent 版本门禁与热更新 | [`operations/agent-version-and-hot-update.md`](./operations/agent-version-and-hot-update.md) |
| 查生产控制面只读诊断边界 | [`operations/production-diagnostics.md`](./operations/production-diagnostics.md) |
| 查新建专项 / 适配新项目怎么做 | [`operations/new-specialty-onboarding-runbook.md`](./operations/new-specialty-onboarding-runbook.md) |
| 查产品范围 | [`prd/00-platform-overview.md`](./prd/00-platform-overview.md) |
| 查架构决策 | [`adr/README.md`](./adr/README.md) |
| 查上线清单 | [`operations/README.md`](./operations/README.md) |
| 查跨模块硬不变量 | 根目录 [`AGENTS.md`](../AGENTS.md) |
| 查 AI Harness 规则入口与本地配置边界 | [`development/ai/harness-adapters.md`](./development/ai/harness-adapters.md) |
| 查哪些旧文档可删除 | [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md) |
| 查设备日志流转审查 / DoD | [`reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](./reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md) |

---

## 目录结构

```
docs/
├── README.md                 ← 本页
├── DOC-MAP.md                ← 文档分层与阅读顺序
├── DOC-RETIREMENT.md         ← 待归档/删除清单
├── adr/                      ← 架构决策（ADR）
├── prd/                      ← 产品需求
├── design/                   ← 技术设计（与代码对齐）
├── acceptance/               ← 验收矩阵
├── development/              ← 本地开发、测试、env
├── operations/               ← 部署、联调、运维索引
├── reviews/                  ← Living 审查（缺陷/DoD，不替代 design/）
├── archive/                  ← 历史文档
└── …
```

---

## 与根目录文档的关系

| 文件 | 角色 |
|------|------|
| [`README.md`](../README.md) | 仓库首页：架构摘要、快速启动、文档指针 |
| [`AGENTS.md`](../AGENTS.md) | 最小启动契约：总原则、跨模块硬不变量、安全红线和按需入口 |
| [`CLAUDE.md`](../CLAUDE.md) | Claude 导入与路由；**状态机与领域细节按需读取** |
| [`backend/agent/DEPLOY.md`](../backend/agent/DEPLOY.md) | Agent 安装与热更新（运维实操） |

---

## 维护约定

1. **新功能**：PRD（或 Epic Issue）→ ADR（若有）→ `design/` → 测试 + `acceptance/`  
2. **小改动**：更新相关 `design/` / `development/` 节 + 测试；根 README 仅更新摘要表  
3. **协议 / 状态机变更**：必更新 [`design/07-execution-protocol.md`](./design/07-execution-protocol.md)  
4. **一次性计划**：完工后移 `docs/archive/`，记入 `DOC-RETIREMENT.md`  
5. **禁止**在 `docs/archive/` 上继续堆新规范（见 [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)）

---

## 详细索引（自 DOC-MAP 迁入，2026-08-27）

> DOC-MAP 常驻层只保留阅读顺序 / 分层 / Living 审查登记簿；下列描述表是查阅型内容，住在这里按需翻。

## 设计文档索引（`design/`）

| 文档 | 内容 |
|------|------|
| [`00-system-overview.md`](./design/00-system-overview.md) | 部署拓扑、分层、领域模型摘要 |
| [`01-execution-pipeline.md`](./design/01-execution-pipeline.md) | Plan→PlanRun→Job 主链路 |
| [`07-execution-protocol.md`](./design/07-execution-protocol.md) | 状态机、abort ACK、snapshot、claim、schema |
| [`02-backend.md`](./design/02-backend.md) | 后端路由、服务、启动 |
| [`03-frontend.md`](./design/03-frontend.md) | 路由、API Client、核心页面 |
| [`04-agent.md`](./design/04-agent.md) | Agent、Watcher、脚本执行 |
| [`05-data-model.md`](./design/05-data-model.md) | ORM 与表关系 |
| [`06-realtime-and-background.md`](./design/06-realtime-and-background.md) | SocketIO、APScheduler、SAQ |
| [`2026-plan-c-storage-and-access.md`](./design/2026-plan-c-storage-and-access.md) | 方案 C 存储与访问 |
| [`2026-adr-0025-log-flow-sequence.md`](./design/2026-adr-0025-log-flow-sequence.md) | 设备日志流转时序（上送规则=ADR-0025；含给人读 / 给其他 Agent 的两版图） |
| [`2026-scan-upload-merge-contract.md`](./design/2026-scan-upload-merge-contract.md) | 控制面与 Agent 的 scan/upload/merge 跨进程契约 |
| [`2026-08-step-stall-detection.md`](./design/2026-08-step-stall-detection.md) | Pipeline 总超时、停滞钟与 PROGRESS 打戳契约 |
| [`2026-storage-roles-and-aliases.md`](./design/2026-storage-roles-and-aliases.md) | 存储/部署角色与别称（CIFS/NFS=中心存储；文件服务器页≠中心存储） |
| [`2026-device-log-event-implementation-spec.md`](./design/2026-device-log-event-implementation-spec.md) | DeviceLogEvent 阶段 3 实现规格（ADR-0028 D1–D8） |
| [`2026-07-plan-execute-page-improvements.md`](./design/2026-07-plan-execute-page-improvements.md) | Plan 执行页：Phase1–6 + §7 已落地；**§8 V2 选机工作台/驾驶舱实现方案** |
| [`2026-08-mtbf-p0-runner-design.md`](./design/2026-08-mtbf-p0-runner-design.md) | MTBF 专项 P0 设计：脚本三件套契约 + realresult schema 实测 + 配置/产物通道（ADR-0030 D6 P0，**已验收**） |
| [`2026-08-mtbf-p1-suite-management.md`](./design/2026-08-mtbf-p1-suite-management.md) | MTBF 专项 P1 设计：test_suite/test_case 实体 + 外部管理面 + D2/D3b 派发门禁（ADR-0030 D6 P1，P1a/P1b 已实施） |
| [`2026-08-project-registry-p25-mapping-workbench.md`](./design/2026-08-project-registry-p25-mapping-workbench.md) | ADR-0029 P2.5：登记簿 = Fleet 事实 + 人工 USER 项目 + `match_models` 精确映射 |
| [`2026-08-honor-flash-firmware-routing.md`](./design/2026-08-honor-flash-firmware-routing.md) | Honor 刷机：固件指纹路由 + NFS manifest 布局 + 刷前/刷后版本核验 + v1.3.0 多设备门控/重试环/环境预检（方向 A，已实施） |
| [`2026-08-governance-surface-protection.md`](./design/2026-08-governance-surface-protection.md) | 治理面防护两层方案：L0 结构门禁 S1–S5 + 本地护栏（pre-commit/settings deny/skill 试点）+ backstop 机械摘要 + L1 按需 evals（synthesis C-G1 落地，含校准教训与重议条件） |
| [`2026-09-external-tools-integration-and-package-architecture.md`](./design/2026-09-external-tools-integration-and-package-architecture.md) | 外部工具统一接入实施计划（ADR-0033 配套：Tool Contract 协议与 §2.5 双轨衔接、manifest 注册流与门禁分工、NFS 工具包布局与 Agent 缓存、去重/专项/Jira 适配器、三阶段排期） |
| [`design/mockups/plan-execute-v2/`](./design/mockups/plan-execute-v2/) | Plan Execute V2 静态预览（§8 视觉基准） |

## 开发 / 运维索引

| 文档 | 内容 |
|------|------|
| [`development/environment-variables.md`](./development/environment-variables.md) | env 详表（含超时与版本门禁） |
| [`development/testing.md`](./development/testing.md) | pytest / vitest / 生产机禁区 |
| [`development/dependencies-and-quality.md`](./development/dependencies-and-quality.md) | 后端依赖分工、lock 更新、lint 与本地门禁 |
| [`development/repository-workflow.md`](./development/repository-workflow.md) | Agent Note、并行 worktree、PR/CI 与 FIFO auto-merge |
| [`development/script-versioning.md`](./development/script-versioning.md) | Agent 脚本版本不可变、参数分层与退役 |
| [`operations/agent-version-and-hot-update.md`](./operations/agent-version-and-hot-update.md) | 滚动升级与 code revision |
| [`operations/production-diagnostics.md`](./operations/production-diagnostics.md) | 生产控制面只读诊断、凭据来源与安全边界 |
| [`operations/device-lease-emergency-release.md`](./operations/device-lease-emergency-release.md) | 设备 ACTIVE 租约紧急释放与回查 |
| [`operations/adr-0028-prune-local-and-spill-gray.md`](./operations/adr-0028-prune-local-and-spill-gray.md) | #217 PRUNE_LOCAL / HddSpill 单机灰度 |
| [`operations/mtbf-api.md`](./operations/mtbf-api.md) | MTBF 用例管理接口说明（§1 P0 validate / §1.5 脚本配置通道与 env 退役 / §2 P1 管理面；ADR-0030） |
| [`operations/README.md`](./operations/README.md) | 运维索引 |
| [`production-minimum-deployment-checklist.md`](./production-minimum-deployment-checklist.md) | 生产最小部署 |
| [`development/cursor-rules.md`](./development/cursor-rules.md) | Cursor 规则说明：`.cursor/rules/*.mdc` 分层与格式（薄适配层） |
| [`development/ai/harness-adapters.md`](./development/ai/harness-adapters.md) | Cursor、Claude Code、Codex、OpenCode 等 Harness 的规则入口与本地配置边界 |

## PRD / 验收索引

| 文档 | 内容 |
|------|------|
| [`prd/00-platform-overview.md`](./prd/00-platform-overview.md) | 平台级 PRD |
| [`prd/2026-plan-c-storage-and-archive.md`](./prd/2026-plan-c-storage-and-archive.md) | 方案 C PRD |
| [`acceptance/00-platform-smoke.md`](./acceptance/00-platform-smoke.md) | 平台冒烟与 CI 映射 |
| [`acceptance/2026-plan-c-sprint2-3.md`](./acceptance/2026-plan-c-sprint2-3.md) | 方案 C Sprint 2/3 验收 |
| [`acceptance/2026-plan-c-sprint4.md`](./acceptance/2026-plan-c-sprint4.md) | 方案 C Sprint 4 自动化验收矩阵 |
| [`acceptance/2026-plan-c-sprint4-real-device.md`](./acceptance/2026-plan-c-sprint4-real-device.md) | 方案 C Sprint 4 真机联调记录 |
| [`acceptance/2026-08-adr-0028-phase3-mtk-signoff.md`](./acceptance/2026-08-adr-0028-phase3-mtk-signoff.md) | ADR-0028 阶段 3 MTK DLE + EventUploader 签字 |
