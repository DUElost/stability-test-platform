# ADR-0018 实施计划：基础设施层框架引入

**关联决策**：[ADR-0018](adr/ADR-0018-infrastructure-layer-framework-adoption.md)  
**预估总工期**：8-10 工作日  
**创建日期**：2026-04-08

---

## 总览

```
Phase 0  准备工作              ▓░░░░░░░░░░░░░░░░  0.5 天
Phase 1  APScheduler 迁移      ░▓▓▓░░░░░░░░░░░░░  1.5 天
Phase 2  SAQ 迁移              ░░░░▓▓▓▓░░░░░░░░░  2   天
Phase 3  python-socketio 迁移  ░░░░░░░░▓▓▓▓▓░░░░  2.5 天
Phase 4  Redis 清理            ░░░░░░░░░░░░░▓░░░  0.5 天
Phase 5  可观测性落地          ░░░░░░░░░░░░░░▓▓░  1   天
Phase 6  验收与文档            ░░░░░░░░░░░░░░░░▓  0.5 天
```

---

## Phase 0：准备工作（0.5 天）

**目标**：建立基线，确保迁移过程可回滚。

### 0.1 创建特性分支

| 项 | 说明 |
|---|------|
| 分支 | `feature/adr-0018-infra-framework` |
| 基线 | 从当前 main 创建 |

### 0.2 安装依赖

**后端** — 追加到 `backend/requirements.txt`：

```
# ADR-0018: Infrastructure framework adoption
apscheduler>=4.0.0a5,<5.0
saq>=0.12.0,<1.0
python-socketio[asyncio]>=5.11.0,<6.0
```

**前端** — 追加到 `frontend/package.json`：

```
socket.io-client@^4.7.0
```

### 0.3 运行现有测试建立基线

```bash
pytest backend/tests/ -v --tb=short
```

记录通过数和失败数，作为迁移后回归对比基准。

### 0.4 确认环境变量

| 变量 | 当前值 | 迁移后是否保留 |
|------|--------|--------------|
| `ENABLE_CRON_SCHEDULER` | `1` | 废弃（APScheduler 始终启用） |
| `USE_SESSION_WATCHDOG` | `true` | 废弃（APScheduler 始终启用，legacy monitor 移除） |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | 保留（SAQ + 控制通道共用） |
| `CRON_POLL_INTERVAL` | `30` | 迁移到 APScheduler miss-fire grace |
| `WORKFLOW_RUN_RETENTION_DAYS` | `3` | 保留（清理 job 参数） |

---

## Phase 1：APScheduler 迁移（1.5 天）— P0

**目标**：用 APScheduler 4.x 替代 `cron_scheduler.py` 守护线程、`recycler.py` 守护线程、`session_watchdog.py` asyncio task。

**前置依赖**：Phase 0 完成

### 1.1 创建调度器初始化模块

**新建文件**：`backend/scheduler/app_scheduler.py`

```python
# 职责：初始化 AsyncScheduler，注册所有定时 job
# - CronTrigger: 从 TaskSchedule 表加载 cron 表达式
# - IntervalTrigger: recycler（60s）、session_watchdog（10s）、数据清理（1h）
# - DataStore: SQLAlchemyDataStore 复用现有 PostgreSQL async_engine
```

**关键设计**：
- `init_scheduler()` 返回 `AsyncScheduler` 实例，由 `main.py` lifespan 管理生命周期
- Recycler/Watchdog 回调直接执行业务逻辑（轻量任务，不经 SAQ）
- Cron dispatch 回调向 SAQ 入队（Phase 2 落地后接入，Phase 1 暂时保持同步调用）

### 1.2 提取 recycler 核心逻辑为纯函数

**修改文件**：`backend/scheduler/recycler.py`

| 修改前 | 修改后 |
|--------|--------|
| `start_recycler()` 启动 `while True` 守护线程 | 提取 `run_recycler_cycle()` 纯函数，包含一次完整的超时扫描 + 锁释放 + 聚合逻辑 |
| 线程内 `time.sleep(interval)` | 由 APScheduler `IntervalTrigger` 控制调用频率 |

保留：`run_recycler_cycle()` 内的全部业务逻辑不变（超时判定、`SessionLocal()` 获取 session、锁释放、聚合、deferred post-completion）。

### 1.3 提取 session_watchdog 核心逻辑为纯函数

**修改文件**：`backend/tasks/session_watchdog.py`

