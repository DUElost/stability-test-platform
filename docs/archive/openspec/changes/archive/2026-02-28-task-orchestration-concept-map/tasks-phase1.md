# Phase 1 实施计划：零决策步骤清单

**Change**: task-orchestration-concept-map / Phase 1
**Date**: 2026-02-27
**Status**: Phase A/B/C Complete — Awaiting Integration Test
**实施完成时间**: 2026-02-27
**前置条件**: spec-phase1.md 所有约束已确认

---

## 总览

```
Task 1: Docker Compose + Redis 配置         [基础设施]
Task 2: Alembic 迁移脚本（建表+删旧表）     [数据库]
Task 3: SQLAlchemy ORM 模型                [数据库]
Task 4: Pydantic API Schema               [后端]
Task 5: JobStateMachine                   [服务层]
Task 6: WorkflowAggregator                [服务层]
Task 7: FastAPI lifespan + Redis 初始化   [应用层]
Task 8: heartbeat_monitor 后台任务        [应用层]
Task 9: pipeline_def JSON Schema 更新     [校验层]
```

依赖顺序：Task 1 → Task 2+3（并行）→ Task 4 → Task 5+6（并行）→ Task 7 → Task 8+9（并行）

---

## Task 1: Docker Compose + Redis 配置

**文件**: `docker-compose.yml`（新建），`backend/.env.example`（更新）

### 1.1 创建 `docker-compose.yml`

内容要求（机械复制自 infra/INFRA.md §2，无需决策）：
- postgres 服务：`postgres:15-alpine`，端口 5432，volume `postgres_data`
- redis 服务：`redis:7-alpine`，命令 `redis-server --appendonly yes --appendfsync everysec --maxmemory 512mb --maxmemory-policy allkeys-lru`，端口 6379
- server 服务：`build: ./backend`，依赖 postgres+redis，端口 8000，env_file `.env.server`
- frontend 服务：`build: ./frontend`，端口 3000（或 5173）
- volumes: `postgres_data`, `tool_packages`

### 1.2 更新 `backend/.env.example`

新增以下变量（有默认值，无需决策）：
```
REDIS_URL=redis://localhost:6379/0
HEARTBEAT_TIMEOUT_SECONDS=30
HEARTBEAT_CHECK_INTERVAL_SECONDS=10
BACKPRESSURE_LAG_THRESHOLD=5000
BACKPRESSURE_RELEASE_THRESHOLD=500
BACKPRESSURE_LOG_RATE_LIMIT=5
TOOL_PACKAGE_BASE_DIR=/opt/stp/tools
```

---

## Task 2: Alembic 迁移脚本

**文件**: `backend/alembic/versions/<hash>_add_stp_spec_phase1_schema.py`

### 2.1 upgrade() 函数执行顺序（严格按此顺序，避免 FK 冲突）

```
1. CREATE TABLE tool
2. CREATE TABLE host（新，id VARCHAR(64)）
3. CREATE TABLE device（依赖新 host）
4. CREATE TABLE workflow_definition
5. CREATE TABLE task_template（依赖 workflow_definition）
6. CREATE TABLE workflow_run（依赖 workflow_definition）
7. CREATE TABLE job_instance（依赖 workflow_run, task_template, device, host）
8. CREATE TABLE step_trace（依赖 job_instance）
9. CREATE INDEX × 6（见 DATABASE.md 索引列表）
10. DROP TABLE run_steps（依赖 task_runs，先删）
11. DROP TABLE task_runs（依赖 tasks, hosts, devices）
12. DROP TABLE workflow_steps（依赖 workflows）
13. DROP TABLE task_templates（旧）
14. DROP TABLE tasks
15. DROP TABLE workflows
16. DROP TABLE tool_categories（依赖 tools）
17. DROP TABLE tools（旧）
18. DROP TABLE devices（旧，已被新 device 替代）
19. DROP TABLE hosts（旧，已被新 host 替代）
20. DROP TABLE audit_logs（如存在）
21. DROP TABLE schedules（如存在）
22. DROP TABLE notifications（如存在）
```

