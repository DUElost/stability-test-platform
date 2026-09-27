# Phase 0 截止补偿用例补齐不可读 detail（#3247）

Status: implemented
Class: bug-fix

## Decision

给 `test_defer_cutoff_stops_reenqueue_for_ancient_orphan` 的终态 Job 增加一条引用不存在的 detail 文件的 `StepTrace`，并在调用 recycler 前断言 `case_result_ingest_pending` 为真。用例现在确实覆盖「detail 长期未到，超过截止期后停止重入队」的分支。

## Alternatives

- 回退 #3341 的生产判据会让 detail 已可读、但主路径漏发的旧 Job 无法得到补偿；不采用。
- 只 mock `case_result_ingest_pending=True` 虽能使测试变绿，却无法验证此集成测试的数据库事实与判据一致；不采用。

## Verification

- 修改前，在隔离测试库重现该单例失败：`assert filled == 0`，实际 `1`。
- 修改后，隔离测试库中本用例及 #3341 的「截止且不可读」「截止但可读」相邻用例共 3 passed。
- 完整 `backend/tests/test_phase0_closure.py`：16 passed。
- `python scripts/run_gates.py check:quick`：16 gates passed；未配置测试库 URL，schema-at-head 按门禁设计提示跳过。

## Revisit

如果「没有 detail_uri」的终态 Job 也应停止重入队，需要先定义其业务语义并修改 `case_result_ingest_pending` 与 #3341 的可读分支测试；本次不扩展该契约。
