# #2635 租约回收器「一候选一事务边界」真正落地：锁序回到 I2 基准

Status: implemented
Class: bug-fix

## Decision

`backend/scheduler/device_lease_reconciler.py`：在 `PlanAggregator.on_job_terminal(terminalize, db)`
返回后**显式 `await db.commit()`**，再进入下一候选。计数（`drained`/`failed_count`）与日志
随之移到 commit **之后**（修复前它们在 `on_job_terminal` 返回后立即自增，而该次可能未提交）。

同步落点：

- `backend/tests/scheduler/test_reconciler_drain_lock_order_2635.py`（新增，**生产形状** seed）；
- `.github/workflows/ci.yml` 把新文件接入 PR 路径的并发回归步骤；
- `docs/notes/architecture/2026-09-14-shared-row-lock-table.md` 的正确性更正（见下）。

## 缺陷确认（逐条回源码，与 issue 一致）

| # | issue 的判据 | 复核 |
|---|---|---|
| 1 | `on_job_terminal` 在候选循环内逐条调用 | ✅ `device_lease_reconciler.py:254` |
| 2 | 无条件先取 `plan_run ... with_for_update(key_share=True)` | ✅ `job_terminalization.py:148-152` |
| 3 | `commit()` 只在 `applied=True` 分支 | ✅ 同文件 `:78-86` |
| 4 | 非末位候选不 `applied`（`terminal < total` → `return False`） | ✅ `plan_run_aggregation.py:314-315` |
| 5 | 生产是多 job 共享一个 PlanRun | ✅ `admission_pump.py:762` |

**锁序实测**（按行号）：

| 侧 | 顺序 |
|---|---|
| `complete_agent_job`（对侧） | `JobInstance`(:200) → `plan_run`(:442) = **I2 基准** |
| reconciler（修复前，候选 #2..N） | `plan_run`(来自 :254) → `JobInstance`(:137) = **相反** |

⇒ 可成环（本仓 09-14 起对该族做过四轮修复）。

## 一处**必需的补充修正**（issue 未点名）：计数与提交对齐

issue 的修复建议 #2 提到「让计数与回滚对齐」。我落地时发现**同一位置的第二个问题**：
修复前 `drained`/`failed_count` 在 `on_job_terminal` 返回后**立即**自增，而该次可能
**没有提交**（非末位候选）。

- 修复前：候选终态化未提交 → 异常时 `db.rollback()` 撤销 → 但 `drained` 已自增，
  `_record_check(label, "success", result)` 与 `reconciler_unknown_grace_released ... drained=N`
  **仍记成已终态化**。
- 修复后：commit 成功才自增 ⇒ 计数与实际落库一致。

## 测试：既有回归钉子为何恒绿（issue 的诊断成立）

既有 `test_reconciler_phase2_commits_each_candidate_independently` 判的判据**是对的**
（「有没有退化回尾部统一提交」），但它的 `_seed()` 每次新建一个 `Plan` ⇒ **一 job 一 PlanRun**
⇒ `total = 1` ⇒ 每候选都 `applied=True`、每候选都真提交 ⇒ **永远走不到退化路径**。

新用例的两处关键设计：

1. **seed 用生产形状**：`_seed_multi_job_run(host, n)` 建**一个 PlanRun 挂 n 个 job**，
   并置 `total_job_count = n` —— 这是让「非末位候选」真正出现的唯一方式；
2. **判「提交时机」而非「最终状态」**：我第一版用例只断言「最终都 FAILED」，
   red 验证时发现**修复前后都通过**（因为外层 `:931` 最终会提交）——
   **无区分度**。改为实测 `pg_locks`：观察会话对候选 #1 的 `plan_run` 行尝试
   `with_for_update()` + `lock_timeout=2s`，修复前**阻塞**（锁被同事务持有到 check 结束）、
   修复后**立即可锁**。第二用例则用「占住末位候选的租约行 → 断言候选 #1 已可读为终态」。

## Verification

- `./scripts/run_pytest.sh backend/tests/scheduler/test_reconciler_drain_lock_order_2635.py -q`
  → **2 passed**；
- **红绿双向**：移除显式 `commit()` → **2 例全部红灯**；恢复 → 2 passed；
- **相关既有回归**：`test_device_lease_reconciler.py` + `test_reconciler_renew_lock_order.py`
  + 新文件 → **37 passed**（无回归）；
- **完整并发回归集**（模拟 CI 那条命令，7 个文件）→ **13 passed**；
- **接线门禁**：`tests/test_lock_order_pr_path_contract.py` → **7 passed**
  （新增文件按命名判据被自动发现并要求接线——**它确实拦住了我**，直到我改 `ci.yml`）；
- `ruff check` 三文件 → All checks passed；
- `check_governance_surface.py --check` → S1–S14、S5x 全绿。

## Alternatives

- **`on_job_terminal` 返回后无条件 commit**（本版采纳）→ 与 issue 建议 #1 一致；
  空提交（`applied=True` 时函数内部已提交）无害，且提交次数仍是每候选一次，
  **保住了 #2531 的排空速率**（不退回「每 tick 一台」）。
- **把终态化移回循环外**（#1172 原形状）→ 否决：正是 #2531 要修的排空速率问题。
- **改 `plan_run_aggregation` 让非末位候选也 `applied`** → 否决：`applied` 的语义是
  「父聚合是否落地」，与事务边界正交；为修锁序而改聚合语义会波及链式派发。
- **只在测试里断言「最终都 FAILED」** → 实测**无区分度**（修复前后皆通过），已弃用；
  这正是 issue 所说「既有钉子恒绿」的同类陷阱，故本单在**测试判据**上也做了同样修正。

## Revisit

- **`on_job_terminal` 返回后不得依赖会话内未提交状态**：本单的 commit 使该约定成为
  显式契约（已写入代码注释）。若将来有调用方在其后读取未提交的 ORM 对象，会拿到
  已过期/已分离的状态——触发条件：新增调用方时。
- **未做真实并发压测**：本单用 `pg_locks` + 行锁阻塞构造判据，**未**在带载环境跑
  「回收器 × complete_job」的真实成环压力（issue 提到 09-18 的 2,713 次成环端点调用
  验收只覆盖当时已修复的路径）。触发条件：本修复上线后的首次带载验收应把
  `stability_db_deadlock_total`（#1958）纳入观察。
- **`_record_check` 的 success 语义**：`_reconcile_checks` 在 `db.commit()` 后按 success
  记录；本单只修了候选级计数，**未**改 check 级语义（那是 #2635 正文提到的第二类影响，
  其表现依赖前者的修复而收敛）。
