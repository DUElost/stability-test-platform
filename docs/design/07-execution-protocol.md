# 执行协议契约（Execution Protocol）

> **最后更新**：2026-10-24（B6 执行与设备归还协议，生效时点见 §9）
> **关联**：主链路概览见 [`01-execution-pipeline.md`](./01-execution-pipeline.md)；实现见近期 migration `c8d9e0f1a2b3`、preflight `backend/scripts/migration/preflight_execution_protocol.py`；abort 时钟主体见 [`ADR-0043`](../adr/ADR-0043-abort-grace-subject-alignment.md)。

本文记录 **PlanRun / Job / Agent** 的硬契约：状态机边界、abort、claim 门禁、snapshot 派发与 schema 约束。产品叙述级流程仍以 `01` 为准。

> **B6 生效边界**：B6 代码合入 main 不代表生产已激活。[#3723](https://github.com/DUElost/stability-test-platform/issues/3723)
> §5 的激活证据发布前，运维执行者必须沿用本文标明的激活前分支。§9 的每项 B6 行为均自 B6 激活起生效；
> 依据为 [ADR-0061 Accepted v1.1](../adr/ADR-0061-device-return-contract.md) 与 #3723 当前正文 v1.7.19。

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
- **UNKNOWN**：围栏恢复态。Agent 对晚到完成必须先续约/恢复为 RUNNING（token 匹配）再 complete；或 grace 到期 → FAILED。
  B6 激活前随后释放租约；自 B6 激活起，控制面必须经 §9.5 的统一入口决定释放或转换为欠账租约（ADR-0061 D3 第 5 条）。
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
  步骤的成功与否不改变 Job 终态；B6 激活前 teardown 结论只进 `teardown_status` metadata，
  自 B6 激活起另存 §9.1 的 `disarm_results`（ADR-0061 D4）；
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
3. B6 激活前：Agent 杀进程树 → `/complete` status=ABORTED → 释租约 → 聚合。
   自 B6 激活起：Agent 必须保留解除的执行机会（§9.2），再经 `/complete` 回报 ABORTED 与解除结果。
   控制面必须在同一终态事务内经统一入口处理租约（§9.5），随后聚合（ADR-0061 D3 第 1–5 条）。
4. ACK 超时（`ABORT_REAPER_GRACE_SECONDS`）→ Job **UNKNOWN**（lease **不释放**）→ 再走 UNKNOWN grace → FAILED。
   reaper **按主体取时钟**：两把钟并存时取更早者（D1/D3），`abort_requested_hosts` 缺失的
   历史 run 退化为只看 run 级 `at`（D4，绝不变成无人回收）；回收主体差异落在 `status_reason`
   ——host 主体为 `abort_ack_timeout_host`、run 主体为 `abort_ack_timeout`（D6）。
   自 B6 激活起，有效解除阶段的有界保护先于本步超时回收判定，保护失效后仍按现行规则回收（§9.2；ADR-0061 D3 第 8 条）。

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

- `PipelineEngine.cancel`：B6 激活前杀进程组（含 abort / fencing）；自 B6 激活起，普通取消保留正在执行的 teardown，
  确认失租时 Agent 不得继续解除（§9.2；ADR-0061 D3 第 3、7 条）。
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

## 9. B6 执行与设备归还（自 B6 激活起生效）

本节全部行为自 B6 激活起生效。激活前的开关机人工处置与出口仍见
[`powercycle-chain-disarm-runbook.md`](../operations/powercycle-chain-disarm-runbook.md)。

### 9.1 终态载荷与足迹（自 B6 激活起生效）

Agent 必须在 `POST /api/v1/agent/jobs/{job_id}/complete` 的可选字段 `disarm_results` 中，
按专项回报本 job 在 init 中开始执行建立足迹步骤的专项。条目键为专项，`result` 为下列六值之一，
条目同时保留步骤、退出码、原因与回读证据：

| `result` | 含义 |
|---|---|
| `confirmed` | 已确认：解除成功，可信探针本次回读无足迹 |
| `executed_unverified` | 已执行未验证：解除成功，组合没有可信验证 |
| `failed` | 失败：解除失败，或探针发现足迹 |
| `readback_failed` | 回读失败：解除动作完成，可信探针未取得读数 |
| `not_executed` | 未执行：解除没有开始 |
| `incomplete` | 未完成：解除被平台中断、缺少终态记录，或已建足迹而没有终态回报 |

Agent 不得把 `return_check.<family>` 的 init 检查条目放入终态 `disarm_results`。
控制面必须保持 job 测试结论与解除结果正交。控制面不得因解除失败改写 `status_reason` 或翻转 job 终态。
控制面只更新本 job 建立过足迹的专项；init 检查另走 §9.6 的入口。

代码入口：`backend/agent/contracts/device_return.py` 的 `note_footprint_started`、`make_disarm_result`；
`backend/agent/pipeline_engine.py` 的 `PipelineEngine._execute_lifecycle`、`_record_disarm_for_step`；
`backend/agent/pipeline_runner.py` 的 `execute_pipeline_run`；`backend/agent/api_client.py` 的 `_build_complete_payload`、`complete_job`；
`backend/services/agent_completion.py` 的 `complete_agent_job`、`apply_disarm_results`。
依据：ADR-0061 D1 第 2、5 条、D4；#3723 §3.5 v1.7.17。

### 9.2 解除执行与有界保护（自 B6 激活起生效）

Agent 必须在建立足迹后的每条结束路径上尝试解除，包括自然到点、abort、manual_exit、init/patrol 失败、
barrier 超时与优雅停机。Agent 处理 manual_exit 时可以跳过结果收取。Agent 不得因此跳过解除。
Agent 不得因普通取消、重复 abort、`ABORT_LOCAL` 或设备重连而丢弃排队的解除或终止执行中的解除。

Agent 必须按 job 冻结的实际步骤版本选择解除义务：旧 `powercycle_finish@1.0.x` 仍承担解除，
`powercycle_finish@1.1.0` 只收取，`powercycle_disarm` 承担解除。版本不明时 Agent 必须保守保留完整 teardown，
并记录 `unknown_legacy_version`。同 job 混用旧 finish 与新 disarm 时，Agent 必须按 pipeline 顺序执行全部启用的解除步骤。

解除排队使用 `WAITING_EXECUTION_SLOT` 加 `phase=queue`；执行使用 `EXECUTING_DISARM` 加 `phase=execute`，
不新增 Job 终态。控制面与 Agent 必须遵守以下预算与证据约束：

- 全部步骤共用排队预算 `Q=7200s`；`E_i` 为冻结的正整数 `timeout_seconds`，历史缺失或非法值取 `300s`，记录 `legacy_missing_wall_clock`。
- `T0` 取最早有效服务器退出触发；`D_job=T0+min(Q+ΣE_i,86400s)`，求和覆盖本次路径全部启用 teardown。
- 控制面首次收到步骤执行证据时冻结 `D_i=min(收到时刻+E_i,D_job)`；重试不重置 `E_i`，各次排队累计扣减 Q。
- Agent 排队存活证据与脚本实际 PROGRESS 分开取证；本地证据至少每 `15s` 推进，解除期间协调心跳间隔最多 `30s`。
  控制面以接收时刻判定两类证据的 `60s` 新鲜度；执行另要求执行心跳有效。extend-batch 不是唯一证据通道。
- Agent pump 对带 seq 的 PROGRESS 只计严格增加，对无 seq 的每条 PROGRESS 都计入；重复 seq 不刷新进度。
  控制面不得用 `updated_at`、续租成功或 Agent 自报时间代替存活或进度。
- Agent 在每个解除步骤启动前必须单次核对 `extend_lock`（超时 `5s`、不重试）；409 表示失租，记 `not_executed/lease_lost` 并停止解除。
  网络失败不阻止启动。Agent 取得 permit 并完成该核对后必须立即启动。Agent 不得等待阶段证据确认；本地执行窗口取 E_i 与最近 D_job 剩余时间的较小者。
- 控制面只在 host 心跳、租约有效期、token、boot 与设备占用均有效且证据新鲜、未到截止时保护 RUNNING；普通等待没有豁免。
  abort reaper、recycler 的 running/patrol/step-trace 候选与最终行锁/CAS 必须复读最新事实并使用同一政策。
- 控制面在 `job_instance.disarm_guard` 持久化锚点，Agent 在 LocalDB 活跃记录保存截止与序号。
  控制面与 Agent 不得因重启、重复/乱序回报或重复 abort 延长截止。
  控制面遇到服务器时钟回拨必须停用保护并记录 `clock_regressed`。Agent 无法恢复可信锚点时不得重建宽限。
- 任一适用截止到达时，Agent 必须停止尾段并记录义务。Agent 不得靠换步骤延长 job。
  保护因预算、证据或所有权失效后，控制面必须按无 guard 的现行规则判定回收与基础活性投影。控制面不得继续借 guard 到期时间推迟。
  控制面不得复活 UNKNOWN 或终态 job 来补偿解除缺口。

代码入口：`backend/agent/pipeline_engine.py` 的 `PipelineEngine.cancel`、`_run_step_with_permit`、`_teardown_lease_lost`、
`_begin_disarm_tail`、`_publish_disarm_phase`、`_execute_teardown_best_effort`，同文件的 `_pump_process`；
`backend/agent/recovery_executor.py` 的 `_cleanup_after_job_exit`；
`backend/agent/contracts/device_return.py` 的 `select_disarm_steps`、`freeze_disarm_guard`、`receive_disarm_evidence`、`disarm_guard_deadline`；
`backend/agent/coordinator.py` 的 `HostRunCoordinator._loop`、`_tick`；
`backend/agent/registry/local_db.py` 的 `LocalDB.load_disarm_state`、`save_disarm_state`；
`backend/services/device_return.py` 的 `accept_job_disarm_evidence`、`guard_owner_valid`、`job_guard_deadline`；
`backend/scheduler/device_lease_reconciler.py` 的 `_reconcile_aborted_running_jobs`；
`backend/scheduler/recycler.py` 的 `recycle_once`、`_final_disarm_grace_blocks_write`；
`backend/services/plan_run_devices.py` 的 `running_heartbeat_deadline`。
依据：ADR-0061 D2 第 9 条、D3 第 1–4、7、8 条；#3723 §3.0 第 5 条（含 v1.7.13、v1.7.14）。

### 9.3 平台终止与兼容推导（自 B6 激活起生效）

Agent 与控制面必须先识别平台墙钟/停滞终止（124/125）及信号中断（143/-15/137/-9），
将已开始的解除记为 `incomplete`，优先于成功 JSON、回读与普通失败。历史 trace 只有 124/125 时按平台保留码处理，
记录 `legacy_reserved_exit`；普通脚本失败仍为 `failed`。

控制面对无 `disarm_results` 的旧回报必须按实际步骤角色与 trace 的 `original_ts` 推导：
STARTED 无终态取 `incomplete`，真实 COMPLETED 最多取 `executed_unverified`，没有解除开始证据取 `not_executed`。
已建立足迹而没有终态回报时补 `incomplete`。同专项按最后真实执行结果取值，迟到 STARTED 与后续 SKIPPED 不覆盖已完成的结果。
控制面不得从 `job.status` 猜测设备已归还；未接入专项的 `incomplete` 同样转欠账，普通 `failed` 才沿用 D8 第 4 条的未验证过渡。

代码入口：`backend/agent/contracts/device_return.py` 的 `map_step_outcome_to_disarm_result`；
`backend/agent/pipeline_engine.py` 的 `PipelineEngine._record_disarm_for_step`；
`backend/services/disarm_derive.py` 的 `derive_disarm_results_from_traces`；
`backend/services/device_return.py` 的 `_supplement_incomplete_for_footprints`、`map_disarm_result_to_family_state`。
依据：ADR-0061 D1 第 6、9 条、D4 第 2 条、D8 第 4 条；#3723 §3.0 第 6 条。

### 9.4 四入口的统一核验（自 B6 激活起生效）

Agent 汇总与控制面必须使用共享 `probe_result_policy`，按平台、型号、权限模型、APK SHA、脚本版本与包 SHA
精确匹配附真机报告的登记组合，并核对 `boot_completed=1`。平台/型号来自设备事实；job 包身份来自冻结 pipeline 与 manifest，
init/清偿包身份来自回报的脚本名、版本经 manifest 派生。缺字段、权限 unknown 或自报 verified 都不产生可信确认。
`absent`、`not_installed` 不直接得到 `confirmed`；本批 1.0.8 的读取失败没有证明动作完成，正式值为 `failed`，不升级成 `readback_failed`。

控制面必须先按各入口的租约/token 规则核对所有权，再对不可信的 `confirmed/readback_failed` 分别处理：

- 普通 `/complete` 照常落终态与测试结论，该专项改按 trace 推导，不因核验失败整份返回 422。
- 迟到确认不关闭条目，不因此新增 4xx。
- 清偿与 init 检查对该专项返回 422 `UNVERIFIED_PROBE_RESULT`，包含 family、reason、expected/observed 与报告引用；
  被拒专项不改业务事实，同一回报的合法专项照常处理。非特权值 `executed_unverified/failed/incomplete/not_executed` 照常接收。

控制面必须对每个被替换或被拒的专项写 `unverified_probe_result` 诊断审计，保留入口、原值、替换值、原因与报告引用；
计数按审计聚合，同一回报的只读重放不重复写入。

代码入口：`backend/agent/contracts/device_return.py` 的 `probe_result_policy`、`probe_entry_response`；
`backend/services/device_return.py` 的 `evaluate_probe_entry`、`verify_job_results`、`try_apply_late_confirmed`、
`apply_discharge_sync`、`check_init_probe_result`、`audit_unverified_probe`、`audit_unverified_probe_async`；
`backend/api/routes/agent_device_return.py` 的 `agent_return_discharge`、`agent_init_return_check`。
依据：ADR-0061 D2 第 5、7、8 条；#3723 §3.8（含 v1.7.11、v1.7.12、v1.7.16）。

### 9.5 欠账生命周期（自 B6 激活起生效）

专项归还状态为 `returned/debt/unverified`；来源为 `job/init_check/discharge/override/stock`。
已接入专项的 failed、readback_failed、not_executed、incomplete 均为欠账；未接入专项的 failed 暂记未验证。
设备聚合优先级为欠账＞未验证＞已归还；缺任何已登记专项的记录也为未验证，只有全部确认才为已归还。
归还状态只表示回读时刻的事实，派发时另有 init 检查。

欠账条目 `device_return_debt` 的 `source_kind` 只有 `job` 与 `stock`（阶段 B 初始化），与专项状态 source 的五种取值不同。
init 失败建立的条目仍来源于本 job。条目为 `open/closed`，关闭原因 `discharged/overridden`，关闭后保留原因与时间。
部分唯一索引 `uq_device_return_debt_open_source` 在 `state='open'` 时约束
`(device_id, family, source_kind, COALESCE(source_job_id, 0))` 唯一，与数据模型 §2 的索引一致。

控制面必须使终态处理、初始化、清偿、放行与退役在设备行锁下串行；涉及 job 的锁序为 Job → Device → Lease。
未退役设备有未关闭条目时，控制面必须保留恰一条 ACTIVE 租约；全部条目共用一条 `MAINTENANCE` 欠账租约
（holder=`platform`、reason=`归还欠账`），无自动到期回收。正在运行的其它占用继续持有原租约，释放时统一处理。
控制面必须在终态同一事务内经 `handle_job_lease_release` 或 `handle_job_lease_release_sync` 原地转换 JOB 租约并推进代次，
使旧 token 失效；没有欠账时才释放。reconciler 与紧急释放不得释放欠账租约。

代码入口：`backend/services/device_return.py` 的 `map_disarm_result_to_family_state`、`device_aggregate_return_state`、
`handle_job_lease_release`、`handle_job_lease_release_sync`、`_convert_active_job_to_debt_lease_sync`；
`backend/models/device_return_debt.py` 的 `DeviceReturnDebt`；
`backend/services/device_lease_emergency_release.py` 的 `release_terminal_job_lease`。
依据：ADR-0061 D1 第 3–8 条、D3 第 5 条、D5 第 2–4、10 条；#3723 §3.4.4。

- **阻断派发**：控制面必须以 `device_return_debt` 原因拒绝欠账设备进入派发或链式继承；claim 的 ACTIVE 租约互斥继续有效。
  入口：`backend/services/plan_dispatcher_sync.py::_classify_dispatch_devices_sync`、
  `backend/services/agent_claim.py::claim_jobs_for_host`；依据：ADR-0061 D5 第 1、9 条。
- **清偿**：可信 `confirmed` 关闭该专项全部未关闭条目；部分清偿保留共用租约，最后一个条目关闭才释放。
  `executed_unverified` 不关闭条目（§9.7）。入口：`backend/services/device_return.py::apply_discharge_sync`；依据：ADR-0061 D5 第 4、5 条。
- **迟到确认**：控制面只凭原 job、原 token 与可信 confirmed 关闭同源条目，不改 job 终态；重放不新增同源条目。
  入口：`backend/services/device_return.py::try_apply_late_confirmed`、`apply_late_confirmed`；依据：ADR-0061 D5 第 6 条。
- **人工放行**：Owner 或管理员可以经 `POST /api/v1/devices/{device_id}/return-override` 指定专项（省略或空列表为全部）。
  执行者必须填写 `reason_category` 与 `evidence_ref`。控制面关闭所选条目，记 `unverified/override` 并写 `device_return_override` 审计，
  不生成 confirmed；最后一个条目关闭才释放租约。API 使用 `require_admin`。
  入口：`backend/api/routes/devices.py::return_override_endpoint`、`backend/services/device_return.py::apply_override_sync`；依据：ADR-0061 D5 第 7 条。
- **带欠账退役与取消退役**：只有欠账租约且无活跃 job/其它 ACTIVE 租约时，控制面可以执行退役，在同一事务内释放欠账租约，条目仍 open。
  取消退役时有 open 条目则同一事务重建欠账租约；退役状态本身阻断派发。
  入口：`backend/api/routes/devices.py::retire_device_endpoint`、`unretire_device_endpoint`，
  `backend/services/device_retirement.py::retire_device`、`unretire_device`，
  `backend/services/device_return.py::release_debt_lease_for_retirement_sync`、`restore_debt_lease_on_unretire_sync`；依据：ADR-0061 D5 第 8 条。
- **阶段 B 初始化**：控制面按各专项最近历史 job 映射状态，不初始化任何设备为已归还；空闲设备经 claim 同一获取机制建欠账租约，
  已被占用设备只写条目、不中断占用，释放时转换。重复执行保持幂等。
  入口：`backend/scripts/init_device_return.py::main`、`_process_one_device`，
  `backend/services/device_return.py::init_device_return_sync`；依据：ADR-0061 D1 第 7 条、D5 第 11 条。
  本文不提供初始化或回退执行步骤，具体激活清单等待 Owner 批准后由 U7 第二个 PR 交付（#3723 §5）。

### 9.6 U4 init 残留检查（自 B6 激活起生效）

本批只有 power-cycle 在 `ONBOARDED` 接入。Agent 必须在普通 job 任何建立足迹的计划步骤前，
用登记脚本及最低版本（`powercycle_disarm@1.0.8`）运行 probe。可信 `clear` 不再解除；其余未能可信确认无足迹的读数或组合，
先运行同版本 disarm，再按共享政策取正式值；平台中断或未执行直接以 init 失败结束。
Agent 对 `confirmed/executed_unverified` 继续 init，对 `failed/readback_failed/incomplete/not_executed` 以 init 失败结束。
包不可用时以 `package_unavailable` 失败，不上报归还结果、不改归还状态。

控制面在 host 心跳响应中声明可选 `return_init_check_available=true`。Agent 仅在声明后，
于继续 init 前经 `POST /api/v1/agent/jobs/{job_id}/return-check` 上报 `fencing_token` 与 `family_results`。
控制面核对本 job 的 JOB 租约，token 不符返回 409；核验规则见 §9.4。
合法 confirmed 关闭该专项全部条目，来源记 init_check，设备继续由本 job 租约占用；合法失败建立来源 job 的欠账条目，重放幂等。
confirmed 收到该专项 422 时，Agent 必须改报 executed_unverified 后继续；有界重试仍未送达则以 `init_check_unreported` 失败。

Agent 对每个专项至多上报一条 STARTED，并且恰好上报一条终态 trace（stage=`init`、step_id=`return_check.<family>`）；
通过时 event_type/status 为 COMPLETED，失败时为 FAILED。probe/disarm 子执行保存在终态 trace 的 metadata/output，
不分别上报 trace。init 检查未通过时，Agent 不得启动计划 teardown，终态 `teardown_status=SKIPPED`；
patrol 断点恢复时不重跑 init 检查，仍执行 teardown。该检查与终态载荷相互独立（§9.1）。

代码入口：`backend/agent/device_return.py` 的 `registered_step`、`execute_registered`、`run_init_checks`；
`backend/agent/pipeline_engine.py` 的 `PipelineEngine._check_init_returns`、`_execute_lifecycle`；
`backend/agent/api_client.py` 的 `report_init_return_check`；`backend/api/routes/heartbeat.py` 的 `_process_heartbeat_with_db`；
`backend/api/routes/agent_device_return.py` 的 `agent_init_return_check`；
`backend/services/device_return.py` 的 `apply_init_check_sync`、`check_init_probe_result`。
依据：ADR-0061 D1 第 5 条、D5 第 10 条、D6 第 1 条；#3723 §3.5 v1.7.15–v1.7.18。

### 9.7 U4 自动清偿（自 B6 激活起生效）

host 心跳的可选 `return_debts` 按设备携带 `device_id/serial/fencing_token/families`，只下发未退役且有欠账租约的条目。
Agent 未收到该字段时不动作；本批只执行已接入专项。设备重连、欠账 token 首次出现触发清偿；
failed/incomplete/not_executed 或回报未送达时至少间隔 30 分钟重试；executed_unverified 或收到 422 后不自动重试，等待重连或 token 变化。
Agent 只有在该设备没有本 Agent 活跃 job 且取得 OperationScheduler permit 后才执行，并在整个解除过程中持有 permit。

Agent 使用同一登记脚本最低版本的 disarm，经
`POST /api/v1/agent/devices/{device_id}/return-discharge` 回报欠账 token 与 `family_results`。
控制面先核对欠账 token（不符 409），再执行 §9.4 核验与 §9.5 清偿；Agent 收到 409 后必须丢弃该 token 的缓存且不再为其重试。
长期不可达或未接入专项的欠账保留，处置为恢复设备后清偿或人工放行，不以新建 PlanRun 为前提。

代码入口：`backend/services/device_return.py::open_debts_for_host_sync`、`apply_discharge_sync`；
`backend/api/routes/heartbeat.py::_process_heartbeat_with_db`；
`backend/agent/heartbeat_thread.py::HeartbeatThread._tick`；
`backend/agent/heartbeat_bindings.py::build_heartbeat_thread` 内的 `_on_devices_reconnected`；
`backend/agent/device_return.py::ReturnDebtConsumer.receive`、`reconnected`、`_schedule`、`_execute`；
`backend/agent/api_client.py::report_return_discharge`；`backend/api/routes/agent_device_return.py::agent_return_discharge`。
依据：ADR-0061 D5 第 5 条；#3723 §3.0 第 8 条、§3.5 v1.7.15 第 3 条。

### 9.8 版本交错兼容（自 B6 激活起生效）

控制面面对没有解除阶段证据的旧 Agent，沿用 v1.0 执行期续期，不伪造 queue 保护；已协商 guard 失效后不回落到更宽松的旧宽限。
Agent 发现旧控制面不支持阶段协议时沿用 v1.0 执行行为，记录 `disarm_protocol_unavailable`，不重建新保护。
旧控制面未声明 `return_init_check_available` 时，Agent 照常执行 init 检查与解除，不上报、不等待归还状态更新，解除失败仍使 init 失败；
未下发 `return_debts` 时不清偿。执行者不得在控制面与 Agent host 版本交错期间开展 B6 归还验收。

代码入口：`backend/core/job_timeout_config.py::disarm_grace_defers_reap`；
`backend/services/device_return.py::job_guard_deadline`；`backend/agent/coordinator.py::HostRunCoordinator._tick`；
`backend/agent/pipeline_engine.py::PipelineEngine._begin_disarm_tail`、`_refresh_disarm_guard`；
`backend/agent/device_return.py::run_init_checks`、`ReturnDebtConsumer.receive`。
依据：ADR-0061 D8 第 8 条、D6 第 1 条；#3723 §3.5 v1.7.15、§5。

---

> **关于本文的「最后更新」头部**：无机器校验（没有任何门禁把该日期与
> `git log -1 --format=%cs -- <本文件>` 对拍）。#3003 起 DOC-MAP 常驻义务要求改正文语义时同步
> 刷新本头部（做不到就删字段）；#2990 落地前正文已改多次而头部仍是 2026-07-15，即该义务写成前的漂证。
> 要么下次把它改成生成物（同 `environment-variables.md` 的附录块做法），要么按「可派生量不写死」（#2663）删掉。
