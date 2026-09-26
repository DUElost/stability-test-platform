# inner-imports 基线回涨清理（#3401 C2-b / #3376 项 2）

Status: implemented
Class: bug-fix

## Decision

把 #3359（ADR-0052 聚合执行器）新增的函数体内 import 中**非循环依赖掩体**的 14 处回顶层，
同 PR 下调棘轮；合并 `main`（#3447 观测面把基线 617→626）后按 post-merge 树重算：
**626 → 610**（原 14 处 + `_prev_window_zero_output` 内已回顶层的 `select`/`PlanRun` 再消 2 处）：

- `plan_run_finalization`（10 处）：`time`、`collections.defaultdict`、`sqlalchemy select/delete`、
  `PlanRunHost`、`JobInstance`（顶层已有、函数内重复）、`PlanRun`（3 个函数）、`PlanRunPendingAggregation`；
- `job_terminalization`（4 处）：`sqlalchemy.dialects.postgresql.insert`、`PlanRunPendingAggregation`、
  `asyncio`、`saq.Job`；
- 合并 #3447 后：`_prev_window_zero_output` 不再重复函数体内取 `select`/`PlanRun`（SessionLocal 仍函数内）；
- `plan_run_finalization` 模块 docstring 的顶层 import 纪律段同步改写（机械载体回扫）。

**边界（刻意不动，属 clean-env / 循环依赖掩体）**：

- `backend.core.database.SessionLocal`（会话）、`backend.core.metrics`、`notification_service`、
  `plan_chain_trigger`、`dedup_scan`、`report_service`、`thread_pool`（finalization 侧）；
- `backend.core.task_queue`（`get_queue`/`enqueue_sync`）与 `plan_run_finalization`（TESTING 分支）
  （terminalization 侧）；
- `sqlalchemy.orm.attributes.flag_modified` 不在复核清单内，保持原位。

## Alternatives

- **只移 models、三方留内**：复核清单把 stdlib/三方纯构造子一并列为「不需要放在函数里」，一并处理；
- **顺带移动 task_queue / SessionLocal**：否决——会改变 clean-env import 闭包的副作用面，
  #2372 契约（`tests/test_plan_run_abort_import_contract.py`）正是为此设立。

## Verification

| 命令 | 结果 |
|---|---|
| `tools/dev/check_inner_imports.py` | **610 处 ≤ 基线 610**（合并 #3447 后重算；121 文件）|
| `tests/test_plan_run_abort_import_contract.py`（clean-env 契约） | **1 passed** |
| `tests/test_plan_run_finalization_structure_3299.py` + `tests/test_inner_import_ratchet.py` | **13 passed**（worktree 需显式 `DATABASE_URL`）|
| `backend/tests/services/` 四个核心文件（terminalization/finalization/decoupling/aggregation_shared） | **58 passed** |
| abort 家族 5 文件 | **37 passed** |
| `ruff check`（改动文件） | All checks passed（format 既有基线不动）|
| `scripts/run_gates.py check:quick` | **[OK] 16 gates** |

裸 import 对照：worktree 无 `.env.backend` 时 `import job_terminalization` 的 `DATABASE_URL`
依赖**改动前后同败**（预存在环境依赖，非本单引入）。

## Revisit

- 基线 610 为合并 #3447 后的新锚点；后续新增函数体内 import 需在 PR 写明理由并上调（棘轮允许但要留痕）；
- 「纯构造子 vs 会话/编排依赖」的边界以本单为准：stdlib、`sqlalchemy` 构造子、models 纯定义可顶层；
  有副作用的服务/会话/队列保持函数内。
