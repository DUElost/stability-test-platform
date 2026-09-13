# #1829 REST retry-dispatch audit user_id

Status: implemented
Class: bug-fix
Issue: https://github.com/DUElost/stability-test-platform/issues/1829

## Decision

`retry_plan_run_dispatch_endpoint` 调用 `retry_plan_run_dispatch` 时未传入 `audit_user_id`，
导致 `plan_dispatch_retry_requested` 审计行的 `user_id` 恒为 NULL。与 abort 端点一致，在 REST
路由层传入 `current_user.id`。

## Alternatives

- 在 `retry_plan_run_dispatch` 内从 `triggered_by` 反查用户：多一次查询且与 abort 路由模式不一致。
- 仅补文档说明 user_id 可为空：不解决审计归因缺口。

## Verification

- `python -m pytest backend/tests/services/test_plan_run_dispatch_retry.py::test_retry_dispatch_records_audit_user_id -q`

## Revisit

无。AI assistant 路径已在 `plan_run_ops.py` 传入 `audit_user_id`，本修复仅补齐 REST 端点。
