# 文档地图（Documentation Map）

> **最后更新**：2026-08-12  
> **文档中心**：[`README.md`](./README.md)  
> **待删/归档清单**：[`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)

本页说明文档**分层、权威来源与阅读顺序**。冲突时以**代码与测试**为准。  
根目录 [`../README.md`](../README.md) 保持精简；环境变量、测试禁区、执行协议细则在子文档。

---

## 阅读顺序

### 新人 onboarding

```
../README.md → ../AGENTS.md → docs/README.md
    → design/00-system-overview.md
    → development/local-development.md
    → design/01-execution-pipeline.md
    → design/07-execution-protocol.md（状态机 / abort / claim）
```

### 新功能开发

```
prd/（或 Epic Issue）→ adr/ → design/
    → 代码 + 测试（见 development/testing.md）→ acceptance/
```

### 发版 / 运维

```
operations/README.md → production-minimum-deployment-checklist.md
    → operations/agent-version-and-hot-update.md（先升 Agent 再开版本门禁）
    → preprod-drill-runbook.md → acceptance/00-platform-smoke.md
```

---

## 文档分层

| 层级 | 位置 | 回答什么 |
|------|------|----------|
| **仓库首页** | [`../README.md`](../README.md) | 是什么、怎么跑起来、文档指针 |
| **需求 PRD** | [`prd/`](./prd/) | 做什么、成功标准、非目标 |
| **架构 ADR** | [`adr/`](./adr/) | 为什么这样定 |
| **技术设计** | [`design/`](./design/) | 模块、接口、数据流、**执行协议** |
| **验收** | [`acceptance/`](./acceptance/) | 可测通过标准 + 测试映射 |
| **开发** | [`development/`](./development/) | 本地环境、**env 表**、测试约定 |
| **运维** | [`operations/`](./operations/) + runbook | 部署、Agent 版本、联调、监控 |
| **不变量摘要** | [`../CLAUDE.md`](../CLAUDE.md) | 架构不变量、关键约定、状态机摘要 |
| **Living 审查** | [`reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](./reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md) | 设备日志流转框架 + 缺陷/DoD/落地顺序（v3.0，阶段 0 ✅）；前一版快照 [`reviews/PROJECT_REVIEW_2026-08-09_previous.md`](./reviews/PROJECT_REVIEW_2026-08-09_previous.md) |
| **Living 审查** | [`reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](./reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md) | 多项目并存需求 + 生产数据基准 + 缺口核对 G1–G14 + 落地顺序（ADR-0029 背景分析） |
| **Living 审查** | [`reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](./reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) | MTBF 多用例平台化研究（runtask.xml 实测 + 平台缺口 G1–G5 + 候选形态 A/B/C + 设计草图；[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md) 背景分析，Proposed） |
| **Sprint 快照** | [`archive/sprints/`](./archive/sprints/) | 已归档一次性任务单 |
| **跟踪** | GitHub Issues | 进行中、审查结论 |

---

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
| [`2026-storage-roles-and-aliases.md`](./design/2026-storage-roles-and-aliases.md) | 存储/部署角色与别称（CIFS/NFS=中心存储；文件服务器页≠中心存储） |
| [`2026-device-log-event-implementation-spec.md`](./design/2026-device-log-event-implementation-spec.md) | DeviceLogEvent 阶段 3 实现规格（ADR-0028 D1–D8） |
| [`2026-07-plan-execute-page-improvements.md`](./design/2026-07-plan-execute-page-improvements.md) | Plan 执行页：Phase1–6 + §7 已落地；**§8 V2 选机工作台/驾驶舱实现方案** |
| [`2026-08-mtbf-p0-runner-design.md`](./design/2026-08-mtbf-p0-runner-design.md) | MTBF 专项 P0 设计：脚本三件套契约 + realresult schema 实测 + 配置/产物通道（ADR-0030 D6 P0，**已验收**） |
| [`2026-08-mtbf-p1-suite-management.md`](./design/2026-08-mtbf-p1-suite-management.md) | MTBF 专项 P1 设计：test_suite/test_case 实体 + 外部管理面 + D2/D3b 派发门禁（ADR-0030 D6 P1，草案） |
| [`2026-08-project-registry-p25-mapping-workbench.md`](./design/2026-08-project-registry-p25-mapping-workbench.md) | ADR-0029 P2.5：编组工作台（Fleet 事实 + 人工映射；HONOR-MLD 等为非正式回填） |
| [`design/mockups/plan-execute-v2/`](./design/mockups/plan-execute-v2/) | Plan Execute V2 静态预览（§8 视觉基准） |

---

## 开发 / 运维索引

| 文档 | 内容 |
|------|------|
| [`development/environment-variables.md`](./development/environment-variables.md) | env 详表（含超时与版本门禁） |
| [`development/testing.md`](./development/testing.md) | pytest / vitest / 生产机禁区 |
| [`operations/agent-version-and-hot-update.md`](./operations/agent-version-and-hot-update.md) | 滚动升级与 code revision |
| [`operations/adr-0028-prune-local-and-spill-gray.md`](./operations/adr-0028-prune-local-and-spill-gray.md) | #217 PRUNE_LOCAL / HddSpill 单机灰度 |
| [`operations/mtbf-api.md`](./operations/mtbf-api.md) | MTBF 用例管理接口说明（§1 P0 validate 定稿 / §2 P1 用例管理占位；ADR-0030） |
| [`operations/README.md`](./operations/README.md) | 运维索引 |
| [`production-minimum-deployment-checklist.md`](./production-minimum-deployment-checklist.md) | 生产最小部署 |

---

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

---

## 权威 vs 归档

- **权威**：本树 `design/` · `development/` · `operations/` · `adr/` · `prd/` · `acceptance/`，及根 `AGENTS.md` / `CLAUDE.md` 摘要  
- **Living 审查**：`reviews/`（缺陷/DoD/落地顺序；不替代 `design/`）  
- **归档**：`archive/`（不新增规范）  
- **过时处理**：见 [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)
