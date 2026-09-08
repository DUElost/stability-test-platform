# R07-F07 落地：recovery sync 失败保留重试标记（#1009）

Status: implemented
Class: bug-fix

## Decision

`run_recovery_sync_if_needed` 吞掉所有失败（`sync_recovery` 网络失败返回
None、异常仅 log）；heartbeat 重连回调只看「是否抛异常」——无异常即
`clear()` 待恢复 serials。拔插（offline→online）时刻同步失败后：pending
被清、设备常在线不再产生新触发 → **恢复可能永久跳过**。

修复（三处，返回值即重试语义）：

1. **`run_recovery_sync_if_needed` → bool**：恢复完成/无事可做 = True；
   瞬时失败（`sync_recovery` 返回 None / 异常 / `execute_actions` 抛）=
   False——调用方据此保留重试状态；
2. **`trigger_recovery_sync_on_device_reconnect` → 透传 run 结果**：无
   jobs / 无 serial 匹配改为 True（**确定无需恢复**，避免 stale pending
   在每心跳空转）；匹配且 run 成功 True；匹配但失败 False；
3. **`heartbeat_thread`**：回调返回 False → 保留 `_pending_reconnected_
   serials` + warning；True/None → 清除。保留后下个心跳 tick 自动重试
   （346 行条件每 tick 检查 pending）——无需再次拔插。

行为语义：恢复是「尽力而为 + 后端权威」协议，失败重试的幂等由后端
recovery 决策保证（重复 RESUME 由 worker token 去重、UPLOAD_TERMINAL
幂等）。

## Alternatives

- **失败后增加独立退避定时器（指数退避重试线程）**——放弃：心跳 tick
  本身是现成节流通道（poll_interval 量级），额外定时器引入生命周期与
  竞态面；验收要求「后续心跳周期可重试」即心跳节流；
- **trigger 用三态（True/None/False）区分「没跑」与「跑了失败」**——
  放弃：None 语义与既有调用方（忽略返回值）混淆，且无 jobs/无匹配
  「清 pending」用 True 表达更直白；两态足够；
- **heartbeat 改为无条件每 tick 调回调查恢复**——放弃：每心跳一次
  recovery HTTP + 本地 active 扫描，量级与噪音不可接受；pending 门控
  保留。

## Verification

- **反例实证**：回退 main/heartbeat 实现保留测试 → 3 关键用例失败
  （sync None 不报 False / 失败不传播 / pending 被清）；修复版全绿；
- 新增用例（`test_recovery_executor.py` +5）：run 返回 None→False、
  trigger 失败传播 False / 成功 True、heartbeat 失败保留 + 下一 tick
  自动重试 / 成功清除；
- 既有用例语义同步：无 jobs / 无 match → True（确定无需恢复），mock
  `run_recovery_sync_if_needed` 补显式 return_value；
- recovery + heartbeat 相关 **29 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- main() startup 时的一次性 sync（boot 路径）失败后仍无独立重试通道——
  该场景依赖设备事件或重启；若未来要覆盖「启动失败 + 设备常在线」静默
  场景，可在 heartbeat 增加 `_recovery_retry_needed` 标志（同 pending
  门控模式）；
- 无匹配 serial 的清 pending 语义依赖「recovery 只关心 active job 的
  设备」——若未来引入非设备维度恢复，需重审该分支。
