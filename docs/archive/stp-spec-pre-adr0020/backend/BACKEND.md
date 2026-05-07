# 后端开发规范：API、业务逻辑、异常处理

> 读本文档前请先读 [`../architecture/ARCHITECTURE.md`](../architecture/ARCHITECTURE.md)

## 1. 项目结构

```
server/
├── app/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 环境变量读取
│   ├── models/                  # SQLAlchemy ORM 模型
│   │   ├── workflow.py          # WorkflowDefinition, WorkflowRun
│   │   ├── job.py               # TaskTemplate, JobInstance, StepTrace
│   │   ├── tool.py              # Tool (Tool Catalog)
│   │   └── host.py              # Host, Device
│   ├── schemas/                 # Pydantic 请求/响应模型
│   ├── api/
│   │   ├── v1/
│   │   │   ├── workflows.py     # 编排层 CRUD
│   │   │   ├── jobs.py          # 调度层：触发、状态查询
│   │   │   ├── tools.py         # Tool Catalog 管理
│   │   │   ├── hosts.py         # Host 注册、心跳
│   │   │   └── agent.py         # Agent 专用接口（认领、上报）
│   ├── services/
│   │   ├── dispatcher.py        # Fan-out 扇出逻辑
│   │   ├── aggregator.py        # WorkflowRun 聚合策略
│   │   ├── reconciler.py        # State Reconciliation
│   │   ├── state_machine.py     # JobStateMachine
│   │   └── notifier.py          # 钉钉 / JIRA 通知
│   ├── mq/
│   │   ├── consumer.py          # Redis Stream 消费者
│   │   └── producer.py          # 背压通知生产者
│   └── tasks/
│       └── heartbeat_monitor.py # 心跳超时检测（定时任务）
```

## 2. API 端点规范

### 统一响应格式

```python
# 成功
{ "data": <payload>, "error": null }

# 失败
{ "data": null, "error": { "code": "INVALID_TRANSITION", "message": "..." } }
```

### 编排层

```
POST   /api/v1/workflows                    创建 WorkflowDefinition
GET    /api/v1/workflows                    列表
GET    /api/v1/workflows/{id}               详情（含 TaskTemplate 列表）
PUT    /api/v1/workflows/{id}               更新蓝图
DELETE /api/v1/workflows/{id}               删除（有关联 Run 时拒绝删除）

POST   /api/v1/workflows/{id}/run           触发执行
  Body: { "device_ids": [...], "failure_threshold": 0.05 }
  返回: WorkflowRun 对象（含 workflow_run_id）

GET    /api/v1/workflow-runs/{run_id}       WorkflowRun 详情（含聚合状态）
GET    /api/v1/workflow-runs/{run_id}/jobs  所有 JobInstance 及其 StepTrace
```

### 调度层（Agent 专用）

```
POST   /api/v1/agent/jobs/claim             Agent 认领待执行 Job
  Body: { "host_id": "...", "capacity": 10 }
  返回: [ JobInstance + pipeline_def（tool_id 已解析为本地路径） ]

POST   /api/v1/agent/jobs/{job_id}/status   上报 Job 状态变更
  Body: { "status": "RUNNING", "reason": "" }

POST   /api/v1/agent/steps                 批量上报 StepTrace
  Body: [ { "job_id", "step_id", "status", "started_at", "ended_at", "output" } ]

POST   /api/v1/agent/heartbeat             Host 心跳
  Body: { "host_id": "...", "tool_catalog_version": "abc123", "load": {...} }
  返回: { "tool_catalog_outdated": true/false, "backpressure": { "log_rate_limit": 10 } }
```

### Tool Catalog

```
GET    /api/v1/tools                        工具列表
POST   /api/v1/tools                        注册新工具
PUT    /api/v1/tools/{tool_id}              更新（会生成新 version，旧 version 保留）
DELETE /api/v1/tools/{tool_id}              逻辑删除（is_active=False）

POST   /api/v1/tools/scan                   扫描目录自动入库
GET    /api/v1/tools/{tool_id}/usages       查询引用该工具的所有 pipeline_def
```

## 3. 核心业务逻辑

### 3.1 触发执行（dispatcher.py）

```python
async def dispatch_workflow(workflow_def_id: int, device_ids: list[int],
                             failure_threshold: float) -> WorkflowRun:
    """
    1. 创建 WorkflowRun
    2. 为每个 device_id 创建 JobInstance (status=PENDING)
    3. 验证 pipeline_def 中所有 tool_id 存在且 is_active=True
       - 若有工具不存在：整个 WorkflowRun 创建失败，返回错误
    4. 将 JobInstance 列表推入 Redis 分发队列
    5. 返回 WorkflowRun
    """
```

