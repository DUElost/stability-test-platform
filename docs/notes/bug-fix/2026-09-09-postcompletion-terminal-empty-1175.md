# post_completion pending 判据：终态空 detail 不再永久扣留报告（#1175）

Status: implemented
Class: bug-fix

## Decision

1. **`case_result_ingest_pending` 语义收窄**（`case_result_ingest.py`）：
   pending ⇔ 引用的 detail **尚不可读**（文件缺失/读失败/非 dict →
   `_load_detail_json` 返回 None，这些才是可能「晚到」的瞬时态）。detail
   文件已存在且解析为 dict 即**终态**，即便 `testpoints` 为空或缺键（合法
   零用例、metrics-only 等）也放行提交——与 `ingest_test_case_results_for_job`
   把空列表当 0 行终态一致。此前「空 testpoints 恒 pending」使这类 job 每次
   post_completion 都 compose 报告后 `db.rollback()`，报告永失且无限重算。

2. **recycler 截止守卫**（`scheduler/recycler.py`）：detail 长期不到的孤儿
   终态 job（`ended_at` 早于 grace + `POST_COMPLETION_MAX_DEFER_SECONDS`
   缺省 6h）停止重入队，内存去重集内每个 job 告警一次
   （`post_completion_defer_cutoff` error）留痕——不再每轮全量重扫重算烧 CPU。

涉及：`backend/services/case_result_ingest.py`、
`backend/scheduler/recycler.py`；测试见
`test_post_completion.py`（空 testpoints / 缺 testpoints 键两终态用例）、
`test_phase0_closure.py::TestDeferredPostCompletion::test_defer_cutoff_stops_reenqueue_for_ancient_orphan`。

## Alternatives

- 保留「空=defer」但给重试计数：需持久化计数字段或 Redis 瞬时计数，前者
  迁移后者违背 Redis 角色，且仍无法区分晚到与永不到；不取。
- 截止后强制落报告（degraded finalize）：会把「用例摄入缺失」静默吞掉，
  数据不可达性无审计面；改为停止重试 + error 告警，保留人工核查路径。

## Verification

- `pytest backend/tests/services/test_post_completion.py`：5 passed（原
  defer-on-missing、late-file、ingest 失败三例语义不变；新增两终态空用例）。
- `pytest "backend/tests/test_phase0_closure.py::TestDeferredPostCompletion"`：
  2 passed（新增 7 天前孤儿不再重入队）。

## Revisit

recycler 截止窗口（缺省 6h）与「detail 晚到上限」的匹配度需线上观察；
`_defer_cutoff_alerted` 为进程内存集，重启后重新各告警一次（可接受）。
