# Agent 认领脑裂恢复与未确认租约快速回收（#3931）

Status: implemented
Class: bug-fix

载体 issue：#3931（跨边界 ACK / 全链路可观测性后续演进见 #3992）。

## Decision

针对「控制面 `claim_agent_jobs` 已提交 `RUNNING` 并发放 `fencing_token`，但 Agent 因 HTTP 读超时、崩溃或断链未能落盘执行」造成的认领脑裂，在 Agent 侧与控制面侧实施协同止血：

1. **Agent `claim_loop` + `api_client` 携带活跃快照并约束请求超时**
   - [`backend/agent/api_client.py:fetch_pending_jobs`](../../../backend/agent/api_client.py)：为 `POST /api/v1/agents/jobs/claim` 请求增加可选 `active_job_ids` 字段（仅当非 `None` 时写入 JSON payload，兼容旧协议语义），并将 HTTP 超时从 `timeout=15` 收紧为分阶段 `(5, 20)`。
   - [`backend/agent/claim_loop.py:process_claim_tick`](../../../backend/agent/claim_loop.py)：每次 claim tick 从 `occupancy.job_ids` 与 `local_db.get_active_jobs()` 收集当前主机真实活跃 `job_id` 列表并排序上报 `active_job_ids`。

2. **控制面 `claim_agent_jobs` 同实例重投递与安全释放**
   - [`backend/services/agent_claim.py`](../../../backend/services/agent_claim.py)：`ClaimRequest` 接收 `active_job_ids`（`None` 时保持旧版 Agent 兼容，不触发误回收）；当 `active_job_ids` 非 `None` 时调用 `_recover_unconfirmed_claims`（在持有 `Host FOR UPDATE` 下按 `Job -> Device -> DeviceLease` 升序加锁）：
     - 匹配同一 `agent_instance_id`、租约仍为 `ACTIVE`、处于宽限后窗口（>15s）且无确认信号（`started_at`、`last_heartbeat_at`、非空 `StepTrace` 均为空、租约未续期且 `job_id` 不在 `active_job_ids` 中）的孤儿 `RUNNING` 任务；
     - 若宿主状态在线、关联 `PlanRun` 仍为 `RUNNING`、设备仍可用/在线且当前认领槽位允许，则轮换 `fencing_token` 与租约 `expires_at` 并重新投递，无需等待租约超时；
     - 若 `PlanRun` 已非 `RUNNING` 或设备不可用/无槽位，则调用 `handle_job_lease_release` 立即释放租约，并将 Job 按 `JobStateMachine` 转为 `UNKNOWN`（`status_reason="claim_unconfirmed"`）。

3. **Reconciler 快速回收未确认孤儿（约 120s 替代 ~15 分钟）**
   - [`backend/core/job_timeout_config.py`](../../../backend/core/job_timeout_config.py)：新增 `UNCONFIRMED_CLAIM_TIMEOUT_SECONDS`（默认 120s），并在 [`tools/dev/env_inventory.py`](../../../tools/dev/env_inventory.py) 声明为内部防孤儿超时旋钮、同步刷新 [`docs/development/environment-variables.md`](../../../docs/development/environment-variables.md)。
   - [`backend/scheduler/device_lease_reconciler.py`](../../../backend/scheduler/device_lease_reconciler.py)：新增 Check 2b `_reconcile_unconfirmed_claims`，在 `UNCONFIRMED_CLAIM_TIMEOUT_SECONDS`（120s）后捕获零心跳、零 `StepTrace`、零续租的未确认 `RUNNING` 任务并释放租约转为 `UNKNOWN("claim_unconfirmed")`，同时 Check 2 `_reconcile_stale_unknown_jobs` 对 `status_reason == "claim_unconfirmed"` 免除额外 300s `UNKNOWN_GRACE_SECONDS` 等待直接终态化。

## Alternatives

- 引入双阶段 Claim-Commit 协议（`CLAIM_PENDING` + 显式 ACK 请求）：需要改动 Agent-控制面通用状态契约与兼容窗口，将在 #3992 中统一评估推进；本次先利用现有 `claim` 心跳与 reconciler 闭环止血。
- 不依赖 `active_job_ids` 仅靠缩短全局租约 TTL：高负载巡检下短租约会放大正常任务误杀概率，仅针对零 `started_at`/零心跳/零 `StepTrace`/未续租的未确认认领启用 120s 检查更安全。

## Verification

在隔离 worktree `.wt/fix-3931`（分支 `fix/3931-claim-split-brain`）完成以下验证：

- 定向单元与 PG 集成测试（`scripts/run_pytest.py`）：
  `backend/agent/tests/test_claim_loop_736.py`、
  `backend/agent/tests/test_vbus_claim_slots_3892.py`、
  `backend/tests/services/test_agent_claim.py`、
  `backend/tests/services/test_vbus_flash_claim_3892.py`、
  `backend/tests/services/test_agent_claim_split_brain_3931.py`、
  `backend/tests/scheduler/test_reconciler_unconfirmed_claim_3931.py`、
  `backend/tests/scheduler/test_device_lease_reconciler.py`、
  `backend/tests/scheduler/test_reconciler_drain_lock_order_2635.py`、
  `backend/tests/scheduler/test_reconciler_renew_lock_order.py`、
  `backend/tests/scheduler/test_reconciler_stale_unknown_lock_order_2787.py`
  （含同实例重投递、已确认运行任务保护、离线/终止 `PlanRun` 立即释放、120s Check 2b 回收及 `claim_unconfirmed` 免 300s grace 立即终态化）。
- `check:quick` 非前端门禁（13 项：`schema-at-head`、`env-inventory`、`ruff`、`compileall`、`layering`、`orphan-models`、`gov-surface`、`ai-work`、`god-files`、`inner-imports`、`tool-contract-fixture`、`tool-manifest`、`transitions`）；worktree 无 `frontend/node_modules`，未修改前端目录。

## Revisit

- 待 #3992 启动跨场景（`claim` / `complete` / `abort` / `return`）显式 ACK 与统一指标体系时，评估将 `active_job_ids` 重投递与 `UNCONFIRMED_CLAIM_TIMEOUT_SECONDS` 纳入统一协议规范。
