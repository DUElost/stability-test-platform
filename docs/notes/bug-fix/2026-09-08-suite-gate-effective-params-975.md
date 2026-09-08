# 托管套件：有效 mtbf project 须与门禁 export_dir 一致（#975）

Status: implemented
Class: bug-fix

## Decision

绑定套件的 precheck 门禁在磁盘/归属校验之后，增加
`project_param_conflict`：从 `plan_snapshot` 合并 mtbf 步骤的
`default_params`+`params`，若已声明非空 `project` 且与
`resolve_export_dir(suite)` 不一致则 fail-fast。

注入路径保留「空键填入、计数键已有值优先」；对 `project` 在
`inject_suite_params` 再做一道冲突拒绝，防止绕过门禁的直调物化消费
错误目录。不取消与 export_dir 一致的显式覆盖。

涉及：`backend/services/suite_binding.py`、
`backend/services/plan_dispatcher_core.py`、
`backend/tests/services/test_suite_binding_gate.py`。

## Alternatives

- 注入时强制覆盖 `project`：取消冲突覆盖能力，与「校验而非取消」不符。
- 仅物化时报错、不进门禁：admission 对 `PlanDispatchError` 无结构化
  `suite_verify_failed` 路径，运维可见性差；放弃。

## Verification

- `test_project_param_conflict_with_export_dir`
- `test_matching_project_override_passes_gate`
- `test_inject_rejects_conflicting_project`
- `test_existing_user_value_wins`（计数键仍优先）
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_suite_binding_gate.py -q`

## Revisit

若步骤级 `params` 与 script `default_params` 之外还有第三层覆盖源，门禁
合并规则需与 `build_lifecycle_from_snapshot` 保持同步。
