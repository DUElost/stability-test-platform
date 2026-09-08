# recovery_sync 尊重 abort_requested，禁止 RESUME（#990）

Status: implemented
Class: bug-fix

## Decision

在 `recovery_sync` 通过 ownership / boot 校验之后、UNKNOWN→RESUME 与
RUNNING same-boot takeover / same-instance NOOP 之前，读取
`PlanRun.run_context.abort_requested`。若存在取消意图：

- 返回 `ABORT_LOCAL`（`reason=abort_requested`），由 Agent 走
  `request_abort` 停止确认路径；
- **不** `release_lease`（ACK 前租约保留，与 abort reaper 口径一致）；
- 若 `agent_instance_id` 变化则仍旋转 fencing token，避免旧 worker 继续持有。

涉及：`backend/api/routes/agent_api.py`；回归于
`backend/tests/api/test_agent_dual_write.py`。

## Alternatives

- 新增专用 action（如 `CONFIRM_ABORT`）：协议面更大，Agent executor 需同步
  扩展；现有 `ABORT_LOCAL` 已调用 `request_abort`，等价覆盖。
- 仅拦 RESUME、对 same-instance 仍 NOOP：Agent 漏收 SocketIO abort 时会继续
  跑，故一并拦截。

## Verification

- `test_recovery_sync_abort_requested_blocks_same_boot_resume`
- `test_recovery_sync_abort_requested_blocks_unknown_resume`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/api/test_agent_dual_write.py -k 'recovery_sync_abort_requested' -q`

## Revisit

若 Agent 侧 `ABORT_LOCAL` 清 fencing 后无法可靠 `/complete` ABORTED，应改为
保留 token 的停止确认语义（新 action 或调整 executor 顺序），并与 abort
reaper ACK 超时路径联调。
