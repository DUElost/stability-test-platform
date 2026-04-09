# ADR-0018: 基础设施层框架引入（SAQ / APScheduler / python-socketio）
- 状态：Accepted
- 优先级：P0
- 目标里程碑：M2
- 日期：2026-04-08
- 接受日期：2026-04-09
- 决策者：平台研发组
- 标签：基础设施, 调度, 消息队列, 实时推送, 框架替代

## 背景

平台自 MVP 阶段起采用"单进程 + 内置后台线程"模型（ADR-0002），在 FastAPI 进程内自研了 cron 调度器、Redis Streams 消费者、WebSocket 连接管理器等基础设施组件。随着功能完善和规模目标扩大（40+ Host、1000+ 设备），这些自研组件暴露出以下问题：

1. **cron_scheduler**：守护线程内使用 `asyncio.run()` 调用异步 dispatch，多任务并发时存在竞争与阻塞风险；缺少 miss-fire 补偿、任务持久化和并发控制。
2. **mq/consumer.py**：手工管理 `XREADGROUP` + `XACK` 消费循环，无 DLQ（Dead Letter Queue）、无 poison message 限制；`xgroup_create` 错误被 bare `except: pass` 吞掉。
3. **websocket.py ConnectionManager**：单进程内存 dict 管理连接，无法水平扩展；缺少 rooms、namespace、断线重连机制。
4. **职责模糊**：Redis Streams 同时承载状态更新（`stp:status`）、日志流（`stp:logs`）、控制指令（`stp:control`）三种不同可靠性要求的数据流，治理复杂。

与此同时，平台的核心业务组件——Pipeline 执行引擎（ADR-0014）、Job 状态机、设备锁租约、Dispatcher 扇出——成熟度高且构成业务差异化，不适合替换。

需要在 **保留核心业务自研** 的前提下，用成熟框架替代 **基础设施层**，降低维护成本、补齐可靠性短板。

## 决策

### 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│                     中心控制平面                              │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  APScheduler │  │ FastAPI 控制面│  │ python-socketio   │  │
│  │  定时触发    │──│ CRUD+权威写入│──│ 实时推送层        │  │
│  └─────────────┘  └──────┬───────┘  └────────┬──────────┘  │
│                          │                    │             │
│                   ┌──────┴───────┐            │             │
│                   │  SAQ Worker  │            │             │
│                   │  异步后台任务│            │             │
│                   └──────┬───────┘            │             │
│                          │                    │             │
│              ┌───────────┴────────┐           │             │
│              │                    │           │             │
│         ┌────┴─────┐     ┌───────┴──┐        │             │
│         │PostgreSQL │     │  Redis   │        │             │
│         │          │     │SAQ broker│        │             │
│         └──────────┘     │+控制指令 │        │             │
│                          └──────────┘        │             │
└──────────────────────────────────────────────┘             │
                                                              │
      ┌──────── Agent 执行集群 (40+) ────────┐               │
      │  ┌──────────┐  ┌──────────┐          │               │
      │  │ Agent 1  │  │ Agent N  │  ...     │               │
      │  │ HTTP权威  │──│ HTTP权威  │──────────┼── FastAPI     │
      │  │ SocketIO  │──│ SocketIO  │─────────┼── SocketIO    │
      │  │ Redis sub │  │ Redis sub│          │               │
      │  └──────────┘  └──────────┘          │               │
      └──────────────────────────────────────┘               │
                                                              │
      ┌── React 前端 ── SocketIO ─────────────────────────────┘
