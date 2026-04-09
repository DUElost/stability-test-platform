# ADR-0018 实施计划：基础设施层框架引入

**关联决策**：[ADR-0018](adr/ADR-0018-infrastructure-layer-framework-adoption.md)  
**预估总工期**：10-12 工作日  
**创建日期**：2026-04-08

---

## 总览

```
Phase 0    准备工作                            0.5 天
Phase 1    APScheduler 迁移                    1.5 天
Phase 2    SAQ 迁移 (dispatch 同步, PC 异步)    1.5 天
Phase 3    python-socketio 迁移                2.5 天
Phase 3.7  Agent 状态上报迁移                   1.5 天
Phase 4    Redis Streams 全面清理               1   天
Phase 5    可观测性落地                         1   天
Phase 6    验收与文档                           0.5 天
```

独立修复项（可与迁移并行，不计入主线工期）：PENDING_TOOL 状态机修复（约 0.5 天）。

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

## Phase 2：SAQ 迁移（1.5 天）— P1

**目标**：用 SAQ 接管 post-completion 异步处理和控制指令发布。Dispatch 保持同步（前端依赖同步返回 `workflow_run_id`）。MQ consumer 暂保留，延迟到 Phase 4 统一清理。

**前置依赖**：Phase 1 完成（APScheduler 已接管定时触发）

### 2.1 创建 SAQ 任务定义模块

**新建文件**：`backend/tasks/saq_tasks.py`

定义以下 SAQ 任务函数：

| 任务名 | 来源 | 触发方 |
|--------|------|--------|
| `post_completion_task` | `services/post_completion.py::run_post_completion` | agent_api.complete_job / consumer.py 终态补偿 |
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

### 2.4 Dispatch 保持同步（已决策）

Dispatch 保持 `await dispatch_workflow(...)` 同步调用不变，前端依赖同步返回 `workflow_run_id`。不做异步化改造。

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

### 2.7 暂保留 MQ Consumer（延迟到 Phase 4）

`consume_status_stream` 和 `consume_log_stream` 在此阶段**不移除**。Agent 尚未完成 step_trace HTTP 迁移（Phase 3.7），MQ consumer 仍是 step_trace 持久化的唯一路径。延迟到 Phase 4 统一清理。

### 2.8 迁移 consumer.py 中的 post-completion 触发

**修改文件**：`backend/mq/consumer.py`

`_persist_job_status` 在终态分支中直接调用 `run_post_completion_async(job_id)` —— 将此调用改为 SAQ enqueue，使 post-completion 触发统一收口到 SAQ：

```python
# 修改前:
#   run_post_completion_async(job_id)

# 修改后:
#   from backend.tasks.saq_worker import get_queue
#   await get_queue().enqueue("post_completion_task", job_id=job_id)
```

这确保 `agent_api.complete_job` 和 MQ consumer 终态补偿两条路径都经过 SAQ 去重。

### 2.9 验证清单

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

使用 python-socketio 官方推荐的 `ASGIApp` 包装模式，将 SocketIO 和现有 FastAPI 应用组合为单一 ASGI 应用。**不要**用 `app.mount("/", sio_app)`——这会将根路径流量整体交给子应用，吞掉现有 FastAPI 路由。

```python
# 修改前：
#   app = FastAPI(...)

# 修改后：
#   from backend.realtime.socketio_server import create_sio_server
#   sio = create_sio_server()
#   combined_app = socketio.ASGIApp(sio, app)  # sio 拦截 /socket.io 路径，其余透传给 FastAPI
#   # uvicorn 启动时使用 combined_app 代替 app
```

`socketio.ASGIApp` 会拦截 `/socket.io` 路径的请求交给 SocketIO 处理，其余所有路径透传给内部的 FastAPI 应用，两者共存互不干扰。

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
| `new WebSocket(url)` | `io(url)` — 使用默认 `/socket.io` 路径，无需额外配置 |
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

## Phase 3.7：Agent 状态上报迁移（1.5 天）— P1