### 2.2 downgrade() 函数

`raise NotImplementedError("Phase 1 migration is irreversible (data is discarded)")`

### 2.3 每张新表的精确 DDL（以 op.create_table 实现）

完全按照 `docs/stp-spec/backend/DATABASE.md` 中的 SQL 定义，逐字段映射，无字段增删。

---

## Task 3: SQLAlchemy ORM 模型

**文件**: `backend/models/` 目录结构重写

### 3.1 文件结构（不保留旧 schemas.py）

```
backend/models/__init__.py          -- 导出所有模型
backend/models/tool.py              -- Tool
backend/models/host.py              -- Host, Device
backend/models/workflow.py          -- WorkflowDefinition, WorkflowRun
backend/models/job.py               -- TaskTemplate, JobInstance, StepTrace
backend/models/enums.py             -- JobStatus, WorkflowStatus, DeviceStatus, HostStatus
```

### 3.2 `enums.py` 内容（全部枚举值，无决策）

```python
class JobStatus(str, Enum):
    PENDING      = "PENDING"
    RUNNING      = "RUNNING"
    COMPLETED    = "COMPLETED"
    FAILED       = "FAILED"
    ABORTED      = "ABORTED"
    UNKNOWN      = "UNKNOWN"
    PENDING_TOOL = "PENDING_TOOL"

class WorkflowStatus(str, Enum):
    RUNNING        = "RUNNING"
    SUCCESS        = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED         = "FAILED"
    DEGRADED       = "DEGRADED"

class HostStatus(str, Enum):
    ONLINE  = "ONLINE"
    OFFLINE = "OFFLINE"

class DeviceStatus(str, Enum):
    ONLINE  = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY    = "BUSY"
```

### 3.3 每个模型文件的字段清单

按 `DATABASE.md` 逐字段实现，SQLAlchemy 2.0 Mapped[] 语法，所有字段与 SQL DDL 一一对应。

---

## Task 4: Pydantic API Schema

**文件**: `backend/api/schemas.py`（重写）

### 4.1 需要定义的 Schema 类

```
# Tool
ToolCreate, ToolUpdate, ToolOut
ToolCategoryOut（已不存在，删除）

# Host & Device
HostCreate, HostUpdate, HostOut
DeviceCreate, DeviceUpdate, DeviceOut
HeartbeatRequest, HeartbeatResponse

# WorkflowDefinition
WorkflowDefinitionCreate, WorkflowDefinitionUpdate, WorkflowDefinitionOut

# TaskTemplate
TaskTemplateCreate, TaskTemplateOut

# WorkflowRun
WorkflowRunCreate (body: {device_ids: list[int], failure_threshold: float})
WorkflowRunOut (含 jobs: list[JobInstanceOut])

# JobInstance
JobInstanceOut (含 step_traces: list[StepTraceOut])
JobStatusUpdate (body: {status: JobStatus, reason: str})

# StepTrace
StepTraceBatchCreate (list[StepTraceCreate])
StepTraceCreate (job_id, step_id, stage, event_type, status, output, error_message, original_ts)
StepTraceOut

# 统一响应包装
class ApiResponse(Generic[T], BaseModel):
    data: Optional[T]
    error: Optional[ErrorDetail]

class ErrorDetail(BaseModel):
    code: str
    message: str
```

### 4.2 所有 API 端点响应必须用 `ApiResponse[T]` 包装

---

## Task 5: JobStateMachine

**文件**: `backend/services/state_machine.py`（新建）

### 5.1 精确实现（无决策，直接复制 stp-spec 逻辑）

