# DLE 幂等重放/迟到补丁不得降级已上送行（#1174）

Status: implemented
Class: bug-fix

## Decision

`agent_api.py` `ingest_device_log_events` 的 `ev.id`（预分配 id）分支增加
**权威态保护**：已有行 `state ∈ {REMOTE, ARCHIVED, PRUNED}`（中心
remote_path/checksum 权威、extract 可识别，见
`services/device_log_event._REMOTE_STATES`）且 incoming `state ∉` 该集合时，
视为过期重放 **no-op 幂等成功**（记 `dle_stale_replay_ignored` warning，不
覆盖任何字段），而非无条件 `row.state=ev.state` + 清空 remote_path/checksum。

背景：#1042 引入的 register outbox 重放（挂 30s 恢复轮询尾部）携带旧 LOCAL
意图；若晚于 EventUploader 的 REMOTE 提升到达，会把行打回 LOCAL 并清路径/
校验和 → extract 不可见；#1083 落地后 REMOTE ack 可能已 prune 本地副本，
回退即**不可逆**。同属权威类的正向推进（REMOTE→PRUNED 等，`_patch_state`/
`patch_event_state` 通道）仍放行。

涉及：`backend/api/routes/agent_api.py`（`_EXTRACTABLE_STATES` + id 分支守卫）；
测试见
`test_agent_device_log_events.py::test_stale_local_register_replay_does_not_demote_remote`。

## Alternatives

- 「仅 LOCAL 行可覆盖」：会拦掉 uploader 对 REMOTE 行的合法 re-ack 与
  REMOTE→PRUNED 回写；权威类集合守卫更贴合状态机。
- 枚举序单调推进：UPLOAD_FAILED→UPLOADING 等重试方向与枚举序冲突，误伤。
- 客户端删 outbox 条目防重放：治标，服务端守卫是最终防线；守卫返回成功
  后客户端自然 ACK 清理。

## Verification

- `pytest backend/tests/api/test_agent_device_log_events.py`：6 passed（新增
  用例：LOCAL 建行 → REMOTE 提升 → LOCAL 重放 → 行保持 REMOTE 且
  remote_path/checksum 完好，响应幂等成功）。

## Revisit

agent_api 无 id 分支的 job+signal 查重路径只回读不覆盖，无此风险。若未来
引入其它 agent 直写非权威态→权威态的降级通道，需在同一不变量下复核。
