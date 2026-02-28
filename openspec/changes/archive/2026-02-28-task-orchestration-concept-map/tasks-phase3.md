# Phase 3 实施计划：后端 API 重构

**Change**: task-orchestration-concept-map / Phase 3
**Date**: 2026-02-27
**Status**: Complete
**实施完成时间**: 2026-02-27
**前置条件**: Phase 1 (数据模型) + Phase 2 (Agent MQ 层) 完成

---

## 总览

```
Task 1: services/dispatcher.py      — WorkflowRun 扇出 + JobInstance 创建    [后端]
Task 2: services/reconciler.py      — StepTrace 幂等 upsert (Agent 重播)     [后端]
Task 3: api/response.py             — ApiResponse[T] 统一响应格式             [后端]
Task 4: api/routes/orchestration.py — WorkflowDefinition CRUD + run 触发      [后端]
Task 5: api/routes/agent_api.py     — Agent 认领/状态/StepTrace/心跳          [后端]
Task 6: api/routes/tool_catalog.py  — 新 Tool 模型 CRUD (替换旧 tools.py)     [后端]
Task 7: mq/consumer.py (backend)    — stp:status 消费 → DB + 背压监控        [后端]
Task 8: main.py 更新               — 注册新路由 + 启动 MQ consumer          [后端]
```

依赖顺序: Task 1+2+3 (并行) → Task 4+5+6 (并行，依赖 1+2+3) → Task 7 → Task 8

---

## Task 1: services/dispatcher.py

**文件**: `backend/services/dispatcher.py`（新建）

### 核心逻辑

```python
async def dispatch_workflow(workflow_def_id, device_ids, failure_threshold, triggered_by, db) -> WorkflowRun:
    1. 加载 WorkflowDefinition（404 时抛 DispatchError）
    2. 加载所有 TaskTemplate（按 sort_order 排序）
    3. 校验 pipeline_def 中所有 tool_id 存在且 is_active=True
    4. 创建 WorkflowRun (status=RUNNING)
    5. 为每个 (device_id × TaskTemplate) 创建 JobInstance (status=PENDING)
    6. commit + 返回 WorkflowRun
```

### 异常类型

- `DispatchError` → HTTP 400: workflow not found / no templates / tool not active

---

## Task 2: services/reconciler.py

**文件**: `backend/services/reconciler.py`（新建）

### 核心逻辑

```python
async def reconcile_step_traces(host_id, traces, db) -> int:
    1. 对每个 trace: INSERT OR IGNORE INTO step_trace (幂等)
    2. 收集受影响的 job_id 集合
    3. 对每个 job_id: _recompute_job_status()
    4. commit，返回 inserted 数量
```

### 幂等保证

- PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`
- 唯一约束 `(job_id, step_id, event_type)` 保障不重复

---

## Task 3: api/response.py

**文件**: `backend/api/response.py`（新建）

```python
class ErrorDetail(BaseModel):
    code: str
    message: str

class ApiResponse(Generic[T], BaseModel):
    data: Optional[T]
    error: Optional[ErrorDetail]

def ok(data) -> ApiResponse:     return ApiResponse(data=data, error=None)
def err(code, message) -> ApiResponse: return ApiResponse(data=None, error=ErrorDetail(code=code, message=message))
```

---

## Task 4: api/routes/orchestration.py

**文件**: `backend/api/routes/orchestration.py`（新建）

### 端点

```
POST  /api/v1/workflows              创建 WorkflowDefinition (含 TaskTemplate 列表)
GET   /api/v1/workflows              列表 (分页)
GET   /api/v1/workflows/{id}         详情 (含 TaskTemplate)
PUT   /api/v1/workflows/{id}         更新
DELETE /api/v1/workflows/{id}        删除 (有活跃 Run 时拒绝)
POST  /api/v1/workflows/{id}/run     触发 → dispatcher.dispatch_workflow()