**目标**：将 Agent 的 step_trace 持久化从 MQ 迁移到 HTTP 批量上报，修复 WS fallback 断裂，使中间信息状态首次通过 SocketIO 生效。这是 Phase 4 移除 MQ 的前提条件。

**前置依赖**：Phase 3 完成（Agent SocketIO 迁移已落地）

### 3.7.1 step_trace 持久化迁移（高优先级）

当前 step_trace 唯一有效持久化路径是 MQ `stp:status` → `_persist_step_trace` → DB。Agent 侧 `LocalDB.get_unacked_traces()` 已定义但全仓库无调用点，不存在现成的 HTTP replay 机制。

采用 **HTTP 批量上报方案**（遵循 ADR-0018 双通道原则：HTTP 权威写入，SocketIO 仅展示）。

**为何不选 SocketIO handler 写 DB**：ADR-0018 明确声明"HTTP 是权威写入路径，SocketIO 是展示路径。禁止 SocketIO handler 直接推进状态机。" `module-responsibilities.md` 进一步要求"需要幂等性保障的操作，必须走 HTTP API"。`reconcile_step_traces` 虽不推进状态机，但属于需要幂等性保障的持久化操作，按项目原则必须走 HTTP。

#### StepTraceUploader 模块

**新建文件**：`backend/agent/step_trace_uploader.py`

设计模式复刻 `OutboxDrainThread`（`main.py` 中的终态重试线程）——同样的 `start()` / `stop()` / `drain_sync()` 接口、daemon 线程、`_stop_event.wait(interval)` 循环。

```python
class StepTraceUploader:
    _BATCH_LIMIT = 100

    def __init__(self, api_url: str, local_db: LocalDB,
                 agent_secret: str = "", interval: float = 5.0):
        self._api_url = api_url
        self._local_db = local_db
        self._agent_secret = agent_secret
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_id = 0

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def drain_sync(self) -> int:
        """Blocking drain for shutdown — 循环直到无剩余数据或出错。"""
        total = 0
        while True:
            try:
                n = self._upload_once()
            except Exception:
                logger.exception("step_trace_drain_error")
                break
            if n == 0:
                break
            total += n
        return total

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._interval)
            if self._stop_event.is_set():
                break
            try:
                self._upload_once()
            except Exception:
                logger.exception("step_trace_upload_error")

    def _upload_once(self) -> int:
        traces = self._local_db.get_unacked_traces(after_id=self._last_id)
        if not traces:
            return 0
        batch = traces[:self._BATCH_LIMIT]
        headers = {"X-Agent-Secret": self._agent_secret} if self._agent_secret else {}
        resp = requests.post(
            f"{self._api_url}/api/v1/agent/steps",
            json=[_to_step_trace_in(t) for t in batch],
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        for t in batch:
            self._local_db.mark_acked(t["id"])
            self._last_id = max(self._last_id, t["id"])
        return len(batch)
```

**约束说明**：

- `drain_sync()` 循环调用 `_upload_once()` 直到返回 0 或抛异常，确保 shutdown 时不遗留未上报的 trace
- HTTP 请求的 `X-Agent-Secret` header 构造对齐 `OutboxDrainThread._drain_once()`：有值则带上，空则不带
- 每次上传上限 `_BATCH_LIMIT=100`，避免单次请求体过大；`drain_sync` 的循环保证大量积压时也能全部 flush

**在 `main.py` 中的接入位置**（OutboxDrainThread 启动之后）：

```python
outbox_drain = OutboxDrainThread(api_url, local_db, interval=15.0)
outbox_drain.start()

step_trace_uploader = StepTraceUploader(api_url, local_db, agent_secret, interval=5.0)
step_trace_uploader.start()
```

**shutdown 序列**（在 `executor.shutdown(wait=True)` 之后、`mq_producer.close()` 之前）：

```python
try:
    flushed = step_trace_uploader.drain_sync()
    if flushed:
        logger.info("shutdown_step_trace_flushed count=%d", flushed)
except Exception:
    logger.exception("shutdown_step_trace_flush_failed")
step_trace_uploader.stop()
```

#### MQProducer.send_step_trace() 两阶段迁移

