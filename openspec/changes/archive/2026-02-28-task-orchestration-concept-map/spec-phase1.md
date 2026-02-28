# Phase 1 规格：基础设施 + 数据模型

**Change**: task-orchestration-concept-map / Phase 1
**Date**: 2026-02-27
**Status**: Approved for Implementation

---

## 已确认约束（所有歧义已消除）

| ID | 约束 | 来源 |
|----|------|------|
| C1 | 历史数据直接丢弃，旧表全部 DROP，无迁移脚本 | 用户决策 |
| C2 | Host.id 使用 VARCHAR(64)，格式由运维人工指定（如 "host-bj-01"），全局唯一 | 用户决策 |
| C3 | Phase 1 范围：Redis + Docker Compose + 8 张新表 + Alembic + JobStateMachine + WorkflowAggregator + heartbeat_monitor | 用户决策 |
| C4 | 保留现有 auth 系统，`created_by` 字段存 username 字符串（非 FK） | 用户决策 |
| C5 | `tool:<id>` 成为唯一的工具 action 类型；`shell:<cmd>` 和 `builtin:run_tool_script` 不在新 schema 中支持 | stp-spec + 用户决策 |
| C6 | `on_failure: continue` 无对应概念，统一用 `retry: 0`（默认 stop 语义） | stp-spec 规格推导 |
| C7 | JobStateMachine 是唯一状态变更入口，禁止直接赋值 job.status | stp-spec C-2 |
| C8 | Redis 7 使用 AOF 持久化，必须在 FastAPI lifespan 中初始化 | Codex 分析 |
| C9 | heartbeat_monitor 使用 asyncio 后台任务（sleep 循环），间隔 10s | Codex + stp-spec |
| C10 | WorkflowAggregator 在 JobInstance 进入终态时事件驱动触发，在同一 DB 事务内检查全量终态 | Codex 分析 |

---

## 功能需求（Requirements）

### R1: 数据库 Schema（8 张新表）

**R1.1** 系统必须创建 `tool` 表
- `(name, version)` 联合唯一索引
- `param_schema` 为 JSONB，必须是合法 JSON Schema object
- `is_active` 默认 TRUE，下线工具设 FALSE

**R1.2** 系统必须创建 `host` 表
- `id` 为 VARCHAR(64) PRIMARY KEY（人工指定）
- `tool_catalog_version` VARCHAR(64) 用于 Agent 增量同步检测
- `cpu_quota` INTEGER DEFAULT 2

**R1.3** 系统必须创建 `device` 表
- `host_id` VARCHAR(64) 外键引用新 `host` 表（非旧 `hosts` 表）
- `platform` VARCHAR(64) 用于 TaskTemplate.platform_filter 过滤

**R1.4** 系统必须创建 `workflow_definition` 表
- `failure_threshold` FLOAT DEFAULT 0.05

**R1.5** 系统必须创建 `task_template` 表
- `workflow_definition_id` FK CASCADE DELETE
- `pipeline_def` JSONB，在创建时必须通过 JSON Schema 校验
- `platform_filter` JSONB NULLABLE（NULL 表示不限平台）

**R1.6** 系统必须创建 `workflow_run` 表
- `failure_threshold` 快照自 `workflow_definition.failure_threshold`（创建时复制）
- `result_summary` JSONB NULLABLE，聚合完成后填入

**R1.7** 系统必须创建 `job_instance` 表
- `pipeline_def` JSONB 快照（从 task_template 复制，创建后不可更新）
- `status_reason` TEXT NULLABLE

**R1.8** 系统必须创建 `step_trace` 表
- `(job_id, step_id, event_type)` 三列联合唯一约束（Reconciliation 幂等保障）
- `original_ts` TIMESTAMPTZ 保存 Agent 本地原始时间戳

### R2: Alembic 迁移

