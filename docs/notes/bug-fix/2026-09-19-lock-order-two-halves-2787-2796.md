# 行锁全序两处残余：stale-UNKNOWN 候选循环的提交边界 + coordinator-heartbeat 集合内序（#2787 / #2796）

Status: implemented
Class: bug-fix

## Decision

两处「I2 基准残余」收口，共用一条判据——**同一事务内取行锁的顺序必须与对侧路径
同向，且集合内部必须全序**：

1. **#2787**：`device_lease_reconciler._reconcile_stale_unknown_jobs`（check 2）补齐
   「一候选一事务边界」——在 `PlanAggregator.on_job_terminal` 成功后显式
   `await db.commit()`，与 #2635 对 check 1 的修复同形。修复前该边界只在末位候选
   成立（`on_job_terminal` 仅在 `applied=True`、即 `terminal_job_count >=
   total_job_count` 时自行提交），从第 2 条候选起同一事务持 `plan_run` 行锁再取
   `job_instance` 行锁 ⇒ 实际锁序 `plan_run → job`，与 `complete_agent_job` 的
   `job → plan_run` 相反 ⇒ 可成环。函数文档串与循环内自述同步改写（原文自述
   「一候选一事务边界」与实现相反，是本次误判的载体之一）。
2. **#2796**：`agent_coordinator_heartbeat` 两段写循环各自按 **id 升序**取锁——
   `payload.jobs` 按 `job_id`、`payload.plan_run_hosts` 按 `id`。修复前集合内部
   顺序 = agent payload 序（agent 侧字典插入序），与 `extend_leases_batch` 的
   `ORDER BY id`（#992 全序约定）交错仍可成环；#1980 只修了**方向**
   （job_instance → plan_run_host）。prh 排序键对非整型 id 容错归 0（随后被既有
   `if not prh_id` 跳过），不把 payload 形态问题变成 500。

## Alternatives

- **只在调用方（reconciler runner）统一提交**：弃——run 级提交无法缩小锁持有窗口，
  环仍然存在；#2635 已把先例钉在循环内，check 2 与 check 1 必须同形，否则两半
  再次漂移。
- **#2796 改 agent 侧按序发送 payload**：弃——排序是控制面持有行锁时的责任，
  依赖 agent 端实现版本会退化成「新 agent 修好、旧 agent 复发」；且控制面无法
  靠契约强制第三方顺序。
- **把 heartbeat 的两段写合并为一条批量 UPDATE**：超出本单范围（会改变响应/
  投影语义），且方向序（#1980）与集合内序已足够闭合环；留待将来若出现
  更宽的锁面需求再议。
- **用 `with_for_update(order_by=...)` 替代显式排序**：对 ORM 脏对象 flush 路径
  不适用（UPDATE 的落库顺序由脏集插入序决定，不由 SELECT 排序决定）。

## Verification

- 新增 `backend/tests/scheduler/test_reconciler_stale_unknown_lock_order_2787.py`
  ——阻塞法把时序钉死（占住末位候选 job 行 ⇒ 回收器停在 #3），判据一：候选 #1
  终态已提交（观察会话读到 FAILED）；判据二：`plan_run` 行锁已释放（NOWAIT 试锁）。
  反向验证：临时注释掉 `await db.commit()` ⇒ **1 failed**（`assert 'UNKNOWN' ==
  'FAILED'`，命中判据一）。
- 新增 `backend/tests/api/test_coordinator_heartbeat_lock_order_2796.py`——占住
  升序第一条应锁行、payload 故意降序：job 与 plan_run_host 两个用例分别断言
  「排队时后继行未被锁」（NOWAIT）。反向验证：撤掉两处 `sorted` ⇒ **2 failed**
  （均命中预期断言，`LockNotAvailableError`）。
- 相关回归批 43 passed：`test_reconciler_drain_lock_order_2635.py`、
  `test_device_lease_reconciler.py`、`test_shared_row_lock_order_1980.py`、
  `test_agent_coordinator_heartbeat.py` + 上述两个新文件（testcontainers PG）。
- 两文件按 `lock_order` 命名判据自动进入
  `tests/test_lock_order_pr_path_contract.py` 覆盖要求；已挂进
  `pr-migrate-empty-db` 的「Run concurrency regressions」pytest 列表（有 PG service
  的 PR required check），避免只活在夜间 `backend-test`。
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- 若 reconciler 的两处「一候选一事务」未来被合并/重构，必须保留「先释放
  `plan_run` 行锁、再取下一候选 job 行锁」这一可观测性质（两个回归文件即为锚点）。
- 若 coordinator-heartbeat 将来改为批量 SQL 或引入新的写集合（如批量 prh），
  需重新枚举集合内序并补全序键。