| 修改前 | 修改后 |
|--------|--------|
| `session_watchdog_loop()` 异步无限循环 | 提取 `run_watchdog_cycle()` async 函数，包含一次完整的心跳超时检测 + UNKNOWN 宽限 + PENDING_TOOL 超时 |
| `asyncio.sleep(interval)` | 由 APScheduler `IntervalTrigger` 控制调用频率 |

### 1.4 提取 cron_scheduler 核心逻辑为纯函数

**修改文件**：`backend/scheduler/cron_scheduler.py`

| 修改前 | 修改后 |
|--------|--------|
| `start_cron_scheduler()` 启动守护线程，内部轮询 `TaskSchedule` 表 | 提取 `check_and_fire_schedules()` 函数，由 APScheduler `IntervalTrigger`（30s）调用 |
| 线程内 `asyncio.run()` 调 dispatch | 直接 async 调用（APScheduler 原生 async） |
| retention cleanup 混在同一线程 | 拆为独立 job `run_retention_cleanup()`，`IntervalTrigger`（1h） |

### 1.5 重构 main.py lifespan

**修改文件**：`backend/main.py`

```python
# 修改前（现状）:
#   cron_thread = start_cron_scheduler()
#   recycler_thread = start_recycler()
#   watchdog_task = asyncio.create_task(session_watchdog_loop())
#   monitor_task = asyncio.create_task(heartbeat_monitor_loop())

# 修改后:
#   from backend.scheduler.app_scheduler import init_scheduler
#   scheduler = await init_scheduler()
#   await scheduler.start_in_background()
#   ...
#   yield
#   ...
#   await scheduler.stop()
```

移除：
- `start_cron_scheduler()` 调用
- `start_recycler()` 调用
- `session_watchdog_loop()` asyncio.create_task
- `heartbeat_monitor_loop()` asyncio.create_task 和 `USE_SESSION_WATCHDOG` 分支
- `ENABLE_CRON_SCHEDULER` 环境变量判断

### 1.6 删除 legacy 文件

**删除**：`backend/tasks/heartbeat_monitor.py`（与 session_watchdog 互斥的 legacy 路径，不再需要）

### 1.7 验证清单

- [ ] `pytest backend/tests/` 全部通过
- [ ] 启动后端，观察日志确认 APScheduler 注册的 job 列表
- [ ] 手动创建 `TaskSchedule` 记录，验证 cron 触发
- [ ] 将一个 JobInstance 置为 PENDING 超过超时阈值，验证 recycler 回收
- [ ] 停止 Agent 心跳，验证 watchdog 将 host 标记为 OFFLINE
- [ ] 停止后端并重启，验证 miss-fire 后 APScheduler 补偿执行

---

## Phase 2：SAQ 迁移（2 天）— P1

**目标**：用 SAQ 替代 `mq/consumer.py` 的后台任务处理，将 dispatch 扇出和 post-completion 异步化。

**前置依赖**：Phase 1 完成（APScheduler 已接管定时触发）

### 2.1 创建 SAQ 任务定义模块

**新建文件**：`backend/tasks/saq_tasks.py`

定义以下 SAQ 任务函数：

| 任务名 | 来源 | 触发方 |
|--------|------|--------|
| `dispatch_workflow_task` | `services/dispatcher.py::dispatch_workflow` | orchestration API / APScheduler cron |
| `post_completion_task` | `services/post_completion.py::run_post_completion` | agent_api.complete_job |
| `send_notification_task` | `services/notification_service.py::dispatch_notification` | post_completion / recycler deferred |
| `publish_control_command` | 新增 | orchestration API (abort/pause) |

### 2.2 创建 SAQ Queue 初始化

**新建文件**：`backend/tasks/saq_worker.py`

```python
# 职责：
# - 创建 SAQ Queue 实例（Redis URL 从环境变量读取）
# - 注册所有 task 函数
# - 提供 get_queue() 供 API 路由调用 enqueue
```

### 2.3 在 main.py lifespan 中启动 SAQ

**修改文件**：`backend/main.py`

```python
# 在 lifespan 中追加：
#   from backend.tasks.saq_worker import start_saq_worker, stop_saq_worker
#   await start_saq_worker()
#   ...
#   yield
#   ...
#   await stop_saq_worker()
```

### 2.4 迁移 dispatch 扇出为异步

**修改文件**：`backend/api/routes/orchestration.py`

```python
# 修改前:
#   run = await dispatch_workflow(wf_def_id, device_ids, ...)

# 修改后:
#   from backend.tasks.saq_worker import get_queue
#   job = await get_queue().enqueue(
#       "dispatch_workflow_task",
#       workflow_def_id=wf_def_id,
#       device_ids=device_ids,
#       ...
#   )
#   return ok({"workflow_run_id": "pending", "saq_job_id": job.id})
```

