# Contributing

本文是贡献入口；详细命令、架构与运维约束以仓库既有文档为唯一权威（不在本文件复制，
避免多份漂移）。改动流程见下，其余主题按「详细约定」表跳转。

## 先读什么

| 入口 | 用途 |
|------|------|
| [`README.md`](./README.md) | 产品概述、快速启动 |
| [`docs/README.md`](./docs/README.md) | 文档中心与维护约定 |
| [`AGENTS.md`](./AGENTS.md) | 最小启动契约、跨模块硬不变量与按需文档入口 |
| [`CLAUDE.md`](./CLAUDE.md) | Claude 导入与按需路由 |

## 改动流程

1. 较大改动先开 Issue / 设计文档（新功能按 `PRD/Epic → ADR → design/ → 测试 + acceptance/` 顺序）。
2. 从最新 `main` 拉分支：

   ```bash
   git switch -c fix/xxx origin/main
   # 或 feat/xxx、docs/xxx、chore/xxx
   ```

3. 小步提交；commit message 参考仓库历史风格（`type(scope): 摘要`）。
4. 开 PR 到 `main`，**不要直接 push main、不要手动点 Merge**（auto-merge 已开启）。
5. PR 门禁与复评规则见
   [`repository-workflow.md`](./docs/development/repository-workflow.md)。

## 详细约定（按需查阅，勿在本文件复制）

| 主题 | 权威位置 |
|------|----------|
| 测试命令 / pytest 坑 / testcontainers | [`testing.md`](./docs/development/testing.md) |
| 合入门禁 / required checks / auto-merge | [`repository-workflow.md`](./docs/development/repository-workflow.md) |
| 生产安全红线（env 源、测试库禁区） | [`testing.md`](./docs/development/testing.md)、[`production-diagnostics.md`](./docs/operations/production-diagnostics.md) |
| 文档要求（ADR / design / acceptance / archive） | [`docs/DOC-MAP.md`](./docs/DOC-MAP.md)、[`docs/README.md`](./docs/README.md) |

## 还有问题

先查 [`docs/DOC-MAP.md`](./docs/DOC-MAP.md)；找不到答案再在对应 Issue / PR 里提问。