### 3.2 param_schema 前置校验

```python
# 在 dispatch_workflow 内，创建 JobInstance 之前执行
def validate_pipeline_params(pipeline_def: dict, tool_registry: dict[int, Tool]) -> None:
    for stage in pipeline_def["stages"].values():
        for step in stage:
            if step["action"].startswith("tool:"):
                tool_id = int(step["action"].split(":")[1])
                tool = tool_registry.get(tool_id)
                if not tool:
                    raise ToolNotFoundError(tool_id)
                validate(instance=step["params"], schema=tool.param_schema)
                # jsonschema.validate，校验失败抛 ValidationError
```

### 3.3 心跳超时监控（heartbeat_monitor.py）

```python
# 每 10s 执行一次的定时任务
async def check_heartbeat_timeouts():
    threshold = utcnow() - timedelta(seconds=30)
    dead_hosts = await db.query(Host).filter(Host.last_heartbeat < threshold).all()

    for host in dead_hosts:
        running_jobs = await db.query(JobInstance).filter(
            JobInstance.host_id == host.id,
            JobInstance.status == JobStatus.RUNNING
        ).all()
        for job in running_jobs:
            JobStateMachine.transition(job, JobStatus.UNKNOWN, reason="host_heartbeat_timeout")
        await alert_manager.send(f"Host {host.id} 失联，{len(running_jobs)} 个 Job 置为 UNKNOWN")
```

### 3.4 State Reconciliation（reconciler.py）

```python
async def reconcile_from_replay(host_id: str, step_traces: list[StepTraceReplay]):
    """
    Agent 重连后调用，处理断连期间缓存的 StepTrace。
    step_traces 已按 original_timestamp 排序。
    """
    for trace in step_traces:
        # 幂等检查：(job_id, step_id, event_type) 联合唯一
        exists = await db.query(StepTrace).filter_by(
            job_id=trace.job_id,
            step_id=trace.step_id,
            event_type=trace.event_type
        ).first()
        if exists:
            continue  # 重复消息，跳过

        await db.add(StepTrace(**trace.dict()))

    # 重建 Job 状态并解除 UNKNOWN 锁定
    affected_jobs = {t.job_id for t in step_traces}
    for job_id in affected_jobs:
        await _recompute_job_status(job_id)
```

## 4. 异常处理规范

### 4.1 Failure Mode 对应处理

| 场景 | 检测位置 | 处理逻辑 |
|---|---|---|
| Agent 崩溃（心跳超时） | `heartbeat_monitor.py` | Job → UNKNOWN，告警，等待 Reconciliation |
| 单设备死锁（Step 超时） | Agent Watchdog → 上报 ABORTED | Server 接受 ABORTED 状态，纳入聚合统计 |
| 消息积压 | `consumer.py` 监控 lag | 向 Agent 发送背压指令（`stp:control` Topic） |
| 工具版本拉取失败（网络） | Agent 上报 PENDING_TOOL | Server 记录状态，告警，等待人工介入 |
| 工具版本拉取失败（不存在） | Agent 上报 FAILED | Server 接受，记录 tool_id 和 version 到 status_reason |
| SQLite 磁盘故障 | Agent 上报特殊错误码 | Job 保持 UNKNOWN，不强制恢复 |
| Last_ACK_ID 竞态 → 重复 Replay | `reconciler.py` | 幂等去重，忽略重复 StepTrace |

### 4.2 状态转换错误

```python
class InvalidTransitionError(Exception):
    """非法状态转换，HTTP 409 Conflict"""

class ToolNotFoundError(Exception):
    """工具不存在或已下线，HTTP 422 Unprocessable Entity"""

class WorkflowDispatchError(Exception):
    """触发执行失败（如参数校验不通过），HTTP 400 Bad Request"""
```

## 5. 背压控制接口

心跳响应中携带背压指令：

```python
# heartbeat 响应体
{
    "tool_catalog_outdated": False,
    "backpressure": {
        # null 表示不限速；正整数表示每秒最多上报 N 条 INFO 日志
        "log_rate_limit": None   # 正常
        # "log_rate_limit": 5    # 积压时下发
    }
}
```

**规则**：
- 背压仅作用于 `stp:logs` Topic 的 INFO 级日志
- `stp:status` Topic 的状态消息**不受背压影响**，Agent 必须忽略对状态消息的限速
