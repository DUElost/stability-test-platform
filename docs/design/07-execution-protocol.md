# 执行协议契约（Execution Protocol）

> **最后更新**：2026-09-21（#3003 校准 + §3 同步 ADR-0043 per-host 宽限时钟；本头部自本日起随正文同步）  
> **关联**：主链路概览见 [`01-execution-pipeline.md`](./01-execution-pipeline.md)；实现见近期 migration `c8d9e0f1a2b3`、preflight `backend/scripts/migration/preflight_execution_protocol.py`；abort 时钟主体见 [`ADR-0043`](../adr/ADR-0043-abort-grace-subject-alignment.md)。

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
| QUEUED | PRECHECK · FAILED（abort / 不可重试错误） |
| PRECHECK | QUEUED（竞争回队 / reaper stale recovery）· RUNNING · FAILED |
| RUNNING | SUCCESS · PARTIAL_SUCCESS · FAILED（三态，ADR-0048 v1.1） |
| FAILED | QUEUED（仅 `retry_plan_run_dispatch` / precheck 重试，回准入队列） |
| SUCCESS / PARTIAL_SUCCESS | ∅ |

聚合（`plan_run_aggregation.py`，语义 = **ADR-0048 v1.1**，2026-09-20）：

- 存在 UNKNOWN Job **不得**落终态。
- 全部 Job 落终态后：`aborted > 0` 或 `abort_requested` → FAILED（#783 保留）；
  `failed_only > 0` → **PARTIAL_SUCCESS**（黄，v1.1 恢复产出；台数多少都不判红、
  不断链、不触发 RUN_FAILED）；其余 → SUCCESS。
- **阈值判定轴仍废止**：`failure_threshold` 列/API/表单不回灌，判定输入只有
  `failed_only/aborted/abort_requested` 三个计数；#1591-④ 里程碑豁免不恢复。

**成败语义（#815 + ADR-0048 v1.1）**：SUCCESS / PARTIAL_SUCCESS / FAILED 描述**执行链**
结果（是否完整跑完、过程中有无设备失败、是否被人工中止），不是**测试结论**；
「failure_threshold 判红轴」不属于执行链轴：

- Job 终态由 lifecycle `termination_reason` 决定（`completed` / `timeout` → COMPLETED；
  `abort` / `manual_exit` → ABORTED；其余 → FAILED，见 `pipeline_engine`），teardown
  步骤的成功与否不改变 Job 终态（只进 `teardown_status` metadata）；
- 测试脚本自判的结论（`final_status=FAIL`、`failed_rounds>0`、设备侧 INCOMPLETE 等）
  不参与该聚合，落在 metrics / `test_case_result` 结果层呈现；
- 因此 **「PlanRun 绿」≠「测试通过」**——判定测试结果须消费结果层字段；INCOMPLETE
  按「收取即成功」处理是同一设计的有意边界。

前端通过 `PlanRun.capabilities`（abort / retry_dispatch / final_archive）与设备矩阵 `is_stuck` / deadline 字段消费权威投影，避免重复实现超时策略。

---

## 3. Abort（保租约 ACK）

`plan_run_abort.py` + Agent `control abort` + `device_lease_reconciler` abort reaper：

1. PENDING → 直接 ABORTED（释租约若存在）。
2. RUNNING → **保持 RUNNING**，写 abort 请求并**按请求主体落时钟**（ADR-0043 D1：请求主体 ≡ 计时主体）：
   run 级 abort 写 `run_context.abort_requested` 的 `at` / `deadline_at`（含 job 列表）；
   host 级 abort **不写 run 级 `at`**，只维护 `abort_requested` 的名单语义，计时落在
   `abort_requested_hosts[host_id].at`，且**首次写入、后续不重置**（D2）。随后按 **host**
   分发 SocketIO `command=abort`（只带本机 `requested_job_ids`）。
3. Agent 杀进程树 → `/complete` status=ABORTED → 释租约 → 聚合。
4. ACK 超时（`ABORT_REAPER_GRACE_SECONDS`）→ Job **UNKNOWN**（lease **不释放**）→ 再走 UNKNOWN grace → FAILED。
   reaper **按主体取时钟**：两把钟并存时取更早者（D1/D3），`abort_requested_hosts` 缺失的
   历史 run 退化为只看 run 级 `at`（D4，绝不变成无人回收）；回收主体差异落在 `status_reason`
   ——host 主体为 `abort_ack_timeout_host`、run 主体为 `abort_ack_timeout`（D6）。

禁止：在 Agent ACK 前释放 ACTIVE lease（避免设备被重新调度而旧进程仍存活）；
禁止 host 级 abort 写或重置 run 级 `at` —— 那正是 ADR-0043 删掉的「N×GRACE + 最后写入者赢」行为。
写入侧 `plan_run_abort.py::abort_plan_run` 的 host 级分支与消费侧
`device_lease_reconciler._reconcile_aborted_running_jobs` 都有测试钉子
（`backend/tests/api/test_plan_run_abort_api.py::test_host_abort_writes_host_clock_not_run_level_at`、
`backend/tests/scheduler/test_abort_reaper.py`）。

---

## 4. Claim 与版本门禁

`POST /agent/jobs/claim`：

- Host 须 ONLINE；容量 = 空闲设备计与 Agent capacity。Agent 侧认领槽位公式
  （`backend/agent/capacity_reporter.py`，#483）：`effective_slots =
  min(max(0, 在线健康设备 − 活跃设备), health_limit, STP_MAX_CLAIM_SLOTS 默认 5)`——
  `host.max_concurrent_jobs` 已删除（migration `q2r3s4t5u6v7`），空闲设备数之外
  的上限只有健康门控与认领上限两个。
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
- 原子：子 PlanRun + `next_plan_triggered`；子 Run 经 `prepare_plan_run` 落 **QUEUED**，
  由 admission pump + `plan_admission_task` 物化（ADR-0026 现行主路径）——历史
  sync gate 任务 `precheck_and_dispatch_task` 仅存于 V1 兜底/显式重试路径。
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
| abort / reaper | `test_plan_run_abort_*`、`test_abort_reaper.py`、`test_abort_subject_predicate_2270.py`（ADR-0043 主体取钟） |
| 链 | `test_plan_chain_trigger.py`、`test_plan_chain_e2e.py` |
| 版本门禁 | `test_agent_version_gate.py` |
| 前端 capabilities | `PlanRunDetailPage.test.tsx`（final_archive 等） |

---

> **关于本文的「最后更新」头部**：无机器校验（没有任何门禁把该日期与
> `git log -1 --format=%cs -- <本文件>` 对拍）。#3003 起 DOC-MAP 常驻义务要求改正文语义时同步
> 刷新本头部（做不到就删字段）；#2990 落地前正文已改多次而头部仍是 2026-07-15，即该义务写成前的漂证。
> 要么下次把它改成生成物（同 `environment-variables.md` 的附录块做法），要么按「可派生量不写死」（#2663）删掉。
