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

**多实例（ADR-0027）**：`STP_SOCKETIO_REDIS_ADAPTER=1` 时挂载 `AsyncRedisManager`（dashboard / `agent:{host_id}` room 跨进程）。P3-3：`STP_AGENT_SID_REGISTRY`（默认跟随 adapter）登记 `host_id` owner；`call_agent_rpc` 本地 sid 未命中时走 room 投递，**不再要求 LB sticky**。

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

**约束**：默认单进程后端。ADR-0027 P3-3：除 `saq_queue_depth_poll` 外，全部 singleton job 经 leader election（`admission_pump` / `counter_reconcile` 为函数内 leadership，其余经 `_instrumented(..., singleton=True)`）。

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
       ├→ enqueue upload_task ─────────────────────────────┐
       │    └→ emit upload_events → Agent upload_event_dirs │ 并行
       │         → NFS devices/{run_id}/                    │
       └→ enqueue merge_task ───────────────────────────────┘
            ├→ run_merge_sync → PlanRunArtifact(merge_result_xls) 注册 DB
            ├→ poll upload:{run_id} SAQ job 至 complete/failed/aborted
            ├→ poll NFS devices/{run_id}/ 直至出现时间戳事件目录（10s × 30 = 300s max）
            └→ enqueue extract_task
                 └→ copy devices/ + merge xls → jira/{run_id}/
```

### 时序依赖

| 步骤 | 依赖 | 路径 | 说明 |
|------|------|------|------|
| scan_task | — | 入口 | 链式入口，poll 完成后 enqueue upload + merge |
| upload_task | scan_task 完成 | `devices/{run_id}/` | 与 merge_task 可并行（读/写不同 NFS 子目录） |
| merge_task | scan_task 完成 | `dedup/{run_id}/` | 读 scan 产物 _org.xls，产出 merge xls |
| extract_task | upload_task **与** merge_task 均完成 | `devices/` → `jira/{run_id}/` | merge 成功后 poll `upload:{run_id}` SAQ job + poll NFS `devices/`；任一超时仍 enqueue extract（best-effort） |
| merge_task SAQ timeout | — | — | `_MERGE_TASK_SAQ_TIMEOUT` = 300 + 660 + 300 + 120s，覆盖子进程与两轮 poll |

- **多 host**：`scan_task` poll 等待所有 triggered host 的 artifact 或超时
- **Agent 上送**：`upload_scan_report` 与 `upload_event_dirs` 由 Agent daemon thread 分别执行

### 五触发场景

| # | 场景 | is_final | 触发方式 | 说明 |
|---|------|----------|---------|------|
| 1 | 终态自动 | True | aggregator enqueue | PlanRun 终态自动触发 |
| 2 | abort | True | 前端确认后 enqueue | 用户 abort → FAILED；scan/merge 自动，仅 extract 需确认 |
| 3 | FAILED/DEGRADED | True | 前端确认后 enqueue | 中断/失败；scan/merge 自动，仅 extract 需确认 |
| 4 | 手动归档 | True | POST /archive | 同时触发 archive_now + scan_now |
| 5 | 自动归档间隔 | RUNNING：增量 False；终态：仅首次 True | `auto_archive_sweep` 周期（默认 120s） | 见下节 |

### auto_archive_sweep 选型与节流（2026-06-27）

每个配置了 `Plan.auto_archive_interval_seconds` 的 Plan，**每轮 sweep 最多 enqueue 一条 PlanRun**：

1. **有 RUNNING PlanRun** → 选该活跃 run，按 interval 做增量 scan（`is_final=False`）。
2. **无 RUNNING** → 选该 Plan **最新终态 run**（`max(id)`）；仅在 `ended_at + interval` 之后且 **尚无** `scan_result_xls` artifact 时触发 **一次** 终态 scan（`is_final=True`）。
3. **终态 run 已有 scan artifact** → **不再扫描**（避免历史终态 run 被周期性 re-scan）。

Agent 侧 **`scan_now` 同 host 串行执行**：单 worker 线程 + FIFO 队列；**同一 `plan_run_id` 在队列中合并为最新一条**（coalesce）。正在执行的 scan 不可中断；busy 期间新来的同 run 请求入队等待，不再 `busy_skip` 丢弃。控制面 SAQ `scan_task` 仍按 NFS poll 等待 artifact（最长 300s），与 Agent 队列独立。

### Agent 端事件目录命名

自动发现（`event_dir_names` 为空时）仅匹配 `YYYY-MM-DD_HH-MM-SS_*` 时间戳前缀的直接子目录，不递归、不匹配非时间戳目录。
