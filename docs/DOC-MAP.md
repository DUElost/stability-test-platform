# 文档地图（Documentation Map）

> **最后更新**：2026-06-21  
> **文档中心**：[`README.md`](./README.md)  
> **待删/归档清单**：[`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)

本页说明文档**分层、权威来源与阅读顺序**。冲突时以**代码与测试**为准。

---

## 阅读顺序

### 新人 onboarding

```
README.md → AGENTS.md → docs/README.md
    → design/00-system-overview.md
    → development/local-development.md
    → design/01-execution-pipeline.md（理解主链）
```

### 新功能开发

```
prd/（或 Epic Issue）→ adr/（架构决策）→ design/（技术方案）
    → 代码 + 测试 → acceptance/（验收矩阵）
```

### 发版 / 运维

```
operations/README.md → production-minimum-deployment-checklist.md
    → preprod-drill-runbook.md → acceptance/00-platform-smoke.md
```

---

## 文档分层

| 层级 | 位置 | 回答什么 |
|------|------|----------|
| **需求 PRD** | [`prd/`](./prd/) | 做什么、成功标准、非目标 |
| **架构 ADR** | [`adr/`](./adr/) | 为什么这样定 |
| **技术设计** | [`design/`](./design/) | 模块、接口、数据流（与代码对齐） |
| **验收** | [`acceptance/`](./acceptance/) | 可测通过标准 + 测试映射 |
| **开发** | [`development/`](./development/) | 本地环境、测试约定 |
| **运维** | [`operations/`](./operations/) + runbook + `DEPLOY.md` | 部署、联调、监控 |
| **百科** | [`CLAUDE.md`](../CLAUDE.md) | 端点表、数据模型详表、FAQ、Changelog |
| **Sprint 快照** | [`archive/sprints/`](./archive/sprints/) | 已归档一次性任务单 |
| **跟踪** | GitHub Issues（[#32](https://github.com/DUElost/stability-test-platform/issues/32) 等） | 进行中、审查结论 |

---

## 设计文档索引（`design/`）

| 文档 | 内容 |
|------|------|
| [`00-system-overview.md`](./design/00-system-overview.md) | 部署拓扑、分层、领域模型摘要 |
| [`01-execution-pipeline.md`](./design/01-execution-pipeline.md) | Plan→PlanRun→Job 主链路 |
| [`02-backend.md`](./design/02-backend.md) | 后端路由、服务、启动 |
| [`03-frontend.md`](./design/03-frontend.md) | 路由、API Client、核心页面 |
| [`04-agent.md`](./design/04-agent.md) | Agent、Watcher、脚本执行 |
| [`05-data-model.md`](./design/05-data-model.md) | ORM 与表关系 |
| [`06-realtime-and-background.md`](./design/06-realtime-and-background.md) | SocketIO、APScheduler、SAQ |
| [`2026-plan-c-storage-and-access.md`](./design/2026-plan-c-storage-and-access.md) | 方案 C 存储与访问 |

---

## PRD / 验收索引

| 文档 | 内容 |
|------|------|
| [`prd/00-platform-overview.md`](./prd/00-platform-overview.md) | 平台级 PRD |
| [`prd/2026-plan-c-storage-and-archive.md`](./prd/2026-plan-c-storage-and-archive.md) | 方案 C PRD |
| [`acceptance/00-platform-smoke.md`](./acceptance/00-platform-smoke.md) | 平台冒烟与 CI 映射 |
| [`acceptance/2026-plan-c-sprint2-3.md`](./acceptance/2026-plan-c-sprint2-3.md) | 方案 C 验收 |

---

## 权威 vs 归档 vs 过时

| 级别 | 位置 |
|------|------|
| **权威** | `docs/prd`、`docs/design`、`docs/acceptance`、`docs/development`、`docs/operations`、`docs/adr`（Accepted）、`CLAUDE.md`、`README.md` |
| **任务快照** | [`archive/sprints/`](./archive/sprints/) — 已归档 Sprint 计划 |
| **归档** | [`archive/`](./archive/)（含 assessments、migrations、plans、stp-spec-pre-adr0020） |
| **过时** | [`openspec/`](../openspec/) |
| **待清理** | 见 [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md) |

---

## 方案 C 快速链接（ADR-0025）

| 类型 | 文档 |
|------|------|
| ADR | [`adr/ADR-0025-phase4-architecture-alignment.md`](./adr/ADR-0025-phase4-architecture-alignment.md) |
| PRD / 设计 / 验收 | 见上表 `2026-plan-c-*` |
| 跟踪 | [GitHub #32](https://github.com/DUElost/stability-test-platform/issues/32) |
| Agent PR | [PR #31](https://github.com/DUElost/stability-test-platform/pull/31) |

---

## 变更时更新清单

| 你改了… | 至少更新… |
|---------|-----------|
| 用户可见行为 / 范围 | `prd/` + `acceptance/` |
| 架构边界 | ADR + `design/` |
| API / 路由 / 页面 | `design/02` 或 `03` + `types.ts` + 测试 |
| Agent / Watcher / 存储 | `design/04` + agent tests |
| 表结构 | Alembic + `design/05` + `CLAUDE.md` 摘要 |
| 仅实现细节 | 测试；design 可选 |
| 文档退役 | `DOC-RETIREMENT.md` |

---

## 相关链接

- [`docs/README.md`](./README.md) — 文档中心  
- [`adr/README.md`](./adr/README.md) — ADR 索引  
- [`project-vision.md`](./project-vision.md) — 愿景  
- [`AGENTS.md`](../AGENTS.md) — 开发命令