```python
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING:       {JobStatus.RUNNING},
    JobStatus.RUNNING:       {JobStatus.COMPLETED, JobStatus.FAILED,
                              JobStatus.ABORTED, JobStatus.UNKNOWN},
    JobStatus.UNKNOWN:       {JobStatus.RUNNING, JobStatus.COMPLETED},
    JobStatus.FAILED:        set(),
    JobStatus.COMPLETED:     set(),
    JobStatus.ABORTED:       set(),
    JobStatus.PENDING_TOOL:  {JobStatus.PENDING},
}

class InvalidTransitionError(Exception):
    """HTTP 409"""

class JobStateMachine:
    @staticmethod
    def transition(job: JobInstance, new_status: JobStatus, reason: str = "") -> None:
        if new_status not in VALID_TRANSITIONS[job.status]:
            raise InvalidTransitionError(
                f"Cannot transition {job.status} -> {new_status} for job {job.id}"
            )
        job.status = new_status
        job.status_reason = reason
        job.updated_at = datetime.utcnow()
```

### 5.2 HTTP 异常处理

在 `main.py` 中注册 `InvalidTransitionError` → 返回 `ApiResponse(data=None, error=ErrorDetail(code="INVALID_TRANSITION", message=str(e)))` + HTTP 409

---

## Task 6: WorkflowAggregator

**文件**: `backend/services/aggregator.py`（新建）

### 6.1 精确实现（直接复制 stp-spec §4 逻辑）

```python
TERMINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED, JobStatus.UNKNOWN}

class WorkflowAggregator:
    @staticmethod
    async def on_job_terminal(job: JobInstance, db: AsyncSession) -> None:
        """在 JobStateMachine.transition 进入终态后调用"""
        run = await db.get(WorkflowRun, job.workflow_run_id)
        all_jobs = await db.execute(
            select(JobInstance).where(JobInstance.workflow_run_id == run.id)
        )
        jobs = all_jobs.scalars().all()

        # 检查是否全部终态
        if not all(j.status in TERMINAL_STATUSES for j in jobs):
            return  # 还有未完成 job，不聚合

        total   = len(jobs)
        failed  = sum(1 for j in jobs if j.status in {JobStatus.FAILED, JobStatus.ABORTED})
        unknown = sum(1 for j in jobs if j.status == JobStatus.UNKNOWN)

        if unknown > 0:
            run.status = WorkflowStatus.DEGRADED
        elif failed == 0:
            run.status = WorkflowStatus.SUCCESS
        elif failed / total <= run.failure_threshold:
            run.status = WorkflowStatus.PARTIAL_SUCCESS
        else:
            run.status = WorkflowStatus.FAILED

        run.ended_at = datetime.utcnow()
        await db.commit()
```

---

## Task 7: FastAPI lifespan + Redis 初始化

**文件**: `backend/main.py`（重写 lifespan）

### 7.1 lifespan 结构

```python
from contextlib import asynccontextmanager
import redis.asyncio as aioredis

redis_client: aioredis.Redis | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    global redis_client
    redis_client = await aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    # 初始化 Redis Stream 消费者组
    for stream, group in [("stp:status", "server-consumer"),
                          ("stp:logs", "log-consumer"),
                          ("stp:control", "agent-consumer")]:
        try:
            await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # 已存在

    # 启动 heartbeat_monitor 后台任务
    monitor_task = asyncio.create_task(heartbeat_monitor_loop())

    yield

    # 关闭
    monitor_task.cancel()
    await redis_client.aclose()
```

### 7.2 `settings.py` 新增配置项

```python
REDIS_URL: str = "redis://localhost:6379/0"
HEARTBEAT_TIMEOUT_SECONDS: int = 30
HEARTBEAT_CHECK_INTERVAL_SECONDS: int = 10
```

---

## Task 8: heartbeat_monitor 后台任务

**文件**: `backend/tasks/heartbeat_monitor.py`（新建）

### 8.1 精确实现（直接复制 stp-spec BACKEND.md §3.3 逻辑）

