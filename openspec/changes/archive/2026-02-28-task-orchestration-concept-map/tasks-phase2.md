# Phase 2 实施计划：Agent MQ 层

**Change**: task-orchestration-concept-map / Phase 2
**Date**: 2026-02-27
**Status**: Complete
**实施完成时间**: 2026-02-27
**前置条件**: Phase 1 完成（数据模型 + Redis + JobStateMachine + Alembic 迁移）

---

## 总览

```
Task 1: mq/producer.py       — Redis Stream 生产者 (stp:status + stp:logs)   [完成]
Task 2: mq/control_listener.py — stp:control 背压/工具更新监听器             [完成]
Task 3: registry/local_db.py  — SQLite WAL StepTrace 缓存                   [完成]
Task 4: registry/tool_registry.py — Tool_ID → 本地路径 解析器               [完成]
Task 5: pipeline_engine.py    — stages 格式 + tool:<id> action 支持          [完成]
Task 6: main.py               — 新组件初始化 + 串联                          [完成]
```

依赖顺序：Task 3 → Task 1（LocalDB 注入 MQProducer）→ Task 4 → Task 2 → Task 5 → Task 6

---

## Task 1: mq/producer.py

**文件**: `backend/agent/mq/producer.py`（新建）

### 实现摘要

- `MQProducer(redis_url, host_id, local_db=None)`
- 连接失败时优雅降级（`connected=False`，所有 send 方法返回 None）
- `send_job_status(job_id, status, reason)` → `stp:status`，不限速
- `send_step_trace(job_id, step_id, stage, event_type, ...)` → 先写 SQLite → XADD stp:status → 成功后 mark_acked
- `send_log(job_id, device_id, level, tag, message)` → `stp:logs`，受 log_rate_limit 控制
- `set_log_rate_limit(limit)` → 1 秒滑动窗口限速，None=无限制
- 线程安全：`threading.Lock` 保护 Redis 调用

### 流控 (stp:logs)

```python
maxlen=500_000  # approximate MAXLEN
rate_limit: 每秒最多 N 条（None=不限）
```

---

## Task 2: mq/control_listener.py

**文件**: `backend/agent/mq/control_listener.py`（新建）

### 实现摘要

- `ControlListener(redis_url, host_id, mq_producer, tool_registry=None)`
- Consumer Group: `agent-consumer`，从 `$`（当前时刻）开始消费
- 后台 daemon 线程，每次 `XREADGROUP` 阻塞 2 秒
- `command=backpressure` → `mq_producer.set_log_rate_limit(limit)`
- `command=tool_update` → 独立 daemon 线程调用 `tool_registry.pull_tool_sync()`
- 所有消息处理完后 XACK
- `start()` / `stop()` 线程安全

---

## Task 3: registry/local_db.py

**文件**: `backend/agent/registry/local_db.py`（新建）

### 表结构

```sql
step_trace_cache (id, job_id, step_id, stage, event_type, status, output, error_message, original_ts, acked)
  UNIQUE(job_id, step_id, event_type)

tool_cache (tool_id PK, version, script_path, script_class, updated_at)

agent_state (key PK, value)
```

### WAL 配置

```python
PRAGMA journal_mode=WAL
PRAGMA synchronous=FULL
```

所有写操作在 `threading.Lock` + `with self.conn:` 事务内完成。

---

## Task 4: registry/tool_registry.py

**文件**: `backend/agent/registry/tool_registry.py`（新建）

### 实现摘要

- `initialize()` → `GET /api/v1/tools` 全量拉取 → 失败时 load_from_sqlite
- `resolve(tool_id, version)` → `ToolEntry`，版本不匹配抛 `ToolVersionMismatch`
- `pull_tool_sync(tool_id, version)` → `GET /api/v1/tools/{id}`，3 次指数退避重试
- `version` 属性 → 目录 hash（MD5 前 12 位），用于心跳上报

### 异常类型

```python
ToolNotFoundLocally(tool_id)    # 本地缓存无此 tool_id
ToolVersionMismatch(tool_id, cached, required)  # 版本不一致
```

