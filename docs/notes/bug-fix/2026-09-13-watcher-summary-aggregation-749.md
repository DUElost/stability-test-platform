# Agent Note: watcher-summary 平台分桶查询收敛（#749）

Status: implemented
Class: bug-fix
Issue: #749

## Decision

`_aggregate_watcher_platform_buckets`（`backend/api/routes/plan_runs.py`）由
**3 次全量聚合 + N 次平台内查询** 收敛为 **3 次定值查询**（N = 平台数）：

1. **`running` / `participating` 两次聚合合并为一次条件聚合**：
   ```python
   func.count(func.distinct(JobInstance.device_id))                # participating
   func.count(func.distinct(case(                                 # running
       (JobInstance.status == JobStatus.RUNNING.value, JobInstance.device_id),
       else_=None,
   )))
   ```
   **等价性**：`count(distinct expr)` 忽略 NULL，故 `case` 未命中 status 时计 NULL →
   与原「带 `JobInstance.status == RUNNING` 过滤的独立聚合」结果一致；两个计数取自
   **同一分组**（`Device.platform`），因此两个字典的平台集合也必然一致。
2. **`affected_total` 移出平台循环**：原来循环内每平台一次
   `count(distinct device_serial)`（N 平台 = N 次查询），改为**一次按平台分组的查询** +
   Python 侧字典取值。
   **等价性**：原查询在 WHERE 里用 `coalesce(Device.platform,"UNKNOWN") == platform` 过滤；
   新查询按**原始** `Device.platform` 分组、Python 侧把 NULL 归一为 `"UNKNOWN"` —— 两处对
   NULL 平台的处理一致（分组键与 `by_platform` 同源）；平台无信号行时字典无键 → `.get(…, 0)`，
   与原 `else: affected_total = 0` 分支等价。

顺带补齐 `case` 的导入（`from sqlalchemy import … case …`）。

## Alternatives

- **用 `GROUPING SETS` 把信号聚合与平台总量并成一次查询**：不选。SQLAlchemy 侧可读性显著下降
  （需要区分「平台总量行」与「(平台, category) 行」），而收益只是 3→2 次定值查询；
  issue 的建议 2 也正是「另起一次查询按平台分组」。
- **顺手优化顶层 `affected_total`（同文件，`:2189`）**：不选。它与本函数的聚合**语义不同**——
  该查询**不 join `Device`/`JobInstance`**、也没有平台过滤（即「本 run 全量受影响设备」，
  含 device 行缺失的 signal）。两者并用一次 sum 替代会改变口径（inner join 会丢行），
  属另一件事。
- **给三个查询加缓存/物化**：不选。前端约 5s 一次轮询，查询次数已恒定；缓存引入失效语义，
  与 issue 的目标（去掉随平台数/规模增长的叠加）不匹配。

## Verification

- **既有端点测试全绿（行为等价性）**：`pytest backend/tests/api/test_plan_run_aggregation_endpoints.py -q`
  → **58 passed**（改前改后一致；其中 `test_watcher_summary_platform_bucket_includes_running_unisoc_without_signals`
  与 `…includes_terminal_unisoc_without_signals` 直接覆盖 `running_device_count` /
  `participating_device_count` / `affected_device_count` 三个字段）。
- **新增回归断言（把性能声明变成可回归锁）**：`TestWatcherSummaryPlatformBucketQueryCount`
  —— 用 `sqlalchemy.event.listen(engine, "before_cursor_execute", …)` 统计含
  `job_log_signal` 的语句数（范式取自 `test_mtbf_suite_routes.py` 的
  `test_case_query_count_constant`），断言**平台数 1→3 时该计数不变**；并顺带断言三个平台
  确实出现在 bucket 里（防止「没数据导致假通过」）。
  改前形态下该断言会失败（每平台多一次查询）。
- `python -m py_compile` + `ruff check` ✓；`check:quick` 见 PR 描述。
- **未验证（诚实标注）**：真机规模（276–1000 设备、`job_ids.in_` 长列表）下的**墙钟收益**。
  本单锁定的是**查询次数**（结构性），未做端到端压测；前端轮询间隔（~5s）与 run 规模决定的
  绝对收益需实测。

## Revisit

- 若后续要在真机上量化收益：可在 watcher-summary 上加一条「聚合查询耗时」的观测点
  （或在压测环境对比改前/改后同一 run 的 p95），把「次数」升级为「时间」。
- `job_ids.in_(long_list)` 的**单查询成本**随 run 规模增长（与本单无关的另一维）。若
  1000 设备规模下这三个定值查询仍慢，下一步是做 run 级物化/预聚合，而不是继续减少次数。
