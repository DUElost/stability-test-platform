# 终态数据生命周期两缺口（#781）

Status: implemented
Class: bug-fix

## Decision

Issue 归并两处 B 级缺口：

1. **enqueue 先于 commit**（`job_terminalization`）：Redis 侧链/去重键
   不可随 DB rollback 收回。当前代码已由 **#986** 收口——
   `_post_aggregation_side_effects_*` 先 `db.commit()` 再
   `trigger_next_plan` / `enqueue_dedup_terminal_*`；本单补回归断言
   （commit→enqueue 顺序），不改路径语义。

2. **retention 孤儿**：`job_log_signal.job_id` 与
   `device_log_event.{job_id,plan_run_id}` 均为 `ON DELETE SET NULL`，但
   `run_retention_cleanup` 注释误写 CASCADE、且未显式删行 → 删 Job/PlanRun
   后 signal/event 变孤儿并单调堆积。修复：删 Job 前先删
   `JobLogSignal`（按 stale job_id），再删 `DeviceLogEvent`（按
   plan_run_id / job_id），并修正注释。

涉及：`backend/scheduler/cron_scheduler.py`、
`backend/tests/scheduler/test_retention_cleanup.py`、
`backend/tests/services/test_job_terminalization.py`。

## Alternatives

- 仅改 FK 回 CASCADE：与 ADR-0028 / #213「orphan 可运维清单」设计冲突；否决。
- retention 全局清 `job_id IS NULL`：会抹掉运维仍在看的历史孤儿；本单只清
  本批即将删除的 Job/Run 关联行；否决全局扫。

## Verification

- `pytest backend/tests/scheduler/test_retention_cleanup.py -q`
- `pytest backend/tests/services/test_job_terminalization.py -q`
- `python3 scripts/run_gates.py check:quick`

## Revisit

若 DLE 另有独立保留策略（长于 PlanRun retention），需把本批 DLE 删除改为
「仅断链 / 延期删文件」并另开 issue。