GET   /api/v1/workflow-runs/{run_id}       WorkflowRun + 聚合状态
GET   /api/v1/workflow-runs/{run_id}/jobs  JobInstances 列表 + StepTraces
```

### 使用 AsyncSession (get_async_db)

---

## Task 5: api/routes/agent_api.py

**文件**: `backend/api/routes/agent_api.py`（新建）

### 端点（全部认证 via X-Agent-Secret header）

```
POST /api/v1/agent/jobs/claim
  Body: { "host_id": "...", "capacity": 10 }
  逻辑: 查找该 host 设备的 PENDING jobs, 限 capacity 条，PENDING→RUNNING

POST /api/v1/agent/jobs/{job_id}/status
  Body: { "status": "RUNNING"|"COMPLETED"|"FAILED"|..., "reason": "" }
  逻辑: JobStateMachine.transition()，终态时 WorkflowAggregator.on_job_terminal()

POST /api/v1/agent/steps
  Body: [{ "job_id", "step_id", "stage", "event_type", "status", "output",
            "error_message", "original_ts" }]
  逻辑: reconciler.reconcile_step_traces()

POST /api/v1/agent/heartbeat
  Body: { "host_id": "...", "tool_catalog_version": "...", "load": {...} }
  返回: { "data": { "tool_catalog_outdated": bool, "backpressure": { "log_rate_limit": int|null } } }
  逻辑: 更新 Host.last_heartbeat + tool_catalog_version; 检查 Redis lag → 返回 backpressure
```

---

## Task 6: api/routes/tool_catalog.py

**文件**: `backend/api/routes/tool_catalog.py`（新建，使用新 `tool` 模型替代旧 `tools.py`）

### 端点

```
GET    /api/v1/tools           列表 (is_active 过滤，供 Agent ToolRegistry 使用)
POST   /api/v1/tools           创建
GET    /api/v1/tools/{id}      详情
PUT    /api/v1/tools/{id}      更新（version 字段不变，需显式传入新 version）
DELETE /api/v1/tools/{id}      逻辑删除 (is_active=False)
```

### Response 包含 Agent 需要的字段

`id`, `name`, `version`, `script_path`, `script_class`, `param_schema`, `is_active`

---

## Task 7: mq/consumer.py (backend)

**文件**: `backend/mq/__init__.py`, `backend/mq/consumer.py`（新建）

### stp:status 消费者

```python
async def consume_status_stream(redis_client):
    """读取 stp:status, 写入 StepTrace/JobInstance DB"""
    XREADGROUP stp:status server-consumer > (block=1000)
    for msg in messages:
        if msg.msg_type == "step_trace":
            reconciler.reconcile_step_traces(...)
        elif msg.msg_type == "job_status":
            update job status via JobStateMachine
        XACK stp:status server-consumer msg_id
```

### 背压监控

```python
async def monitor_backpressure(redis_client):
    """每 5s 检查 stp:status lag, 超阈值时向 stp:control 发背压指令"""
    lag = xinfo_groups("stp:status")[0]["lag"]
    if lag > 5000: xadd(stp:control, backpressure, log_rate_limit=5)
    elif lag < 500: xadd(stp:control, backpressure, log_rate_limit=null)
```

---

## Task 8: main.py 更新

**修改**: `backend/main.py`

1. 替换旧 `tools_router` 为新 `tool_catalog_router`
2. 替换旧 `workflows_router` 为新 `orchestration_router`
3. 注册新 `agent_api_router`
4. lifespan 中启动 MQ consumer 后台任务 + backpressure monitor

---

## 验收标准

- [x] `POST /api/v1/workflows/{id}/run` 能正确创建 WorkflowRun + JobInstances
- [x] `POST /api/v1/agent/jobs/claim` 返回 PENDING jobs, 并转换为 RUNNING
- [x] `POST /api/v1/agent/steps` 幂等写入 StepTrace
- [x] `POST /api/v1/agent/heartbeat` 返回 backpressure 指令
- [x] `GET /api/v1/tools` 返回新 Tool 模型格式（含 version 字段），Agent ToolRegistry 可用
- [x] stp:status 消费者将 MQ 消息写入 DB
- [x] 所有响应格式符合 `{ "data": ..., "error": null }`
