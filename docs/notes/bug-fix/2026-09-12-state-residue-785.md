# 状态残留 / 重试无收敛三处（#785）

Status: implemented
Class: bug-fix

## Decision

1. **UNISOC `aee_ts` 错位**：`unisoc_reconciler._emit_event` 把
   `meta.event_subtype` 写入 `extra.aee_ts`。改为写入设备时间戳原文
   （`EventMetadata.device_timestamp_raw`，由 collector 从
   `unievent_info.json` 抽出）；`aee_ts_utc` 仍为 UTC 换算。对齐 MTK
   reconciler 口径。

2. **gpu_check 跨 run `dead_streak`**：已由 **#1028 / gpu_check v1.0.6**
   以 `STP_JOB_ID` fencing 收口；本单不重复改脚本版本。

3. **UPLOAD_FAILED attempt 不持久**：600s `_retry_failed_loop` 重入队时
   `attempt` 恒为 0，每轮再烧满 `_MAX_RETRIES`。修复：内存 +
   `agent_state`（`event_upload_attempts:{id}`）双写；耗尽后 skip
   UPLOAD_FAILED；UPLOADING 中断恢复仍按已记 attempt 续传；REMOTE 成功
   清零。

## Alternatives

- 新增控制面 DEAD_LETTER 状态：需扩状态机与 extract 口径；本单先 Agent
  侧收敛；否决为本单范围。
- 简单互换 processor emit/落盘（#803）：与本单无关。

## Verification

- `pytest backend/agent/tests/test_unisoc_reconciler.py -q`
- `pytest backend/agent/tests/test_event_uploader.py -q`
- `python3 scripts/run_gates.py check:quick`

## Revisit

若运维需要人工「复活」已耗尽事件，可加 Agent 管理命令清
`event_upload_attempts:*` 或控制面新状态；当前需重启并清 state 键。