**注意**：dispatch 异步化后，API 立即返回，前端需通过轮询或 SocketIO 获知 WorkflowRun 创建结果。如果产品上需要同步返回 `workflow_run_id`，可保留 dispatch 同步执行，仅将 post-completion 异步化。此处需评审确认。

### 2.5 迁移 post-completion 为异步

**修改文件**：`backend/api/routes/agent_api.py`

```python
# 修改前（agent_api.py:519）:
#   from backend.services.post_completion import run_post_completion_async
#   run_post_completion_async(job_id)

# 修改后:
#   from backend.tasks.saq_worker import get_queue
#   await get_queue().enqueue("post_completion_task", job_id=job_id)
```

**同步修改**：`backend/scheduler/recycler.py` 中的 `_fill_deferred_post_completions` 也改为 SAQ enqueue。

### 2.6 迁移控制指令发布

**修改文件**：`backend/api/routes/orchestration.py`（abort workflow 等）

```python
# 修改前：直接写 Redis Streams
# 修改后：
#   await get_queue().enqueue("publish_control_command", host_id=..., command="abort", ...)
```

### 2.7 移除 consume_status_stream

**修改文件**：`backend/main.py`

移除：
- `mq_consumer_task = asyncio.create_task(consume_status_stream(redis_client))`
- `mq_bp_task = asyncio.create_task(monitor_backpressure(redis_client))`

`consume_log_stream` 在 Phase 3 迁移，此阶段暂保留。

### 2.8 验证清单

- [ ] `pytest backend/tests/` 全部通过
- [ ] 在前端触发 workflow dispatch，确认 JobInstance 正确创建
- [ ] Agent 完成 job 后，确认 report_json / jira_draft_json 被 SAQ 异步填充
- [ ] SAQ Web UI（如启用）可查看任务队列状态
- [ ] 模拟 SAQ task 失败，确认 retry 和 DLQ 行为
- [ ] post-completion 幂等性：同一 job_id 多次 enqueue，`post_processed_at` 只写一次

---

## Phase 3：python-socketio 迁移（2.5 天）— P1

**目标**：用 python-socketio 替代自研 `ConnectionManager`，实现 rooms/namespace/断线重连；日志流从 Redis Streams 迁移到 SocketIO 直推。

**前置依赖**：Phase 1 完成（Phase 2 可并行，但建议串行以减少风险）

### 3.1 创建 SocketIO 服务端模块

**新建文件**：`backend/realtime/socketio_server.py`

```python
# 职责：
# - 创建 socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=...)
# - 定义 namespace /agent：接收 Agent emit 的日志、步骤状态
# - 定义 namespace /dashboard：向前端推送实时数据
# - Agent 认证：on_connect 验证 X-Agent-Secret
# - 前端认证：on_connect 验证 JWT token
# - 日志持久化：on step_log 事件 → 广播到 room + 异步写文件
```

### 3.2 挂载 SocketIO 到 FastAPI

**修改文件**：`backend/main.py`

```python
# 追加：
#   from backend.realtime.socketio_server import create_sio_app
#   sio_app = create_sio_app()
#   app.mount("/sio", sio_app)  # SocketIO ASGI app
```

### 3.3 创建服务端日志持久化

**新建文件**：`backend/realtime/log_writer.py`

```python
# 职责：
# - 异步将 SocketIO 收到的日志行追加到文件
# - 文件路径：{LOG_BASE_DIR}/jobs/{job_id}/console.log
# - 使用 asyncio 文件 I/O 或 aiofiles，不阻塞事件循环
# - 不做 DB 写入
```

### 3.4 迁移后端广播调用

**修改文件**（全部替换 `manager.broadcast` 调用）：

| 文件 | 修改 |
|------|------|
| `backend/api/routes/heartbeat.py` | `manager.broadcast("/ws/dashboard", ...)` → `sio.emit("device_update", ..., namespace="/dashboard")` |
| `backend/scheduler/recycler.py` | `schedule_broadcast(...)` → `sio.emit(...)` |
| `backend/mq/consumer.py`（`_process_log_message` 部分） | 整段移除（日志不再经过 Redis） |

### 3.5 迁移 Agent ws_client

**修改文件**：`backend/agent/ws_client.py`

