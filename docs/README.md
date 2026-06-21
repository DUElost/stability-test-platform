# 稳定性测试平台 — 文档中心

> **最后更新**：2026-06-21  
> 本目录为项目**权威文档**入口。冲突时以**代码与测试**为准，并回写此处。

---

## 快速导航

| 我想… | 去看 |
|--------|------|
| 了解文档分层与权威来源 | [`DOC-MAP.md`](./DOC-MAP.md) |
| 查哪些旧文档可删除 | [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md) |
| 跑起开发环境 | [`development/local-development.md`](./development/local-development.md) |
| 理解系统架构 | [`design/00-system-overview.md`](./design/00-system-overview.md) |
| 理解 Plan 执行主链路 | [`design/01-execution-pipeline.md`](./design/01-execution-pipeline.md) |
| 查后端模块 / API | [`design/02-backend.md`](./design/02-backend.md) |
| 查前端页面 / API Client | [`design/03-frontend.md`](./design/03-frontend.md) |
| 查 Agent / Watcher | [`design/04-agent.md`](./design/04-agent.md) |
| 查数据模型 | [`design/05-data-model.md`](./design/05-data-model.md) |
| 查实时推送与后台任务 | [`design/06-realtime-and-background.md`](./design/06-realtime-and-background.md) |
| 查产品范围（平台级） | [`prd/00-platform-overview.md`](./prd/00-platform-overview.md) |
| 查架构决策 | [`adr/README.md`](./adr/README.md) |
| 查测试怎么跑 | [`development/testing.md`](./development/testing.md) |
| 查上线清单 | [`operations/README.md`](./operations/README.md) |
| 查运维百科（端点表、FAQ） | 根目录 [`CLAUDE.md`](../CLAUDE.md) |

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
├── development/              ← 本地开发、测试
├── operations/               ← 部署、联调、运维索引
├── archive/                  ← 历史文档（assessments / sprints / migrations / plans）
├── superpowers/              ← 已归档占位（见 superpowers/README.md）
├── stp-spec/                 ← 文档入口指针（不新增 spec）
├── prototypes/               ← UI 原型 HTML（非规范）
└── grafana/                  ← Dashboard JSON
```

---

## 与根目录文档的关系

| 文件 | 角色 |
|------|------|
| [`README.md`](../README.md) | 仓库首页：快速启动、env 摘要 |
| [`AGENTS.md`](../AGENTS.md) | AI/开发者命令速查 |
| [`CLAUDE.md`](../CLAUDE.md) | 项目百科 + Changelog；**细节设计以 `docs/design/` 为准** |
| [`backend/agent/DEPLOY.md`](../backend/agent/DEPLOY.md) | Agent 安装与热更新（运维实操） |

---

## 维护约定

1. **新功能**：PRD（或 Epic Issue）→ ADR（若有架构决策）→ `design/` → 测试 + `acceptance/`  
2. **小改动**：更新相关 `design/` 节 + 测试；`CLAUDE.md` 仅摘要  
3. **一次性计划**：完工后移 `docs/archive/sprints/plans/`，记入 `DOC-RETIREMENT.md`  
4. **禁止**：在 `openspec/`、`docs/archive/` 上继续堆新规范（见 [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)）
