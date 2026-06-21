# 执行主链路：Plan → PlanRun → Job

> **状态**：Living  
> **关联 ADR**：ADR-0020、ADR-0021、ADR-0022、ADR-0019、ADR-0025

---

## 1. 端到端流程

```
[用户] Plan 编辑 / 定时 / Plan 链
    ↓
[控制面] POST /plans/{id}/run 或 SCHEDULE/CHAIN 触发
    ↓
[派发门禁] plan_precheck — 主机 SSH、脚本 sha、NFS 同步
    ↓
[分发] plan_dispatcher(_sync) — 创 PlanRun + N×JobInstance(PENDING)
    ↓
[Agent] GET /agent/jobs/pending → claim → RUNNING
    ↓
[pipeline_engine] init → patrol(循环) → teardown
    ↓ step_trace / patrol-heartbeat / log_signal
[控制面] 聚合 plan_run_aggregation → PlanRun 终态
    ↓
[后处理] dedup scan/merge、report、JIRA（ADR-0025）
```

---

## 2. Plan 触发方式

| run_type | 触发 | 派发路径 |
|----------|------|----------|
| MANUAL | UI「执行」 | SAQ → async precheck → dispatch |
| SCHEDULE | APScheduler cron | `dispatch_plan_sync` inline gate |
| CHAIN | 上游 PlanRun 终态 | `plan_chain_trigger` → sync gate |

派发失败：PlanRun / Job 显式 FAILED + 审计；支持 `retry-dispatch`（ADR-0021）。

---

## 3. 派发门禁（Precheck）

**服务**：`backend/services/plan_precheck.py`

| phase | 含义 |
|-------|------|
| verifying | SSH 可达性 |
| syncing | 脚本同步到 Agent 可访问路径 |
| reverifying | 同步后 sha 复核 |
| ready | 可创 Job |
| failed | 阻塞派发 |

状态写入 `PlanRun.run_context.precheck`；SocketIO `precheck_update` 推前端（`DispatchGateCard`）。

---

## 4. Job 生命周期

**状态机**：`backend/services/state_machine.py` + ADR-0003

| 状态 | 说明 |
|------|------|
| PENDING | 已创建，待 Agent claim |
| RUNNING | Agent 执行中 |
| COMPLETED / FAILED / ABORTED | 终态 |

**设备锁**：claim 时创 `device_leases` ACTIVE；续期 `LeaseRenewer`；异常由 `recycler` / `session_watchdog` 释放。

**超时**（`backend/core/job_timeout_config.py`）：

- PENDING 过久 → FAILED  
- RUNNING 心跳丢失 → UNKNOWN → grace → FAILED  
- Patrol 阶段独立心跳窗口（`PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS`）

---

## 5. Pipeline 阶段

```json
{
  "lifecycle": {
    "init": [ { "step_id", "action": "script:...", "version", "params", ... } ],
    "patrol": { "interval_seconds": 60, "steps": [ ... ] },
    "teardown": [ ... ],
    "timeout_seconds": 0
  }
}
```

| 阶段 | 行为 |
|------|------|
| init | 一次性：check_device、monkey_setup 等 |
| patrol | 周期执行 monkey_check；失败退避；manual-retry / manual-exit（ADR-0022） |
| teardown | 终态清理 monkey_teardown |

**Agent**：`pipeline_runner` → `pipeline_engine` → `ScriptRegistry` → subprocess 脚本。

---

## 6. PlanRun 聚合与 Plan 链

**服务**：`aggregator_sync.py` / `plan_run_aggregation.py`

- 全部 Job 终态后计算 PlanRun：SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED  
- `next_plan_id` 链式触发下游 Plan（`next_plan_triggered` 防重）  
- 终态可触发 dedup `scan_task`（`STP_DEDUP_AUTO_SCAN`）

**查询聚合 API**（PlanRun 详情页）：

| 端点 | 用途 |
|------|------|
| `GET .../chain` | Plan 链 |
| `GET .../timeline` | 三阶段时间线 |
| `GET .../events` | 多源事件流 |
| `GET .../devices` | 设备矩阵 |
| `GET .../watcher-summary` | Watcher 异常聚合 |
| `POST .../abort` | 中止 |

---

## 7. Watcher 与日志（Agent）

Job 执行期间 `JobSession` 绑定 `DeviceLogWatcher`：

1. 检测 AEE / 信号 → `POST /agent/log-signals`  
2. 路径 B 拉取 → **Agent HDD**（方案 C）  
3. 运行日志写 **Agent SSD** `logs/runs/{job_id}/`

控制面：`watcher-summary`、`crash-details`、AnomalyDashboard。

---

## 8. 去重与 JIRA（ADR-0025）

### 8.1 调用模型

- **scan / merge / extract**：绑定 **PlanRun**；由 `RunConsole` 通过 **subprocess** 调用外部工具（Py3.7/3.8 解释器），**非 import** 进后端进程。
- **jira 提单**：与 PlanRun **解耦** → `/api/v1/jira/runs`（运维上传复核后的 xls）。

未配置工具 env 时，去重入口 disabled / 后端 409，不影响主链。

### 8.2 端点

| 环节 | 端点 |
|------|------|
| scan | `POST/GET /api/v1/plan-runs/{id}/dedup/scan`、`.../status` |
| merge | dedup merge API |
| extract | PlanRun 终态确认后 extract |
| jira | `POST/GET /api/v1/jira/runs` |

### 8.3 环境变量（控制面）

| 变量 | 说明 |
|------|------|
| `STP_DEDUP_SCAN_PYTHON` | start_log_scan 解释器 |
| `STP_DEDUP_SCAN_SCRIPT` | `start_log_scan.py` 路径 |
| `STP_JIRA_TOOL_PYTHON` | Jira-Automation 解释器 |
| `STP_JIRA_TOOL_DIR` | 厂商 Jira 工具目录 |
| `STP_DEDUP_AUTO_SCAN` | 终态自动触发 scan |

### 8.4 方案 C 演进

scan 输入路径从「15.4 NFS archives」改为 **Agent 本地 HDD**（Sprint 4）；`check_archive_completed` 不得再依赖 `run_log_bundle`。见 [`2026-plan-c-storage-and-access.md`](./2026-plan-c-storage-and-access.md) §4.2。

**历史详设（已归档）**：[`archive/migrations/adr-0025-dedup-integration-design-2026-06-16.md`](../archive/migrations/adr-0025-dedup-integration-design-2026-06-16.md)

---

## 9. 关键代码索引

| 步骤 | 文件 |
|------|------|
| 触发 PlanRun | `api/routes/plans.py` |
| Precheck | `services/plan_precheck.py` |
| 分发 Job | `services/plan_dispatcher_sync.py` |
| Agent claim | `api/routes/agent_api.py` |
| 执行 | `agent/pipeline_engine.py` |
| 聚合 | `services/aggregator_sync.py` |
| 前端详情 | `pages/execution/PlanRunDetailPage.tsx` |

---

## 10. 验收

- 主链集成：`backend/tests/integration/test_main_chain_happy_path.py`  
- 平台冒烟：[`acceptance/00-platform-smoke.md`](../acceptance/00-platform-smoke.md)
