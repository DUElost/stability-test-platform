# 父 Run 终态 `plan_run_status`：单一发送点收口（#3384）

Status: implemented
Class: bug-fix

关联：[#3384](https://github.com/DUElost/stability-test-platform/issues/3384)（本单）、
[#3359](https://github.com/DUElost/stability-test-platform/pull/3359)（ADR-0052 D1–D5 实现，引入三处死代码）、
[PR #3382](https://github.com/DUElost/stability-test-platform/pull/3382)（中央补发点，已合）、
[ADR-0052](../../adr/ADR-0052-terminal-fact-parent-aggregation-decoupling.md)、
[`docs/design/06-realtime-and-background.md`](../../design/06-realtime-and-background.md)。

## Decision

删除「调用返回后立即读父行再广播」的三处遗留发送点，`plan_run_status`
收口为**编排者中央补发**唯一发送点（`plan_run_finalization._emit_parent_terminal_status`，
在父终态 commit 之后经 `plan_run_events.emit_plan_run_status` 发出）：

| 旧点 | 现状 |
|---|---|
| `agent_completion.py:466-470`（`/complete` 主链路） | 删除；保留 `broadcast_run_job_update` |
| `agent_step_status.py:83-87`（`_broadcast_transitioned_jobs`） | 删除；保留 job 推送 |
| `recycler.py:454-460 + 488-495`（PENDING 超时回收） | 删除父行终态判定与 `schedule_emit("plan_run_status")` |

`docs/design/06-realtime-and-background.md` 增补「PlanRun 终态的推送发送点」段，
写明新发送点与「三处旧点为何删除」，既有 device lease reconciler 豁免段不变。

### 发送覆盖核查（删除前）

| 终态生产者 | 发送点 | 结论 |
|---|---|---|
| `/complete`、step、recycler 三路径 | 标记 `plan_run_pending_aggregation` → 聚合排空 → `finalize_parent_run_*` → 中央补发 | ✅ 唯一发送 |
| `counter_reconciler` 重放（`counter_reconciler.py:260`） | 同上（`finalize_parent_run_sync`） | ✅ |
| abort（run 级 / host 级） | `plan_run_abort.py:831` 自身 `schedule_emit` | ✅ 保留，不动 |
| abort 的 queued/precheck 早退分支 | 仅 `notify_plan_run_terminal`（站内通知），本就无 socket 发送 | 既有形态，不在本单范围 |
| device lease reconciler abort 对账 | `device_lease_reconciler.py:976` 自身广播 | ✅ 文档豁免段保留 |
| dispatch / precheck / admission_pump 直发 FAILED | 本就无 `plan_run_status` 发送 | 既有形态，不在本单范围 |

## Alternatives

- **保留三处旧点**：生产语义下恒不命中（dead code），TESTING 内联排空下则与中央
  补发**双发**——同一次终态迁移发两条 `plan_run_status`，不采纳。
- **把发送点补到三处（而非中央）**：每条路径各发一次会在聚合排空路径上重复
  （三路径都可能触发同一 run 的终态），且回到「调用方自判父终态」的旧假设，不采纳。
- **TESTING 下门控旧点**：把语义分叉藏进环境判断，测试永远覆盖不到旧点的真实行为，
  不接受（#3384 的根因之一就是「测试语义 ≠ 生产语义」）。

## Verification

- 新增守卫 `backend/tests/services/test_plan_run_status_emission_3384.py`（6 例）：
  静态锚点（三处旧点不得复活）+ `/complete` 默认语义恰好一次 +
  `/complete` 关闭 `TESTING` 内联排空时路径不发、`drain_plan_run_aggregation_sync`
  中央补发恰好一次 + step 路径不发父终态 + recycler 超时路径不发父终态；
  传输层探针（`get_sio().emit`）确保旧异步发送点复活也会被判红。
- **反向验证**：源码退回 #3359 后状态（`git stash` 三文件），新测试
  **4 failed / 2 passed**（静态锚点 + 三条行为路径红）；修复后 6 passed。
- 存量面：`test_plan_run_finalization.py`、`test_terminal_aggregation_decoupling_3244.py`、
  `test_job_terminalization.py`、`scheduler/test_recycler.py`、
  `api/test_plan_run_abort_api.py`、`test_aggregator_deadlock_regression.py`
  → **96 passed**。
- `ruff check`（改动文件）通过；`python scripts/run_gates.py check:quick` 通过（见 PR）。

## Revisit

- `reconcile_step_traces` 当前恒返回空 `transitioned_jobs`（step replay 明确不推进
  Job 状态机）——若未来放开该约束，step 路径的发送点需重新评审（不得恢复自读父行）。
- abort queued/precheck 早退分支无 socket 推送、dispatch/precheck/pump 直发 FAILED
  无推送：均为既有形态；若被判定为缺口，另立单，不在本单顺手补。
