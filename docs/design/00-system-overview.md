# 系统总览

> **状态**：Living（与 `main` 代码对齐）  
> **关联**：ADR-0001、ADR-0020、ADR-0018、ADR-0025

---

## 1. 产品定位

**稳定性测试管理平台**：中心化调度 Android 设备执行 **Plan** 编排的稳定性专项，采集日志与崩溃信号，支持长跑无人值守、结果聚合与去重/JIRA 后处理。

详见 [`prd/00-platform-overview.md`](../prd/00-platform-overview.md)、[`project-vision.md`](../project-vision.md)。

---

## 2. 物理部署

```
┌─────────────────────────────────────────────────────────────┐
│  控制平面（Windows 开发 / Linux 生产）                        │
│  FastAPI :8000  ·  React :5173  ·  PostgreSQL  ·  Redis      │
│  APScheduler（进程内）·  SAQ Worker（进程内）·  SocketIO       │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP REST + SocketIO (/agent, /dashboard)
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌──────────┐     ┌──────────┐      ┌──────────┐
   │ React    │     │ Agent 1  │  …   │ Agent N  │
   │ Dashboard│     │ (Linux)  │      │ (Linux)  │
   └──────────┘     └────┬─────┘      └────┬─────┘
                         │ ADB             │ ADB
                    Android 设备       Android 设备
```

| 组件 | 默认端口 | 说明 |
|------|----------|------|
| 后端 API | 8000 | FastAPI；生产经 Nginx 反代 |
| 前端 | 5173（dev）/ 80/443（prod） | Vite + React |
| PostgreSQL | 5432 | 唯一生产库 |
| Redis | 6379 | SAQ 任务队列 |
| Agent HTTP（运行日志） | 8900 | 方案 C；见 [`2026-plan-c-storage-and-access.md`](./2026-plan-c-storage-and-access.md) |

**网络**：局域网 `172.21.15.*`；中心存储 `172.21.15.4`（CIFS/NFS）。

---

## 3. 逻辑分层

| 层 | 职责 | 代码位置 |
|----|------|----------|
| **控制平面** | Plan CRUD、派发、聚合、UI API、审计、调度 | `backend/api/`、`backend/services/` |
| **执行平面（Agent）** | 拉 Job、跑 pipeline、ADB、Watcher、本地归档 | `backend/agent/` |
| **数据** | PostgreSQL ORM、Agent SQLite LocalDB | `backend/models/`、`backend/agent/registry/local_db.py` |
| **实时** | SocketIO 推送、日志落盘 | `backend/realtime/` |
| **后台** | 回收、Cron、清理、SAQ 异步任务 | `backend/scheduler/`、`backend/tasks/` |

入口：`backend/main.py` → `socketio.ASGIApp(sio_server, fastapi_app)`。

---

## 4. 核心领域模型（ADR-0020）

```
Plan + PlanStep（编排定义）
    ↓ POST /plans/{id}/run
PlanRun（一次专项执行）
    ↓ 按设备扇出
JobInstance（单设备执行，含 pipeline_def JSON）
    ↓ Agent claim
StepTrace / JobArtifact / JobLogSignal（运行时数据）
```

- **不再有** WorkflowDefinition / TaskTemplate / WorkflowRun。  
- Plan 的 `lifecycle` **不存表列**，由 `PlanStep` 行 + `patrol_interval_seconds` / `timeout_seconds` 在派发时组装。

详见 [`05-data-model.md`](./05-data-model.md)、[`01-execution-pipeline.md`](./01-execution-pipeline.md)。

---

## 5. Pipeline 执行契约

- 引擎只接受 `pipeline_def.lifecycle` 顶层键。  
- **唯一 action 类型**：`script:<name>`（`backend/agent/pipeline_engine.py`）。  
- 脚本目录：`backend/agent/scripts/<name>/v<version>/`；参数来自 Script `default_params`（不可变，新版本新建）。

---

## 6. 存储拓扑（方案 C 摘要）

| 位置 | 内容 |
|------|------|
| Agent SSD | 运行日志 `logs/runs/{job_id}/` |
| Agent HDD | AEE 事件目录（第一落点） |
| 15.4 CIFS | 汇总 xls、按需事件、HDD 溢出；**不含**全量运行日志 |

完整设计：[`2026-plan-c-storage-and-access.md`](./2026-plan-c-storage-and-access.md)。

---

## 7. 关键横切能力

| 能力 | ADR | 实现要点 |
|------|-----|----------|
| 设备租约 | ADR-0019 | `device_leases`、fencing_token、Recycler |
| 派发门禁 | ADR-0021 | `plan_precheck`、脚本 sha 对齐 |
| Patrol 心跳 | ADR-0022 | `patrol-heartbeat` 聚合、退避、manual-retry |
| 浏览器会话 | ADR-0024 | HttpOnly Cookie、CSRF、refresh 黑名单 |
| Watcher | ADR-0018 | `log_signal`、路径 B、JobSession |
| 去重/JIRA | ADR-0025 | dedup scan/merge、RunConsole、`/jira/runs` |

---

## 8. 文档与代码同步

| 主题 | 文档 |
|------|------|
| 后端模块 | [`02-backend.md`](./02-backend.md) |
| 前端 | [`03-frontend.md`](./03-frontend.md) |
| Agent | [`04-agent.md`](./04-agent.md) |
| 实时/后台 | [`06-realtime-and-background.md`](./06-realtime-and-background.md) |
| API 端点表 | [`CLAUDE.md`](../../CLAUDE.md) §对外接口 |
| 待删旧文档 | [`DOC-RETIREMENT.md`](../DOC-RETIREMENT.md) |
