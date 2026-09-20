# recovery_sync 的 job 行锁集合内全序（#2871；#2796 同形第三处）

Status: implemented
Class: bug-fix

## Decision

现象复核（逐处实读）：`sync_agent_recovery` 的 active_jobs 循环按 **payload 上报顺序**
逐条 `SELECT … FOR UPDATE` job 行，且整函数只在末尾 `await db.commit()` 一次——即一次
请求在上报期间持有全部 job 行锁；对侧 `agent_lease_extend.py:243` 是
`ORDER BY JobInstance.id` 全序。两者交错（payload 降序 = job(hi)→job(lo) vs id 升序批量）
即可成环，与 #2635 / #2787 / #2796 同一加锁顺序家族。

修复取**与 #2796 同形**的最小半边——集合内按 id 升序：

```python
for entry in sorted(payload.active_jobs, key=lambda e: e.job_id):
```

配套把新回归接线到 PR 路径：`backend/tests/api/test_recovery_sync_lock_order_2871.py`
按命名判据（文件名含 `lock_order`）自动进 `tests/test_lock_order_pr_path_contract.py`
的接线要求，故本 PR 同步把它加进 `ci.yml` 的「Run concurrency regressions (PostgreSQL)」
pytest 列表——不接线该守卫即红。

**未做（留给 owner）**：事务边界是否下沉为「一候选一提交」。循环内确有写操作
（`rotate_recovery_lease_token` 轮换 fencing token、`job.ended_at` 与 `db.flush()`、
outbox 状态迁移），下沉会改变该路径的**部分成功语义**，属 owner 决策；而闭环的
**序条件**已由全序消除（#2796 的先例正是不动提交边界、只补集合内序）。

## Alternatives

- **只让 agent 侧按 id 升序上报**：弃——与 #2796 同理由：排序是**持有行锁一侧**的责任，
  依赖 agent 端版本会退化成「新 agent 修好、旧 agent 复发」。
- **用 `with_for_update(order_by=JobInstance.id)` 替代显式排序**：不适用（同 #2796 note）：
  逐条 ORM 对象的取锁顺序由循环序决定，不由单条 SELECT 的排序决定。
- **本单同时下沉事务边界**：超范围——issue 明示「取决于部分成功语义，需 owner 定」，
  且序条件已闭合可成环的主因；强做会把语义决策夹带进锁序修复。
- **不加接线**：弃——家族 note 的教训正是「PG-only 回归只活在夜间 `backend-test`」；
  命名判据 + 接线守卫就是为了让新增同类回归自己进 PR 路径。

## Verification

- **反例优先（先证伪再采信）**：把 `agent_recovery.py` 退回 `HEAD` 后跑新用例 →
  **FAILED**，命中预期断言 `assert hi_lock_error is None`（`LockNotAvailableError:
  could not obtain lock on row in relation "job_instance"`）——即「recovery 在等待低 id
  job 行时已持有高 id job 行锁」，payload 序确认存在；恢复后 **passed**。
- 阻塞法判据（形态沿用 #2796 / #2015）：另一会话持低 id job 行锁，payload 故意降序 ⇒
  断言（a）recovery 在该行排队（`pg_stat_activity.wait_event_type='Lock'`）、
  （b）高 id 行尚未被锁（NOWAIT 试锁成功）。
- 锁序回归全集（10 个文件，PR 路径同一批）：**17 passed**。
- `pytest tests/test_lock_order_pr_path_contract.py -q` → **7 passed**（接线判据：新文件
  已在 `ci.yml` 的 PR 路径 job 内）。
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (12 gates)**

## Revisit

- **事务边界半边仍开放**：若 owner 决定下沉为「一候选一提交」，必须保留本单钉住的
  可观测性质——集合内按 `job_id` 升序取锁；本回归文件即该性质的锚点（改回 payload 序会红）。
- 家族枚举口径：本单按「`for/async for` 循环体内出现 `with_for_update`」AST 扫全仓
  （`backend/`，排除 tests/alembic），命中 5 处并逐条核对顺序来源——本处（#2871，已修）、
  `device_lease_reconciler.py:115`（`sorted(expired, key=…)` ✓）、同文件 `:329`
  （查询 `order_by(JobInstance.id)` ✓），以及 **`session_watchdog.py:52/60` 两处无顺序**
  ——后者同属本家族但不在本单范围，已另立 **#2901** 并附同一扫描口径。
- 成环的另一半在 `agent_lease_extend.py`（对侧 `ORDER BY id`，另一会话在飞）：
  若其锁面改动（例如引入第二个写集合），本单的全序前提需复核。
