# 实时通信与后台任务

> **关联 ADR**：ADR-0002、ADR-0006、ADR-0009、ADR-0018

---

## 1. 实时通道架构

```
Agent ──SocketIO /agent──► backend/realtime/socketio_server.py
                                │
                                ▼ broadcast
Frontend ◄──SocketIO /dashboard──┘
```

**挂载**：`socketio.ASGIApp(sio_server, fastapi_app)`（`main.py`）

| Namespace | 客户端 | 典型事件 |
|-----------|--------|----------|
| `/agent` | `agent/socketio_client.py` | 日志流、状态、RPC |
| `/dashboard` | `useSocketIO` | `job_status`、`plan_run_status`、`watcher_signal`、`precheck_update` |

**鉴权**：`AGENT_SECRET`；生产必配。  
**Legacy**：`/ws/agent/{id}` 等为 deprecated stub。

---

## 2. 日志持久化

| 路径 | 组件 | 说明 |
|------|------|------|
| 控制面运行日志 | `realtime/log_writer.py` | 写本地文件；非 Loki |
| Agent 运行日志 | SSD `logs/runs/` | 方案 C 不上送中心 |
| Agent 实时 | SocketIO 推 dashboard | 详情页 XTerminal |

---

## 3. APScheduler（进程内）

`backend/scheduler/app_scheduler.py`

| Job | 模块 | 间隔/触发 |
|-----|------|-----------|
| Recycler | `recycler.py` | ~15s 过期租约/僵死 Job |
| Cron 调度 | `cron_scheduler.py` | Plan 定时 |
| Precheck reaper | `precheck_reaper.py` | 卡住门禁清理 |
| Device lease reconciler | `device_lease_reconciler.py` | 租约一致性 |
| Revoked token cleanup | `revoked_token_cleanup.py` | 24h refresh 黑名单 |

**约束**：单进程后端；多实例会重复调度（ADR-0025 D1 推迟水平扩展）。

---

## 4. SAQ 异步队列

| 组件 | 说明 |
|------|------|
| `tasks/saq_worker.py` | 进程内 worker（Redis broker） |
| `tasks/saq_tasks.py` | post_completion、通知、dedup scan 等 |
| `STP_ENABLE_INPROCESS_SAQ` | 生产建议开启 |

**派发**：MANUAL PlanRun 的 async precheck 常经 SAQ；Redis 不可用 → 503（显式失败）。

---

## 5. RunConsole

`services/run_console.py` — dedup 子进程生命周期；lifespan 内启动，退出时收尾（ADR-0025 §8.3）。

---

## 6. Session Watchdog

`tasks/session_watchdog.py` — 会话/租约看门狗，与 ADR-0003/0004 配合。

---

## 7. Prometheus 指标

`backend/core/metrics.py` + `GET /metrics`

| 指标族 | 场景 |
|--------|------|
| `stability_plan_run_*` | PlanRun 终态 |
| `stability_dispatch_gate_*` | 派发门禁 |
| `stability_patrol_*` | Patrol 心跳 |
| `stability_csrf_rejected_total` | CSRF |
| `stability_log_signal_total` | Watcher |
| `stability_apscheduler_job_*` | 调度 job |

告警草案：`deploy/prometheus/alerts-stability-platform.yml`（ADR-0011 待运维挂载）。

---

## 8. 测试

- `backend/tests/realtime/` — SocketIO  
- `backend/tests/tasks/` — SAQ  
- `backend/tests/scheduler/` — 调度回调
