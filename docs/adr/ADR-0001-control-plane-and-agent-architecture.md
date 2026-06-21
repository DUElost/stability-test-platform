# ADR-0001: 控制面 + 执行面分层架构
- 状态：Accepted
- 日期：2026-02-18
- 决策者：平台研发组
- 标签：架构, 控制面, Agent, 分布式执行

## 背景

平台目标是“通过 Linux Host Agent 集群，无人值守运行大规模 Android 设备稳定性测试”，并要求后续可衔接报告与 JIRA 自动化流程。

## 决策

采用三层协作模型：

- 控制面（Control Plane）：FastAPI 提供 API、调度、状态管理、WebSocket 推送。
- 执行面（Execution Plane）：每台 Linux Host 运行常驻 Agent，负责设备发现、任务执行、心跳回传。
- 连通性验证层（Connectivity Layer）：对 SSH、挂载点、主机可达性进行监测与补偿。

该决策明确“中心调度 + 轻量 Agent”为平台主干，测试工具能力在 Agent 侧落地，控制面保持编排与状态权威。

## 备选方案与权衡

- 方案 A：纯中心化远程执行（控制面直接 SSH 到设备主机）。
  - 优点：架构简单、节点侧无常驻服务。
  - 缺点：扩展性和容错弱，长任务心跳与状态回传困难。
- 方案 B：当前方案（控制面 + Agent）。
  - 优点：可横向扩展，节点自治，任务生命周期可追踪。
  - 缺点：需要维护 Agent 生命周期和版本管理。

## 影响

- 正向影响：
  - 支持多主机并发执行和统一调度。
  - 任务执行与设备管理解耦，便于专项能力扩展。
- 代价：
  - 增加 Agent 发布、运维、主机接入的一致性要求。
  - 控制面与 Agent 之间的协议演进需要版本兼容策略。

## 落地与后续动作

- 已落地：控制面 API、~~调度线程~~（已由 APScheduler 替代）、Agent 心跳与拉取任务机制。
- ~~后续：完善 Agent 注册与版本协商机制，避免协议漂移。~~ → ✅ 已实现：`backend/agent/host_registry.py` 支持 `HOST_ID=AUTO` 自动注册 + heartbeat 携带 `agent_version` 字段（见 [ADR-0004](./ADR-0004-heartbeat-driven-host-device-liveness.md)）

> ⚠️ **实现路径偏差 (2026-06-12 勘误)**：
> - 原文"调度线程"不适用——后台调度已由 APScheduler 4.x（`backend/scheduler/app_scheduler.py`）替代，见 [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md)
> - Agent 不再通过 Redis Stream 拉取任务，改为 HTTP POST `/api/v1/agent/jobs/claim` + SocketIO `/agent` namespace
> - 异步后台任务由 SAQ Worker（`backend/tasks/saq_worker.py`）替代自研线程池
> - 实时推送由 python-socketio（`backend/realtime/socketio_server.py`）替代自研 WebSocket

## 关联实现/文档

- `backend/main.py`
- `backend/agent/main.py`
- `backend/agent/heartbeat.py`
- `backend/scheduler/app_scheduler.py` — APScheduler 4.x 统一调度器（ADR-0018 引入）
- `backend/tasks/saq_worker.py` — SAQ Worker（ADR-0018 引入）
- `backend/realtime/socketio_server.py` — python-socketio 服务端（ADR-0018 引入）
- `docs/project-vision.md`
- `docs/design/00-system-overview.md`（原 `stability-platform-integrated.md` 已归档删除）
