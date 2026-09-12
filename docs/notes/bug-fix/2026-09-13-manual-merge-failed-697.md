# FAILED 终态手动 merge/extract 放行（#697）

Status: implemented
Class: bug-fix

## Decision

修订 ADR-0028 D2：**自动** merge 对 FAILED 仍 skip；**手动** `POST …/dedup/merge|extract`
不再因 FAILED 返回 409。

实现：

1. `run_merge_sync(..., allow_failed=False)`：FAILED 且未放行时返回
   ``skipped_failed``（与工具失败 ``""`` 区分，避免 #1527 raise 误伤）。
2. 手动路由传 `allow_failed=True`；extract 仅保留「无 merge 产物」409。
3. `merge_task` 遇 ``skipped_failed``：写 `upload_summary` 后正常结束（不入队
   extract、不 raise）。

## Alternatives

- **FAILED 自动也 merge**：违背「失败跑不自动出汇总包」产品口径；否决。
- **仅删路由 409、不改 `run_merge_sync`**：手动 API 仍被服务层 skip；否决。

## Verification

- `python -m pytest backend/tests/api/test_dedup_scan_endpoints.py::TestMergeEndpoint -q`
- `python -m pytest backend/tests/services/test_dedup_scan_merge.py -k 'failed or allow_failed' -q`
- `python -m pytest backend/tests/tasks/test_saq_tasks.py -k 'merge_task_skipped_failed or merge_task_all_platforms' -q`
- `python scripts/run_gates.py check:quick`

## Revisit

手动 merge 成功后的 extract 依赖已有 merge 产物；upload 进度 UI 已消费
`upload_summary`，FAILED skip 路径现会写入 incomplete_reason。