---

## Task 5: pipeline_engine.py 修改

**文件**: `backend/agent/pipeline_engine.py`（修改）

### 变更清单

1. **新增 import**: `import importlib.util`, `import os`（补缺失导入）
2. **`__init__` 新增参数**: `mq_producer=None`, `tool_registry=None`
3. **`execute()` 格式检测**: `if "stages" in pipeline_def` → `_execute_stages_format()`
4. **`_resolve_action()` 修正**: `tool:` action 返回清晰错误（旧格式不支持 ToolRegistry）
5. **删除 `_run_tool_action()` 死代码**
6. **新增方法**（全部为 stages 格式专用）:
   - `_execute_stages_format()` — prepare/execute/post_process 串行执行
   - `_run_step_with_retry_stages()` — 指数退避重试
   - `_execute_step_stages()` — 单步执行，上报 STARTED/COMPLETED/FAILED via MQ
   - `_resolve_action_stages()` — builtin: + tool: 解析（无 shell:）
   - `_run_tool_action_stages()` — ToolRegistry 解析 + PENDING_TOOL 处理
   - `_execute_tool_script()` — importlib 动态加载 Tool 脚本
   - `_report_step_trace_mq()` — MQ 优先，WS fallback
   - `_report_job_status_mq()` — MQ 优先，WS fallback
   - `_make_mq_logger()` — 创建 `_MQStepLogger` 实例
7. **新增 `_MQStepLogger` 类**: 写 stp:logs via MQ + 本地文件

### 向下兼容

旧 `phases` 格式完全不变，56 个现有测试全部通过。

---

## Task 6: main.py 修改

**文件**: `backend/agent/main.py`（修改）

### 新增初始化序列（在 ws_client.start_reconnect_loop() 之后）

```python
local_db = LocalDB()
local_db.initialize(str(BASE_DIR / "agent_state.db"))

tool_registry = ToolRegistry(local_db, api_url, agent_secret)
tool_registry.initialize()

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
mq_producer = MQProducer(redis_url, str(host_id), local_db=local_db)

control_listener = ControlListener(redis_url, str(host_id), mq_producer, tool_registry)
control_listener.start()
```

### 清理序列（finally 块）

```python
control_listener.stop()
mq_producer.close()
local_db.close()
ws_client.disconnect()
```

### 其他变更

- `_run_task_wrapper(...)` 新增 `mq_producer=None, tool_registry=None` 参数
- `_execute_pipeline_run(...)` 新增 `mq_producer=None, tool_registry=None` 参数，透传给 `PipelineEngine`
- `pipeline_def` 合法性检查兼容 `stages` 格式

---

## 验收标准

Phase 2 完成当且仅当：

- [x] `backend/agent/mq/producer.py` 存在，MQProducer 无 Redis 时优雅降级
- [x] `backend/agent/mq/control_listener.py` 存在，处理 backpressure + tool_update
- [x] `backend/agent/registry/local_db.py` 存在，WAL 模式，线程安全写
- [x] `backend/agent/registry/tool_registry.py` 存在，resolve/pull_tool 正确
- [x] `pipeline_engine.py` stages 格式能正确执行（prepare→execute→post_process）
- [x] `pipeline_engine.py` tool:<id> action 正确处理 ToolVersionMismatch → PENDING_TOOL
- [x] `main.py` 启动时初始化全部组件，退出时清理
- [x] 所有 56 个现有 agent 测试通过（pre-existing JSONB test 失败为 Phase 1 遗留问题）

---

## 遗留项（Phase 3 范围）

- 心跳响应中解析 `tool_catalog_outdated` 字段并触发 `tool_registry.initialize()`
- `last_ack_id` 基于心跳响应更新（目前为 Redis 写成功即 acked）
- Reconciler (`reconciler.py`): 重连时 replay SQLite 中 unacked traces
- Backend Server 侧 Redis Stream 消费者（`stp:status` → DB StepTrace 写入）
- Backend 背压监控（lag > 5000 → 下发 stp:control backpressure 指令）
