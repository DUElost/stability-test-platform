# 上传标记超时不得标 ready=true（#1079）

Status: implemented  
Class: bug-fix

## Decision

`merge_task` 将就绪拆成 `mark_ready`（本轮 `upload_mark`）与
`events_ready`（pending==0）。`upload_summary.ready` 仅在二者皆真时为 true。
标记超时仍可 best-effort 继续 extract，但写入
`incomplete_reason=upload_mark_timeout` 与 `compensation=best_effort_extract`，
避免 LOCAL 未计入 pending 时的假就绪。

涉及：`backend/tasks/saq_tasks.py`；测试见 `test_saq_tasks.py`。

## Alternatives

- 标记超时直接中止 extract：过严，与现有 best-effort 链不一致。
- 把 LOCAL 计入 pending：改变 ADR-0028 过滤模型语义，面过大。
- 仅打日志不改 ready：缺口仍不可观测，不满足验收。

## Verification

- `test_merge_task_mark_timeout_sets_ready_false_despite_pending_zero`
- `test_wait_for_upload_mark_times_out_on_missing_or_stale_round`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/tasks/test_saq_tasks.py -k 'upload_mark or mark_timeout' -q`

## Revisit

若产品需要「部分成功」就绪态，可另增 `ready_partial` 枚举，不复用
`ready=true`。
