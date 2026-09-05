# AGENTS.md

本文件是所有 AI Coding Harness 的最小启动契约。只保留对任何 Requirement 都成立的
原则；命令、实现和运维细节必须按任务从链接文档读取。

## 总原则

- 先检查代码、测试和当前文档再修改；冲突时以代码与测试为准，并同步权威文档。
- 只改当前 Requirement 必需内容，不顺手重构，不把目录分片当作所有权。
- 不读取、打印、提交或复制当前任务不需要的凭据、token、私钥、连接串与主机清单。
- 本机可能同时是生产控制面和生产数据库宿主；测试必须使用隔离环境，禁止在生产库
  试跑测试、迁移或破坏性诊断。
- 已发布 `backend/agent/scripts/<name>/v<version>/` 不可原地修改或删除；新行为使用
  新版本。
- Python 工具和测试使用当前解释器的 `python -m ...` 形式，避免命中另一套环境。
- 非平凡变更必须附 Agent Note；方向级决策使用 ADR。
- `main` 只通过 PR 合入；不要直推或手动 Merge，现有 FIFO auto-merge 负责串行集成。

## 硬不变量

- ASGI 入口是 `socketio.ASGIApp(sio_server, fastapi_app)`；不要拆成相互覆盖的挂载。
- Pipeline 顶层只接受 `lifecycle`，action 唯一格式是 `script:<name>`。
- Plan 不存 lifecycle；dispatcher 从 PlanStep 与 Plan 时间字段组装
  `pipeline_def.lifecycle`。
- Redis 只承载队列与瞬时跨进程通信，不作为业务事实存储。
- 生产环境必须满足 secure cookie、受限 SameSite 和 CSRF guard。
- Pydantic 只使用 v2 API；数据库业务表名使用单数。
- 已存在脚本版本的 `default_params` 不可原地修改；参数变化通过新版本表达。
- 前端 API 类型以 `frontend/src/utils/api/types.ts` 为入口，并与后端 schema 同步。

## 开始任务时

1. 从 [`docs/DOC-MAP.md`](docs/DOC-MAP.md) 和下表定位当前 Requirement 的权威文档；
2. 检查目标代码、测试和相邻目录内的 scoped `CLAUDE.md`；
3. 查看其他 worktree 的实际 diff，避免同时修改同一批文件；
4. 共享元文件（本文件、`CLAUDE.md`、Harness rules）同一时间只由一个 Execution 修改。

当前并行约定见
[`repository-workflow.md`](docs/development/repository-workflow.md)；改变现行执行语义前
必须先由 ADR 正式裁决。

## 按需入口

| 任务 | 权威入口 |
|---|---|
| 启动、环境、迁移 | [`local-development.md`](docs/development/local-development.md) |
| 测试与生产数据库边界 | [`testing.md`](docs/development/testing.md) |
| 依赖、lock、lint、门禁 | [`dependencies-and-quality.md`](docs/development/dependencies-and-quality.md) |
| PR、CI、Agent Note、并行 worktree | [`repository-workflow.md`](docs/development/repository-workflow.md) |
| 架构、状态机、模块设计 | [`docs/DOC-MAP.md`](docs/DOC-MAP.md) |
| 脚本版本、参数与退役 | [`script-versioning.md`](docs/development/script-versioning.md) |
| scan/upload/merge | [`2026-scan-upload-merge-contract.md`](docs/design/2026-scan-upload-merge-contract.md) |
| 生产只读诊断 | [`production-diagnostics.md`](docs/operations/production-diagnostics.md) |
| Harness 适配与本地配置 | [`harness-adapters.md`](docs/development/ai/harness-adapters.md) |

## 提交前

- 运行与改动范围匹配的测试，再运行 `python scripts/run_gates.py check:quick`；
- 只报告实际运行过的命令与结果；未完成的检查标为 pending，命令成功不等于验证通过；
- 检查 diff 不含凭据、无关格式化或本地 Harness 状态；
- Agent Note 使用 Decision、Alternatives、Verification、Revisit 四节；
- required checks 为 `lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、
  `pr-agent-tests`、`pr-migrate-empty-db`。