```

### 1. APScheduler 4.x 替代 cron_scheduler + recycler 定时触发

**替代范围**：
- `backend/scheduler/cron_scheduler.py` 的 cron 循环 → APScheduler `CronTrigger`
- `backend/scheduler/recycler.py` 的 `while True` 定时循环 → APScheduler `IntervalTrigger`
- `backend/tasks/session_watchdog.py` 的 asyncio 无限循环 → APScheduler `IntervalTrigger`

**保留不变**：
- Recycler 的超时判定、锁释放、延迟补偿逻辑（作为 APScheduler job 的回调函数）
- Session watchdog 的心跳检测、UNKNOWN 宽限逻辑（同上）

**技术要点**：
- 使用 `SQLAlchemyDataStore` 复用现有 PostgreSQL，获得任务持久化与 miss-fire 补偿
- APScheduler 运行在 FastAPI 进程内（async-native），维持单进程模型
- 废弃 `backend/tasks/heartbeat_monitor.py`（legacy）

**回调模式区分**：
- 轻量级周期任务（recycler 超时判定、watchdog 心跳检测）：直接在 APScheduler 回调函数中执行业务逻辑，不经过 SAQ
- 重量级任务（cron 触发的 workflow dispatch、数据清理涉及大批量删除）：回调函数仅向 SAQ 入队，由 SAQ worker 异步执行

### 2. SAQ 替代 mq/consumer.py 的后台任务处理

**替代范围**：
- `backend/mq/consumer.py` 中的 `consume_status_stream` 后台处理逻辑 → SAQ worker
- Post-completion 触发（报告生成、JIRA 草稿、通知推送） → SAQ 异步任务
- Dispatch 扇出（展开 TaskTemplate × Device 矩阵） → SAQ 异步任务

**保留不变**：
- Agent 终态上报以 HTTP `POST /api/v1/agent/jobs/{id}/complete` 为唯一主路径（ADR-0017）
- Agent outbox 机制（本地 SQLite + drain 重试）
- Job 状态机（`state_machine.py`）的转换逻辑
- `complete_job` 的 409 幂等语义

**SAQ 职责边界**：
- **不在** API 与 DB 之间充当中间层——CRUD 操作由 API 直写 DB
- **仅处理** 不需要即时响应用户的异步工作：dispatch 扇出、post-completion、通知、报告生成
- SAQ 使用 Redis 作为 broker，与控制指令通道共享同一 Redis 实例（不同 key prefix）

**数据流变化**：

| 数据流 | 当前路径 | 新路径 |
|--------|---------|--------|
| Agent 终态上报 | HTTP API → DB + MQ 补偿 | HTTP API → DB → SAQ(post-completion) |
| 日志流 | Agent → Redis Streams → consumer → WS 广播 | Agent → SocketIO 直推（服务端异步写文件） |
| 控制指令 | API → Redis Streams | API → SAQ → Redis Pub/Sub |
| Dispatch 扇出 | API 同步执行 | API → SAQ 异步执行 |

**完整数据流速查表**（含不变路径）：

| 数据类型 | 方向 | 通道 | 可靠性 | 持久化 |
|---------|------|------|--------|--------|
| 主心跳（host/device 快照） | Agent → 中心端 | HTTP API (`/api/v1/heartbeat`) | 必须可靠 | PostgreSQL |
| 任务领取（claim/pending） | Agent → 中心端 | HTTP API | 必须可靠 | PostgreSQL |
| 任务终态上报 | Agent → 中心端 | HTTP API（outbox 保障） | 必须可靠 | PostgreSQL |
| Step 状态上报 | Agent → 中心端 | HTTP API | 必须可靠 | PostgreSQL (StepTrace) |
| 设备锁续期 | Agent → 中心端 | HTTP API (`extend_lock`) | 必须可靠 | PostgreSQL |
| 实时日志/进度 | Agent → 服务端 → 前端 | SocketIO | 传输允许丢失 | 服务端异步写文件 |
| 控制指令 | 中心端 → Agent | Redis Pub/Sub | 允许丢失 | 不持久化 |
| 前端 CRUD 操作 | 前端 → 中心端 | HTTP API | 必须可靠 | PostgreSQL |
| 前端实时更新 | 中心端 → 前端 | SocketIO | 允许丢失 | 不持久化 |

> 详见 `docs/module-responsibilities.md` 数据流路径速查表。

### 3. python-socketio 替代自研 WebSocket ConnectionManager

**替代范围**：
- `backend/api/routes/websocket.py` 的 `ConnectionManager` 类 → python-socketio Server
- `frontend/src/hooks/useWebSocket.ts` → `socket.io-client`
- `backend/agent/ws_client.py` → `python-socketio` AsyncClient

**获得能力**：
- 内置 rooms（按 `job:{id}` 订阅，替代手工 dict 管理）
- 内置 namespace：`/agent`（Agent 通道）、`/dashboard`（前端通道），隔离不同消费者
- Redis adapter 支持多进程消息同步（为后续水平扩展预留）
- 自动断线重连与事件重放

**双通道原则（延续 ADR-0017）**：
- HTTP 通道：权威路径——claim、complete、heartbeat 必须走 HTTP API
- SocketIO 通道：展示路径——实时日志流、step 状态展示、dashboard 事件推送

**服务端日志持久化**：
- SocketIO server handler 接收 Agent emit 的日志后，异步写入文件系统供后续查阅
- 传输链路允许丢失（浏览器不在线则看不到实时流），但服务端收到的日志会被持久化
- SocketIO handler **不做** DB 写入、**不推进**状态机——仅做日志文件 I/O

### 4. Redis 职责简化

**当前**：Redis Streams 承载 `stp:status` + `stp:logs` + `stp:control` 三种流。

**调整后**：
- `stp:logs` → 废弃，由 Agent → SocketIO 直推替代
- `stp:status` → 废弃，由 Agent HTTP 上报 + SAQ post-completion 替代
- `stp:control` → 保留，简化为 Redis Pub/Sub 控制指令通道（abort、pause、config push）
- SAQ broker → 新增，SAQ 使用 Redis 作为任务队列

Redis 从 "三流合一的 Streams" 简化为 "控制指令 Pub/Sub + SAQ 任务队列"，职责更清晰。

## 备选方案与权衡

### 方案 A：Celery + Celery Beat（未采纳）

- 优点：生态完整（Beat、Flower 监控、结果后端），社区成熟。
- 缺点：Celery 是同步模型，与当前 async FastAPI + SQLAlchemy[asyncio] 架构摩擦大；引入额外进程（worker），偏离单进程模型目标；配置复杂度高。

### 方案 B：arq 替代 SAQ（备选）

- 优点：同样 async-native + Redis backend，API 简洁。
- 缺点：功能较 SAQ 少（无内置 Web UI、无 cron 调度），社区活跃度稍低。
- 如果 SAQ 在实践中不满足需求，arq 可作为降级替代。

### 方案 C：Centrifugo 替代 python-socketio（远期备选）

- 优点：专用实时消息服务器，支持万级连接；后端仅需 HTTP publish。
- 缺点：引入额外基础设施服务；当前规模（40 host、<100 前端连接）下过度设计。
- 保留为 1000+ 设备规模下的演进方案。

### 方案 D：维持全自研（未采纳）

- 优点：无迁移成本。
- 缺点：上述四个问题持续存在；DLQ、miss-fire、rooms 等能力需自行实现，预估工期远超框架引入。

## 不变量（护栏）

以下约束在框架引入后 **必须继续遵守**：

1. **ADR-0017 双通道原则**：HTTP 是权威写入路径，SocketIO 是展示路径。禁止 SocketIO handler 直接推进状态机。
2. **ADR-0017 Outbox 幂等**：Agent 侧 outbox + drain 机制不变，409 冲突语义不变。
3. **ADR-0017 Post-completion 幂等**：`post_processed_at` 检查不变，无论由 SAQ 还是补偿路径触发。
4. **ADR-0002 单进程约束**：APScheduler、SAQ worker、SocketIO server 均运行在 FastAPI 进程内，不引入额外独立进程。
5. **ADR-0014 Pipeline 引擎**：Agent 侧 `pipeline_engine.py` 的 stages 执行模型完全保留，不受本次变更影响。
6. **设备锁租约**：claim/lock/extend_lock 路径完全保留，不经过 SAQ 异步化。

## 影响

### 正向影响

- 消除 cron_scheduler 的 `asyncio.run` hack，获得 miss-fire 补偿与任务持久化
- MQ 消费获得 DLQ、retry、超时等生产级可靠性
- WebSocket 获得 rooms、namespace、多进程同步、自动重连
- Redis Streams 从三流简化为单一控制通道，降低运维心智负担
- 为 ADR-0011（可观测性）落地扫清障碍——SAQ 内置监控 + SocketIO 连接指标

### 代价与约束

- 引入三个新依赖（`apscheduler>=4.0`, `saq`, `python-socketio[asyncio]`）
- Agent 侧需将 `ws_client.py` 迁移到 `python-socketio` AsyncClient
- 前端需将原生 WebSocket hook 迁移到 `socket.io-client`
- 迁移期间需维护新旧路径并行，直到验证完毕后移除旧代码
- SAQ 依赖 Redis，Redis 成为单点——但当前 Redis 已是事实单点，风险未增加

## 迁移策略

采用 **逐层替换、逐步验证** 策略，不做大爆炸式迁移：

### Phase 1：APScheduler（预计 1-2 天）

1. 安装 `apscheduler>=4.0.0a5`
2. 在 `main.py` lifespan 中初始化 `AsyncScheduler`（SQLAlchemy data store）
3. 将 `cron_scheduler.py` 的调度逻辑注册为 `CronTrigger` job
4. 将 `recycler.py` 的循环注册为 `IntervalTrigger` job
5. 将 `session_watchdog.py` 的循环注册为 `IntervalTrigger` job
6. 验证：定时触发准确性、miss-fire 后补偿执行、进程重启后 job 恢复
7. 移除旧的线程启动代码和 asyncio task 启动代码

### Phase 2：SAQ（预计 2-3 天）

1. 安装 `saq`
2. 定义 SAQ tasks：`dispatch_workflow_task`、`post_completion_task`、`send_notification_task`
3. 在 API 路由中将同步 dispatch 调用改为 `await queue.enqueue(...)`
4. 在 `complete_job` 中将 post-completion 触发改为 SAQ enqueue
5. 验证：dispatch 扇出正确性、post-completion 幂等性、DLQ 行为
6. 移除 `mq/consumer.py` 中的 `consume_status_stream`（日志流在 Phase 3 迁移）

### Phase 3：python-socketio（预计 2-3 天）

1. 安装 `python-socketio[asyncio]`（后端）和 `socket.io-client`（前端）
2. 创建 SocketIO server，挂载到 FastAPI（ASGI mount）
3. 定义 namespace：`/agent`（Agent 通道）、`/dashboard`（前端通道）
4. 迁移 Agent `ws_client.py` → `python-socketio` AsyncClient
5. 迁移前端 `useWebSocket.ts` → `socket.io-client` hook
6. 验证：日志实时推送、rooms 订阅隔离、断线重连
7. 移除 `ConnectionManager` 和 `mq/consumer.py` 中的 `consume_log_stream`

### Phase 4：Redis 清理（预计 0.5 天）

1. 移除 `stp:status` 和 `stp:logs` stream 创建代码
2. 保留 `stp:control` 或迁移到 Redis Pub/Sub
3. 移除 `agent/mq/producer.py`
4. 清理 `main.py` 中的 stream consumer group 创建逻辑

## 新增依赖

```
apscheduler>=4.0.0a5,<5.0
saq>=0.12.0,<1.0
python-socketio[asyncio]>=5.11.0,<6.0
```

前端：
```
socket.io-client@^4.7.0
```

## 关联 ADR

- **Supersedes 部分 ADR-0002**：后台线程/异步任务的启动方式由 APScheduler + SAQ 接管，但单进程约束保留
- **Supersedes 部分 ADR-0006**：WebSocket 实现由 python-socketio 替代，但 REST + WS 分工原则保留
- **兼容 ADR-0017**：所有护栏条款不变
- **推进 ADR-0011**：SAQ 内置监控 + SocketIO 连接指标为可观测性落地提供基础

## 落地与后续动作

- ✅ Phase 1（APScheduler 迁移）：commit e69763f + cb7e957
- ✅ Phase 2（SAQ 迁移）：commit bfdf686
- ✅ Phase 3（python-socketio 迁移）：commit 7fc7242
- ✅ Phase 3.7（Agent 状态上报迁移）+ Phase 4（Redis Streams 清理）：commit 6bdefac
- ✅ Phase 5（可观测性落地）：框架级 Prometheus 指标 + Grafana dashboard 模板
- ✅ Phase 6（验收与文档）：文档更新、废弃代码确认
- 回归测试：14 failed / 154 passed / 11 skipped（与迁移前基线一致，零新增 FAILED）

## 关联实现

- `backend/main.py` — lifespan 初始化 APScheduler + SAQ + SocketIO
- `backend/scheduler/app_scheduler.py` — APScheduler 4.x 统一调度入口
- `backend/scheduler/cron_scheduler.py` — 重构为 APScheduler job 回调（纯函数）
- `backend/scheduler/recycler.py` — 重构为 APScheduler interval job 回调（纯函数）
- `backend/tasks/session_watchdog.py` — 重构为 APScheduler interval job 回调（纯函数）
- `backend/tasks/saq_tasks.py` — SAQ 异步任务定义
- `backend/tasks/saq_worker.py` — SAQ Worker 生命周期管理
- `backend/realtime/socketio_server.py` — python-socketio 服务端（/agent + /dashboard）
- `backend/realtime/log_writer.py` — 异步日志文件持久化
- `backend/core/metrics.py` — Prometheus 指标定义（含框架级指标）
- `backend/agent/ws_client.py` — 迁移到 python-socketio Client（同步版）
- `backend/agent/step_trace_uploader.py` — Step 状态 HTTP 批量上报
- `frontend/src/hooks/useSocketIO.ts` — 迁移到 socket.io-client
- `docs/grafana/stability-platform-dashboard.json` — Grafana dashboard 模板
- ~~`backend/mq/consumer.py`~~ — 已删除（由 SAQ + SocketIO 替代）
- ~~`backend/tasks/heartbeat_monitor.py`~~ — 已删除（由 APScheduler session_watchdog 替代）