```python
async def heartbeat_monitor_loop():
    while True:
        await asyncio.sleep(settings.HEARTBEAT_CHECK_INTERVAL_SECONDS)
        try:
            await check_heartbeat_timeouts()
        except Exception as e:
            logger.error(f"heartbeat_monitor error: {e}")

async def check_heartbeat_timeouts():
    threshold = datetime.utcnow() - timedelta(seconds=settings.HEARTBEAT_TIMEOUT_SECONDS)
    async with AsyncSessionLocal() as db:
        dead_hosts = (await db.execute(
            select(Host).where(Host.last_heartbeat < threshold,
                               Host.status == HostStatus.ONLINE)
        )).scalars().all()

        for host in dead_hosts:
            running_jobs = (await db.execute(
                select(JobInstance).where(
                    JobInstance.host_id == host.id,
                    JobInstance.status == JobStatus.RUNNING
                )
            )).scalars().all()
            for job in running_jobs:
                JobStateMachine.transition(job, JobStatus.UNKNOWN, "host_heartbeat_timeout")
                await WorkflowAggregator.on_job_terminal(job, db)
            host.status = HostStatus.OFFLINE
        await db.commit()
```

---

## Task 9: pipeline_def JSON Schema 更新

**文件**: `backend/schemas/pipeline_schema.json`（重写）

### 9.1 新 JSON Schema 核心约束（无决策）

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["stages"],
  "additionalProperties": false,
  "properties": {
    "stages": {
      "type": "object",
      "properties": {
        "prepare":      { "$ref": "#/$defs/stage" },
        "execute":      { "$ref": "#/$defs/stage" },
        "post_process": { "$ref": "#/$defs/stage" }
      },
      "additionalProperties": false
    }
  },
  "$defs": {
    "stage": {
      "type": "array",
      "items": { "$ref": "#/$defs/step" }
    },
    "step": {
      "type": "object",
      "required": ["step_id", "action", "timeout_seconds"],
      "properties": {
        "step_id":          { "type": "string", "minLength": 1 },
        "action":           { "type": "string", "pattern": "^(tool:\\d+|builtin:.+)$" },
        "version":          { "type": "string" },
        "params":           { "type": "object" },
        "timeout_seconds":  { "type": "integer", "minimum": 1 },
        "retry":            { "type": "integer", "minimum": 0, "maximum": 10 }
      },
      "if": { "properties": { "action": { "pattern": "^tool:" } } },
      "then": { "required": ["version"] }
    }
  }
}
```

规则：
- `stages` 只允许三个键：`prepare`, `execute`, `post_process`
- `action` pattern：`^(tool:\d+|builtin:.+)$`（**不允许 shell: 或 run_tool_script**）
- `tool:<id>` 类型的 step 必须有 `version` 字段（JSON Schema `if/then`）

### 9.2 文件模板同步更新

更新 `backend/schemas/pipeline_templates/` 下的所有 JSON 文件，将旧格式转换为新格式：
- `phases` 数组 → `stages` 对象
- `name` → `step_id`
- `timeout` → `timeout_seconds`
- `action: "builtin:run_tool_script"` → 需要先在 `tool` 表中有对应记录才能替换为 `tool:<id>`；**Phase 1 中暂时标记为 `builtin:run_tool_script_MIGRATION_PENDING`（不通过 schema 校验，仅作占位）**

---

## 验收标准

Phase 1 完成当且仅当：

- [ ] `docker compose up -d postgres redis` 成功启动，Redis 使用 AOF
- [ ] `alembic upgrade head` 成功执行：8 张新表创建，旧表全部 DROP
- [ ] `pytest backend/tests/unit/test_state_machine.py` 通过 7 条 PBT 属性测试
- [ ] `pytest backend/tests/unit/test_aggregator.py` 通过聚合逻辑测试（10+ 随机状态组合）
- [ ] heartbeat_monitor 在 Host 心跳超时时正确将 RUNNING Job 置为 UNKNOWN
- [ ] `pipeline_schema.json` 拒绝 `shell:` 和 `run_tool_script` action，拒绝无 `version` 的 `tool:<id>` action
- [ ] FastAPI 启动时 Redis 连接成功，lifespan 中 Stream 消费者组创建成功
- [ ] 所有 API 响应符合 `{ "data": ..., "error": null }` 格式

---

## Codex Session（后续恢复用）

- Codex: SESSION_ID `019c9e79-75d3-7e61-a841-2982daace3b7`
- Gemini: SESSION_ID `1d665fa7-e197-4094-a36e-4dcedfdabcfb`
