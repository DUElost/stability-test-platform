# Contributing

本文是贡献入口与红线清单；详细命令、架构与运维约束以仓库既有文档为准。

## 先读什么

| 入口 | 用途 |
|------|------|
| [`README.md`](./README.md) | 产品概述、快速启动 |
| [`docs/README.md`](./docs/README.md) | 文档中心与维护约定 |
| [`AGENTS.md`](./AGENTS.md) | 开发命令、测试约束、PR/CI 机制 |
| [`CLAUDE.md`](./CLAUDE.md) | 架构不变量与关键约定 |

## 开发环境

- 本地开发：`docs/development/local-development.md`
- 测试：`docs/development/testing.md`
- Agent 联调与热更新：`docs/operations/agent-version-and-hot-update.md`

常用命令（详见上述文档）：

```bash
# Agent 测试（不连 DB/Redis，推荐先跑）
python -m pytest backend/agent/tests/ -q

# 前端
cd frontend
npm ci
npm run type-check
npm run test -- run
npm run build

# lint（CI 已阻塞，--max-warnings 0）
ruff check backend/ tools/ scripts/
npm run lint -- --max-warnings 0
```

## 改动流程

1. 较大改动先开 Issue / 设计文档（新功能建议按 `PRD/Epic → ADR → design/ → 测试 + acceptance/` 顺序）。
2. 从最新 `main` 拉分支：

   ```bash
   git switch -c fix/xxx origin/main
   # 或 feat/xxx、docs/xxx、chore/xxx
   ```

3. 小步提交；commit message 参考仓库历史风格（`type(scope): 摘要`）。
4. 开 PR 到 `main`，**不要直接 push main**。
5. Auto-merge 已开启：同仓库非 draft PR 会自动挂 auto-merge；**不要手动点 Merge**。

## 合入门禁

- 稳定 required checks：`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、`pr-agent-tests`。
- CodeRabbit 是 **best-effort 参考**，不是硬门禁：仅当它对**当前 head** 有明确 APPROVED / CHANGES_REQUESTED 时生效；skipped / rate limited / 无决策时不阻断合入。
- 需要 CodeRabbit 对当前 head 复评时，在 PR 评论 `@coderabbitai review`；不触发也不会卡合入。
- PR 合入前不跑 PG 全量 / vitest / docker 全量；由 main 后置全量兜底。需要“合并前全量校验”时应引入 Merge Queue。

## 测试与 lint 纪律

- Agent 测试自包含、优先跑；控制面 PG 套件走 testcontainers / CI。
- ruff 与 ESLint 均已清零并阻塞，新增代码不得引入新告警。
- 空行注入污染检查是阻塞门禁；提交前用 `python tools/dev/collapse-blank-pollution.py --check <file>` 自查。
- 改动 `requirements.txt` 后必须重新生成 `requirements.lock`（Python 3.11 下）。

## 生产安全红线

- 仓库根 `.env.backend` 是生产唯一 env 源；**不提交、不打日志、不贴进 PR**。
- 不在生产机上把 `TEST_DATABASE_URL` 指到生产库或 `stp_dev`；后端 PG 测试用 Docker testcontainers。
- Alembic 迁移试验不在生产库执行，在容器 / CI / 开发机验证。
- `backend/.env`、`backend/agent/.env`、`/home/debian13/hosts.ini`、`.env.backend`、`opencode.json` 均在 `.gitignore`，不得强制添加。

## 文档要求

- 新功能：PRD/Epic → ADR（若有）→ `docs/design/` → 测试 + `docs/acceptance/`。
- 协议 / 状态机变更：必更新 `docs/design/07-execution-protocol.md`。
- 小改动：同步更新相关 `design/` / `development/` 文档。
- 一次性计划完成后移入 `docs/archive/` 并记入 `docs/DOC-RETIREMENT.md`；禁止在 archive 继续堆新规范。

## 还有问题

先查 `docs/DOC-MAP.md`；找不到答案再在对应 Issue / PR 里提问。
