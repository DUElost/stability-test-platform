# 后端技术设计

> **入口**：`backend/main.py`  
> **状态**：Living（路由以代码为准；Swagger `/docs` 为运行时补充）

---

## 1. 目录结构

```
backend/
├── main.py              # FastAPI + lifespan + router 挂载
├── api/routes/          # HTTP 路由（23 模块）
├── api/schemas/         # Pydantic 请求/响应
├── services/            # 业务逻辑
├── models/              # SQLAlchemy ORM
├── core/                # 数据库、安全、CSRF、指标、限流
├── scheduler/           # APScheduler 回调
├── tasks/               # SAQ 异步任务
├── realtime/            # SocketIO、日志写文件
├── connectivity/        # SSH、挂载检查
├── alembic/             # 数据库迁移
├── agent/               # Agent 源码（与 control plane 同仓）
└── tests/               # 控制面测试
```

---

## 2. 应用启动（lifespan）

`backend/main.py` 启动顺序（`TESTING=1` 时跳过基础设施）：

1. 生产 guard（Cookie、CSRF、注册开关等）  
2. Redis PING  
3. APScheduler jobs：recycler、cron、precheck_reaper、revoked_token_cleanup 等  
4. `RunConsole` 子进程管理器  
5. SAQ in-process worker（`STP_ENABLE_INPROCESS_SAQ`）

**ASGI**：`app = socketio.ASGIApp(sio_server, fastapi_app)`  
**中间件**（外→内）：CORS → RateLimit → CSRF

---

## 3. API 路由一览

| 路由模块 | 前缀 | 职责 |
|----------|------|------|
| `auth.py` | `/api/v1/auth` | 登录、Cookie、refresh、logout |
| `users.py` | `/api/v1/users` | 用户管理 |
| `hosts.py` | `/api/v1/hosts` | 主机 CRUD、热更新 |
| `devices.py` | `/api/v1/devices` | 设备 CRUD、状态 |
| `heartbeat.py` | `/api/v1` | Agent 心跳 |
| `plans.py` | `/api/v1` | Plan CRUD、run、preview |
| `plan_runs.py` | `/api/v1` | PlanRun 查询、聚合、abort、manual-retry |
| `agent_api.py` | `/api/v1/agent` | claim、complete、artifacts、log-signals |
| `runs.py` | `/api/v1` | Job 报告、步骤、产物下载 |
| `logs.py` | `/api/v1` | 运行时日志查询 |
| `scripts.py` | `/api/v1/scripts` | 脚本目录 scan/版本 |
| `pipeline.py` | `/api/v1/pipeline` | Pipeline 模板 |
| `schedules.py` | `/api/v1/schedules` | 定时调度 |
| `dedup.py` | `/api/v1/jira` + `/api/v1/plan-runs` | JIRA 草稿、dedup scan/merge |
| `resource_pools.py` | `/api/v1/resource-pools` | WiFi 等资源池 |
| `audit.py` | `/api/v1/audit-logs` | 审计日志 |
| `notifications.py` | `/api/v1/notifications` | 通知规则 |
| `action_templates.py` | `/api/v1/action-templates` | Action 模板 |
| `results.py` | `/api/v1/results` | 结果查询 |
| `stats.py` | `/api/v1/stats` | 统计 |
| `metrics.py` | `/metrics` | Prometheus |
| `devices.py` | `/api/v1/devices` | 设备 |

完整路径表见 [`CLAUDE.md`](../../CLAUDE.md) §对外接口。

---

## 4. 核心服务

| 服务 | 职责 |
|------|------|
| `plan_dispatcher_sync.py` | 同步派发、创 Job、写 pipeline_def |
| `plan_precheck.py` | 派发门禁状态机 |
| `plan_run_aggregation.py` | PlanRun 级聚合查询（chain/timeline/events/…） |
| `aggregator_sync.py` | PlanRun 终态计算、链式触发、dedup enqueue |
| `plan_run_abort.py` | 中止 PlanRun |
| `plan_chain_trigger.py` | Plan 链下游触发 |
| `lease_manager.py` | 设备租约 |
| `reconciler.py` | 过期租约 / 僵死 Job 回收 |
| `script_catalog.py` | 脚本扫描入库 |
| `report_service.py` | Job 报告、risk_summary、JIRA 草稿 |
| `dedup_scan.py` | 去重 scan 完成判定、路径解析 |
| `run_console.py` | dedup 子进程 RunConsole |
| `post_completion.py` | Job 完成后 SAQ 任务 |
| `notification_service.py` | 通知发送 |
| `token_blacklist.py` | Refresh token 吊销 |
| `host_updater.py` | Agent 热更新 Ansible/SSH |

---

## 5. 数据访问

- **同步引擎**：路由内 `get_db()` Session（psycopg3）  
- **异步引擎**：部分实时/长连接场景  
- **迁移**：仅 Alembic（ADR-0008）；`cd backend && python -m alembic upgrade head`

ORM 见 [`05-data-model.md`](./05-data-model.md)。

---

## 6. 认证与授权

| 场景 | 机制 |
|------|------|
| 浏览器 | HttpOnly Cookie + CSRF（ADR-0024） |
| Swagger/脚本 | `POST /auth/token` Bearer |
| Agent | `X-Agent-Secret` + SocketIO auth |
| Admin 路由 | 前端 `AdminRoute` + 后端 role 校验 |

---

## 7. 与 Agent 的边界

| 控制面 | Agent |
|--------|-------|
| 权威 Job/Plan 状态 | 执行 pipeline、上报 step_trace |
| PostgreSQL | SQLite LocalDB（outbox、watcher_state） |
| 聚合展示 | Watcher 拉取、本地 HDD/SSD |
| dedup merge | 本地 scan（Sprint 4） |

---

## 8. 测试

- 目录：`backend/tests/`（`api/`、`services/`、`integration/`）  
- 见 [`development/testing.md`](../development/testing.md)
