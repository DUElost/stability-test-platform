# post_processed_at 晚于用例摄入提交（#1076）

Status: implemented
Class: bug-fix

## Decision

`run_post_completion` 原先先 `commit post_processed_at` 再摄入且吞掉 ingest 异常，
recycler 只选 `post_processed_at IS NULL`，导致晚到 JSON / 首次摄入失败永久卡死。

1. 新增 `case_result_ingest_pending`：trace 有 `detail_uri` 但尚无
   `test_case_result` 且文件未就绪/无 testpoints 时返回 True。
2. 摄入移到 `post_processed_at` 之前；pending 或异常时 `rollback` 并返回 False。
3. 已标完成但仍 pending 的旧 Job 走 `_retry_stuck_case_ingest` 修复路径。

## Alternatives

- **拆分 post_processed_at / case_ingested_at 两列**：更精确但需迁移，本单不取。
- **仅改 recycler 过滤**：不解决主路径先提交完成标记，弃用。

## Verification

```bash
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/tests/services/test_post_completion.py \
  backend/tests/services/test_test_case_result_ingest.py -q
```

## Revisit

- trace 未到（无 `detail_uri`）的 MTBF Job 仍会被标完成；需 pipeline 级探测时再开 Requirement。
