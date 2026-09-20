# ADR-0021：背景段幽灵 `plan_snapshot…content_sha256` 勘误

Status: proposed
Class: architecture

## Decision

#2856 已纠 ADR-0021 D1/D4 机制句里的 `plan_snapshot.script_meta`，但**背景**段仍写
`PlanRun.plan_snapshot`「已内嵌…`content_sha256`」。

仓内事实：`build_plan_snapshot`（`plan_dispatcher_core.py`）steps 键为
`script_name` / `script_version` / `nfs_path` / params…，**无** `content_sha256`；
verify 期望 sha 现取于 live `Script.content_sha256`
（`backend/services/precheck/scripts.py`）。本刀只改背景一句，对齐 D1/D4 与 #2856。

不碰 ownership（#2904）；不升 ADR-0039；不改 `AGENTS.md`。

## Alternatives

- 捆 ownership X1 概念名 → 否决：#2904 在窗，避让。
- 顺带改 ADR-0039 `plan_step.script_sha` → 否决：文件避让；Accepted 前置未齐。
- DOC-MAP `Draft`→`Living Draft` 标签同步 → 否决：弱于代码矛盾；易读成升格。
- 「无刀」跳过 → 否决：Accepted ADR 背景仍与 `build_plan_snapshot` 矛盾，#2856 过声明「机制已齐」。

## Verification

- `rg -n 'nfs_path` / `content_sha256' docs/adr/ADR-0021-script-content-alignment-gate.md` → 0（旧「内嵌列表」已拆）
- `rg -n 'content_sha256' docs/adr/ADR-0021-script-content-alignment-gate.md` → 仅 DB/verify/「**无**」否定句
- `python3 scripts/run_gates.py check:quick`

## Revisit

- ADR-0039 同类 `plan_step.script_sha` 幽灵：待 #735 / Accepted 前置齐后再动
- ADR-0029「快照建议补 content_sha256 冻结」为前瞻建议，非现态伪称——保持不动
