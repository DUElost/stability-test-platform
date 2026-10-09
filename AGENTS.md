# AGENTS.md

本文件是所有 AI Coding Harness 的最小启动契约。只保留对任何 Requirement 都成立的
原则；命令、实现和运维细节必须按任务从链接文档读取。

## 总原则

- 先检查代码、测试和当前文档再修改；冲突时以代码与测试为准，并同步权威文档。
- 要不要做、有没有必要做：站在问题本质判断（本质问题 / 最小方案），不因惯性或先例而动；
  临时止血必须标注为过渡并写明终态出口——不标注的止血会沉淀为技术债与代码腐化。
- 只改当前 Requirement 必需内容，不顺手重构，不把目录分片当作所有权。
- 不读取、打印、提交或复制当前任务不需要的凭据、token、私钥、连接串与主机清单。
- 本机可能同时是生产控制面和生产数据库宿主；测试必须使用隔离环境，禁止在生产库
  试跑测试、迁移或破坏性诊断。
- 共享工作树里的未提交改动可能属于别的会话：不用 `git reset --hard`、`git stash`
  清理现场，未提交工作显式 commit 到分支（禁止集合见 `repository-workflow.md`「Git 破坏性操作纪律」）。
- 已发布的发布单元不可原地修改（ADR-0051）：`tool_manifest.json` 条目与站点 `packages/`
  只增不改；`backend/agent/scripts/<name>/` 是可演进的族源码树，改了树必须登记新版本；
  删除按 ADR-0051 D5。
- 工具与测试使用[项目 Python 入口](docs/development/local-development.md#项目-python-入口)；模块用 `-m`，pytest 经内存保护 runner。
- 非平凡变更必须附 Agent Note；方向级决策使用 ADR。
- `main` 只通过 PR 合入；不要直推或手动 Merge，现有 FIFO auto-merge 负责串行集成；
  也不得自行启用/维持 auto-merge（`gh pr merge --auto`、GraphQL 同义调用，含
  `--squash`）或替他人的 PR 做 update-branch / nudge——只允许队首持有 auto-merge。

## 硬不变量

- ASGI 入口是 `socketio.ASGIApp(sio_server, fastapi_app)`；不要拆成相互覆盖的挂载。
- Pipeline 顶层只接受 `lifecycle`，action 唯一格式是 `script:<name>`。
- Plan 不存 lifecycle；dispatcher 从 PlanStep 与 Plan 时间字段组装
  `pipeline_def.lifecycle`。
- Redis 只承载队列与瞬时跨进程通信，不作为业务事实存储。
- 生产类环境（production 与 internal）必须满足 secure cookie、受限 SameSite 和 CSRF guard；唯一例外是 internal 无 TLS 内网部署豁免 Secure 启动强制（ADR-0024 v1.1，#46 TLS 落地后收窄）。
- Pydantic 只使用 v2 API；新建数据库业务表名使用单数（历史复数表例外见 `docs/design/05-data-model.md`）。
- 已存在脚本版本的 `default_params` 不可原地修改；参数变化通过新版本表达。
- 前端 API 类型以 `frontend/src/utils/api/types.ts` 为入口，并与后端 schema 同步。

## 开始任务时

1. 从 [`docs/DOC-MAP.md`](docs/DOC-MAP.md) 和下表定位当前 Requirement 的权威文档；
2. 检查目标代码、测试和相邻目录内的 scoped `AGENTS.md`（Claude 侧为其 `CLAUDE.md` symlink 薄壳）；
3. 分流：默认按单点 Requirement 实施；已属 ADR-0058 批次，或明显命中其 D7 强判据
   （同形态跨多 issue/族、需协调激活、数据丢失/安全/难回退），不按普通单点领单——先读
   「批次交付流程」与载体 issue 的**当前方案**（不得以派单提示词转述替代）；实施者不重新设计，
   需复核单元保持 draft，ready 与激活遵守 D8–D9，合入不等于生效；
4. 并行前检与领单：实施者先查开放 PR；协调域内再用项目入口运行 `tools/dev/ai_work.py status --risk`
   查在窗 Execution 后 `declare`，编码结束 `finish` / `update --pr`；实际 diff 作 ground truth 交叉验证。
   协调域外实施者不 declare，按 [执行契约](docs/development/ai/execution-contract.md) §3.6
   做 PR 可见性与冲突前检；规划 / 复核工作面改仓库文件前同样查开放 PR（ADR-0058 D10）；
5. 共享元文件（本文件、`CLAUDE.md`、Harness rules）同一时间只由一个工作面修改（协调域内经 Registry、协调域外查开放 PR 串行）；改变现行执行语义前必须先由 ADR 正式裁决。

## 按需入口

| 任务 | 权威入口 |
|---|---|
| 启动、环境、迁移 | [`local-development.md`](docs/development/local-development.md) |
| 测试与生产数据库边界 | [`testing.md`](docs/development/testing.md) |
| 依赖、lock、lint、门禁 | [`dependencies-and-quality.md`](docs/development/dependencies-and-quality.md) |
| PR、CI、Agent Note、并行 worktree | [`repository-workflow.md`](docs/development/repository-workflow.md) |
| 脚本版本、参数与退役 | [`script-versioning.md`](docs/development/script-versioning.md) |
| scan/upload/merge | [`2026-scan-upload-merge-contract.md`](docs/design/2026-scan-upload-merge-contract.md) |
| 生产只读诊断 | [`production-diagnostics.md`](docs/operations/production-diagnostics.md) |
| Harness 适配与本地配置 | [`harness-adapters.md`](docs/development/ai/harness-adapters.md) |
| 批次交付：分流、实施、复核、激活（ADR-0058） | [`repository-workflow.md`](docs/development/repository-workflow.md)「批次交付流程」 |
| 批次规划 / 独立复核做法 | [`batch-planning.md`](docs/development/ai/batch-planning.md)、[`batch-review.md`](docs/development/ai/batch-review.md) |
| 执行状态机与 Agent 终态协议 | [`07-execution-protocol.md`](docs/design/07-execution-protocol.md) |
| 存储角色与路径 | [`2026-storage-roles-and-aliases.md`](docs/design/2026-storage-roles-and-aliases.md) |
| 环境变量清单 | [`environment-variables.md`](docs/development/environment-variables.md) |
| ADR 状态 | [`docs/adr/README.md`](docs/adr/README.md) |

## 提交前

- 运行匹配范围的测试，再按[门禁覆盖关系](docs/development/dependencies-and-quality.md#本地检查与-required-ci)运行 `check:quick`；本地成功不替代 required CI。
- 只报告实际运行过的命令与结果；未完成的检查标为 pending，命令成功不等于验证通过，调用失败或超时不算通过；
- 检查 diff 不含凭据、无关格式化或本地 Harness 状态；
- 改前端交互/布局：jsdom 测不了几何/命中/autofill/下载，走静态守卫或真实浏览器（[`testing.md`](docs/development/testing.md) §4）；
- Agent Note 使用 Decision、Alternatives、Verification、Revisit 四节；契约类文档与 SOP 中新写或改动的句子，作者必须按[写作约定](docs/development/writing-conventions.md)书写（ADR-0059）；
- required checks 为 `lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、
  `pr-agent-tests`、`pr-migrate-empty-db`。