当前 `send_step_trace()` 在 XADD 成功后立即 `mark_acked(trace_id)`，被标记 `acked=1` 的记录不会再被 `get_unacked_traces()` 读到。因此 Redis 正常时 Uploader 看不到任何数据。

**Phase A（MQ 主路径 + HTTP 补传兜底，本阶段落地时）**：

- `MQProducer.send_step_trace()` **不改动**：仍然 `save_step_trace` → `XADD` → `mark_acked`
- `StepTraceUploader` 同时运行：读取 `acked=0` 的记录 → HTTP POST → `mark_acked`
- 实际效果：
  - Redis 正常时：MQ XADD 成功 → `mark_acked` → Uploader 无事可做
  - Redis 异常时：XADD 失败 → `mark_acked` 不执行 → 记录留在 `acked=0` → Uploader 补传到 HTTP → `mark_acked`
- 此阶段不能验证 HTTP 链路在正常流量下的表现，仅验证补传兜底能力
- 服务端 `reconcile_step_traces` 的 `ON CONFLICT DO NOTHING` 保证 MQ 与 HTTP 偶发重叠时幂等

**Phase B（MQ 切除，Phase 4 执行时）**：

修改 `MQProducer.send_step_trace()`：

```python
def send_step_trace(self, job_id, step_id, stage, event_type, status,
                    output=None, error_message=None) -> Optional[int]:
    """Save step_trace to SQLite WAL only. HTTP upload delegated to StepTraceUploader."""
    ts = _utcnow()
    if self._local_db is not None:
        try:
            from datetime import datetime as _dt
            return self._local_db.save_step_trace(
                job_id=job_id, step_id=step_id, stage=stage,
                event_type=event_type, status=status,
                output=output, error_message=error_message,
                original_ts=_dt.fromisoformat(ts.replace("Z", "+00:00")),
            )
        except Exception as e:
            logger.warning("SQLite save_step_trace failed: %s", e)
    return None
    # 移除：_xadd_status(msg)
    # 移除：mark_acked（由 StepTraceUploader 成为唯一 ack owner）
```

### 3.7.2 PENDING_TOOL — 暂不处理

当前 `PENDING_TOOL` 在所有路径中均为死代码（`VALID_TRANSITIONS` 无 `RUNNING→PENDING_TOOL` 边），Agent 报告后立即返回 FAIL。迁移过程中保持原样不动：

- 不修改状态机
- 不修改 Agent 的 `_report_job_status_mq("PENDING_TOOL", ...)` 调用（迁移后此调用改走 SocketIO emit，服务端同样不做 DB 写入，行为不变）
- 迁移完成后作为独立清理项决策保留或移除

### 3.7.3 中间信息状态首次生效

- `INIT_RUNNING` / `PATROL_RUNNING` / `TEARDOWN_RUNNING` 当前在所有路径中均被丢弃
- 迁移到 SocketIO 后，Agent emit 这些状态 → SocketIO server 广播到 dashboard namespace（仅展示，不写 DB）
- 这是一个**功能增强**而非回归风险，迁移后首次实现中间状态的前端展示

### 3.7.4 终态 job_status 确认无需改动

- Agent 已使用 HTTP `complete_job` 作为终态主路径
- MQ 终态补偿路径随 Phase 4 移除，`complete_job` HTTP 端点已包含完整的锁释放、快照、聚合、post-completion 逻辑
- 无需额外迁移工作

### 3.7.5 修复当前 WS fallback 的断裂

- 现有 `_report_step_trace_mq` / `_report_job_status_mq` 的 WS fallback 因 message type 不匹配而无效
- SocketIO 迁移后，Agent 使用 SocketIO emit 替代原始 WS send，server handler 显式处理所有事件类型
- 此修复是迁移的自然副产物，不需要额外工作

### 3.7.6 验证清单

