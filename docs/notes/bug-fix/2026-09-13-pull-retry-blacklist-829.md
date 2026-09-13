# pull_retry_limit 跨轮复活（#829 / R10-F04）

Status: implemented
Class: bug-fix

## Decision

`process_device_logs` 在单 tick 内对 pending 行递增 `retry_count`，达到
`pull_retry_limit` 时仅从 `pending_pull` pop，**未**写入 `processed_entries`。
下一轮 db_history tick（尤其伴随新崩溃行、hash 变化触发 burst）会把同一行以
`retry_count=0` 重新入队，再次吃满 `pull_retry_limit × pull_timeout_seconds` 重试墙。

**修复**：retry 耗尽分支在 pop pending 后把该行并入 `processed_lines` 并持久化，
与成功 pull 共用同一「不再处理」集合；`on_pull_failed(exhausted=True)` 语义不变。

## Alternatives

- **独立 blacklist key**：语义更清晰但多一套 state 迁移/对账；并入 processed 最小改动且与
  「该行不再拉取」目标一致；
- **指数退避 + 持久化计数**：更重，#829 只需阻断跨轮复活；
- **成功才写 processed、失败永入 pending**：即现状，正是本 bug 根因。

## Verification

- `python -m pytest backend/agent/tests/test_aee_processor.py::test_process_logs_pull_retry_exhausted_marks_processed_no_cross_tick_revival -q`
- `python -m pytest backend/agent/tests/test_aee_processor.py -q`（全文件回归）
- 既有 `test_process_logs_on_pull_failed_called_once_keeps_pending` 仍验证非 exhausted 路径

## Revisit

- 若需「设备侧目录恢复后可手动重拉」，应走运维/新 Job 清 state 或独立 requeue API，不在本单范围。
