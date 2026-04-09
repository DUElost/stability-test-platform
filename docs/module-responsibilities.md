# Stability Test Platform 核心模块职责定义

**版本**：2.2.0  
**更新时间**：2026-04-09  
**状态**：ADR-0018 全量落地后修订

---

## 一、设计原则

在定义各模块职责前，确立以下架构原则，作为职责划分的“宪法”：

| 原则 | 说明 |
| :--- | :--- |
| **HTTP 是权威路径** | 所有影响状态机、涉及资源锁、需要幂等性保障的操作，必须走 HTTP API。 |
| **WebSocket/SocketIO 是展示路径** | 用于实时日志、进度更新等允许丢失的展示性数据，不承担权威状态变更。 |
| **异步任务与同步请求分离** | 用户触发的 CRUD 操作同步响应；耗时后台任务异步处理。 |
| **单一事实来源** | PostgreSQL 是所有持久化状态的唯一权威存储。 |

---

## 二、模块职责定义

### 1. FastAPI 控制面

**定位**：平台的权威入口，所有状态变更的必经之路。

**核心职责**：
- 提供 RESTful API，处理所有 CRUD 操作（工作流、任务、主机、设备等资源的增删改查）
- 作为 Agent 权威请求的接收端点：
  - `POST /api/v1/agent/jobs/claim` — 任务领取（Push 模型，中心端分配）
  - `GET /api/v1/agent/jobs/pending` — 任务拉取（Pull 模型，Agent 主动获取并原子 claim）
  - `POST /api/v1/agent/jobs/{id}/complete` — 任务终态上报（409 幂等）
  - `POST /api/v1/agent/jobs/{id}/extend_lock` — 设备锁续期
  - `POST /api/v1/agent/jobs/{id}/steps/{step_id}/status` — Step 状态上报
  - `POST /api/v1/heartbeat` — 主心跳（host/device 状态的唯一权威写入路径）
  - `POST /api/v1/agent/heartbeat` — Agent 辅助心跳（tool catalog 版本 + backpressure）
- 向 SAQ 发起异步后台任务（扇出、后处理、通知等）
- 实施幂等性校验（如 409 冲突响应、状态机转换保护）

**不应承担的职责**：
- 执行长时间运行的后台任务（应委托给 SAQ）
- 直接处理流式日志的中转（由 SocketIO 承担）
- 直接向 Agent 推送指令（由 Redis 控制通道承担）

---

### 2. SAQ Worker

**定位**：异步后台任务执行器，由 API 触发，而非 API 与数据库之间的中间层。

**核心职责**：
- 消费由 API 入队的异步任务
- 执行任务扇出（将 Workflow 展开为多个 Job 并分配到各 Host）
- 执行任务后处理（Post-completion）：日志归档、报告触发、JIRA 同步等
- 通过 Redis 向 Agent 发布控制指令（取消、重试、审批决策等）
- 处理需要重试、延迟、超时控制的逻辑

**不应承担的职责**：
- 处理同步 CRUD 请求
- 直接消费 Agent 上报的日志流或状态流
- 作为 API 与数据库之间的写缓冲

---

### 3. Redis

**定位**：SAQ 任务队列的 broker。单一 Redis 实例。

**核心职责**：
- **SAQ broker**：存储 SAQ 异步任务队列，提供入队、消费、重试、DLQ 等基础设施

**不应承担的职责**：
- 作为 Agent 上报数据的通道（日志走 SocketIO，状态走 HTTP API）
- 作为持久化存储（Redis 数据允许丢失，持久化状态在 PostgreSQL）
- 作为控制指令通道（已迁移到 SocketIO `on_control` event）

---

### 4. python-socketio

**定位**：实时数据推送层，负责 Agent → 服务端 → 前端的方向性数据流。

**核心职责**：
- 接收 Agent 直接 emit 的实时日志、步骤状态、进度百分比
- 向 Web 前端推送实时数据（按 `job:{id}` 房间隔离订阅）
- 管理 WebSocket 连接的生命周期（rooms、namespace、断线重连）
- **服务端日志持久化**：SocketIO server handler 接收日志后，异步写入文件系统供后续查阅（传输链路允许丢失，但服务端收到的日志会被持久化）
- Namespace 划分：`/agent`（Agent 通道）、`/dashboard`（前端通道）

**不应承担的职责**：
- 接收 Agent 的权威请求（如任务完成、心跳，这些必须走 HTTP API）
- 推进状态机或写入业务状态到数据库（SocketIO handler 仅做日志文件写入，不做 DB 写入）

---

### 5. APScheduler

**定位**：定时任务调度与周期性维护触发器，运行在 FastAPI 进程内（async-native）。

**核心职责**：
- 管理以下定时任务（使用 SQLAlchemy data store 持久化，支持 miss-fire 补偿）：
  - **Cron 调度**（`CronTrigger`）：按 `TaskSchedule` 表中的 cron 表达式触发 workflow dispatch
  - **Recycler**（`IntervalTrigger`）：PENDING/RUNNING 超时判定、设备锁释放、workflow 聚合、延迟 post-completion 补偿
  - **Session Watchdog**（`IntervalTrigger`）：Host 心跳超时检测、UNKNOWN 宽限期收敛、PENDING_TOOL 超时
  - **数据清理**（`IntervalTrigger`）：历史 WorkflowRun 保留期删除
