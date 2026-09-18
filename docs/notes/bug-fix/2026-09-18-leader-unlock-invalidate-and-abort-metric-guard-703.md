# #703 残留两面：释锁失败作废连接 + abort 族观测不得递异常给业务

Status: implemented
Class: bug-fix

## Decision

24h 审计（issue #703 评论，tip `f1179f95`）在本单域登记了两处静默形态的残留，
两者共享同一条纪律——**「失败路径的出口形状必须显式定义」**。

1. **`backend/core/leader_election.py`**：`finally` 里 `pg_advisory_unlock` 抛错时
   只记 debug 后**照常** `conn.close()`。session 级 advisory 锁绑后端进程，unlock
   失败 ⇒ 这条连接可能仍持锁，回池 = 锁随连接被池吞走、再没人认领：该 singleton
   job 在所有副本上从此永无 leader，且只有一行 debug 可查。修形 = `except` 分支
   先 `conn.invalidate()`（标记作废 ⇒ 随后的 close 丢弃后端连接，锁随其后端进程
   消失）再走**唯一的 close 出口**；日志 debug→warning，让该路径至少可告警。
2. **`backend/core/metrics.py`**：`record_plan_run_abort_lock_seconds` /
   `record_plan_run_abort_fanout` 只对入参转换包了 try，`labels(...).observe(...)`
   裸奔；而 abort 族的 5 个调用点（4×lock_seconds + 1×fanout）全部位于
   `db.commit()` **之后**的返回路径——观测层一抛错，业务方看到的就是「一次已成功
   的 abort 返回 500」。修形 = 新增 `_safe_emit(emit)`（异常降级为 debug 日志），
   在**函数内**包住两个 abort 族写出，保护不再依赖调用点自觉。借还侧
   （`database._record_pool_checkout`）早已在调用点用同款包裹且只服务池计时，
   维持原样不动。

测试三档，各挡一类回归（同 #703 前作的分档纪律）：

- `tests/test_leader_election.py`（离线，PR 路径）：`_FakeConn` 增记
  `invalidate`；`test_unlock_failure_still_closes` 改写为
  `test_unlock_failure_invalidates_before_close` 并钉完整顺序
  `["lock","commit","unlock","invalidate","close"]`——**原判据恰好把泄漏形态
  固化成了期望**（只断言 `stages[-1]=="close"`），是「测试把缺陷当契约」的实例。
- `backend/tests/core/test_leader_election_txn.py`（真 PG）：新增
  `test_unlock_failure_discards_the_lock_holding_connection`，向
  `Connection.execute` 注入**一次性** unlock 异常（后端存活、锁仍在手——只有这个
  形态能模拟「回池带锁」），退出上下文后以 `pg_locks` holder 数与同 key 可重取
  作判据。生产/测试引擎均 `pool_pre_ping` + `pool_recycle=1800`，旧实现回池的锁
  不会被 ping/recycle 意外冲掉，判据无巧合变绿路径。
- `backend/tests/services/test_plan_run_abort_fanout_metric.py`：新增
  `TestObservabilityCannotBreakAbort` 两条——直接向 Histogram 对象注入异常，
  要求 record 函数吞下。

## Alternatives

- **①只升级日志不 invalidate**：泄漏仍在，只是看得见；本单缺陷语义是「永无
  leader」，观测救不回来。否决。
- **①unlock 失败后跳过 close 直接弃连接**：绕过唯一关闭出口，正是 #703 前作
  （「旧实现在方言分支上不关闭，只靠 GC 兜」）清掉过的形态。否决。
- **②在 5 个调用点各包 try/except**：与借还侧先例同形，但调用点会随 abort 逻辑
  生长、每处都要记得包——保护放函数内，形状只有一处。若未来要求与借还侧完全
  统一，属 `metrics.py` 全族重构，另单裁决。
- **②把 `metrics.py` 全部 record_* 一次性套 `_safe_emit`**：越出本单面——多数
  成员不在业务返回路径（后台任务/reconciler），异常语义不同。见 Revisit。

## Verification

- `.venv/bin/python -m pytest tests/test_leader_election.py
  backend/tests/core/test_leader_election_txn.py
  backend/tests/services/test_plan_run_abort_fanout_metric.py
  backend/tests/services/test_plan_run_abort_scale.py
  backend/tests/services/test_abort_lock_order_1985.py -q` → **27 passed**
  （testcontainers 隔离 PG，未触生产库）。
- **反向验证（实现退回 → 用例红）**：离线 1 failed / 真 PG 1 failed /
  metrics 2 failed，三档全部当场红，退回实现（恢复）后全绿。
- `ruff check` 5 个触及文件 → All checks passed；`compileall` → OK。

## Revisit

- 「观测回写不抛异常」是否升为 `metrics.py` 全族纪律（或静态守卫：record_* 内
  禁止裸 `labels(`）。等下一个真实案例或巡检再裁决，不为形状造守卫。
- `db_pool_*` 两个函数自身仍裸写 `labels(...)`，安全性依赖唯一调用点的包裹；若
  出现第二个调用点，收编进 `_safe_emit`。
- 本 PR `Fixes #703`：审计登记的两处残留即本单最后已知缺陷。若评审发现同域
  新残留，另立单不重开。