| 修改前 | 修改后 |
|--------|--------|
| 原生 `websocket.WebSocketApp` | `socketio.AsyncClient` 或 `socketio.Client`（Agent 是同步线程模型，用 `Client`） |
| 手动重连逻辑 | SocketIO 内置自动重连 |
| `send_log(run_id, line)` | `sio.emit("step_log", {"job_id": ..., "line": ...}, namespace="/agent")` |

### 3.6 迁移前端 WebSocket hook

**修改文件**：`frontend/src/hooks/useWebSocket.ts`

| 修改前 | 修改后 |
|--------|--------|
| `new WebSocket(url)` | `io(url, { path: "/sio/socket.io" })` |
| 手动 JSON parse | SocketIO 自动序列化 |
| 手动重连 setTimeout | SocketIO 内置 reconnection |

**新建文件**：`frontend/src/hooks/useSocketIO.ts`（如果选择新建而非原地重构）

同步修改所有使用 `useWebSocket` 的页面组件。

### 3.7 移除旧 WebSocket 和 Redis 日志消费

**修改文件**：`backend/main.py`

```python
# 移除：
#   mq_log_task = asyncio.create_task(consume_log_stream(redis_client))
#   capture_main_loop()
```

**修改文件**：`backend/api/routes/websocket.py`

- 移除 `ConnectionManager` 类
- 移除 `manager` 全局实例
- 移除 `/ws/agent/{host_id}` 和 `/ws/logs/{run_id}` 原生 WebSocket 端点
- 保留 router 如果有非 WS 端点，否则整个文件可标记为 deprecated

### 3.8 验证清单

- [ ] `pytest backend/tests/` 全部通过
- [ ] 前端打开 Dashboard，设备状态实时刷新
- [ ] 前端打开 Job 详情，Agent 执行中的日志实时滚动
- [ ] 关闭前端再打开，确认日志文件已持久化可回溯
- [ ] Agent 断网 30s 后恢复，确认 SocketIO 自动重连
- [ ] 多个前端同时订阅不同 Job，确认 rooms 隔离正确

---

## Phase 4：Redis 清理（0.5 天）— P2

**目标**：移除已被 SAQ + SocketIO 替代的 Redis Streams 遗留代码。

**前置依赖**：Phase 2 + Phase 3 全部完成

### 4.1 移除 Stream Group 创建

**修改文件**：`backend/main.py`

```python
# 移除：
#   _STREAM_GROUPS = [...]
#   for stream, group in _STREAM_GROUPS:
#       await redis_client.xgroup_create(...)
```

### 4.2 移除 Agent MQ Producer

**删除文件**：`backend/agent/mq/producer.py`

**修改文件**：`backend/agent/main.py`

```python
# 移除：
#   from .mq.producer import MQProducer
#   mq_producer = MQProducer(redis_url, host_id, local_db=local_db)
#   mq_producer.close()
# 以及所有传递 mq_producer 参数的调用
```

### 4.3 清理 Consumer 模块

**删除或归档**：`backend/mq/consumer.py`（所有消费函数已被替代）

### 4.4 精简 Redis 连接用途

**修改文件**：`backend/main.py`

Redis 连接保留，但注释明确其职责：

```python
# Redis 用途：
# 1. SAQ broker（任务队列）
# 2. stp:control Pub/Sub（控制指令下发）
# 3. python-socketio Redis adapter（多进程消息同步，远期）
```

### 4.5 验证清单

- [ ] `pytest backend/tests/` 全部通过
- [ ] `rg "stp:status\|stp:logs\|consume_status\|consume_log\|MQProducer" backend/` 无残留引用
- [ ] Agent 端移除 `mq_producer` 后正常运行（心跳、任务执行、日志推送）
- [ ] Redis 中不再出现 `stp:status` 和 `stp:logs` stream

---

## Phase 5：可观测性落地（1 天）— P2

**目标**：落地 ADR-0011 第一层，利用 Phase 1-4 引入的框架能力。

**前置依赖**：Phase 1 完成（其余可渐进）

### 5.1 暴露 Prometheus metrics 端点

**修改文件**：`backend/api/routes/metrics.py`

确认 `/metrics` 端点正确暴露所有 Counter/Histogram（`backend/core/metrics.py` 已定义）。如果当前是 JSON 格式，需改为 Prometheus text format。

### 5.2 新增框架级指标

| 指标名 | 类型 | 来源 |
|--------|------|------|
| `saq_tasks_total` | Counter | SAQ 任务完成数（by task_name, status） |
| `saq_task_duration_seconds` | Histogram | SAQ 任务执行时长 |
| `saq_queue_depth` | Gauge | SAQ 当前队列深度 |
| `socketio_connections_active` | Gauge | 当前活跃 SocketIO 连接数（by namespace） |
| `apscheduler_job_runs_total` | Counter | APScheduler job 执行次数（by job_name, outcome） |