**R2.1** 所有 Schema 变更必须通过 Alembic 管理，禁止直接 DDL
**R2.2** 一个 Alembic revision 创建全部 8 张新表和索引
**R2.3** 旧表（hosts, devices, tasks, task_runs, run_steps, workflows, workflow_steps, tools, tool_categories, task_templates）在同一 revision 中 DROP
**R2.4** 迁移文件命名：`add_stp_spec_phase1_schema`

### R3: JobStateMachine

**R3.1** `services/state_machine.py` 包含 `JobStateMachine` 类，`transition(job, new_status, reason)` 为唯一状态变更入口
**R3.2** 非法转换抛出 `InvalidTransitionError`，HTTP 409

合法转换表（不可更改）：
```
PENDING      → {RUNNING}
RUNNING      → {COMPLETED, FAILED, ABORTED, UNKNOWN}
UNKNOWN      → {RUNNING, COMPLETED}
FAILED       → {} (终态)
COMPLETED    → {} (终态)
ABORTED      → {} (终态)
PENDING_TOOL → {PENDING}
```

### R4: WorkflowAggregator

**R4.1** `services/aggregator.py` 包含 `WorkflowAggregator` 类
**R4.2** 触发时机：任何 `JobInstance` 进入终态时（COMPLETED/FAILED/ABORTED/UNKNOWN），在同一 DB session 中检查
**R4.3** 聚合逻辑（来自 stp-spec §4）：
- 存在 UNKNOWN jobs → DEGRADED
- failed == 0 → SUCCESS
- failed / total ≤ threshold → PARTIAL_SUCCESS
- 其他 → FAILED

### R5: Redis 基础设施

**R5.1** Docker Compose 新增 `redis` 服务（`redis:7-alpine`，AOF 持久化开启）
**R5.2** Server 环境变量：`REDIS_URL`（默认 `redis://localhost:6379/0`）
**R5.3** FastAPI lifespan 中初始化 Redis 连接，应用关闭时断开

### R6: heartbeat_monitor

**R6.1** `tasks/heartbeat_monitor.py` 中的 `check_heartbeat_timeouts()` 作为 asyncio 后台任务
**R6.2** 检查间隔：10 秒（HEARTBEAT_CHECK_INTERVAL_SECONDS 环境变量控制）
**R6.3** 超时阈值：30 秒（HEARTBEAT_TIMEOUT_SECONDS 环境变量控制）
**R6.4** 超时时对该 Host 的所有 RUNNING JobInstance 调用 `JobStateMachine.transition(job, UNKNOWN, reason="host_heartbeat_timeout")`

---

## PBT 不变量（Property-Based Testing）

| ID | 不变量 | 伪造策略（Falsification） |
|----|--------|--------------------------|
| P1 | JobStatus 只能在合法转换表内变化 | 尝试 PENDING→COMPLETED 直接赋值，必须抛出 InvalidTransitionError |
| P2 | `(job_id, step_id, event_type)` 在 step_trace 中最多一条 | 并发插入同一三元组，后一条必须 ON CONFLICT DO NOTHING |
| P3 | WorkflowRun 聚合状态与 JobInstance 终态集合一致 | 随机生成 jobs 状态组合，验证 aggregator 计算结果与预期一致 |
| P4 | `job_instance.pipeline_def` 创建后不可更改 | 更新 task_template.pipeline_def，历史 job_instance 不受影响 |
| P5 | Host 心跳超时 30s 后，所有 RUNNING Jobs 必须变为 UNKNOWN | 设置 last_heartbeat 为 31s 前，运行 check_heartbeat_timeouts，验证状态变更 |
| P6 | `workflow_run.failure_threshold` 是创建时快照，不受后续 definition 修改影响 | 修改 workflow_definition.failure_threshold，已有 workflow_run 值不变 |
| P7 | `tool.param_schema` 必须是合法 JSON Schema（`type: object`） | 插入 `{"not_a_schema": true}`，应拒绝 |
