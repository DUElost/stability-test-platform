# audit_logs.resource_id downgrade guard（#832）

Status: implemented
Class: bug-fix

## Decision

在已发布迁移 `g0b1c2d3e4f5` 的 **downgrade** 前置 `guard_integer_castable`：若 `audit_logs.resource_id` 存在非整型文本（如 host 派生 id `172-21-x-x`），先抛清晰 `RuntimeError`，避免裸 `::integer` 的 `invalid input syntax` 在降级链中段莫名中断。强制放行仍走 `STP_ALLOW_DESTRUCTIVE_DOWNGRADE=1`，并用 `integer_cast_using` 将不可解析行置 `NULL` 后再收窄类型。

## Alternatives

1. **只改 USING 为 CASE…NULL，不加守卫** — 静默丢关联，演练时无提示；否决。
2. **新建后续 migration 只修 downgrade** — 已应用 revision 的 downgrade 路径仍坏，演练仍踩坑；否决。
3. **原地改本 revision 的 downgrade + 共享 guard**（采纳）— upgrade 不变；与 `guard_nonempty` 同强制开关。

## Verification

- `python -m pytest backend/tests/test_migration_guard.py -q` → 11 passed
- `python scripts/run_gates.py check:quick`（pending re-run after Status/Class header）

## Revisit

若未来还有 varchar→integer 收窄，复用 `guard_integer_castable` / `integer_cast_using`；出界整数（超 32-bit）仍可能被 PG 拒绝，本守卫只覆盖「非数字文本」主故障面。
