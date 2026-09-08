# Suite 门禁/在途守卫按 Run 冻结套件身份（#965）

Status: implemented
Class: bug-fix

## Decision

`collect_suite_gate_error` 与 `active_run_ids_bound_to_suite` 的套件**身份**
改为优先 `run_context.dispatch_suite.suite_id`，无冻结块时回落
`plan.suite_id`。内容指纹 / 磁盘校验仍针对该身份的**活表**套件行（不推翻
「重导即可过门禁」）。在途守卫用 JSONB `contains` 匹配冻结块，避免 prepare
后解绑/改绑逃逸导出保护。

涉及：`backend/services/suite_binding.py`；回归于
`backend/tests/services/test_suite_binding_gate.py`。

## Alternatives

- 仅改门禁、在途仍 join Plan：解绑后导出仍可能覆盖在途 A 的工具目录。
- 冻结内容指纹参与放行：与现行「活表三方一致」设计冲突，且会强制重
  prepare 才能吃到重导。

## Verification

- `test_gate_uses_frozen_suite_after_plan_unbind`
- `test_gate_uses_frozen_suite_after_plan_rebind`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_suite_binding_gate.py -q`

## Revisit

若 JSONB `contains` 在极大 ACTIVE 集合上成为热点，可考虑为
`run_context->dispatch_suite->>suite_id` 加表达式索引。
