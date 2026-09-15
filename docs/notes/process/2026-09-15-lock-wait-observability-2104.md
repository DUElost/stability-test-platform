# 补锁等待观测面：让「死锁改等待」可见

Status: implemented
Class: process

## Decision

给「锁序修复后的代价」补上可观测面。此前只有 `#1958` 的
`stability_db_deadlock_total` / `StabilityDbDeadlockDetected`，而锁序统一（`#1959`/`#1980`/
`#1985`/`#2022`）之后，环路等待消失、**普通等待**成为新的代价形态：

- 保留清理事务持有 `plan_run` 与候选子树行锁期间，热路径（`/complete`、批量续租）在同一批
  行上排队；反向亦然；
- 这类等待对死锁计数**完全不可见** —— 只看它就会得出「计数为 0 = 无代价」的结论，正是本仓
  反复出现的「绿而空」形态（共享行加锁表的 Revisit 已登记该缺口）。

新增三样东西：

| 指标 / 告警 | 含义 |
|---|---|
| `stability_db_lock_waiters`（Gauge） | 此刻本库有多少会话在等锁 |
| `stability_db_lock_wait_max_seconds`（Gauge） | 等最久的那个等了多久 |
| `stability_retention_txn_seconds`（Histogram） | 保留清理的**持锁窗口**（取第一把行锁 → 事务结束），即「`#2022` 只统一顺序没缩短窗口」的度量 |
| `StabilityDbLockWaitSustained`（告警） | `max_over_time(...[10m]) > 30` 且持续 5m |

采样方式与既有舰队 gauge 同口径：**拉取期现算**（`/metrics` 端点里一条聚合），不新增周期
任务、没有 staleness，失败只跳过本组不拖垮抓取（`except SQLAlchemyError` 与
`_refresh_fleet_gauges` 一致）。

## Alternatives

- **只靠死锁计数**：否决。等待与死锁是两种形态，前者无异常、无日志、无计数——本单的存在理由。
- **后台周期任务采样**：否决。需要新任务 + 有 staleness；`/metrics` 已经拿得到 DB session，
  舰队 gauge 就是现成的拉取期先例。
- **告警阈值用「有等待」**（`waiters > 0`）：否决。NFS 长回收造成的秒级等待属正常，
  `> 0` 会长期噪声化，反而训练出「忽略它」；取 **>30s 且持续 5m**，到得了就说明是真实争用或
  长持锁。
- **只在保留清理里记时长**：保留（`retention_txn_seconds` 确实加了），但**不足以**替代 PG 侧
  gauge——等锁的可能是别处的持锁者（未来的新路径），只有 PG 侧的面才不依赖「我们知道是谁」。
- **只加指标不加场景测试**：否决。本仓 promtool 可用（`/usr/bin/promtool`），
  `tests/test_prometheus_alerts_contract.py` 的场景层会真跑；新规则/新场景已实跑通过。
- **回归只断言「字段存在」**：否决。那是假绿——采样恒 0 也会「字段存在」。用例必须制造真实
  排队并断言非零，再断言释放后回零。

## Verification

- **新回归**（`backend/tests/api/test_metrics_lock_wait_gauges.py`）：两个额外会话制造稳定的
  行锁排队 → 抓 `/metrics` 断言 `waiters >= 1` 且 `max_wait_seconds > 0` → 释放后回 0。
  `pytest … -q` → **1 passed**。
- **两条负向对照**（分别回退一个修复，各自以**不同**断言失败，证明鉴别力）：

  | 回退 | 结果 |
  |---|---|
  | 去掉 `pg_stat_clear_snapshot()` | `waiters` 读到 0 → `确有会话在等锁…却是 0.0` |
  | `now()` 替代 `clock_timestamp()` | `max_wait_seconds` 读到 0 → `等待已持续…却是 0.0` |

- **告警契约**：`promtool check rules` → `SUCCESS: 17 rules found`；
  `promtool test rules` → `SUCCESS`（新场景在内）；`tests/test_prometheus_alerts_contract.py`
  → **3 passed**（promtool 可用，场景层真跑）。
- **三段测试面**：`backend/agent/tests/` → **1992 passed**（含 13 条 `test_cron_scheduler.py`，
  该文件覆盖 `run_retention_cleanup`）；retention + metrics + 死锁契约面 → 33 passed；
  根 `tests/` → **599 passed**；`ruff check` 全绿。

### 这条回归逼出来的两个真 bug（都已修，且各自有负向对照）

1. **必须清统计快照**：`pg_stat_activity` 属 `pg_stat_*` 视图族，PG 15+ 在**同一事务内**读的是
   事务起始时的写时复制快照；`/metrics` 请求里舰队 gauge 先跑、已把快照定住 → 采样**恒 0**。
   修法：读之前 `SELECT pg_stat_clear_snapshot()`。（`#2022` 在
   `pg_stat_database.deadlocks` 上踩过同一坑——同一教训在同一个仓库复发第二次。）
2. **必须用 `clock_timestamp()`**：`now()` 是**事务起始**时间，而抓取事务通常开在等待出现
   **之前** → `now() - query_start` 为负 → 取 max 后被 clamp 成 0，
   `max_wait_seconds` 恒 0。

### 另一处踩点：回归的就绪判定必须锁定「自己那个会话」

`_wait_until_this_session_waits` 不能用「存在任意未获授锁」：进程内后台泵
（admission queue pump）会有瞬时等待，只判「有等待」会在**我方尚未阻塞**时就返回，抓取早于
阻塞 → 读到 0 而误判实现有问题。判据取「`pg_locks.granted=false AND pid = <本用例的等待会话
pid>」，pid 由等待线程自己 `SELECT pg_backend_pid()` 上报。

## Revisit

- **阈值校准**：30s 是依据「NFS 回收应在秒级」定的。上生产后先看
  `stability_retention_txn_seconds` 的分布与 `stability_db_lock_wait_max_seconds` 的峰，若
  正常回收就能到 30s，则应上调阈值而不是让人习惯忽略它。
- **多库/多实例**：采样限定 `datname = current_database()`。若将来出现单实例多库或分库，
  需要按库打 label（当前刻意不打，避免无界值域进指标面）。
- **采样成本**：一条 `pg_stat_activity` 聚合 + 一次 `pg_stat_clear_snapshot()`，只在抓取时发生
  （默认 15–30s 一次）。若抓取频率提高或 `pg_stat_activity` 行数变大，评估缓存到
  `t` 秒粒度。
- **与 `#1958` 的分工**：死锁计数（异常驱动、稳态 0）看**环路**；本组 gauge（快照驱动、允许
  非零）看**排队**。两者都不为 0 时先看锁序表 I1–I4，而不是先怀疑语句形状。
