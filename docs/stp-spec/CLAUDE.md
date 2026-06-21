# Stability Test Platform — 文档入口（极简版）

> 本目录原存放 ADR-0020 落地前的领域 spec（架构 / 后端 / 数据库 / MQ / Agent / 前端 / 部署）。
> 这些文档已与当前实现失配（WorkflowDefinition、Tool catalog、Redis Stream、stages 格式等），
> 已于 2026-05-07 整体归档至 [`../archive/stp-spec-pre-adr0020/`](../archive/stp-spec-pre-adr0020/)，详见该目录下 `README.md`。

## 当前权威开发依据

| 你要了解 | 看这里 |
|---|---|
| **文档中心 / 阅读顺序** | [`docs/README.md`](../README.md)、[`DOC-MAP.md`](../DOC-MAP.md) |
| **项目整体约定 / 模块清单 / 启动入口** | 项目根 [`CLAUDE.md`](../../CLAUDE.md)（百科）；模块设计见 [`design/`](../design/) |
| Plan / PlanStep / PlanRun 编排模型 | [`adr/ADR-0020-plan-step-one-shot-migration.md`](../adr/ADR-0020-plan-step-one-shot-migration.md) |
| Pipeline 执行引擎 + `lifecycle` 格式 + `script:<name>` action | [`adr/ADR-0014-pipeline-execution-engine.md`](../adr/ADR-0014-pipeline-execution-engine.md) |
| Watcher 子系统 + `log_signal` + `JobArtifact` | [`adr/ADR-0018-infrastructure-layer-framework-adoption.md`](../adr/ADR-0018-infrastructure-layer-framework-adoption.md) |
| Device lease + fencing_token + Recovery API | [`adr/ADR-0019-android-device-lease-and-capacity-scheduling.md`](../adr/ADR-0019-android-device-lease-and-capacity-scheduling.md) |
| 实时通道（SocketIO `/agent` + `/dashboard`） | [`adr/ADR-0006-realtime-communication-rest-plus-websocket.md`](../adr/ADR-0006-realtime-communication-rest-plus-websocket.md) / [`ADR-0009-websocket-auth-and-endpoint-config-unification.md`](../adr/ADR-0009-websocket-auth-and-endpoint-config-unification.md) |
| 单进程内多调度器（APScheduler + SAQ） | [`adr/ADR-0002-single-process-with-internal-schedulers.md`](../adr/ADR-0002-single-process-with-internal-schedulers.md) |
| 状态机 / 设备 lease / 心跳 | [`adr/ADR-0003-task-run-state-machine-and-device-lock-lease.md`](../adr/ADR-0003-task-run-state-machine-and-device-lock-lease.md) / [`ADR-0004-heartbeat-driven-host-device-liveness.md`](../adr/ADR-0004-heartbeat-driven-host-device-liveness.md) |
| ADR 索引（按时间） | [`adr/README.md`](../adr/README.md) |
| **文档地图（PRD / 设计 / 验收）** | [`DOC-MAP.md`](../DOC-MAP.md) |
| 方案 C PRD / 设计 / 验收 | [`prd/`](../prd/)、[`design/`](../design/)、[`acceptance/`](../acceptance/) |
| 非 ADR-0020 架构债务清单 | [`architecture/non-adr20-followups.md`](../architecture/non-adr20-followups.md) |
| 部署 / 环境变量 / WSL 联调 | 项目根 [`CLAUDE.md`](../../CLAUDE.md) §"入口与启动" + [`backend/agent/DEPLOY.md`](../../backend/agent/DEPLOY.md) |
| 数据库 schema / 迁移 | `backend/alembic/versions/` 下 Alembic 文件 + 项目根 `CLAUDE.md` §"数据模型" |
| API 路由 | `backend/api/routes/` + 项目根 `CLAUDE.md` §"对外接口" |

## 写在前面

- **代码事实优先于文档**：跟当前 `backend/` 与 `frontend/` 不一致的描述以代码为准。
- **不要在本目录新增 spec**：领域 spec 留在各 ADR；项目级约定留在项目根 `CLAUDE.md`；DDL 留在 Alembic 迁移文件。
- **历史文档查询入口**：`../archive/stp-spec-pre-adr0020/README.md`。