- [ ] StepTraceUploader 单元测试：mock `get_unacked_traces` 返回数据 → 验证 HTTP POST 被调用 → 验证 `mark_acked` 被调用
- [ ] Phase A 集成测试：Agent 执行 job → step_trace 通过 MQ 入库 → 手动断开 Redis → 新的 step_trace 由 Uploader 补传入库
- [ ] `drain_sync` 测试：积压 200+ traces → shutdown → 验证全部 flush（分多批）
- [ ] 前端打开 Job 详情，验证 INIT_RUNNING / PATROL_RUNNING 等中间状态通过 SocketIO 实时展示
- [ ] PENDING_TOOL 行为不变：Agent tool pull 失败 → 报告 PENDING_TOOL → 立即 FAIL（迁移前后行为一致）

---

## Phase 4：Redis Streams 全面清理（1 天）— P2

**目标**：移除已被 SAQ + SocketIO + HTTP 替代的 Redis Streams 遗留代码，包括 MQ Producer/Consumer、ControlListener、以及所有 Redis Streams 读取点。

**前置依赖**：Phase 2 + Phase 3 + Phase 3.7 全部完成（Agent 已完全切换到 SocketIO + HTTP）

### 4.1 ControlListener 迁移

**修改文件**：`backend/agent/mq/control_listener.py`

将 `stp:control` stream 的控制指令（backpressure / tool_update / abort 等）迁移到 SocketIO event 或独立 Redis Pub/Sub（与 Streams 无关）。

### 4.2 Redis 读取点清理

| 读取点 | 当前用途 | 迁移方案 |
|--------|---------|---------|
| `agent_api.py._get_backpressure` 读取 `stp:backpressure:log_rate_limit` key | 背压控制 | 改为 SAQ 或 SocketIO 内部状态 |
| `tasks.py` / `websocket.py` 的 `xrevrange("stp:logs")` | 历史日志查询 | 改为从文件系统读取（Phase 3.3 的 `log_writer.py` 已落地） |

### 4.3 移除 consume_status_stream / consume_log_stream

**修改文件**：`backend/main.py`

移除：
- `mq_consumer_task = asyncio.create_task(consume_status_stream(redis_client))`
- `mq_log_task = asyncio.create_task(consume_log_stream(redis_client))`
- `mq_bp_task = asyncio.create_task(monitor_backpressure(redis_client))`

此时 Agent 已完全切换到 SocketIO + HTTP，MQ consumer 不再有消费者依赖。

### 4.4 移除 Stream Group 创建

**修改文件**：`backend/main.py`

```python
# 移除：
#   _STREAM_GROUPS = [...]
#   for stream, group in _STREAM_GROUPS:
#       await redis_client.xgroup_create(...)
```

### 4.5 移除 Agent MQ Producer（执行 Phase B 切除）

**修改文件**：`backend/agent/mq/producer.py`

按 Phase 3.7.1 Phase B 方案修改 `send_step_trace()`，移除 XADD 和 `mark_acked` 调用。`StepTraceUploader` 成为唯一 ack owner。

**修改文件**：`backend/agent/main.py`

```python
# 移除：
#   from .mq.producer import MQProducer
#   mq_producer = MQProducer(redis_url, host_id, local_db=local_db)
#   mq_producer.close()
# 以及所有传递 mq_producer 参数的调用
```

### 4.6 清理 Consumer 模块

**删除或归档**：`backend/mq/consumer.py`（所有消费函数已被 Phase 2 SAQ + Phase 3 SocketIO + Phase 3.7 HTTP 替代）

### 4.7 精简 Redis 连接用途

**修改文件**：`backend/main.py`

Redis 连接保留，但注释明确其职责：

```python
# Redis 用途：
# 1. SAQ broker（任务队列）
# 2. python-socketio Redis adapter（多进程消息同步，远期）
# 注：stp:control Pub/Sub 已迁移到 SocketIO event（Phase 4.1）
```

### 4.8 验证清单

- [ ] `pytest backend/tests/` 全部通过
- [ ] `rg "stp:status|stp:logs|consume_status|consume_log|MQProducer|stp:control" backend/` 无残留引用
- [ ] Agent 端移除 `mq_producer` 后正常运行（心跳、任务执行、日志推送）
- [ ] step_trace 仅通过 HTTP 批量上报入库，MQ 路径已不存在
- [ ] Redis 中不再出现 `stp:status` 和 `stp:logs` stream
- [ ] 控制指令（abort/pause）通过 SocketIO 或 Pub/Sub 正常下发

