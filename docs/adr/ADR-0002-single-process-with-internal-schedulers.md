# ADR-0002: 单进程后端 + 内置后台调度线程
- 状态：Accepted
- 日期：2026-02-18（2026-03-24 更新）
- 决策者：平台研发组
- 标签：调度, 线程模型, 部署约束

## 背景

当前平台需要同时运行任务分发、超时回收、工作流推进、Cron 触发等后台能力。为保证 MVP 快速落地，需优先降低部署复杂度。

## 决策

在 FastAPI 进程启动时拉起后台线程与异步任务：

### 后台线程（daemon thread）

- `recycler`：回收超时 JobInstance、释放设备锁、清理物理 artifact 文件。
- `cron_scheduler`：按 cron 创建任务（受 `ENABLE_CRON_SCHEDULER` 环境变量控制，默认启用）。

### 异步任务（asyncio task）

- `session_watchdog`（默认启用）：Host 心跳超时检测、设备锁过期释放、UNKNOWN 宽限期管理。与 legacy `heartbeat_monitor` 互斥，由 `USE_SESSION_WATCHDOG` 环境变量控制。
- `consume_status_stream`：消费 Redis Streams 状态事件。
- `consume_log_stream`：消费 Redis Streams 日志事件。
- `monitor_backpressure`：监控 Redis Streams 积压。

### 已废弃 / 未启动

- ~~`dispatcher`~~：`backend/scheduler/dispatcher.py` 保留用于遗留 Task/TaskRun 路径，当前 **不在 `main.py` 中启动**。Workflow 任务的派发已由 `backend/services/dispatcher.py`（`dispatch_workflow`）接管。
- ~~`workflow_executor`~~：文件已删除。多步骤工作流推进由 Workflow dispatcher + Agent claim 机制替代。

由于后台线程在进程内启动，生产 MVP 强制单实例后端运行，避免多实例重复调度。

## 备选方案与权衡

- 方案 A：独立调度服务（独立进程/服务）。
  - 优点：天然支持横向扩展与职责隔离。
  - 缺点：运维成本高，MVP 周期长。
- 方案 B：当前方案（进程内线程 + 异步任务）。
  - 优点：部署简单，代码路径短。
  - 缺点：水平扩展受限，缺少 leader election。

## 影响

- 正向影响：快速形成闭环，便于本地/预发布演练。
- 负向影响：后端扩容不能直接加 worker；多进程会引入重复执行风险。

## 落地与后续动作

- ✅ 已落地：启动钩子内拉起 recycler + cron_scheduler 线程，以及 session_watchdog + Redis 消费者异步任务。
- ✅ Legacy dispatcher 和 workflow_executor 已被 Workflow dispatcher + Agent claim 机制替代。
- ⚠️ **部分被 ADR-0018 supersede**：后台线程（recycler、cron_scheduler）和异步任务（session_watchdog、Redis 消费者）的启动方式将由 APScheduler + SAQ + python-socketio 接管。**单进程约束保留不变**。详见 [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md)。
- 远期：重构为"调度作业服务 + 选主机制"时，需新增替代 ADR 并将本 ADR 标记为 `Superseded`。

## 关联实现/文档

- `backend/main.py` — lifespan 启动钩子
- `backend/scheduler/recycler.py` — JobInstance 超时回收（后台线程）
- `backend/scheduler/cron_scheduler.py` — Cron 定时任务（后台线程）
- `backend/tasks/session_watchdog.py` — 会话看门狗（异步任务）
- `backend/mq/consumer.py` — Redis Streams 消费者（异步任务）
- `backend/scheduler/dispatcher.py` — Legacy Task 派发器（保留但未启动）
- `backend/services/dispatcher.py` — Workflow 派发服务（替代 legacy dispatcher + workflow_executor）
- `docs/production-minimum-deployment-checklist.md`
