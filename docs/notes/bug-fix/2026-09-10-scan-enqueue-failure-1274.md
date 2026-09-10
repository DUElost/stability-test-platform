# 手动触发 scan 入队失败不再假成功（#1274）

Status: implemented
Class: bug-fix

## Decision

`POST /api/v1/plan-runs/{run_id}/dedup/scan`（`backend/api/routes/dedup.py::trigger_scan`）
在 #1151（fix #1077）后改调 `enqueue_dedup_terminal_async`，而该 helper
（`backend/services/dedup_scan.py`）`except Exception` 吞掉全部异常、无返回值——
路由随后无条件 `ok({"enqueued": "scan_task"})`。SAQ 未运行 / Redis 故障时 UI 收到
200「已入队」但没有任何 scan 轮次在跑（旧实现走 `emit_agent_control`，失败会上抛）。

修复：

- `enqueue_dedup_terminal_async` 改为返回 `bool`：`True` = 本轮 `scan_task` 已在
  队列（新入队，或 SAQ 键去重返回 `None` = 同轮任务已在跑，属**幂等成功**）；
  `False` = 入队失败。后台最佳努力调用方（job_terminalization / cron / abort）
  行为不变（继续保持吞错、忽略返回值）。
- 路由据此判定：`False` → `HTTPException(503, "scan enqueue failed ...")`。

## Alternatives

- **helper 增加 `required=True` 参数、失败时上抛**——放弃：调用方就要写 try/except
  映射状态码，返回 bool 让「失败」成为显式数据流，也便于后续统一收敛。
- **把键去重（`enqueue` 返回 `None`）也算失败**——放弃：键去重表示同轮任务已在
  队列，正是期望状态；误判会把正常重试变成 503。
- **同步变体 `enqueue_dedup_terminal_sync` 一并改**——放弃：其调用方
  （abort / cron 终态触发）是后台最佳努力链路，无人消费失败信号；契约不变可避免
  无谓改动，留待需要时按同一模式收敛。

## Verification

- `venv/bin/python -m pytest backend/tests/api/test_dedup_scan_endpoints.py -q` →
  **23 passed**，含新增：
  - `test_scan_enqueue_failure_returns_503_not_false_success`（路由层 503）
  - `test_enqueue_dedup_terminal_async_returns_true_when_enqueued`
  - `test_enqueue_dedup_terminal_async_deduped_key_is_success`（`None` → True）
  - 既有 `..._swallows_errors` 增断言 `is False`
- 反事实：把 `dedup.py` / `dedup_scan.py` 还原至 origin/main 后，路由用例稳定
  `assert 200 == 503` 失败、helper 用例 `assert None is False/True` 失败；恢复后全绿。
- `python scripts/run_gates.py check:quick` → **[OK]（7 gates）**。

## Revisit

若前端把 503 展示为「重试」入口，可考虑在响应体区分
`saq_unavailable` / `deduped` 两种情形；当前仅按 #1274 收敛「不得假成功」。
