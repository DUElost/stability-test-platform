# TRUNCATE 清库死锁根因收敛：清库前排空后台池 + 现场取证，移除重试止血（#2074）

Status: implemented
Class: bug-fix
Issues: #2074（#1273 残留的根因侧载体）

## Decision

#1273 的根因侧收口。**泄漏者 = 共享后台线程池（`backend/core/thread_pool`）上的
fire-and-forget 任务**，两条提交路径：

- **通知 SAQ 降级直达**（`notification_service._dispatch_notification_via_pool`）：
  测试环境 SAQ 不运行，`enqueue_sync` 全部走 `notification_enqueue_unavailable_
  fallback_pool` 降级——即**每个触发通知的用例**都会向池里投递
  `dispatch_notification`，它「Opens its own DB session」（`SessionLocal()`）并做
  **多条语句事务**（读 prior channel delivery / delivery facts + 写 NotificationLog）；
- **post_completion 缓存刷新**（`refresh_report_cache_for_plan_run`）：同样池上
  fire-and-forget、自开 DB 会话。

这些任务与测试用例**无生命周期耦合**：用例结束、下一个用例的 `db_session` fixture
执行全表 `TRUNCATE ... RESTART IDENTITY CASCADE`（AccessExclusiveLock）时，在途
任务的短事务仍持有已读表上的 AccessShareLock 并要继续访问别的表——循环等待，
PG 把 TRUNCATE 判为牺牲者（#1273 DETAIL：`111 waits AccessExclusive on 17332 ←
114; 114 waits AccessShare on 16604 ← 111`，正是「泄漏会话已读一张表、想读第二张」
的形态）。出错集合随运行漂移 = 投递时序依赖；套件串行运行所以竞争者必是同进程
后台线程（advisory lock 因此无效，与旧 Note 的否决一致）。

修复（三件，同 PR）：

1. **`thread_pool.drain(timeout=10.0)`**（新原语）：有界轮询 `queue_depth()` 等
   「在途 + 排队」清零。生产语义 = 优雅停机前的在飞工作判定；测试语义 = 清库前
   静默化。`_truncate_all_tables` 在 TRUNCATE 前调用它，让在途短事务落地——
   顺带消除了「上个用例的通知行写进下个用例库」的跨用例数据竞态。
2. **`_dump_deadlock_scene(engine)`**（常驻诊断，#1273 时代缺失的能力）：死锁一旦
   再现，立即对同库 dump `pg_stat_activity`（pid/state/last_query）与
   `pg_locks`（带 `pg_class.relname`），把锁环两侧落到**表级 + 调用方语句级**——
   不再只有 relation oid。诊断自身异常只记日志，绝不掩盖原异常。
3. **移除 `_TRUNCATE_DEADLOCK_RETRIES` 重试**（旧 Note Revisit 的要求）：重试
   掩盖的正是这个泄漏；收敛后死锁应恒为 0，若再现则是新的泄漏者，取证 dump 会
   直接给出答案，而不是靠 1.2s 退避碰运气。

## Alternatives

- **测试环境禁用通知降级（`TESTING=1` 时 `_fallback_to_pool` 短路）**：放弃。
  降级路径本身是被测对象（`test_notifications.py` 断言降级行为），短路会让
  93 个通知用例失去真实路径；且 post_completion 不走通知，禁用不完整。
- **每个后台任务改同步执行（测试下）**：放弃——改动面大、改变被测并发语义，
  且治的是调用点不是生命周期。
- **TRUNCATE 前对应用侧会话做 `pg_terminate_backend`**：放弃——同进程会话
  终止会把「后台任务写了一半」变成静默丢失，跨用例污染更隐蔽。
- **`pg_advisory_lock` 串行化清库**：维持旧 Note 的否决（竞争者不参与其锁序）。
- **清库加锁等待超时（`lock_timeout`）后重试**：本质仍是重试止血，且不提供
  根因可见性；drain + 取证组合在两端（防与诊）都更强。

## Verification

- **泄漏者定位（反事实复现 + 现场 dump，2026-09-15 本机）**：`_TRUNCATE_DEADLOCK_
  RETRIES = 0`、无 drain、带 `_dump_deadlock_scene` 跑全量 `backend/tests/`
  （2759 项，12m02s）——**复现 11 次 DeadlockDetected**（CI 时代 7–8），全部落在
  admission / dispatcher 系列用例的 setup（这些路径触发通知 → SAQ 缺位 → 降级池
  投递）。死锁瞬间 dump 显示环另一侧是**秒龄连接、最后语句 COMMIT 的短事务**
  （如 pid 336/337 于死锁前 5s 建立）——与池上 `dispatch_notification` 的
  `with SessionLocal()` 短事务签名一致（TRUNCATE 被判牺牲者回滚后对方立即
  跑完，故 locks 快照为空、只余 idle 会话）。同跑中旧重试用例
  `test_truncate_deadlock_retry.py` 两项失败 = 「重试移除」的反事实面。
- 收敛后终态全量（drain 生效、重试已删）：**0 次 DeadlockDetected、
  admission/dispatcher 无 setup ERROR**（数字见 PR 描述）。
- 新契约回归 `backend/tests/test_truncate_quiescence.py`（替换
  `test_truncate_deadlock_retry.py`）：drain 先于 TRUNCATE / 死锁一次上抛且取证
  恰一次 / 非死锁不取证 / drain 真实语义（Event 精确编排，不依赖 sleep 时长）/
  取证自失败不外溢 —— **5 passed**。
- 重试移除的反事实：`test_deadlock_raises_immediately_and_dumps_scene` 在
  「恢复重试」的旧实现下会因 `calls == 1` 断言失败（旧行为是 4 次调用）。
- 通知面（受 drain 影响最直接）：`test_notifications.py` + `test_notification_
  delivery.py` + `test_precheck_notify.py` + `test_notification_service.py`
  → **93 passed**。
- 全量 `backend/tests/`（重试已移除、drain 生效）：见 PR 描述（含
  TRUNCATE_DEADLOCK_SCENE 是否出现的核对）。
- `python scripts/run_gates.py check:quick`：见 PR 描述。

## Revisit

- **drain 的 10s 上界**：后台池任务都是秒级短事务（DB 写 / SMTP 已有独立超时）；
  若未来出现 >10s 的池上任务，清库会退化为「不等 + 可能死锁」，此时取证 dump
  会点名该任务——正确响应是给它挪池或加生命周期，而不是回调 drain 上界。
- **取证覆盖面**：`_dump_deadlock_scene` 只挂在清库路径。若其它 setup 型
  事务（如 `pr-migrate-empty-db`）再现死锁，把同一 dump 挂过去即可（函数可复用）。
- **`queue_depth()` 的覆盖边界**：它只统计走 `thread_pool.submit` 的任务；
  绕开池的裸线程若再出现（`#1122` 的初衷就是收编），不在 drain 保护内——
  review 总纲的「后台工作必须走共享池」检查项是对应防线。
- **CI 验证义务**：本地全量通过 ≠ 现象根除（时序分布不同）。若后续夜间全量
  再现 `TRUNCATE_DEADLOCK_SCENE` 日志，泄漏者已自报家门，按 dump 收敛后
  本 Note 的结论不需要推翻——drain + 取证是终态形态，不需要恢复重试。
