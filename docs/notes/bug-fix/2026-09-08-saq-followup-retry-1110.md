# SAQ 后继投递失败可重试（#1110）

Status: implemented
Class: bug-fix

## Decision

`scan_task` / `merge_task` 在 upload/merge 或 extract 入队失败时只打 log 仍正常返回，
SAQ 将父任务标完成且不重试；`auto_archive_sweep` 终态 Run 只要有 scan artifact 即永久跳过。

1. 后继 `enqueue` 异常在记录后 **re-raise**，交由 SAQ `retries` 重试。
2. 终态 auto-archive 仅在 **merge artifact + run_context.extract** 齐备时跳过；
   仅有 scan 时允许再次 `enqueue_dedup_terminal_sync(is_final=True)` 补偿断链。

## Alternatives

- **持久化阶段状态机新列**：更精确但需迁移，本单用既有 artifact/run_context 判定。
- **cron 单独补 merge/extract 任务**：与 scan 链分叉，弃用。

## Verification

```bash
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/agent/tests/test_saq_scan_pipeline.py -q -k 'enqueue or auto_archive_sweep_retries_terminal'
```

## Revisit

- `enqueue_dedup_terminal_sync` 自身仍吞异常（只 log）；断链入口若也失败需另开 Requirement。
