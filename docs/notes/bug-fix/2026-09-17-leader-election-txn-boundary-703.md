# #703 残余：scheduler leadership 取锁事务不得跨 job 体

Status: implemented
Class: bug-fix

## Decision

`backend/core/leader_election.py::hold_scheduler_leadership` 改两处，都服务于同一条不变量
——**「锁要保住，事务不许带走」**：

1. **用 `Connection` 而不是 `Session` 承载 advisory lock**，并在 `finally` 里设唯一的
   `conn.close()` 出口。旧实现有三处内联 `try: db.close()`，而「方言探测不匹配」那条提前
   `yield True; return` 分支**根本不在关闭路径上**（只靠 GC 兜）——在本单讨论的连接耗尽面上，
   这就是一轮 tick 一条悬空连接。
2. **取到锁之后立刻 `conn.commit()`**。此前取锁的 `SELECT pg_try_advisory_lock(:k)` 开启了
   一个事务，然后代码 `yield True` 把整个 job 体（singleton job 分钟级）挂在这个事务上，
   那条连接全程停在 `idle in transaction`。

两条都是静默形态：不报错、不影响功能正确性，只在压力上来时显形。ADR-0027 P3-1 的
「锁仅在 tick 期间持有」没有被改变，变的是**事务**不再与 tick 同寿。

日志 reason 词表随之从 `session_factory` 改为 `connect`（现在的失败点就是取连接），
`lock_acquire` 与 fail-closed 分级（#890 / R01-F10）原样保留。

测试分两档，各挡一类回归：

- `tests/test_leader_election.py`（离线，PR 路径）：假 `Connection` 记录**调用顺序**——
  进入 job 体时必须是 `[lock, commit]` 且尚无 `close`，退出时必须是
  `[lock, commit, unlock, commit, close]`。顺序而不是次数，因为本单的缺陷就是顺序。
- `backend/tests/core/test_leader_election_txn.py`（真实 PostgreSQL，夜间 `backend-test`）：
  离线档看不见 psycopg / QueuePool 的归还行为，而整条因果链压在上面。用例在 job 体期间
  **额外从池里取走一条连接**（模拟别的 worker），再断言：持锁连接的 `state` 不是
  `idle in transaction`；第二个会话取同一 key 必须失败（互斥仍在）；退出上下文后该 key 上
  不再有任何锁（没有锁被留在池里）。

为什么不接进 PR 路径的并发回归登记表（`tests/test_lock_order_pr_path_contract.py`
`_REGISTERED`）：那条判据针对「只有并发才能抓住的**业务**不变量」。本单的 PR 路径兜底已经
存在（顺序判据在离线档，改回 `Session` 或不 commit 都会当场红），真 PG 用例证的是**语义
前提**（session 级锁不随 commit 释放），前提很少变化，不值得再给 `pr-migrate-empty-db` 加
一条容器级耗时。若评审认为该接，成本是登记表 + `ci.yml` 那一行 pytest 参数。

## Alternatives

- **`Session` + 取锁后 `commit()`**（看起来最短的改法，被实测否决）：`Session.commit()` 会把
  连接**归还池**，而 session 级 advisory lock 绑在后端进程上、不随 commit/rollback 释放。
  一次性 Postgres 上的实测：取锁者 pid 109，commit 后池把 109 交给了另一次 checkout，我们的
  Session 转到 pid 112，随后 `pg_advisory_unlock` 返回 **false**、锁仍记在 109 名下；只有
  109 自己 unlock 才清空。落地后果是「该 job 从此再也没有 leader」且零报错——比原缺陷更坏。
  这条同时被真 PG 用例挡住（把实现换成 Session 后，红在「退出上下文后锁未释放」那一条）。
- **改用 `pg_advisory_xact_lock`（事务级）**：commit 即释放，锁活不过取锁那一步，job 体期间
  毫无互斥 → 所有副本同时自认 leader，正是 #890 要防的双跑。否决。
- **只加指标不除根**：把 `idle in transaction` 的时长做成告警。信号该有，但它不改变
  「根会话后面排着 85 个等待者」这件事；本单已修的可见性部分是另外四条 PR 的活。
- **调池参数 / 加大 `max_connections`**：09-13 那次是 app 池（30+60）与 PG 全局 100 同时
  见顶，但真正让排查工具自身失效的是那个长事务；先除事务，再谈容量——属方向级取舍，需
  ADR 裁决，不在本单。

## Verification

- 真实 Postgres 对照（一次性容器，仅回环随机端口，未触生产库）：
  - 修复前 job 体内：`backends=[(90, 'idle in transaction', 'SELECT pg_try_advisory_lock($1)')]`
    + advisory key `held_by=[(90, True)]` —— 与 #703 评论里 09-13 生产根会话逐字同形态；
  - 修复后 job 体内：`backends=[(90, 'idle', 'COMMIT')]`、锁仍 `held_by=[(90, True)]`、
    第二个会话取同一 key 返回 False、退出后 `held_by=[]`。
- `env -u DATABASE_URL python -m pytest tests/test_leader_election.py -q` → **11 passed**；
  `env -u DATABASE_URL -u TEST_DATABASE_URL JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/core/test_leader_election_txn.py -q` → **1 passed**（testcontainers 独立库）。
- 变异自证（每条只改一处、跑完还原）：离线档 5 条——去掉取锁后的 commit（2 红，均为
  `test_acquire_transaction_is_closed_before_the_body_runs` 与 happy path）、退回 `Session`
  （7 红）、方言分支不归还连接（只红 `test_dialect_probe_failure_path_closes`）、退出时不
  unlock（只红 happy path）、提前 close（3 红）；真 PG 档 3 条——不 commit（红在
  `'idle in transaction' != 'idle in transaction'`）、换成 Session（红在「锁留在池里」）、
  不 unlock（红在退出后仍有锁）。还原后两档复跑全绿。
- `python scripts/run_gates.py check:quick`、`ruff check`、`python -m compileall`：见 PR 描述。
- `backend/agent/tests/test_leader_election.py` 与 `tests/test_lock_order_pr_path_contract.py`
  复跑通过（前者只覆盖纯函数与豁免路径，不触达本次改动的分支）。

## Revisit

- **#703 未闭合**：本单只做「长事务占连接」这一条具名残余。仍有待办的面：大规模 abort
  （约 500 job / 30 host）压测验证控制面可响应、池与超时的容量取向（需 ADR）、
  扇出限流与可观测信号的补全。
- `backend/api/routes/plans.py` 用的是 `pg_advisory_xact_lock`（事务级）：如果将来有人为了让
  锁活得更久而在同一个 Session 上补 commit，就会踩进本单 Alternatives 第一条描述的那个坑。
  真要跨语句持锁，参照本文件的 `Connection` 写法。
- 若出现「leader 从此选不出来」的现场报告，第一步查
  `SELECT pid, state, query FROM pg_stat_activity WHERE state = 'idle in transaction'` 与
  `pg_locks` 里 locktype='advisory' 的归属——锁悬在池里连接上时，只有那条后端进程消失才释放。
- 多实例部署形态（`STP_SCHEDULER_LEADER_ELECTION`）仍是单进程为主，SQLite / `TESTING=1`
  豁免路径下这些判据不参与；哪天真的滚动到多副本，本单的连接归属假设需要一条现场验证。
