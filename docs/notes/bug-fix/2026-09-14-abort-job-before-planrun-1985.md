# abort_plan_run 加锁顺序对齐 job → plan_run，并给出首份共享行加锁表（#1985）

Status: implemented
Class: bug-fix

## Decision

### 一、修复：`abort_plan_run` 先锁 PENDING job 行，再锁 `plan_run`

在 `backend/services/plan_run_abort.py` 的**第一次 plan_run 加锁之前**插入一段
预锁：按 `id` 升序 `SELECT JobInstance.id … FOR UPDATE`，候选为
`plan_run_id == ? AND status='PENDING'`（`host_id` 给定时再按 host 收敛）。

之后的逻辑一律不动：终端态复检、`_bulk_abort_pending_jobs` 的
`WHERE status='PENDING'`、run/host 计数器、聚合、审计、
`#1552` 的 `_reload_run_context`、`#703` 的「批量前 commit 释放 plan_run 行锁」全部保留。
预锁只是把 abort **本来就会锁的同一批行提前**，因此不改变判定与语义。

`plan_run_abort` 用 `FOR NO KEY UPDATE` 与 `complete_job` 的 FK `FOR KEY SHARE` 兼容
（`#1473`）这一**锁模式**选择是对的，本单只改顺序。

新增 `backend/tests/services/test_abort_lock_order_1985.py`（1 例，需 PostgreSQL）。

### 二、为什么：同一条 (job_instance × plan_run) 行上顺序相反

| 路径 | 顺序 | 位置 |
|---|---|---|
| `recycler` PENDING 超时 | **job → plan_run** | `recycler.py:849-853`（`UPDATE job_instance … status='PENDING'`）→ `:873-879` `plan_aggregator_sync` → `job_terminalization.py:205-209`（`SELECT PlanRun … FOR NO KEY UPDATE`） |
| `abort_plan_run`（改前） | **plan_run → job** | `plan_run_abort.py:459-463`（锁 P）→ `:481` `_bulk_abort_pending_jobs` → `:101-115`（`UPDATE job_instance … status='PENDING'`） |

两者争用**同一批行**：回收器的超时候选 = `status='PENDING'`（`created_at` 早于
`DISPATCHED_TIMEOUT_SECONDS`）；abort 的 `pending_ids` 也是该 run 的 PENDING job。
交错后回收器持 J 等 P、abort 持 P 等 J，成环。

注：`plan_run_abort.py:450-454` 的 commit（`#703`，为在 PENDING 风暴下提前释放
`plan_run` 行锁、缓解 QueuePool 压力）在 `:459` 又立刻重新加锁，所以它对**顺序**没有
帮助——P → J 依然成立，本单的预锁补上了这一半。

### 三、首份共享行加锁表（`#1960` §3 第 7 条的首个落地产物）

本轮把 `#1959`/`#1980` 之后未覆盖的写者枚举完毕。下表是**当前**的全量结论。

**`plan_run_host` × `job_instance`**（点名覆盖的 5 个模块）：

| 路径 | job 侧 | prh 侧 | 顺序 | 判定 |
|---|---|---|---|---|
| `complete_job` → `on_job_terminal` | `SELECT … FOR UPDATE` | `_bump_host_counters` | Job → PRH | ✓ 基准 |
| `_reconcile_expired_leases` / `_reconcile_stale_unknown_jobs` | 同左 | 同左 | Job → PRH | ✓（`#1959`） |
| `recycler`（PENDING / RUNNING 超时）→ `plan_aggregator_sync` | `update(JobInstance)` | 经 `on_job_terminal_sync` | Job → PRH | ✓ |
| `coordinator_heartbeat` | `update(JobInstance)` | ORM 改 `PlanRunHost` | Job → PRH | ✓（`#1980`） |
| `plan_run_abort._bulk_abort_pending_jobs` | `update(JobInstance) … 'PENDING'` | `update(PlanRunHost)` | Job → PRH | ✓ |
| `admission_pump.admission_transaction` | **INSERT**（新行） | `FOR UPDATE ORDER BY host_id` | PRH → Job(INSERT) | ✓ 非反序（新行无既有行锁，且 prh 已定序） |
| `plan_dispatcher_sync.prepare_plan_run` | 不触碰 | INSERT | — | ✓ 不参与 |
| `host_retirement._assert_no_inflight_work` | 只读 count | 只读 count | — | ✓ 不参与 |

`plan_run_host` 的写点全仓只有三处 ORM（`_bump_host_counters`、
`admission_transaction` 的 `:729-735`、`coordinator_heartbeat`）+ 一处批量
（`plan_run_abort:130`）；已全部覆盖。

**`plan_run` × `job_instance`**：

| 路径 | 顺序 | 判定 |
|---|---|---|
| `complete_job` / `recycler` / reconciler / `coordinator_heartbeat` | job → plan_run | ✓ 基准 |
| `abort_plan_run` | 改前 plan_run → job | ✗ → 本单修正 |
| `admission_pump`（`SELECT PlanRun FOR UPDATE` → job INSERT） | plan_run → job(INSERT) | ✓ 非反序（INSERT） |

