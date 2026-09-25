# #3299 PlanRun 生命周期五模块环：显式编排者 `plan_run_finalization` 落地（第一步纯结构重构）

Status: implemented
Class: architecture

关联：[#3299](https://github.com/DUElost/stability-test-platform/issues/3299)（本单，选定方案=issue owner 评论）、
[#3292](https://github.com/DUElost/stability-test-platform/pull/3292)（SAQ 队列环拆除，本环显露的源头）、
[#738](https://github.com/DUElost/stability-test-platform/issues/738)（函数体内 import 掩盖循环依赖）、
[ADR-0052](../../adr/ADR-0052-terminal-fact-parent-aggregation-decoupling.md)（同问题域的终态解耦裁决，Proposed——本单不等它，
第一步只做结构；见 Revisit）。

## Decision

新建 `backend/services/plan_run_finalization.py` 作为**父 Run 终态后副作用的唯一编排者**，
独占 链触发 / dedup 入队 / RUN_* 通知 / #1082 报告缓存刷新 / 链恢复入口；不采用事件总线
（订阅关系对 import-linter 不可见，正是 #738 要消除的形态）。归属迁移与新增：

1. `notify_plan_run_terminal` / `maybe_notify_risk_high` ← `plan_run_aggregation`；
   `_finalize_plan_run` 收口为**纯计算+落库**（transition/ended_at/result_summary/终态指标），
   不再内联通知与刷新调度。C5 基线行 `plan_run_aggregation -> backend.services.post_completion`
   **删除**（验收字面判据）。
2. `refresh_report_cache_for_plan_run` / `schedule_report_cache_refresh`（原私有
   `_schedule_report_cache_refresh`，公开化）← `post_completion`。若留在 post_completion
   并让 finalization 反向调用，会与「post_completion 的链修复改走 finalization 恢复入口」
   形成新的 2 模块环——故一并迁入。
3. `finalize_parent_run_async/_sync` = 原 `job_terminalization._post_aggregation_side_effects_*`
   并入（保持 #986 顺序：announce→commit→链→dedup）；RUN_* 通知/刷新的 announce 输入
   （`result_summary` 的 total/completed/failed）即 `_finalize_plan_run` 刚写入的值，
   文案逐字不变（含空集路径「no jobs」，由调用方以 `no_jobs` 旗标显式传入，不靠
   result_summary 缺失反推）。
4. `recover_chain_trigger`：链恢复公开入口；`post_completion` 不再直接 import
   `plan_chain_trigger` / `plan_run_aggregation`（环的两条边）。

**拓扑裁决**：`plan_run_abort` 直调 `apply_*` 后必须保留通知+刷新（#1082 快照语义在
abort 终态路径同样成立），因此需要 `abort → finalization` 边；而
`finalization → chain_trigger → dispatcher_sync → abort` 三边不可动 ⇒ 环从第四边断开：
`run_abort_pending` / `abort_pending_job_ids`（ADR-0043 纯判据）下沉叶子模块
`plan_run_context`（run_context 域模块，判据与其键语义同家），`dispatcher_sync → abort`
边消灭。`counter_reconciler`（#789 补偿路径，第三处依赖内联通知的编排点）改经
`announce_parent_terminal`。事务边界**零改动**：abort 的两处聚合点只加 announce，
不引入 commit/链触发。

不留转发壳：迁出符号在旧家彻底消失（双归属=漂移温床，正是本单要消除的形态），
测试随迁移重定向，新增 AST 结构守卫
`tests/test_plan_run_finalization_structure_3299.py`（基线行不复活 + 三条归属边 +
「旧家无残留符号」）。

## Alternatives

- **事件总线**：#3299 选定方案已否决——依赖藏进运行时订阅，import-linter 看不见；
  D4 要求副作用有序执行且「已执行」标记与父终态同事务落库；跨进程总线还会让 Redis
  承载业务事实（AGENTS.md 硬不变量）。
- **只删 C5 基线行、副作用位置不动**：否。验收的后半句「5 模块无环」要求真的解开环，
  而环之所以闭合集正是「aggregation 调度副作用」+「post_completion 承担 Run 级副作用」
  两个归属错位；换个 import 写法（如 lazy registry）只是把环藏深一层。
- **finalization 不 import chain_trigger（链触发留在 job_terminalization 原地）**：
  少改一处，但「终态后副作用唯一编排者」名不副实，dedup/链/通知分住两模块的形态正是
  本单的问题陈述；否决。
- **把 `abort → finalization` 换成「abort 也走 `finalize_parent_run_*` 全家桶」**：
  abort 的聚合点在事务中段，全家桶含 commit——改事务边界，越出「第一步纯结构重构」
  的授权；abort 只取 announce 子集。
- **`run_abort_pending` 下沉到新建的第 6 模块**：两判据即 `run_context['abort_requested*']`
  的读侧，与既有 `plan_run_context`（同键的写侧 helper）同域，新建文件反而稀释归属。

## Verification

以下均为本 PR diff（worktree，testcontainers PG）实测：

- `venv/bin/python -m pytest backend/tests/services backend/tests/scheduler -q`
  → **1497 passed**（含新 `test_plan_run_finalization.py`、重定向后的
  `test_job_terminalization` / `test_post_completion` / `test_plan_run_aggregation_shared` /
  abort 族（scale/fanout/backflow/1985/aggregator_race）/ `test_abort_subject_predicate_2270` /
  `test_counter_reconciler_aggregation` / `test_reconciler_drain_lock_order_2635`）
- `venv/bin/python -m pytest backend/tests/api -q` → **1311 passed**
- `venv/bin/python -m pytest backend/agent/tests tests/test_agent_import_boundary.py
  tests/test_lock_order_collection_total_order.py tests/test_agent_test_import_ratchet.py -q`
  → **2179 passed**（dispatcher→abort 断边后的 agent clean-env 侧回归）
- `venv/bin/python -m pytest tests/test_plan_run_finalization_structure_3299.py
  tests/test_plan_run_abort_import_contract.py -q` → 全绿（结构守卫 + #2372 import 契约）
- import-linter（`.importlinter` 全合约）：C1–C5 **5 kept, 0 broken**，C5 ignored 5→4
- `tools/dev/check_inner_imports.py` → **597 ≤ 基线 597**（JobInstance 提顶层后回表）
- `venv/bin/python scripts/run_gates.py check:quick` → **全绿（14 gates）**
- `venv/bin/python tools/dev/check_governance_surface.py --check` → **S1–S15、S5x 全绿**
- 行为面冒烟：clean-env 下 `import backend.services.plan_run_abort` 不拉起
  `socketio_server`（#2372 探针脚本手跑）✓

## Revisit

- ADR-0052 D3–D4 裁决落地时（热行移出 / 「已执行」标记与父终态同事务）：本模块即其
  聚合者载体，`announce_parent_terminal` 与 `finalize_parent_run_*` 的内部形状会变，
  但归属拓扑（编排者→聚合器单向）不再需要动。
- `#3299` 只解了五模块环这一条边集；`.importlinter` C5 余下 4 条基线边
  （project_registry、report_service、ai_assistant×2）各有其载体单，不混装。
- 若发现第四个 `apply_*` 直调点绕开编排者：AST 守卫
  `test_aggregation_imports_no_side_effect_modules` 拦不住「调用方忘记 announce」，
  届时考虑把 announce 做成 apply 的返回值契约（返回对象携带待执行副作用清单）。
