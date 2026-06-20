# ADR-0018: 基础设施层框架引入（SAQ / APScheduler / python-socketio）
- 状态：Accepted（2026-06-18 修订：Watcher 子系统补充日志类型契约 + 路径 B 默认开 + 存储结构改造）
- 优先级：P0
- 目标里程碑：M2
- 日期：2026-04-08（2026-04-09 接受；2026-06-18 Watcher 子系统契约补充）
- 决策者：平台研发组
- 标签：基础设施, 调度, 消息队列, 实时推送, 框架替代, Watcher

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
      │  │         │  │        │          │               │
      │  └──────────┘  └──────────┘          │               │
      └──────────────────────────────────────┘               │
                                                              │
      ┌── React 前端 ── SocketIO ─────────────────────────────┘
```

> **架构图修正 (2026-06-12)**：Agent 框中 "Redis sub" 已抹除——Agent 不直接订阅 Redis。控制指令通过 SocketIO  namespace 推送或 HTTP polling。Redis 仅作为 SAQ broker（控制面侧），Agent 无 Redis 连接。

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

### 遗留清理项（2026-04-12 审计补充）

> ⚠️ **2026-04-12 Wave 7+8 已完成清理**：以下残留代码在双轨合并 Wave 7+8（2026-04-12）中已全部移除。状态更新如下：

| 残留项 | 位置 | 说明 | 原清理计划 | 实际状态 |
|--------|------|------|------------|---------|
| ~`ConnectionManager` + WS stub 路由~ | ~`backend/api/routes/websocket.py`~ | deprecated stub | Wave 8-3 移除 | ✅ 已删除 |
| ~`websocket_*` Prometheus 指标~ | ~`backend/core/metrics.py` L189-209~ | 无调用方 | Wave 7-4 移除 | ✅ 已删除 |
| ~`useWebSocket.ts` + 测试~ | ~`frontend/src/hooks/useWebSocket.ts`~ | 原生 WS hook | Wave 7-3 移除 | ✅ 已删除 |
| `WS_*` 常量命名 | `frontend/src/config/index.ts` | `/ws/dashboard` 形式，仅作 room 解析键 | Wave 7-3 重命名或移除 | 仍活跃（作为 room 解析键） |
| ~`WebSocketMock`~ | ~`frontend/src/test/setup.ts`~ | 测试夹具 | Wave 7-3 同步清理 | ✅ 已删除 |

## Watcher 子系统铺路（2026-04-17 起 · ADR-0018 延伸）

本章节记录 ADR-0018 基础设施层框架落地之后，在同一分支 `feature/adr-0018-infra-framework` 上完成的 **Watcher 子系统**交付（inotifyd 设备日志观察者 + NFS 富化 + JobArtifact 独立持久化）。这部分不改变前置框架决策，但沿用本 ADR 的单进程约束、HTTP 权威写入、SocketIO 展示路径等护栏。

### 交付结构（Commit `f366b1b`，单 commit 主线）

| 阶段 | 范围 | 状态 |
|------|------|------|
| **Stage 5A** — Watcher 基础设施 | `backend/agent/watcher/` 模块骨架（`sources.py` / `batcher.py` / `emitter.py` / `manager.py` / `policy.py` / `contracts.py` / `exceptions.py`）、Alembic `k9f0a1b2c3d4` 增补 `watcher_policy` / `watcher_capability` / `watcher_state_*`、Alembic `m1g2h3i4j5k6` 设备 active job 部分唯一索引、`JobLogSignal` ORM + `backend/agent/job_session.py` + `pipeline_engine.StepContext.job_id` 透传、`agent_api POST /log-signals` + `claim` PENDING→RUNNING、完整单测 + 契约测试 | ✅ |
| **Stage 5B1** — LogPuller 异步 adb pull + envelope 富化 | `watcher/puller.py` per-device async worker（adb pull → NFS `$nfs_base_dir/jobs/{job_id}/{CATEGORY}/`）、`sha256` / `size_bytes` / `first_lines` 富化、`DeviceLogWatcher.attach_puller` + `_on_pull_done` 回调、`LogWatcherManager` 在 `nfs_base_dir` 非空时注入 puller、`test_puller` 覆盖 oversized/失败/drain 路径 | ✅ |
| **Stage 5B2** — JobArtifact 独立端点 + Agent ArtifactUploader | Alembic `n2h3i4j5k6l7` 为 `job_artifact` 增补 `source_category` / `source_path_on_device` + `UniqueConstraint(job_id, storage_uri)`、`POST /api/v1/agent/jobs/{job_id}/artifacts` whitelist + PG `ON CONFLICT DO NOTHING` 幂等、`backend/agent/artifact_uploader.py` 进程级单例 + 队列 + daemon worker（fire-and-forget）、`DeviceLogWatcher._maybe_submit_artifact` 仅对 AEE / VENDOR_AEE 且 pull 成功时转发、`main.py` lifecycle `configure()` / `start()` / `stop()` 串入、`test_agent_api_artifacts` (7) + `test_artifact_uploader` (13) + `test_device_watcher_artifact` (7) 全部通过 | ✅ |
| **Stage 6** — JobSession 真实闭环 E2E | `test_job_session_e2e.py` 7 cases 仅 mock `adb` 子进程与 HTTP；bugfix：`LogWatcherManager._prober_factory` 改用 lambda 兼容 `timeout_seconds` keyword-only 签名 | ✅ |

**回归测试基线**：5B2 新增 20 passed + 7 skipped；watcher 整体回归 126 passed + 14 skipped，零新增 FAILED（5 个 pre-existing failure 与 watcher 无关，见"遗留失败治理"追踪项）。

### 收口契约（不变量）

以下 4 条契约是 Watcher 子系统的**硬边界**，后续演进必须继续遵守：

1. **`log_signal` 是异常事件权威流** — Agent outbox 持久化后 `POST /api/v1/agent/log-signals` 幂等上报，`(host_id, device_serial, job_id, category, seq_no)` 单调。任何 Watcher 路径（immediate / batch / puller 回调 / reconciler）最终都必须通过 `SignalEmitter.emit()` 落 outbox，**不允许**因为 pull 失败、artifact 入库失败、uploader 异常而丢信号。
2. **`JobArtifact` 是独立的异步持久化面** — 产物入库走 `POST /api/v1/agent/jobs/{job_id}/artifacts`，与 `log_signal` 在数据库层**完全解耦**：`JobArtifact.storage_uri` 幂等键是 `(job_id, storage_uri)`；相同文件重复提交命中 `ON CONFLICT DO NOTHING`，不返回 409 也不覆写旧记录。白名单（`aee_crash` / `vendor_aee_crash` / `bugreport` / `run_log_bundle`）在端点侧校验，越界类型返回 400。
3. **`ArtifactUploader` 是 fire-and-forget，不回压 watcher** — Agent 侧 `ArtifactUploader` 为进程级单例，内部持有有界队列 + daemon worker；`submit()` 非阻塞且吞所有异常。HTTP 失败走本地重试 + 退避；队列满丢弃时记 `submits_dropped` 计数，**不会**把压力传导回 `DeviceLogWatcher` 的 emit 主链路。
4. **AEE 拉取走路径 B（Reconciler），日志按事件目录聚合**（2026-06-18 增补） — `STP_WATCHER_AEE_RECONCILE_ENABLED` 默认 `true`；crash 发生时拉取 AEE 整目录 + dblog + bugreport(300s 冷却) + 前后各 2 个 mobilelog(main+kernel)；mobilelog/bugreport 下沉到事件目录 `{ts}_{db_path}/` 内，非统一 `correlated_*` 混放。此契约是 ADR-0025 归档-3 分类提取的前提（按 db 路径定位关联日志）。

### 灰度路径（`STP_WATCHER_ENABLED`）

> 2026-06-18 修订：原 `STP_WATCHER_ENABLED` 默认 `false` 已在 ADR-0025 Sprint 1 改为 `true`（`enable.py:11`）。本节更新为当前状态。

Watcher 默认**开启**（`backend/agent/watcher/enable.py:11` — `STP_WATCHER_ENABLED = os.getenv("STP_WATCHER_ENABLED", "true").lower() == "true"`，与 `main.py:69` 一致）。

- 开启态：Agent 启动 `LogWatcherManager` / `LogPuller` / `ArtifactUploader`，订阅 inotifyd。
- 回滚：置 `STP_WATCHER_ENABLED=false` + systemctl restart 即可，无数据库残留（`log_signal` / `job_artifact` 表独立，不污染 `job_instance` 主线）。

### AEE 拉取双路径与日志类型契约（2026-06-18 增补）

Watcher 子系统有**两条并行 AEE 拉取路径**，拉取范围不同：

| 路径 | 入口 | 拉取范围 | 默认状态 |
|------|------|---------|---------|
| A. inotifyd LogPuller | `watcher/puller.py` | 单个 AEE 文件 + 可选 bugreport | 默认开 |
| B. AeeDbHistoryReconciler | `aee/reconciler.py` → `aee/processor.py` | AEE 整目录 + dblog + mobilelog 时间窗(前后各2) + bugreport(300s 冷却) | **改为默认开**（原 `STP_WATCHER_AEE_RECONCILE_ENABLED=false`，2026-06-18 改为 `true`） |

**路径 B 默认开的理由**：路径 A 只拉单个 AEE 文件，缺 dblog 和 mobilelog，无法支撑 crash 诊断（需 crash 前后日志上下文）。路径 B 已具备完整能力（对齐上一代工具 `MonkeyAEEinfo_260523.py` 的 `dblog + bugreport + 前后各2 mobilelog` 需求），但原设计为灰度默认关。ADR-0025 D3 修订明确路径 B 为默认路径。

**日志类型契约**（路径 B，对齐上一代工具）：

| 日志类型 | 设备路径 | 拉取方式 | 落盘位置 |
|---------|---------|---------|---------|
| AEE 整目录（dblog） | `/data/aee_exp` + `/data/vendor/aee_exp` | `adb pull` 整目录 | `{nfs_root}/{folder_name}/{serial}/aee_exp/{ts}_{db_path}/` |
| db_history 转储 | `{aee_path}/db_history` | `adb shell cat` | 事件目录上级 `db_save_org.txt`（全量）+ `db_save.txt`（过滤后） |
| bugreport | crash 时刻导出 | `adb bugreport` | 事件目录内 `bugreport/{ts}_bugreport.zip`（300s 冷却） |
| mobilelog（main_log） | `/data/debuglogger/mobilelog/` | `adb pull` 时间窗前后各 2 个 | 事件目录内 `mobilelog/main_log.*` |
| mobilelog（kernel_log） | 同上 | 同上 | 事件目录内 `mobilelog/kernel_log.*` |
| mobilelog（sys_log） | 同上 | 同上 | 默认关（`mobilelog.py:22` `"enabled": False`） |

### 日志存储结构改造（2026-06-18 增补）

**原结构**（当前代码）：mobilelog/bugreport 按设备维度统一放在 `correlated_mobilelogs/`、`correlated_bugreports/`，与具体 AEE 事件无关联——无法知道哪个 mobilelog 对应哪个 crash。

**改造后结构**：mobilelog/bugreport 下沉到每个 AEE 事件目录内，与该事件的 db_path/时间戳一一对应。

当前结构：
```
{nfs_root}/{folder_name}/{serial}/
  aee_exp/
    db_save_org.txt
    db_save.txt
    {ts}_{db_path}/              ← 事件目录
      __exp_main.txt
      main.dbg
  correlated_mobilelogs/         ← 所有事件混放
  correlated_bugreports/         ← 所有事件混放