**`job_instance` × `device_leases`**：`complete_job` / `extend_leases_batch` /
`_reconcile_expired_leases` / `extend_job_lock` 均已统一为 Job → Lease
（`#992` / `#1959` / `#1980`）。

**`host` × 其它**：`claim`、`host_retirement`、`host_maintenance` 都只取 `Host` 行锁，
且 `admission_transaction` 的 host 加锁按 `host_id` 升序（`_lock_admission_resources`），
未发现与 prh/job 的相反顺序。

### 四、未观测边界

2026-09-13/14 的 83 次死锁被阻塞对象统计里**没有 `plan_run`**（只有 job_instance 48 /
device_leases 19 / plan_run_host 9），故本单这处属**代码级成立、样本未观测到**的活路径
——与 `#1980` 一并修掉的 `extend_job_lock` 同类。不做「已发生故障」的宣称。

## Alternatives

- **改 recycler 的 PENDING 路径为 plan_run → job**：放弃。会把全局规则拆成两个方向
  （complete 等仍是 job → plan_run），正是 `#1960` 第 7 条要防的形态；且该路径按 job
  分批，先拿 plan_run 会让整批串行在同一个 run 上。
- **abort 的批量 UPDATE 用 `SKIP LOCKED` / `NOWAIT` 绕开**：放弃。abort 必须终止**全部**
  PENDING job，跳过即语义错误。
- **只保留 `#703` 的 commit、去掉 `:459` 的重新加锁**：放弃。那次 commit 是为了在
  PENDING 风暴下提前释放 plan_run 行锁；`_bulk_abort_pending_jobs` 会写
  `pr.aborted_job_count`/`terminal_job_count` 并据计数器做聚合判定，缺少 plan_run 行锁
  会让它与 aggregator 的「stale RUNNING 视图」竞态复活（见该文件 `:250-254` 的来由）。
  保留锁、改顺序才是对症的。
- **给整条 abort 换一个更粗的锁（如 advisory lock）**：放弃。引入第二套互斥语义，且
  不与既有行锁序列协调。
- **顺带把 `plan_run` 也纳入 `#1980` 的回归文件**：放弃。abort 是同步路径、需要线程 +
  跨连接可见性，独立文件更清楚。

## Verification

本机实跑（`.venv`；PG 经 testcontainers，未设 `TEST_DATABASE_URL`）：

| 项 | 结果 |
|---|---|
| `pytest backend/tests/services/test_abort_lock_order_1985.py -q` | **1 passed** |
| **负向对照**：`plan_run_abort.py` 换回 `origin/main` 后重跑 | **1 failed**（`LockNotAvailable` on `plan_run`：abort 持 P 等 J）→ 回归有鉴别力 |
| `pytest backend/tests/api/test_plan_run_abort_api.py backend/tests/services/test_plan_run_abort_aggregator_race.py backend/tests/services/test_job_terminalization.py backend/tests/scheduler/test_recycler.py -q` | **62 passed** |
| `run_gates.py check:quick` | **`[OK] check:quick (10 gates)`** |
| `ruff check backend/ tools/ scripts/` | All checks passed |

判据：第三会话 `SELECT PlanRun … FOR NO KEY UPDATE NOWAIT` 探测「等待方是否已持有
plan_run 行锁」，不看返回值（沿用 `#1959`/`#1980`）。

## Revisit

- **上线后观测**：三个修复（`#1959` / `#1980` / 本单）合入后，`stability_db_deadlock_total`
  （`#1958`）应回落为 0。若仍有增量，按本表继续枚举尚未覆盖的组合：`plan_run` ×
  `plan_run_host`、`device_leases` × `plan_run`，以及 host 行与 job/prh 的交叉。
- **本表的维护方式**：`#1960` §3 第 7 条要求「以共享行为单位枚举」，但目前只有本表。
  下一轮审查应把**这张表**作为产物之一更新（增量核对新增加锁点即可），而不是每次
  重新枚举；若表长期只在本 Note 里，应评估把它移入 `docs/design/` 成为常驻契约。
- **abort 的预锁代价**：预锁会让「PENDING 风暴」下的 abort 先等 job 行锁再锁 plan_run。
  实测未出现 QueuePool 压力回归（`check:quick` 与 abort 用例全绿），但生产上若出现
  abort 延迟升高，应先量测再决定是否把候选集合进一步收敛（例如只锁预读到的
  `pending_ids`，而不是按 plan_run 全表扫）。
- **`plan_run_abort.py` 的锁序注释**：本单在同一文件里已有三处历史说明（`#1473` 锁模式、
  `#703` commit、`#1552` 视图同步）。若后续再有第四处，应把这四条合并成一段
  「本文件锁序契约」，避免读者逐条拼。
