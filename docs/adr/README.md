# ADR（Architecture Decision Record）索引与规范

本目录用于沉淀 `stability-test-platform` 的架构决策，服务于长期维护与 AI 检索。

## 状态定义

- `Proposed`：已提出，待评审/待实施。
- `Accepted`：已确认并作为当前基线执行。
- `Superseded`：已被后续 ADR 替代。
- `Deprecated`：不再推荐使用，但暂未完全移除。

## 优先级定义

- `P0`：必须优先完成，直接影响系统稳定性/安全性/发布可行性。
- `P1`：应在近期里程碑内完成，显著影响效率与可维护性。
- `P2`：中期推进，偏能力增强与体验提升。

## 编号与命名

- 文件名格式：`ADR-xxxx-<slug>.md`
- 编号规则：按提交顺序递增，不复用旧编号。
- 推荐先新增 ADR，再做代码改动；若代码已先落地，需补录 ADR 并标明“补录日期”。

## 编写模板

```md
# ADR-xxxx: 标题
- 状态：Proposed | Accepted | Superseded | Deprecated
- 优先级：P0 | P1 | P2（Proposed 建议必填）
- 目标里程碑：M1 | M2 | M3（Proposed 建议必填）
- 日期：YYYY-MM-DD
- 决策者：架构组/研发组
- 标签：调度, 数据库, 安全

## 背景

## 决策

## 备选方案与权衡

## 影响

## 落地与后续动作

## 关联实现/文档
```

## 当前 ADR 清单

| 编号 | 标题 | 状态 | 优先级 | 目标里程碑 | 类型 |
|---|---|---|---|---|---|
| [ADR-0001](./ADR-0001-control-plane-and-agent-architecture.md) | 控制面 + 执行面分层架构 | Accepted | - | - | 已实现 |
| [ADR-0002](./ADR-0002-single-process-with-internal-schedulers.md) | 单进程后端 + 内置后台调度线程 | Accepted | - | - | 已实现 |
| [ADR-0003](./ADR-0003-task-run-state-machine-and-device-lock-lease.md) | 任务状态机与设备锁租约机制 | Accepted | - | - | 已实现（2026-03-16 更新：统一锁服务 + 会话看门狗） |
| [ADR-0004](./ADR-0004-heartbeat-driven-host-device-liveness.md) | 心跳驱动的主机/设备在线性模型 | Accepted | - | - | 已实现（2026-03-16 更新：watchdog 接管心跳超时） |
| [ADR-0005](./ADR-0005-database-strategy-sqlite-first-postgresql-ready.md) | SQLite 起步 + PostgreSQL 兼容演进 | Deprecated | - | - | 已废弃，使用 PostgreSQL |
| [ADR-0006](./ADR-0006-realtime-communication-rest-plus-websocket.md) | REST + WebSocket 的实时通信分工 | Accepted | - | - | 已实现 |
| [ADR-0007](./ADR-0007-tool-template-workflow-extension-model.md) | 工具配置 + 任务模板 + 工作流扩展模型 | Accepted | - | - | 已实现 |
| [ADR-0008](./ADR-0008-schema-migration-governance-alembic-only.md) | 统一 Schema 迁移治理（Alembic Only） | Accepted | P0 | M1 | 预扩展/重构 |
| [ADR-0009](./ADR-0009-websocket-auth-and-endpoint-config-unification.md) | WebSocket 鉴权与端点配置统一化 | Accepted | P0 | M1 | 已实现（2026-03-24） |
| [ADR-0010](./ADR-0010-deployment-pipeline-jobification.md) | 部署能力作业化（异步、幂等、可回放） | Proposed | P1 | M2 | 预扩展/重构 |
| [ADR-0011](./ADR-0011-observability-and-alerting-evolution.md) | 可观测性与告警体系演进 | Proposed | P1 | M2 | 预扩展/重构 |
| [ADR-0012](./ADR-0012-post-completion-pipeline-jira-automation.md) | 后处理流水线到 JIRA 自动提交演进 | Accepted | P2 | M3 | 第 1 层已实现 |
| [ADR-0013](./ADR-0013-frontend-feature-expansion.md) | 前端功能模块扩展（任务实例、问题追踪、环境资源） | Accepted | P1 | M2 | 已实现 |
| [ADR-0014](./ADR-0014-pipeline-execution-engine.md) | Pipeline 执行引擎架构 | Accepted | P1 | M2 | 已实现（2026-03-16 更新：锁验证 + 参数表单） |
| [ADR-0015](./ADR-0015-audit-log-system.md) | 审计日志系统 | Accepted | P1 | M2 | 已实现 |
| [ADR-0016](./ADR-0016-deprecate-base-test-case.md) | 废弃 BaseTestCase，以 Pipeline Action 为唯一执行模型 | Accepted | P0 | M2 | 冻结中，待迁移 |
| [ADR-0017](./ADR-0017-phase0-state-closure.md) | Phase 0 状态闭环 | Accepted | P0 | M1 | 已实现 |
| [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md) | 基础设施层框架引入（SAQ / APScheduler / python-socketio） | Accepted | P0 | M2 | 已实现 |
| [ADR-0019](./ADR-0019-android-device-lease-and-capacity-scheduling.md) | Android Device Lease 与容量调度模型 | Proposed | P0 | M3 | 待评审 |

## Proposed 里程碑看板（2026 上半年）

| 里程碑 | 目标日期 | 包含 ADR |
|---|---|---|
| M1 | 2026-03-15 | ADR-0008, ADR-0009 |
| M2 | 2026-04-15 | ADR-0010, ADR-0011, ADR-0013, ADR-0014, ADR-0016, ADR-0018 |
| M3 | 2026-05-15 | ADR-0012（第 2-3 层）, ADR-0019 |

## 维护约定

- 每次关键架构变化必须新增或更新 ADR，并在 MR/PR 中引用。
- 若 ADR 被替代，旧 ADR 不删除，仅将状态改为 `Superseded` 并指向新 ADR。
- AI 生成方案或修改代码时，优先检索本目录并遵循 `Accepted` ADR。