---

## Phase 5：可观测性落地（1 天）— P2

**目标**：落地 ADR-0011 第一层，利用 Phase 1-4 引入的框架能力。

**前置依赖**：Phase 1 完成（其余可渐进）

### 5.1 暴露 Prometheus metrics 端点

**修改文件**：`backend/api/routes/metrics.py`

`/metrics` 已使用 `generate_latest()` 输出 Prometheus text format。确认端点正确暴露所有 Phase 1-4 新增指标（`saq_*`、`socketio_*`、`apscheduler_*`）。

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
| ~~`backend/scheduler/dispatcher.py`~~ | 该文件不存在；`backend/services/dispatcher.py` 是活跃核心服务，不应删除 |
| `backend/api/routes/websocket.py` ConnectionManager | Phase 3 移除 |

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| APScheduler 4.x 仍为 alpha | 生产稳定性未充分验证 | Phase 1 验证后评估；降级方案为退回 3.x（无 async-native，需 wrapper） |
| SocketIO 与现有原生 WebSocket 端点并行期间增加复杂度 | 迁移期 bug | Phase 3 内尽快完成全量迁移，避免长期并行 |
| Agent 侧 SocketIO Client 与同步线程模型的兼容性 | Agent 用 threading，SocketIO Client 有 async/sync 两种 | 使用 `socketio.Client`（同步版本），适配现有 Agent 线程模型 |
| step_trace 持久化迁移期断链 | 执行中的 step 状态不入库 | Phase 3.7 先落地 HTTP 批量上报 + 验证通过后再移除 MQ consumer；过渡期双写由幂等 upsert 保障 |
| Phase 2~4 期间 MQ consumer 与新路径并行 | 双写可能导致幂等冲突 | `reconcile_step_traces` 使用 `ON CONFLICT DO NOTHING`，天然幂等 |
| ControlListener 依赖 MQ Producer + stp:control | Phase 4 删除 MQ Producer 后控制指令断开 | Phase 4 先迁移 ControlListener 到 SocketIO，再删除 MQ |
| WS fallback 当前不工作（step_trace/job_status 静默丢弃） | 此为已有缺陷，非迁移引入 | SocketIO 迁移后所有事件类型显式处理，属于净改善 |
| Agent step_trace 从 fire-and-forget MQ 改为 HTTP 批量上报 | 批量窗口（5s）内的 trace 延迟入库 | 可调整批量间隔；SQLite WAL 本地持久化保证不丢数据；Agent 崩溃恢复后 `get_unacked_traces` 可重放 |
| PENDING_TOOL 状态机缺陷 | 预置 bug：job 执行中无法进入 PENDING_TOOL | 独立修复项（见 Phase 3.7.2），不阻塞迁移主线 |

---

## 已决策项

以下设计选择已在评审中完成决策：

| # | 决策项 | 结论 | 依据 |
|---|--------|------|------|
| 1 | Dispatch 是否异步化 | **保持同步**（方案 A） | 前端依赖同步返回 `workflow_run_id` |
| 2 | SocketIO mount path | **默认 `/socket.io`**（方案 B） | 减少 client 配置 |
| 3 | Agent SocketIO Client 同步 vs 异步 | **`socketio.Client` 同步**（方案 A） | 不改动 Agent 线程模型 |
| 4 | step_trace 持久化路径 | **HTTP 批量上报** | ADR-0018 双通道原则：HTTP 权威写入，SocketIO 仅展示 |

## 延迟决策项

以下事项在迁移完成后单独处理：

- **PENDING_TOOL 状态机**：当前为死代码（`VALID_TRANSITIONS` 无 `RUNNING→PENDING_TOOL` 边），不影响迁移。迁移完成后再决策保留（需加转换边 + Agent 暂停等待逻辑）或删除（移除枚举 + watchdog 代码 + Agent 报告）。
