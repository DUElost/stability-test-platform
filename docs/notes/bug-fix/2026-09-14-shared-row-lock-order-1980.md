# 共享行加锁全序补齐：coordinator-heartbeat 与 extend_lock 两处反向（#1980）

Status: implemented
Class: bug-fix

## Decision

按 #1960 新增的方法（**以共享行为单位枚举全部加锁点**）对
`job_instance` / `device_leases` / `plan_run` / `plan_run_host` 做了一遍枚举，
修掉 `#1959` 之后仍存在的两处反向：

1. **`coordinator_heartbeat`**（`backend/api/routes/agent_api.py`）：把「写
   `job_instance`」的循环移到「写 `plan_run_host`」之前。两个循环互相独立，交换顺序
   即可；`db.execute(update(JobInstance) …)` 会先执行并锁住 job 行，`plan_run_host`
   的 ORM 变更随后才 flush。
2. **`extend_job_lock`**（同文件）：`db.get(JobInstance, job_id)` 换成
   `SELECT … FOR UPDATE`（与 `complete_job` 同形），使 Job 行锁先于 `extend_lease`
   的 `device_leases` 行锁。

新增 `backend/tests/api/test_shared_row_lock_order_1980.py`（2 例，需 PostgreSQL）。

### 枚举结果（本次的依据）

| 共享行组合 | 正序 | **反序（本单修复）** |
|---|---|---|
| `job_instance` → `plan_run_host` | `complete_job` → `on_job_terminal` → `_bump_host_counters`（`backend/services/job_terminalization.py:152-162`）；回收器尾部 `on_job_terminal` 同序 | `coordinator_heartbeat`：先改 `PlanRunHost`（原 `:1766-1794`），再 `UPDATE job_instance`（原 `:1796-1826`） |
| `job_instance` → `device_leases` | `complete_job`、`extend_leases_batch`、`_reconcile_expired_leases`（`#992`/`#1959` 已统一） | `extend_job_lock`：先 `extend_lease`（`lease_manager.py:160`）再 `job.updated_at`（原 `:1334-1340`） |

两处都是**活路径**：`coordinator_heartbeat` 是 Agent Coordinator 的周期上报
（`backend/agent/coordinator.py`）；`extend_job_lock` 仍在
`PipelineEngine._verify_device_lease` 的巡逻租约校验里被调用
（`backend/agent/pipeline_engine.py:1093-1127`）——`#291` 只删掉了 LeaseRenewer 的
per-job 回退，巡检路径没有改。

### 与已观测死锁的对应（含一处对既有归因的修正）

2026-09-13/14 PostgreSQL 服务日志的 83 次死锁中，另一路分析（其自身声明为「语句形状
↔ 代码位置」推断、非逐事件追踪）给出「20 次涉及 `job_instance.updated_at` touch；
9 次等待 `plan_run_host` 元组」。本次按代码枚举复核：

- **9/83** 与上表第一行吻合（终态化持 job 等 prh，本端点持 prh 等 job）→ 本单修复；
- **20/83** 倾向归因于 **`#1959` 的同一条环**（受害者语句是批量续租的 CAS
  `UPDATE device_leases … FROM job_instance …`，第三方持租约行），即该子集随
  `#1959` 一并消除，**不需要单独动作**；
- `extend_job_lock` 的反序**未出现在该样本**中，属同类但未被观测到的活路径，
  本单一并修掉（成本极小，且它必然与 complete/批量续租竞争同一 job）。

### 判据为什么不是「看返回码」

沿用 `#1959` 的形态：死锁的受害者可能把异常吞进上层通用 `except`，或被路由转成
409/500，只看返回码会漏判。本单两条回归都用第三会话 `FOR UPDATE NOWAIT` 直接探测
「等待方是否已经持有了它不该持有的行锁」，再释放对方后核对业务结果
（续租生效 / `execution_state` 与 `coordinator_heartbeat_at` 落库）。

## Alternatives

- **只修 `coordinator_heartbeat`（已观测到的那 9/83）**：放弃。`extend_job_lock` 是
  同一不变量的同类活路径，改法只有一行且与既有 `complete_job` 同形；留到下次观测到
  再修，等于把「枚举共享行」的结论又丢掉一次。
- **把 `coordinator_heartbeat` 的两次写拆成两个事务**：放弃。两个循环本可原子提交，
  拆开会引入「prh 已更新而 job 状态未落」的中间态，代价大于换序。
- **给 `coordinator_heartbeat` 加显式 `FOR UPDATE` 锁 prh 行**：放弃——错方向。
  问题不是 prh 行没锁，而是它的锁**先于** job 行；加锁只会把反序固化。
- **在路由层捕获死锁重试**：放弃。不消除环，只增加失败与延迟（与 `#992`/`#1959` 同结论）。

## Verification

本机实跑（`.venv`；PG 经 testcontainers，未设 `TEST_DATABASE_URL`）：

| 项 | 结果 |
|---|---|
| `pytest backend/tests/api/test_shared_row_lock_order_1980.py -q` | **2 passed** |
| **负向对照**：把 `agent_api.py` 换回 `origin/main` 后重跑同一文件 | **2 failed**（两例各自报出持有不应持有的行锁）→ 回归有鉴别力 |
| `pytest backend/tests/api/ backend/tests/services/test_execution_state_signals_step5a.py -q` | **1170 passed**（6m08s） |
| `ruff check backend/ tools/ scripts/` | All checks passed |
| `compileall backend/ tools/ scripts/` | exit 0（仅既有 SyntaxWarning） |
| `check_governance_surface.py --check` | `[OK] 治理面结构检查通过` |

`run_gates.py check:quick` 在本工作树仍会停在 `env-inventory`：这是 `#1978` 的
worktree 假红（PR #1979 在途），与本改动无关，故上面的门禁是逐项实跑的。

## Revisit

- **上线后观测**：`#1959` + 本单合入后，`stability_db_deadlock_total`（#1958）应回落为
  0。若仍有增量，优先按同一方法重新枚举：现在未被覆盖的写者还有
  `recycler` / `admission_pump` / `plan_run_abort` / `host_retirement` /
  `plan_dispatcher_sync` 对 `plan_run_host` 与 `job_instance` 的触碰组合。
- **`extend_job_lock` 的长期去向**：它是 `#291` 之后仅存的 per-job 续租入口（巡检
  租约校验）。若后续把巡检也切到 batch，应按 `script-versioning` 同精神走退役而不是
  就地改语义；本单只统一锁序，不改接口形态。
- **枚举表应进入审查产物**：本单是「共享行加锁表」第一次真正被用来发现问题；
  #1960 已把它写进 §3 第 7 条，下次轮次应直接产出这张表而不是临时枚举。
