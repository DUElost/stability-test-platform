# 稳定性测试平台 — 文档中心

> **最后更新**：2026-07-15  
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
| 理解系统架构 | [`design/00-system-overview.md`](./design/00-system-overview.md) |
| 理解 Plan 执行主链路 | [`design/01-execution-pipeline.md`](./design/01-execution-pipeline.md) |
| 查执行协议硬契约 | [`design/07-execution-protocol.md`](./design/07-execution-protocol.md) |
| 查后端 / 前端 / Agent | [`design/02`](./design/02-backend.md) · [`03`](./design/03-frontend.md) · [`04`](./design/04-agent.md) |
| 查数据模型 · 实时与后台 | [`design/05`](./design/05-data-model.md) · [`06`](./design/06-realtime-and-background.md) |
| 查 Agent 版本门禁与热更新 | [`operations/agent-version-and-hot-update.md`](./operations/agent-version-and-hot-update.md) |
| 查产品范围 | [`prd/00-platform-overview.md`](./prd/00-platform-overview.md) |
| 查架构决策 | [`adr/README.md`](./adr/README.md) |
| 查上线清单 | [`operations/README.md`](./operations/README.md) |
| 查运维百科（端点表、FAQ） | 根目录 [`CLAUDE.md`](../CLAUDE.md) |
| 查哪些旧文档可删除 | [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md) |

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
├── archive/                  ← 历史文档
└── …
```

---

## 与根目录文档的关系

| 文件 | 角色 |
|------|------|
| [`README.md`](../README.md) | 仓库首页：架构摘要、快速启动、文档指针 |
| [`AGENTS.md`](../AGENTS.md) | AI/开发者命令速查 + 生产机测试约束摘要 |
| [`CLAUDE.md`](../CLAUDE.md) | 项目百科 + Changelog；**细节设计以 `docs/design/` 为准** |
| [`backend/agent/DEPLOY.md`](../backend/agent/DEPLOY.md) | Agent 安装与热更新（运维实操） |

---

## 维护约定

1. **新功能**：PRD（或 Epic Issue）→ ADR（若有）→ `design/` → 测试 + `acceptance/`  
2. **小改动**：更新相关 `design/` / `development/` 节 + 测试；根 README 仅更新摘要表  
3. **协议 / 状态机变更**：必更新 [`design/07-execution-protocol.md`](./design/07-execution-protocol.md)  
4. **一次性计划**：完工后移 `docs/archive/`，记入 `DOC-RETIREMENT.md`  
5. **禁止**在 `docs/archive/` 上继续堆新规范（见 [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)）
