# 数据库死锁指标化：按错误类计数，不再只靠服务端日志（#1958）

Status: implemented
Class: bug-fix

## Decision

把「数据库检测到死锁（SQLSTATE 40P01）」变成一个**按错误类**存在的可观测事实，
而不是某一条业务路径的副产品：

1. `backend/core/metrics.py`：新增 `stability_db_deadlock_total{engine}`（`sync|async`）
   与 `record_db_deadlock()`。
2. `backend/core/database.py`：新增 `_attach_db_error_metrics()`，用 SQLAlchemy 的
   `handle_error` 事件挂在**两个引擎**上（async 侧挂 `async_engine.sync_engine`，
   与既有 `_attach_pool_metrics` 同法），命中 40P01 时计数并打一条
   `db_deadlock_detected engine=… statement=…` 的 warning。注册的回调记录在
   `_db_error_handlers` 里，供测试证明「接线确实生效」。
3. `deploy/prometheus/alerts-stability-platform.yml`：新增
   `StabilityDbDeadlockDetected`（`increase(stability_db_deadlock_total[15m]) > 0`，
   for 5m，warning）。
4. `backend/tests/core/test_db_deadlock_metrics.py`：13 例，含**真实死锁**的端到端
   用例。

### 为什么挂在引擎层而不是逐路径埋点

Job/Lease 锁序死锁复发约四周而无人察觉的直接原因是**它只在 PostgreSQL 服务端日志
里看得见**：受害事务被上层的通用 `except Exception` 收走，日志还记成与原因不符的
`reconciler_job_load_failed`，随后该 check 照常 commit、`reconciler_runs` 记
`success`；业务侧只剩零散 500。

`handle_error` 在 DBAPI 异常**交给调用方之前**触发，因此：

- 上层吞不吞异常都不影响计数（回收器那条路因此自动被覆盖）；
- 新增任何数据库访问路径都自动纳入，不需要每次记得埋点；
- 判定按错误类（40P01），而不是按「谁在校验什么」——这正是 #743/#729
  「只记日志无人盯」教训在数据库侧的落地方式：当时只给那一个幽灵端点加了告警，
  没有升级成通用要求，于是同一个教训在死锁上原样复用了一遍。

### 与 issue 描述的偏离（显式记录）

#1958 原文写的是「`lease_extend_batch_total` 增加失败/异常出口」。**本 PR 没有那样做**，
理由：该出口要覆盖「路由内任意语句死锁」就得给 `extend_leases_batch` 约 250 行函数体
整体加 try/except 重缩进；而它要提供的信号已经由两处覆盖——
`stability_db_deadlock_total`（按原因）与既有的
`stability_api_requests_total{status_code="500"}`（按结果）。把「死锁」做成错误类计数
比在每条路径上复制一份 outcome 更少、更不会漏。若后续仍需要 per-route outcome，
应在该路由拆分（#1520 的 God-module 收敛）时一并做。

### 涉及

- `backend/core/metrics.py`、`backend/core/database.py`
- `deploy/prometheus/alerts-stability-platform.yml`
- `backend/tests/core/test_db_deadlock_metrics.py`（新增）

### 顺带记录的一处测试环境事实

`pg_stat_database.deadlocks` **不能**在长事务里即读即用：PG 15+ 的统计是写时复制
快照，会话事务若早于事件开始，读到的仍是旧值（本次在 #1959 的回归里实测漏判过
一次，改用「新事务 + `pg_stat_clear_snapshot()`」后正常）。应用侧自计数不受该问题
影响，这也是不依赖 `pg_stat_database` 而自己计数的一个理由。

## Alternatives

- **只给 `extend_leases_batch` 加 `outcome="error"`**：放弃。见上「与 issue 描述的
  偏离」；覆盖不全（回收器侧仍无信号），且要重缩进大函数。
- **在回收器逐候选的 `except` 里单独计数**：放弃。只在那一处可见，等于把
  「按路径埋点」的漏检模式再来一遍；引擎层计数已覆盖它。
- **采集 `pg_stat_database.deadlocks`（PG exporter / 自定义抓取）**：作为补充可以，
  但作为唯一信号不可靠——它要求监控栈与导出器在位，且经上面那条快照坑；应用自计数
  与部署形态解耦。
- **把死锁做成 error 级日志而非指标**：放弃。日志正是**已经存在**的那条信息，
  缺的是「无人盯」的收敛点（#743 已给出的教训）。

## Verification

实跑（本机 `.venv`；PG 用例经 testcontainers `postgres:16`，未设 `TEST_DATABASE_URL`）：

| 命令 | 结果 |
|---|---|
| `python -m pytest backend/tests/core/test_db_deadlock_metrics.py -q` | **13 passed**（3.3s） |
| `python -m pytest tests/test_prometheus_alerts_contract.py -q` | **3 passed**（结构层：新规则的选择器在指标注册表内；promtool 场景层亦通过） |
| `python -m ruff check backend/ tools/ scripts/` | All checks passed |

覆盖到的关键断言：

- **真实死锁端到端**：两个会话以相反顺序取同一对 `pg_advisory_xact_lock`，PG 报
  40P01，`stability_db_deadlock_total{engine="async"}` 恰好 +1，且异常照常向上传播。
  这条用例的意义是证明 `handle_error` 在 **async 引擎**上确实触发——只测回调本身
  会漏掉这一点，而回收器走的正是 async 路径。
- 判定边界：`40001` / `23505` / `57014` / `08006` 均不计数；无 `sqlstate` 时按消息兜底；
  `original_exception` 为空不计数。
- 观测不改变错误传播：计数器后端抛错时回调只记 debug。
- 接线：`_db_error_handlers` 同时含 `sync` 与 `async`（避免「metric 写了但没接上、
  测试仍绿」）。
- 规则存在性：告警文件里必须有规则引用该指标（有指标无规则 = 又一个无人盯的信号）。

## Revisit

- **效果观测**：#1959 合入后 `stability_db_deadlock_total` 应回落到 0 并在告警上无
  增量。若仍有增量，说明另有环路（已知候选：`job_heartbeat`/`extend_job_lock` 的
  `updated_at` touch 家族、终态化聚合的 `plan_run → plan_run_host → job_instance`
  三方环），此时告警描述已指向该排查方向。
- **阈值**：当前是「> 0 即告警（for 5m）」。若出现被接受的瞬时死锁（例如某个已知
  可重试路径），应改为按速率/比例并显式记录接受理由，而不是直接调高阈值掩盖。
- **告警场景文件**：本 PR 只加了规则，未在
  `alerts-stability-platform.test.yml` 里补同规则场景（promtool 对未列规则不校验）；
  若要给该告警加回归场景，应连同标签形状一起补。
- 该指标是「错误类计数」的第一个实例。若后续为其它数据库错误类（锁等待超时、
  序列化失败）加同类指标，应先确认需要区分到什么粒度，避免无边界地扩标签。
