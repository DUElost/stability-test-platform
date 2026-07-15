# 执行协议契约（Execution Protocol）

> **最后更新**：2026-07-15  
> **关联**：主链路概览见 [`01-execution-pipeline.md`](./01-execution-pipeline.md)；实现见近期 migration `c8d9e0f1a2b3`、preflight `backend/scripts/migration/preflight_execution_protocol.py`。

本文记录 **PlanRun / Job / Agent** 的硬契约：状态机边界、abort、claim 门禁、snapshot 派发与 schema 约束。产品叙述级流程仍以 `01` 为准。

---

## 1. Job 状态机（权威）

`backend/services/state_machine.py`：

| From | To |
|------|-----|
| PENDING | RUNNING · FAILED · ABORTED |
| RUNNING | COMPLETED · FAILED · ABORTED · UNKNOWN |
| UNKNOWN | RUNNING · FAILED（**禁止**直接 COMPLETED） |
| COMPLETED / FAILED / ABORTED | ∅ |

要点：

- **终态写入唯一入口**：`POST /api/v1/agent/jobs/{id}/complete`。`/status` 与 heartbeat **只接受 RUNNING**；试图上报终态 → `TERMINAL_STATUS_REQUIRES_COMPLETE`。
- **UNKNOWN**：围栏恢复态。晚到完成须先续约/恢复为 RUNNING（token 匹配）再 complete；或 grace 到期 → FAILED + 释租约。
- **`terminal_payload_digest`**：同 payload 幂等；冲突 → 409 `TERMINAL_PAYLOAD_CONFLICT`。
- **`trace_event_id`**：step_trace 幂等键（含 retry/cycle）；取代旧 `(job_id, step_id, event_type)` 唯一约束。

### 设备并发约束（DB）

- `uq_job_instance_plan_run_device`：同一 PlanRun × device 至多一行 Job。
- `uq_job_active_per_device`：同一 device 至多一个 `PENDING|RUNNING|UNKNOWN` Job（部分唯一索引）。

升级前跑：`python -m backend.scripts.migration.preflight_execution_protocol`。

---

## 2. PlanRun 状态机

| From | To |
|------|-----|
| RUNNING | SUCCESS · PARTIAL_SUCCESS · FAILED |
| FAILED | RUNNING（仅 `retry_plan_run_dispatch` / precheck 重试） |
| SUCCESS / PARTIAL_SUCCESS / DEGRADED | ∅（DEGRADED **仅历史可读**，聚合不再生产） |

聚合（`plan_run_aggregation.py`）：

- 存在 UNKNOWN Job **不得**落终态。
- 全部 Job 进入 COMPLETED/FAILED/ABORTED 后计算；`abort_requested` 会把自然 SUCCESS/PARTIAL 覆盖为 FAILED。

前端通过 `PlanRun.capabilities`（abort / retry_dispatch / final_archive）与设备矩阵 `is_stuck` / deadline 字段消费权威投影，避免重复实现超时策略。

---

## 3. Abort（保租约 ACK）

`plan_run_abort.py` + Agent `control abort` + `device_lease_reconciler` abort reaper：

1. PENDING → 直接 ABORTED（释租约若存在）。
2. RUNNING → **保持 RUNNING**，写 `run_context.abort_requested`（含 `deadline_at`、job 列表），按 **host** 分发 SocketIO `command=abort`（只带本机 job_ids）。
3. Agent 杀进程树 → `/complete` status=ABORTED → 释租约 → 聚合。
4. ACK 超时（`ABORT_REAPER_GRACE_SECONDS`）→ Job **UNKNOWN**（lease **不释放**）→ 再走 UNKNOWN grace → FAILED。

禁止：在 Agent ACK 前释放 ACTIVE lease（避免设备被重新调度而旧进程仍存活）。

---

## 4. Claim 与版本门禁

`POST /agent/jobs/claim`：

- Host 须 ONLINE；容量 = 空闲设备计与 Agent capacity。
- 仅 `PlanRun.status=RUNNING` 且未 abort 的 PENDING。
- `agent_version` 可选；仅当控制面设置了 `STP_AGENT_MIN_VERSION` 时比较（短版本按数字段零填充，如 `2.1` → `2.1.0`）。未设置门控 → 不拦截。

Watcher policy 取自 **PlanRun.plan_snapshot**，不再读 live `Plan.watcher_policy`。

---

## 5. Snapshot 派发

`prepare_plan_run` 写入不可变 `plan_snapshot`（含 `next_plan_id`、`timeout_seconds`、`auto_archive_interval_seconds`、步骤 sha 等）。

`complete_plan_run_dispatch`：

- 物化 lifecycle 仅来自 snapshot（`build_lifecycle_from_snapshot`）。
- `abort_requested` 或非 RUNNING → 跳过建 Job。
- ACTIVE 语义含 UNKNOWN（占容量，防双开）。

巡航约束：有 enabled patrol steps **当且仅当** `patrol_interval_seconds` 已设（API 422 + dispatch gate）。

---

## 6. Plan 链

- 触发读 snapshot 的 `next_plan_id`；旧 Run 缺键时 **fallback** live `Plan.next_plan_id`。
- 原子：子 PlanRun + `next_plan_triggered`；gate 经 SAQ `precheck_and_dispatch_task`。
- 补偿：`scheduler/plan_chain_reconciler.py` + `reconcile_chain_trigger_sync`（孤儿 flag / 缺子 Run）。
- enqueue 失败后：子 Run 可由 `precheck_reaper` 补队列。

---

## 7. Agent 运行时要点

- `PipelineEngine.cancel`：杀进程组（含 abort / fencing）。
- step_trace：`trace_event_id` = hash(run, fencing, stage, step, event, retry, cycle)。
- pipeline 校验异常须仍能 `/complete` FAILED，避免无终态 ACK。
- 热更新 tarball 含 `stp_schemas/pipeline_schema.json`。

---

## 8. 相关测试

| 区域 | 用例入口（示例） |
|------|------------------|
| 协议 / complete | `backend/tests/api/test_agent_dual_write.py` |
| abort / reaper | `test_plan_run_abort_*`、`test_abort_reaper.py` |
| 链 | `test_plan_chain_trigger.py`、`test_plan_chain_e2e.py` |
| 版本门禁 | `test_agent_version_gate.py` |
| 前端 capabilities | `PlanRunDetailPage.test.tsx`（final_archive 等） |