```

改造后结构：
```
{nfs_root}/{folder_name}/{serial}/
  aee_exp/
    db_save_org.txt
    db_save.txt
    {ts}_{db_path}/              ← 事件目录（唯一聚合点）
      __exp_main.txt
      main.dbg
      mobilelog/                 ← 该事件关联的 mobilelog
      bugreport/                 ← 该事件的 bugreport
```

**改造点**：
- `processor.py:211` `export_correlated_mobilelogs(output_dir=base_output_dir)` → `output_dir=local_target_dir`（事件目录）
- `processor.py:219` `export_bugreport_for_timestamp(output_dir=base_output_dir)` → `output_dir=local_target_dir`
- `mobilelog.py:51` `mobilelog_dir = output_dir / mobilelog/`（output_dir 已是事件目录）
- `bugreport.py` 同理

**改造理由**：归档-3 分类提取需按去重 Result_*.xls 的 db 路径定位到对应 mobilelog/bugreport，混放结构无法定位。

**路径举例**：
- folder_name = `X6851-OP_16.3.0.022_0527_MonkeyAEEinfo`（由 `ro.product.name` + `ro.build.display.id` + run_date 生成）
- serial = `R32M30F12345`
- db_path basename = `db.01`（`db.NN` 风格，非 `db_crashtime_...`）
- 事件目录名 = `2026_0527_101522_123_db.01`（时间戳格式 `%Y_%m%d_%H%M%S_{ms3}` + db_path basename）
- 完整路径 = `sonic_tinno/X6851-OP_16.3.0.022_0527_MonkeyAEEinfo/R32M30F12345/aee_exp/2026_0527_101522_123_db.01/mobilelog/main_log.2026_0527_101400`

## 关联实现

### 当前活跃
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

#### Watcher 子系统（2026-04-17 起；2026-06-18 契约补充）
- `backend/agent/watcher/sources.py` — InotifydSource + ProbeResult + WatcherCapability
- `backend/agent/watcher/batcher.py` — EventBatcher（immediate / batch 双路由）
- `backend/agent/watcher/emitter.py` — SignalEmitter（seq_no 单调分配 + outbox 落库）
- `backend/agent/watcher/device_watcher.py` — per-device 编排器
- `backend/agent/watcher/manager.py` — LogWatcherManager + WatcherHandle
- `backend/agent/watcher/puller.py` — 路径 A：per-device async adb pull → NFS + 富化（默认开，拉单个 AEE 文件 + 可选 bugreport）
- `backend/agent/watcher/policy.py` / `contracts.py` / `exceptions.py`
- `backend/agent/aee/reconciler.py` — 路径 B：AeeDbHistoryReconciler per-Job daemon（db_history diff → process_device_logs）；`STP_WATCHER_AEE_RECONCILE_ENABLED` 默认 `true`（2026-06-18 改）
- `backend/agent/aee/processor.py` — 路径 B 核心：AEE 整目录 pull + dblog 转储 + mobilelog 时间窗 + bugreport；`output_dir` 改为 `local_target_dir`（事件目录，Sprint 2 落地）
- `backend/agent/aee/mobilelog.py` — mobilelog 时间窗拉取（前后各 2 个 main+kernel）；输出改到事件目录内（Sprint 2 落地）
- `backend/agent/aee/bugreport.py` — bugreport 导出（300s 冷却）；输出改到事件目录内（Sprint 2 落地）
- `backend/agent/aee/paths.py` — NFS 路径解析（`resolve_device_output_dir` / `get_aee_nfs_root` / subdir 布局）
- `backend/agent/aee/folder_name.py` — folder_name 生成（`ro.product.name` + `ro.build.display.id` + run_date）
- `backend/agent/aee/db_history.py` — db_history 解析（`db.NN` 风格）+ 增量状态管理
- `backend/agent/aee/timestamp.py` — 时间戳格式化（`%Y_%m%d_%H%M%S_{ms3}`）
- `backend/agent/job_session.py` — Job lifecycle 绑定 watcher start/stop
- `backend/agent/artifact_uploader.py` — ArtifactUploader 进程级单例（fire-and-forget）
- `backend/agent/registry/local_db.py` — `log_signal_outbox` / `watcher_state` 表 + drain API
- `backend/alembic/versions/k9f0a1b2c3d4_add_watcher_lifecycle_fields.py` — watcher 生命周期字段
- `backend/alembic/versions/m1g2h3i4j5k6_add_job_active_per_device_unique.py` — 设备 active job 部分唯一索引
- `backend/alembic/versions/n2h3i4j5k6l7_add_job_artifact_uniq_and_source_fields.py` — JobArtifact 幂等键 + 来源字段
- `backend/api/routes/agent_api.py` — `POST /log-signals` + `POST /jobs/{id}/artifacts` + claim PENDING→RUNNING
- `backend/models/job.py` — `JobLogSignal` / `JobArtifact` ORM

### 已删除
- ~~`backend/mq/consumer.py`~~ — 已删除（由 SAQ + SocketIO 替代）
- ~~`backend/tasks/heartbeat_monitor.py`~~ — 已删除（由 APScheduler session_watchdog 替代）
- ~~`backend/api/routes/websocket.py`~~ — 已删除（Wave 8-3，2026-04-12）
- ~~`frontend/src/hooks/useWebSocket.ts`~~ — 已删除（Wave 7-3，2026-04-12）
- ~~`backend/core/metrics.py` 中 `websocket_*` 指标~~ — 已删除（Wave 7-4，2026-04-12）

## 关联 ADR

- **ADR-0025**（2026-06-18 修订）：D3 表拉取行改为「路径 B 默认开」+ D4 归档三阶段重定义。本 ADR 的 Watcher 日志类型契约（第 4 条）+ 存储结构改造是 ADR-0025 归档-1 搬运 + 归档-3 分类提取的前提。
- **ADR-0025 Sprint 2**：路径 B 默认开（`reconciler.py:151`）+ 存储结构改造（`processor.py` / `mobilelog.py` / `bugreport.py` 输出路径下沉到事件目录）的具体实现计划。
- 上一代工具参考：`MonkeyAEEinfo_260523.py`（`device_paths` / `bugreport` / `mobilelog_filter` 配置项 + CIFS 挂载 `//172.21.15.4/jxtinno/sonic_tinno`）。