### 5.3 创建 Grafana Dashboard 模板

**新建文件**：`docs/grafana/stability-platform-dashboard.json`

包含以下面板：
- Host 在线数与心跳超时数
- Job 状态分布（PENDING/RUNNING/COMPLETED/FAILED）
- SAQ 队列深度与任务吞吐量
- SocketIO 活跃连接数
- APScheduler job 执行频率与失败率

### 5.4 验证清单

- [ ] `curl http://localhost:8000/metrics` 返回 Prometheus text format
- [ ] Grafana 导入 dashboard 模板后数据正常展示

---

## Phase 6：验收与文档（0.5 天）

### 6.1 回归测试

```bash
pytest backend/tests/ -v --tb=short
```

对比 Phase 0.3 基线，确认无回归。

### 6.2 集成验证场景

| 场景 | 预期 |
|------|------|
| 创建 Workflow → Dispatch → Agent claim → 执行 → 完成 | 全链路正常，报告和 JIRA 草稿由 SAQ 异步生成 |
| Agent 执行中断网 30s → 恢复 | SocketIO 自动重连，日志不中断 |
| 后端进程重启 | APScheduler miss-fire 补偿，SAQ 未完成任务恢复 |
| 前端打开 Job 详情 → 查看历史日志 | 从文件系统读取持久化日志 |
| Cron 定时触发 | APScheduler 按表达式触发 dispatch |

### 6.3 更新文档

| 文档 | 更新内容 |
|------|---------|
| `CLAUDE.md` | 更新启动方式、移除 MQ 相关说明、更新环境变量表 |
| `ADR-0018` | 状态改为 `Accepted`，补录接受日期 |
| `ADR-0002` | 如果所有后台线程/异步任务已迁移完毕，状态改为 `Superseded by ADR-0018` |
| `ADR-0011` | 第一层已落地，更新状态为 `Accepted`（部分） |
| `module-responsibilities.md` | 版本号更新，移除 `[计划中]` 中已实现的部分 |

### 6.4 废弃代码清理确认

| 文件 | 处置 |
|------|------|
| `backend/tasks/heartbeat_monitor.py` | Phase 1 删除 |
| `backend/mq/consumer.py` | Phase 4 删除 |
| `backend/agent/mq/producer.py` | Phase 4 删除 |
| `backend/scheduler/dispatcher.py`（legacy） | 确认不再引用后删除 |
| `backend/api/routes/websocket.py` ConnectionManager | Phase 3 移除 |

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| APScheduler 4.x 仍为 alpha | 生产稳定性未充分验证 | Phase 1 验证后评估；降级方案为退回 3.x（无 async-native，需 wrapper） |
| SAQ dispatch 异步化后前端无法立即获得 workflow_run_id | 用户体验变化 | 评审确认：可选择保留 dispatch 同步 + 仅 post-completion 异步 |
| SocketIO 与现有原生 WebSocket 端点并行期间增加复杂度 | 迁移期 bug | Phase 3 内尽快完成全量迁移，避免长期并行 |
| Agent 侧 SocketIO Client 与同步线程模型的兼容性 | Agent 用 threading，SocketIO Client 有 async/sync 两种 | 使用 `socketio.Client`（同步版本），适配现有 Agent 线程模型 |

---

## 决策待确认项

以下设计选择需在实施前评审确认：

1. **Dispatch 是否异步化？**
   - 方案 A：dispatch 保持同步（API 直接调 `dispatch_workflow`），仅 post-completion 走 SAQ
   - 方案 B：dispatch 也走 SAQ 异步（API 立即返回 "pending"，前端轮询/SocketIO 获取结果）
   - **建议**：Phase 2 先用方案 A，后续按产品需求升级到 B

2. **SocketIO mount path？**
   - 方案 A：`/sio`（独立 ASGI app mount）
   - 方案 B：`/socket.io`（SocketIO 默认 path，client 无需配置）
   - **建议**：方案 B，减少 client 配置

3. **Agent SocketIO Client 同步 vs 异步？**
   - 方案 A：`socketio.Client`（同步，适配现有 threading 模型）
   - 方案 B：`socketio.AsyncClient`（需在 Agent 中引入 asyncio 事件循环）
   - **建议**：方案 A，不改动 Agent 线程模型
