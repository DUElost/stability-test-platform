> ⚠️ **已归档（HISTORICAL）—— 不要作为开发依据**
>
> 本目录下的所有 `.md`（`CLAUDE.md` / `architecture/ARCHITECTURE.md` / `backend/{BACKEND,DATABASE,MQ}.md` / `agent/AGENT.md` / `frontend/FRONTEND.md` / `infra/INFRA.md`）是 **ADR-0020 落地之前** 的历史 spec。
>
> 它们仍在描述以下**已废弃**概念，与当前实现不一致：
>
> - `WorkflowDefinition` / `WorkflowRun` / `TaskTemplate`（已被 ADR-0020 替换为 `Plan` / `PlanRun` / `PlanStep`）
> - Tool Catalog + `tool:<id>` / `builtin:<name>` / `shell:<cmd>` action 类型（已被 ADR-0020 的 `script:<name>` 单一类型替换；`PENDING_TOOL` 状态同期废弃）
> - `pipeline_def` 的 `stages` 顶层键（已被 `lifecycle` 顶层键替换，见 ADR-0014）
> - Redis Stream `stp:status` / `stp:logs` 双 topic（Agent↔Server 已迁至 python-socketio `/agent` namespace + SAQ 队列，见 ADR-0006/0009）
> - `device_lock` / `lock_run_id`（已被 `device_leases` + fencing_token 替换，见 ADR-0019）
> - Watcher 子系统未提及（见 ADR-0018）

## 当前权威源

| 你要了解 | 看这里 |
|---|---|
| 项目整体约定 / 模块清单 / 入口 | 项目根 `CLAUDE.md` |
| Plan-based 编排 + 一次性迁移 | `docs/adr/ADR-0020-plan-step-one-shot-migration.md` |
| Pipeline 执行引擎 + lifecycle 格式 | `docs/adr/ADR-0014-pipeline-execution-engine.md` |
| Watcher 子系统 + log_signal | `docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md` |
| Device lease + fencing_token | `docs/adr/ADR-0019-android-device-lease-and-capacity-scheduling.md` |
| 实时通道（SocketIO + WS deprecated） | `docs/adr/ADR-0006-realtime-communication-rest-plus-websocket.md` / `docs/adr/ADR-0009-websocket-auth-and-endpoint-config-unification.md` |
| 单进程内多调度器 | `docs/adr/ADR-0002-single-process-with-internal-schedulers.md` |
| ADR 索引 | `docs/adr/README.md` |
| 非 ADR-0020 后续债务 | `docs/architecture/non-adr20-followups.md` |

## 保留原因

仅供溯源参考：理解某些设计决策的演进路径（例如 Tool Catalog 为何被废弃、为何从 stages 迁到 lifecycle）。**严禁** 直接拷贝这些文档中的代码片段、SQL 定义或 API 路由作为新开发依据。

归档时间：2026-05-07
