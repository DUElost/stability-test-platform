# ADR-0021 / 0033：幽灵 `script_meta` / `script_sha` 勘误

Status: proposed
Class: architecture

## Decision

Mode C（#2546 / e82515 F-06、7f3504）残留仍成立：#2546 落地只把 ADR-0021 **关联区** `:297` 的「作为权威」降级，**未**改 D1/D4 机制句里的幽灵键。

1. **ADR-0021 D1 verify / D4**：对账载体改为 plan_snapshot 步骤身份 ∩ live `Script.content_sha256`（与 `backend/services/precheck/scripts.py` 一致）；明示快照无 `script_meta`。
2. **ADR-0021 关联区**：同步去掉 `plan_snapshot.script_meta` 假键名，保留「非独立权威源」。
3. **ADR-0033 D3**：`plan_step.script_sha` → `Script.content_sha256`（`PlanStep` 无该列）。

不升 ADR-0039 Accepted；不改 `AGENTS.md` / S11 锚；不碰 #2853 已合入面。

## Alternatives

- 只改 ownership 表 X1 行、不动 ADR 决策句 → 否决：幽灵键写在 Accepted ADR 决策机制里，表行无法单独纠代码矛盾。
- 顺带改 ADR-0039 同类幽灵 / ADR-0023 nfs_path 过时句 → 本刀不做：0039 与 #2853 同主题且 Accepted 前置未齐；0023 超出本轮 Mode C 残留主线。
- 「无刀」跳过 → 否决：相对 #2842/#2853「script_meta 已纠」的过声明，D1/D4 仍与代码矛盾。

## Verification

- `rg 'plan_snapshot\.script_meta' docs/adr/ADR-0021-script-content-alignment-gate.md` → 仅出现在「非…」否定句
- `rg 'plan_step\.script_sha' docs/adr/ADR-0033-tool-kit-ecosystem-integration.md` → 仅否定句
- `python3 tools/dev/check_governance_surface.py --check`
- `python3 scripts/run_gates.py check:quick`

## Revisit

- ownership X1 / `script-meta-freeze` 行仍用 `script_meta` 作概念名：可在后续表行勘误（非阻断）
- ADR-0039 同类 `script_sha` / `script_meta` 幽灵：待 #735 与 Accepted 前置齐后再动
- ADR-0023「snapshot 缺 nfs_path」过时前提：独立小刀
