# 租约回收器 Phase 2 收口速率：一轮多解、一候选一事务（#2531）

Status: implemented
Class: bug-fix

## Decision

`device_lease_reconciler` 的 Phase 2（UNKNOWN 过宽限 → 释放租约 + 判 FAILED）与
`stale_unknown` 分支原先**处理一条候选就 `break`**——`#1172` 为了适配 `on_job_terminal`
的自管理提交（`#986`）把终态化移到函数尾部执行一次，收尾即本 tick。代价没有被任何地方
记下：解锁速率被钉在 1 台 / `RECONCILER_INTERVAL_SECONDS`（默认 15s ⇒ 实测 12s/台、
≈4 台/分钟），按 `agent_api` 的 60 host × ~17 device ≈ 1000 台容量口径外推，大面积失联
后全部设备恢复可用要 ≈3.3 小时，期间准入队列一律 `DEVICE_BUSY`。

改法（**保留 `#1172`/`#986` 的调用契约，只换事务边界的粒度**）：

1. **一候选一事务边界**：候选的 savepoint 一释放就立刻为**该候选**调用
   `on_job_terminal`（其内部聚合后自管理提交），而不是攒到函数尾部。于是每条候选的
   「Job 终态 + 计数 + 父聚合」自成一个提交点，既不跨候选混交父终态化，也不把前序候选
   押在最后一条上；`job_terminalization` 模块 docstring 同步补上这条批量调用方形态。
2. **单轮上限 `RECONCILER_DRAIN_BATCH`（默认 20，`ge=1 / le=1000`）**，进调度域
   Settings（ADR-0042 同域，不裸 `os.getenv`）。计数只算走到终态化的候选——Phase 1
   与孤儿/D5 分支本来就不设预算，也不能被挤掉（`#2531` 实测 Phase 1 一轮就吃掉 25 台，
   慢的从来只有解锁那一步）。下界 1 的理由与 `#2278` 同形：0 会让解锁静默永久停摆、
   且读数看起来像「无积压」。
3. **跨候选锁序显式化**：`stale_unknown` 的候选扫描补 `ORDER BY job_instance.id`
   （`expired_leases` 原先已在 Python 侧按 `job_id` 升序定序）。一候选一事务时顺序无关
   紧要，一轮多解后它决定会不会与 `extend_leases_batch`
   （`WHERE id IN (...) ORDER BY id FOR UPDATE`）互等。共享行加锁表 I1 行已按
   「每轮增量核对」回填。
4. **观测面**：每轮 tick 末尾一条 `reconciler_unknown_backlog` 日志 +
   `stability_reconciler_unknown_backlog{state}` gauge（gauge 与接线同 PR——`#2287`
   刚删掉过两只同族的死 gauge），三桶 `grace_expired` / `within_grace` /
   `missing_ended_at`；被上限截断时另打 `reconciler_drain_truncated remaining=<n>`。
   `missing_ended_at` 单独成桶不是凑数：两条回收路径的判据都要 `ended_at`，该桶的行
   **永远不会自愈**，混进 `within_grace` 就是把「写坏时钟的死行」报成「还在正常宽限」。
5. **失败面收敛**：终态化抛错（外层事务已被打成 aborted）时 `rollback()` + 收尾本条
   检查，已提交的候选保持不动、未提交的下轮重来；不这么处理会余下每个候选都撞一次
   `InFailedSqlTransaction`。

## Alternatives

- **只把 `RECONCILER_INTERVAL_SECONDS` 调小**（运维侧曾提过的止血形态）——放弃：全局
  旋钮，牵动整个对账周期（含 D5 与 abort 对账），且 12s/台 × 1000 台要周期压到 30ms
  才够，方向就是错的：**速率不该由 tick 频率承担**。
- **尾部收集多条候选、一次循环终态化**（`#1172` 原形状的直白放大）——放弃：这正是
  `test_reconciler_phase2_commits_each_candidate_independently` 判掉的形状。前序候选
  的写入会押在被堵住的最后一条上（既读不到也不落库），一旦回滚整批一起丢；并且多条
  候选的父聚合会混进同一个提交，`#986` 的「父终态先提交」变成只对最后一条成立。
- **每候选一个独立 `AsyncSession`（弃外层单事务）**——放弃：改动面与连接占用都成倍
  （`#1172` 当时同样理由弃过一版），而 `on_job_terminal` 的自管理提交已经把每条候选
  变成独立提交点，再换 session 换不来额外的隔离性，只换来「一候选一事务」的另一种写法。
- **不加 gauge、只留日志**——放弃：日志只能事后 grep，读不出「现在还剩多少、要多久」，
  而 `reconciler_actions` 是增量计数器，恰恰在速率变成离散批量后无法反推进度（`#2365`
  同族的「看不见就没法运维」）。

## Verification

- 新增/改写 5 条 PG 回归（`backend/tests/scheduler/test_device_lease_reconciler.py`）：
  一 tick 收口 3 台（含租约 RELEASED 与父 Run 终态）、**速率律** `cap=2` 时 5 台按
  `[2, 2, 1]` 收口（3 个 tick 而非 5 个）且不饿死、后序候选被行锁堵住时前序候选已
  **独立提交**（`pg_stat_activity` 等到真的在排队才开始断言）、`stale_unknown` 一 tick
  收 3 条、积压三桶计数 + gauge 真的被写入。该文件 **27 passed**（基线 22 + 新增 5）。
- `backend/tests/scheduler/` 全目录 + `test_db_deadlock_metrics` +
  `test_agent_dual_write` + 两条锁序回归（`1980` / `2015`）**222 passed**
  （testcontainers PG，未连生产库；#1172 记的「scheduler 独跑 TRUNCATE 顺序 error」
  本轮未出现）。
