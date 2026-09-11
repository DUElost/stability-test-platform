# AI 助手 dispatch_plan_run plan_id 归一包 ToolValidationError（#758）

Status: implemented
Class: bug-fix

## Decision

`normalize_dispatch_params` 对 `plan_id` 对齐 `wifi_pool_id` / `_parse_run_id`：`int(...)` 包 `try/except (TypeError, ValueError)` 并转 `ToolValidationError`，避免裸 `ValueError` 击穿 orchestrator（只捕 `ToolValidationError`）导致 SAQ turn（`retries=0`）静默失败。

## Alternatives

- **仅在 orchestrator 扩捕 `ValueError`**：过宽，会吞掉真正编程错误；否决。
- **本处与 wifi_pool_id 同构 try/except**（采纳）。

## Verification

- `python -m pytest backend/tests/services/test_ai_dispatch.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

`int(1.5)` 仍截断为 `1`（与现有 `wifi_pool_id` / `_parse_run_id` 一致）；若需拒绝浮点，应抽共享严格解析器一并改。
