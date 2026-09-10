# 全量 backend-test 偶发死锁：清库 TRUNCATE 加有界重试（#1273）

Status: implemented
Class: bug-fix

## Decision

**根因证据**（CI run 34399899982 的 deadlock DETAIL，见下）：全量 `backend/tests/`
的 7–8 个 setup ERROR 来自 `db_session` fixture 的
`TRUNCATE TABLE ... RESTART IDENTITY CASCADE`：

```
Process 111 waits for AccessExclusiveLock on relation 17332; blocked by process 114.
Process 114 waits for AccessShareLock on relation 16604; blocked by process 111.
```

即 TRUNCATE（AccessExclusiveLock）与同进程内仍存活的连接/后台线程
（AccessShareLock）形成循环等待，PG 把 TRUNCATE 判为牺牲者。出错用例集合随运行
漂移（首跑 8 / 重跑 7）正符合「谁先拿到锁」的时序依赖。

修复：把清库抽成 `_truncate_all_tables(engine, table_names)`，对
`DeadlockDetected` 做 **3 次退避重试**（0.2s/0.4s/0.6s）；非死锁错误与重试耗尽
原样抛出（不掩盖真实失败）。

**过渡性说明**：这是对「瞬态循环等待」的收口，不是对「谁泄漏了并发会话」的终态
修复——泄漏方（后台线程/异步引擎会话）未定位，见 Revisit。

## Alternatives

- **定位并关闭泄漏会话/后台线程**——正确方向但当前无证据指向具体持有者
  （DETAIL 只给 relation oid，未映射表名/调用方）；且全量套件串行运行，重试
  已能稳定消抖。留作 Revisit 的终态出口。
- **给清库加 `pg_advisory_lock` 串行化**——放弃：竞争者不是另一个清库，而是
  应用侧连接，advisory lock 不参与其锁序，无效果。
- **调大 `deadlock_timeout`/降低锁强度（`TRUNCATE` → `DELETE`）**——放弃：
  DELETE 慢且不重置 identity，会改变 isolate 语义。

## Verification

- `venv/bin/python -m pytest backend/tests/test_truncate_deadlock_retry.py -q` →
  **3 passed**（重试后成功 / 重试耗尽上抛 / 非死锁不重试）。
- 反事实：把 `_TRUNCATE_DEADLOCK_RETRIES` 置 0（等价无重试）后，重试相关两用例
  失败（`assert 1 == 4`）；恢复为 3 后通过。
- 真库 smoke（docker PG16）：`backend/tests/api/test_health_saq.py` 连同本单用例
  一并通过（16 passed），确认 fixture 走新路径无回归。
- `python scripts/run_gates.py check:quick` → **[OK]（7 gates）**。

## Revisit

若死锁仍偶发（重试不足以覆盖长事务），按 DETAIL 的 relation oid 反查
`pg_class` 定位表名，再顺藤找出持锁的后台线程/未关闭会话并收敛其生命周期；
届时应把重试视为过渡手段并在同一 PR 中移除。
