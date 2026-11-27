# B12 U1：统一 FinishingGuard 契约 / 加性 Schema / run 级 abort 时钟修复

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 2 底座单元 U1（按 `#4206` v1.1 与 PR #4208 第 1 轮复核返修）：

1. 新增共享契约 `backend/agent/contracts/finishing_guard.py`，以
   `kind ∈ {teardown_disarm, atomic_commit}` 统一有界护盾判据；遗留 B6
   `disarm_guard` 线协议字段完整兼容并吸收为 `teardown_disarm`。
2. 过渡期双列权威（B1）：A 类任一列 `clock_regressed`（旧 `reason` 或标准
   `guard_invalidated_reason`）即不可逆吸收失效；否则按单调 `seq`/新鲜度取
   最新收据，避免陈旧 `finishing_guard` 盖过 live B6 写入。
3. `finishing_guard_deadline` 与 `finishing_guard_expiry` 共同识别两种回拨标记（B2）。
4. 加性迁移 `b12u1a2b3c4d5`：仅四列（`finishing_guard` /
   `negotiated_capabilities` / `atomic_group` / `skip_satisfies_delivery`）；
   **不含** `finishing_guard_admission`（归 U4，B6 / v1.1）。
5. 能力常量仅登记到 `capabilities.py`，不并入 `AGENT_CAPABILITIES`。
6. `plan_run_abort`：持锁后 `populate_existing` 刷新；run 级重复 abort 保留
   首次时钟与 ACK，且 `requested_job_ids` 并集不缩减（B4）。
7. 强度 `max` 与时钟 `min` 对同一 Job 共用 `abort_request_covers_job` 主体覆盖
   判据（B5）；`min_abort_clock` 将 naive 归一为 UTC-aware（B7）。
8. recycler / abort reaper 在候选预筛与最终行锁复读两阶段均调用
   `job_guard_deadline`（B3）。

## Alternatives

- 在 DB `status` 枚举新增 `FINISHING`：触及活跃索引与 reaper 全链路，已否决。
- U1 即建准入闸门表以满足 orphan-models：v1.1 改为归 U4 与真实持锁消费方同 PR。
- U1 即开启 Agent 心跳上报三向能力：半成品协商会让旧控制面误判，明确延后 U5。

## Verification

- `scripts/run_pytest.py backend/agent/tests/test_finishing_guard_u1_4206.py -q`
- `scripts/run_pytest.py backend/tests/services/test_finishing_job_deadline_u1_4206.py backend/tests/scheduler/test_finishing_recycle_candidate_u1_4206.py backend/tests/api/test_plan_run_abort_api.py -q -k "b1_ or b2_ or b4_ or b5_ or c1_ or candidate_ or finishing or job_guard"`
- 空库 `upgrade head` → `downgrade -1` → `upgrade head` + `check_schema_sync`
- `scripts/run_gates.py check:quick`（含 orphan-models）

## Revisit

- U2 投影 `ui_status=finishing`；U4 落地准入闸门表/持锁预登记/force 写入与 PG 交错；
  U5 开启能力心跳上报。
