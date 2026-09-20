# ADR-0039：幽灵 `plan_step.script_sha` 勘误

Status: proposed
Class: architecture

## Decision

Mode C（#2546 / 7f3504）登记的 `plan_step.script_sha` 幽灵列，#2856 已在 ADR-0033 D3 收口；ADR-0039 §1.3 / D4 仍写该假列。

1. **§1.3 pin 表述**：`plan_step.script_sha` → `Script.content_sha256`（ADR-0021 / ADR-0023）；明示 `PlanStep` 无该列。
2. **D4 历史溯源**：改为 `plan_step.(script_name, script_version)` + `plan_snapshot` + `step_trace` + `job_instance`。

不升 Accepted；不改 `AGENTS.md` / S11；不碰 #2904 / #2922 触及文件；不实现 #735 退役写操作。

## Alternatives

- 等 #735 写操作齐后再随 Accepted 同改 → 否决：幽灵列与已纠的 ADR-0033 并存，属独立事实勘误，不必绑 Accepted。
- 顺带改可行性研究 / reviews 历史文中的同名幽灵 → 否决：非权威面；超出本刀。
- 「无刀」→ 否决：Mode C B2 在权威 ADR 面仍开放。

## Verification

- `rg 'plan_step\.script_sha' docs/adr/ADR-0039-script-version-immutability-narrowing.md` → 仅否定句
- `python3 tools/dev/check_governance_surface.py --check`
- `python3 scripts/run_gates.py check:quick`

## Revisit

- ADR-0039 → Accepted + `AGENTS.md` / S11 硬不变量收窄：仍待 #735 退役写操作前置。
- 历史 notes/reviews 中的 `plan_step.script_sha` 转述：非阻断，不回写。
