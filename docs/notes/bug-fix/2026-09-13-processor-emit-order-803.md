# processor emit/落盘顺序（#803 第 1 处）

Status: implemented
Class: bug-fix

## Decision

`_finalize_processed_entry` 原先顺序为 `on_new_entry`（emit log_signal + 注册 DLE）
→ 写 `processed_entries` / `pending_pull`。回调成功、状态未落盘即崩溃时，重启
会重拉同一 db_history 行并重 emit，产生重复 log_signal/DLE。

**修复（折衷）**：先持久化 processed/pending，再调用 `on_new_entry`。崩溃窗口
从「重复 emit」变为「已 processed 但 emit 未发生」的丢失风险；回调失败路径
本就吞异常且仍标 processed，行为与调换前一致。

第 2 处（prune MAX(seq_no) 守卫）已于 #1474 合入；本单关闭 #803 余留缺口。

## Alternatives

- **同一 SQLite 事务收敛 outbox + processed**：正确但需跨回调链传事务，改动面大；
- **emit 补偿通道（last_emit_attempt + 重放）**：后续若丢失不可接受再单开；
- **维持 emit 在前**：保留重复 emit 窗口，否决。

## Verification

- `python -m pytest backend/agent/tests/test_aee_processor.py -q`
- 新增 `test_process_device_logs_persists_processed_before_on_new_entry`
- 既有 `test_process_device_logs_on_new_entry_exception_swallowed` 回归

## Revisit

- 若运维要求「processed 后 emit 失败必须可补偿」，单开 emit replay 机制，不在本折衷范围。