- 离线契约面 `tests/test_settings_scheduler.py` / `test_env_example_parity.py` /
  `test_env_inventory.py` / `test_alert_metric_producers.py` /
  `test_prometheus_alerts_contract.py` / `test_grafana_dashboard_contract.py`
  **63 passed**（新 gauge 有真实生产者，故生产者门禁与仪表板豁免表都不需要动）。
- `python scripts/run_gates.py check:quick` **10 gates OK**；
  `python scripts/run_gates.py check:pr` **19 gates OK**。
- 变异自证 7 条，全部 on-target：`M1` 恢复 `break`（3 条红）、`M2` 去掉上限
  （只有速率律那条红，实测 `[5, 0]`）、`M3` 把终态化放回 savepoint 内（违反 `#986`，
  3 条红）、`M4` 不写 gauge（`written == []`）、`M5` 去掉截断读数、
  `M6` 把 `missing_ended_at` 折进 `within_grace`（该桶归 0、`within_grace` 1→2）、
  `M7` 恢复 stale 分支 `break`（`#1172` 前的原用例即红）。
  过程中一次**假绿自证**：变异脚本第一次带了本仓 pytest 不认的 `--timeout` 参数，
  6 条「红」全是 rc=4 用法错误——去掉参数重跑后，红条才是断言级。
- **一次性容量探针**（testcontainers PG，同机；探针脚本跑完即删、不入仓）：把
  `RECONCILER_DRAIN_BATCH` 当自变量测排空成本，验证「一候选一事务」在批量下仍是线性：

  | N（台） | cap | 需要的 tick 数 | 单轮墙钟 | 每台成本 |
  |---|---|---|---|---|
  | 40 | 20 | 2 | 204 / 171 ms | ≈9 ms |
  | 40 | 40 | 1 | 354 ms | ≈9 ms |
  | 200 | 20 | 10 | 163–222 ms | ≈9 ms |
  | 200 | 200 | 1 | 1654 ms | ≈8 ms |

  20→200 候选的单轮成本 165–222ms → 1654ms（×10 候选 ≈ ×8–10 时间，**无超线性**），
  于是两台设备之间的排空成本约 8–9ms，与批大小无关。按 `agent_api` 的 1000 台口径外推：
  默认 `cap=20` 时收口 1000 台 = 50 tick × 15s ≈ **12.5 分钟**（修前 1000 tick ≈ 4.2 小时），
  而这 50 轮真正干活的时间合计只有 ≈9s（占空比 <1%）。
  口径边界（不外推成结论）：本机容器 PG、每台一个 PlanRun（聚合是 O(1) 逐 run），
  且测试环境里链式派发/dedup 入队被 SAQ 未初始化短路掉——真实成本只会略高，不会低一个量级。
- **未完成（pending）**：issue 判据 3 的端到端**墙钟**复测需要把本分支部署进 dev
  隔离栈，本轮未做。已在正文说明为什么墙钟钉子以 tick 计数律的形式进 CI
  （`2×宽限` 与 `×周期` 两项都不受本改动影响，唯一变化的就是 tick 数）。

## Revisit

- **issue 判据 3 的端到端墙钟钉子仍未执行**：合入后在 dev 隔离栈按
  `serve --devices 30` → 批量 run → `claim --capacity 40` → 批量 abort 复测一次，
  断言「最后一条 `lease_released` − 宽限到期」≤ `ceil(N/RECONCILER_DRAIN_BATCH) ×
  RECONCILER_INTERVAL_SECONDS + 容差`。没跑之前，`#2531` 只算「代码级修复 + 单元级
  证据」，不算「现场复测通过」。
- **跨候选顺序没有独立的行为钉子**：`stale_unknown` 的 `ORDER BY id` 与
  `expired_leases` 的 `sorted(...)` 目前只被代码与共享行加锁表钉住。PG 里行锁不进
  `pg_locks` 的可见视图，要钉住「升序处理」只能靠 NOWAIT 逐行探测的构造（成本高、
  且与本单主断言正交）。若将来再出现「回收器 × 别的批量写者」的死锁环，第一嫌疑是
  这条顺序被改坏，而不是先怀疑语句形状。
- **`le=1000` 只在容器 PG 上验证到线性**：上面的探针说明 200 台一轮 ≈1.65s、
  线性外推 1000 台一轮 ≈8.3s，仍小于默认的 15s 周期（且 `_reconcile_lock` 会
  在下轮还没跑完时直接跳过而不是叠加）——所以 1000 这个上界**不是拍脑袋**，
  但它也不是生产形态的安全证明：生产上每台候选的终态化还要叠真实的链式派发与
  dedup 入队、PlanRun 是多 job 共享（同一 run 的 `FOR NO KEY UPDATE` 会被批内
  多次排队）、以及与其它写者的锁竞争。真要调大到几百，需要的是**单轮耗时读数**
  （当前只能靠 `reconciler_skip_previous_still_running` 反推「上一轮没跑完」），
  这属于 `#2365` 那类背景任务耗时面，不在本单内顺手补。
- **Phase 1 仍不设预算**：一轮把全部 RUNNING 过期候选打成 UNKNOWN 是现状且更快，
  但它同样在一个事务里持有多少把 Job/Lease 锁没有上界。今天没证据说它出问题
  （`#2531` 实测 Phase 1 单轮 25 台无滴漏），若将来大面积失联与批量续租同时发生
  并出现死锁增量，应把预算概念推广到 Phase 1，而不是继续调 `RECONCILER_DRAIN_BATCH`。
