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

---

## 9. ADR-0025 终态 dedup 管道时序

PlanRun 进入终态（SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED）后，控制面自动或手动触发以下管道：

```
PlanRun 终态
  └→ enqueue_dedup_terminal_sync → scan_task
       ├→ emit scan_now → 各 ONLINE Agent
       │    ├→ ScanRunner.run_local_scan → _org.xls on HDD
       │    └→ UploadManager.upload_scan_report → NFS dedup/{run_id}/{host_id}_Result_*_org.xls
       ├→ poll NFS dedup/{plan_run_id}/ (10s × 30 = 300s max)
       │    等待 registered >= len(triggered_host_ids) 或超时
       ├→ run_scan_sync → PlanRunArtifact(scan_result_xls) 注册 DB
       ├→ enqueue upload_task
       │    └→ emit upload_events → Agent upload_event_dirs → NFS devices/{run_id}/
       └→ enqueue merge_task
            └→ run_merge_sync → PlanRunArtifact(merge_result_xls) 注册 DB
```

### 时序依赖

| 步骤 | 依赖 | 路径 | 说明 |
|------|------|------|------|
| scan_task | — | 入口 | 链式入口，内部串行 enqueue upload + merge |
| upload_task | scan_task 完成 | `devices/{run_id}/` | 与 merge_task 可并行（读/写不同 NFS 子目录） |
| merge_task | scan_task 完成 | `dedup/{run_id}/` | 读 scan 产物 _org.xls，产出 merge xls |
| extract | merge_task 完成 | `devices/` → `jira/{run_id}/` | 需参考 merge xls 确认 db 路径 |

- **多 host**：`scan_task` poll 等待所有 triggered host 的 artifact 或超时
- **Agent 上送**：`upload_scan_report` 与 `upload_event_dirs` 由 Agent daemon thread 分别执行

### 五触发场景

| # | 场景 | is_final | 触发方式 | 说明 |
|---|------|----------|---------|------|
| 1 | 终态自动 | True | aggregator enqueue | PlanRun 终态自动触发 |
| 2 | abort | True | 前端确认后 enqueue | 用户 abort → FAILED |
| 3 | FAILED/DEGRADED | True | 前端确认后 enqueue | 中断/失败需用户确认 |
| 4 | 手动归档 | True | POST /archive | 同时触发 archive_now + scan_now |
| 5 | 自动归档间隔 | 首次 True / 增量 False | auto_archive_sweep 周期 | 已有 scan 时增量 re-scan |

### Agent 端事件目录命名

自动发现（`event_dir_names` 为空时）仅匹配 `YYYY-MM-DD_HH-MM-SS_*` 时间戳前缀的直接子目录，不递归、不匹配非时间戳目录。
