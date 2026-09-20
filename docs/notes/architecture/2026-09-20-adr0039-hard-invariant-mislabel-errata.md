# ADR-0039：D1 / 落地序「硬不变量」误标勘误

Status: proposed
Class: architecture

## Decision

#2940 在 Living ownership §8 登记：ADR-0039 D1 首句与 §4.3 落地序第 2 条仍写
「`AGENTS.md` **硬不变量**」，而该句实际位于 **总原则** `:15-16`（S11 第 12 锚）。
当时因在飞 #2929 同文件冲突而延后；#2929 已合入后本刀只做层级误标纠偏：

1. **D1**：`AGENTS.md` 硬不变量 → **总原则**（并括注不在 `## 硬不变量` / S11 第 12 锚）。
2. **§4.3 落地序第 2 条**：同口径改为修订 **总原则**该句。
3. **ownership §8**：② 改为「已由 #2929 收」；③ 标为已纠；**不**升 Accepted。

## Alternatives

- 等 ADR-0039 → Accepted 再改 → 否决：误标与契约收窄正交；#2853 已对 §1.1 同病先纠，D1/落地序不应因 Accepted 前置继续撒谎。
- 顺带升 Accepted / 改 `AGENTS.md` / 改门禁判据 → 否决：禁令；#735 退役写操作未齐。
- 只改 ownership「在飞 #2929」状态、不动 ADR → 否决：权威 ADR 决策句仍误标章节。

## Verification

- `rg -n '硬不变量' docs/adr/ADR-0039-script-version-immutability-narrowing.md` → 仅否定/括注语境（§1.1、D1 括注）。
- `python3 tools/dev/check_governance_surface.py --check`
- `python3 scripts/run_gates.py check:quick`

## Revisit

- ADR-0039 → Accepted：同 PR 收窄 `AGENTS.md` 总原则句 + S11 锚 + `check-script-version-immutability.py`（`script-version-immutability` 行触发器）。
- #735 退役写操作仍是 Accepted 前置。
