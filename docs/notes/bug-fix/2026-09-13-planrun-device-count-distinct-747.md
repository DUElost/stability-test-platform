# PlanRun device_count 按 distinct device_id（#747）

Status: implemented
Class: bug-fix

## Decision

列表 `GET /plan-runs` 的 `device_count` 改为
`count(distinct JobInstance.device_id)`；`_plan_run_out` 在未显式传入
计数时，从 jobs 回落也用 `{device_id}` 去重。Schema 注释改为「设备」语义。

与 watcher-summary / UI「设备」列口径对齐。现有
`uq_job_instance_plan_run_device` 下同设备多 job 不可写入，故行数虚高在
当前库约束下难复现；仍用 distinct 锁定语义并防御约束放宽/脏数据。

## Alternatives

- **继续用行数并注释「≈ total_job_count」**：与「设备」列语义冲突；否决。
- **detail 另查一次 distinct count**：jobs 已加载时可本地去重，无需二次查询。

## Verification

- `python -m pytest backend/tests/api/test_plan_runs_api.py -k device_count -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若未来 detail 改为延迟加载 jobs，需在 detail 路径显式传入与列表同源的
distinct 统计，避免回落空 jobs → 0。