- 对于轻量级周期任务（recycler、watchdog），直接在回调函数中执行业务逻辑
- 对于重量级任务（dispatch 扇出、post-completion），通过回调函数向 SAQ 入队
- **SAQ 队列深度轮询**（`IntervalTrigger`）：每 15s 采样 SAQ 队列深度写入 Prometheus gauge
- 所有 job 自动通过 `_instrumented()` 装饰器记录 Prometheus 指标（执行次数、耗时、成功/失败）

**不应承担的职责**：
- 替代 SAQ 处理一次性异步任务（如用户触发的 dispatch、通知推送）
- 管理 Agent 侧的定时逻辑（Agent 有独立的心跳线程和锁续期线程）

---

### 6. PostgreSQL

**定位**：平台持久化状态的单一事实来源。

**核心职责**：
- 存储所有业务实体：WorkflowDefinition、WorkflowRun、TaskTemplate、JobInstance、StepTrace、Host、Device、Tool 等
- 存储 APScheduler 任务调度持久化数据（SQLAlchemy data store）
- 存储 [计划中] Pipeline 执行检查点数据（用于断点续传）
- 存储 [计划中] 人工审批请求记录
- 提供事务性保障（savepoint 用于设备锁原子获取、状态机转换）

**不应承担的职责**：
- 存储实时日志流（由 SocketIO 服务端写入文件系统）
- 存储时序指标数据（应走 Prometheus / Loki）

---

### 7. Agent

**定位**：运行在 Linux Host 上的执行节点，负责设备管理与测试执行。

**核心职责**：

*通信通道*：
- **HTTP API**（权威路径）：心跳上报、任务领取/完成、Step 状态上报、设备锁续期
- **SocketIO**（展示路径 + 控制接收）：实时日志流、步骤进度推送；接收 `on_control` event（abort / backpressure / tool_update）

*执行能力*：
- 执行 Pipeline 定义的测试流程（stages 生命周期：prepare → execute → post_process）
- 管理所连接 Android 设备的生命周期（通过 ADB 发现、监控、交互）
- 工具注册表（ToolRegistry）：从中心端同步工具定义并缓存到本地

*可靠性机制*：
- **本地 SQLite Outbox**（ADR-0017）：终态事件先写本地 outbox 再发 HTTP；未 ACK 事件由 `OutboxDrainThread` 后台重试；SIGTERM 时 `drain_sync()` 同步刷出
- **LockRenewalManager**：后台线程每 60s 调用 `extend_lock` 续期设备锁；收到 409 时主动放弃任务
- **per-device 并发控制**：通过 `_active_device_ids` 集合保证同一设备同一时刻只执行一个 Job

**不应承担的职责**：
- 将权威状态变更（如任务完成）通过 SocketIO 上报（必须走 HTTP API）
- 将实时日志写入 Redis Streams 再由中心端消费（直接通过 SocketIO 推送）

---

## 三、数据流路径速查表

| 数据类型 | 方向 | 通道 | 可靠性要求 | 持久化 |
| :--- | :--- | :--- | :--- | :--- |
| 主心跳（host/device 快照） | Agent → 中心端 | HTTP API (`/api/v1/heartbeat`) | 必须可靠 | PostgreSQL |
| 任务领取（claim/pending） | Agent → 中心端 | HTTP API | 必须可靠 | PostgreSQL |
| 任务终态上报 | Agent → 中心端 | HTTP API（outbox 保障） | 必须可靠 | PostgreSQL |
| Step 状态上报 | Agent → 中心端 | HTTP API | 必须可靠 | PostgreSQL (StepTrace) |
| 设备锁续期 | Agent → 中心端 | HTTP API (`extend_lock`) | 必须可靠 | PostgreSQL |
| 实时日志/进度 | Agent → 服务端 → 前端 | SocketIO | 传输允许丢失 | 服务端异步写文件 |
| 控制指令 | 中心端 → Agent | SocketIO `on_control` event | 允许丢失 | 不持久化 |
| 前端 CRUD 操作 | 前端 → 中心端 | HTTP API | 必须可靠 | PostgreSQL |
| 前端实时更新 | 中心端 → 前端 | SocketIO | 允许丢失 | 不持久化 |
| [计划中] 审批请求创建 | Agent → 中心端 | HTTP API | 必须可靠 | PostgreSQL |
| [计划中] 审批结果下发 | 中心端 → Agent | SocketIO `on_control` event | 允许丢失 | 不持久化 |

---

## 四、版本记录

| 版本 | 日期 | 变更说明 |
| :--- | :--- | :--- |
| 1.0.0 | 2026-01-21 | 初始版本 |
| 2.0.0 | 2026-04-08 | 引入 SAQ/APScheduler/python-socketio 后的职责重定义；明确 HTTP 权威 / SocketIO 展示的双通道原则；简化 Redis 角色 |
| 2.1.0 | 2026-04-08 | 对照代码实现修正：补全 Agent API 端点（双心跳路径、claim/pending 双模型、extend_lock、step status）；Redis 增加 SAQ broker 职责；日志持久化策略修正为服务端异步写文件；模型名称对齐（TaskRun→JobInstance 等）；APScheduler 明确 recycler/watchdog/cron 三类具体 job；Agent 补充 outbox/锁续期/per-device 并发控制；未实现特性标注 [计划中]；数据流表补充 StepTrace、锁续期等缺失路径 |
| 2.2.0 | 2026-04-09 | ADR-0018 全量落地后修订：Redis 简化为仅 SAQ broker（控制指令迁移到 SocketIO on_control event）；Agent 通信通道合并 SocketIO 控制接收；APScheduler 补充 SAQ queue depth 轮询和 Prometheus 指标埋点；数据流表控制指令通道改为 SocketIO |