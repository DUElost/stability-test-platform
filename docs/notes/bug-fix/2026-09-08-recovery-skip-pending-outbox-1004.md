# 恢复时待补传终态优先于 RESUME（#1004）

Status: implemented
Class: bug-fix

## Decision

`execute_recovery_actions_impl` 调整为：

1. 收集「终态优先」job 集 = `outbox_actions` 中 `UPLOAD_TERMINAL` ∪ 本地
   `get_pending_outbox()`；
2. **先** `drain_sync` 补传终态并清理已 ack 的 active/fencing；
3. 再处理 job actions；若 `RESUME` 命中终态优先集则跳过（不
   `register_active_job` / `resume_job`）。

这样「终态已入队 + active 未清 + 同 boot 恢复」不会再跑脚本，也避免旋转
token 后原终态被拒。涉及 `backend/agent/main.py`；旧「RESUME+UPLOAD 并存保
留 active」用例改为互斥断言。

## Alternatives

- 仅后端不发 RESUME：控制面看不到本地 outbox 是否已写入终态事实，无法单独裁决。
- 保留 RESUME 但延后到 upload 成功之后：成功上传后本不应再执行；失败时
  RESUME 仍会重跑已完成脚本。

## Verification

- `test_resume_skipped_when_pending_terminal_outbox`
- `test_resume_skipped_for_local_pending_outbox_without_upload_action`
- `python -m pytest backend/agent/tests/test_recovery_executor.py -q`

## Revisit

若控制面也要在 `recovery_sync` 对「Agent 上报的 pending_outbox ∩ active_jobs」
直接抑制 RESUME，可作双端加固；当前以 Agent 本地事实为准已满足验收。
