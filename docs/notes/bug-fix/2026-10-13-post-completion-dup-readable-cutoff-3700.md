# post_completion 重名入库合并 + 可读截止重入队上限（#3700）

Status: implemented
Class: bug-fix

## Decision

按 #3700 规划方案 **v1.1** 实施，不改表结构、不新增 env：

- **F1**（`backend/services/case_result_ingest.py`）：`ingest_test_case_results_for_job`
  入库前按去首尾空白后的 `case_name` 合并同名 testpoint。状态：任一次非 PASS 则
  非 PASS，取最后一次非 PASS 的状态；全 PASS → PASS。有重名时 `detail` 以
  「执行 N 次：S1 → S2 …」开头，再接被采用那次的失败信息，总长截到 2000。无重名
  行为与修改前一致。不改 `uq_test_case_result_job_case`。合并只作用于 `testpoints`
  （sleep/powercycle 当前 `entries` 仍按既有语义入库 0 行）。
- **F2**（`backend/services/post_completion.py` + `backend/scheduler/recycler.py`）：
  额度按「post_completion **实际执行且未落库**」累计。`run_post_completion_async`
  拿到 `run_post_completion` 返回值后记录（False +1 / True 清零，线程安全）；
  recycler 截止分支**只读取**该计数，达 3 次后停止重入队并 ERROR 一次（键
  `post_completion_cutoff_readable_exhausted`，含 job / plan_run / `failure_count`），
  SQL 排除已耗尽 id。入队返回 False、抛异常、异步未执行或同键去重**不消耗额度**。
  `stability_post_completion_cutoff_jobs` 计数口径不变。本上限为过渡做法；终态出口见
  规划方案 §2。外部 SAQ worker 形态下上限不生效（v1.1 接受）。
- **F3**（`deploy/prometheus/alerts-stability-platform.yml`）：告警
  `StabilityPostCompletionCutoffJobs` 说明覆盖「可读但持续失败、已停止重入队」。

## Alternatives

- 改唯一键 / 按 attempt 多行入库：属 schema 变更，规划方案明确不做。
- 在入队时计数（v1.0）：入队失败/去重也会耗尽额度（#3701 R1），队列恢复后无法补偿。
- 跨进程持久化重试计数：正确终态，但本单 Appetite 为过渡进程内上限；升级问题留给 §2。

## Verification

- A1–A4：`backend/tests/services/test_test_case_result_ingest.py`
- B1–B6：`backend/tests/scheduler/test_recycler.py`（含
  `test_review3701_enqueue_failure_recovers` 的 false/raise 两变体；B5 未执行不耗额度；
  B6 成功清零；执行侧 `run_post_completion_async` 记数）
- B3：既有 `#3341` 截止用例保持通过
- phase0：调用前清理进程内截止/失败计数（TRUNCATE RESTART IDENTITY 复用 job id）
- 变异：去掉合并 → A1/A2/A4 红；去掉上限 → B1/B2 红；把计数挪回入队时 → B4/B5 红
- `scripts/run_gates.py check:quick`；`scripts/run_pytest.py tests/ -q`；required CI

## Revisit

- 若回收器改为多进程且需要全局一致上限，或需要 UI 展示「报告未生成及原因」，
  升级为 job 级持久化处理状态（#3700 规划方案 §2）。
- 若需要按执行次数独立落库，再议 attempt 维度与唯一键变更。
- 不可读截止行仍可能占 LIMIT 名额（规划方案 §8），另开单跟踪。
- 外部 SAQ worker（`STP_ENABLE_INPROCESS_SAQ=0`）下执行侧计数不在 recycler 进程，
  上限不生效（v1.1 §3 / §6）。
