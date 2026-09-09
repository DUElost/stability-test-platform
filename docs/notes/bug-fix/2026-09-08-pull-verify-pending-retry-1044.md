# pull/校验失败落检测事实；hash 未变仍重试 pending

Status: implemented
Class: bug-fix

## Decision

运行期 AEE 采集在 adb pull / strict verify 失败时只写本地
`pending_pull` 与 `ProcessResult.errors`，不调 `on_new_entry` → 主路径无
log_signal / PULL_FAILED DLE。随后若各份 `db_history` 可读且内容未变，
Reconciler D2 直接跳过 `process_device_logs`，pending 永不重试；同时
inotifyd 在 reconciler 接管期间禁发 AEE，无法补异常计数。

修复（#1044）：

1. `process_device_logs` 增可选 `on_pull_failed`：首次 pull/verify 失败
   （及 retry 耗尽）回调一次（`failure_reported` 持久在 pending task），
   **不** mark processed。
2. Reconciler 接线 `_handle_pull_failed`：emit `extra.pull_failed=true` 的
   reconciler signal，并 `create_pull_failed_event`。
3. 记录 `_runtime_has_pending`；`changed is False` 且仍有 pending 时**不**
   跳过 process——把「失败补采」与「发现新历史」的 hash 优化分开。

未改 `device_watcher._should_emit_inotifyd`（reconciler 独占 AEE emit 的
边界保持）；根因在 reconciler 跳过 + 失败无事实。

涉及文件：

- `backend/agent/aee/processor.py`
- `backend/agent/aee/reconciler.py`
- `backend/agent/tests/test_aee_processor.py`
- `backend/agent/tests/test_aee_reconciler.py`

## Alternatives

- **失败时走 `on_new_entry`（无 output_subdir）**：会误走
  `_finalize_processed_entry` 语义或需大改 finalize；独立回调更清晰。
- **取消 hash 跳过优化**：性能回退；保留跳过、仅 pending 时旁路即可。
- **reconciler active 时重新允许 inotifyd 发 AEE**：双源重复计数风险；放弃。

## Verification

- `pytest backend/agent/tests/test_aee_processor.py backend/agent/tests/test_aee_reconciler.py`
- 重点：`test_process_logs_on_pull_failed_called_once_keeps_pending`、
  `test_hash_unchanged_still_processes_when_pending_remaining`
- `python scripts/run_gates.py check:quick`

## Revisit

若需「每次重试都记事实」或失败 DLE 在成功后升格为 LOCAL，再扩展
`failure_reported` / 状态机；当前契约是检测事实一次 + pending 补采。
