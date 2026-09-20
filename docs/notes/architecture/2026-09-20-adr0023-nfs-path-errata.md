# ADR-0023：plan_snapshot `nfs_path` 幽灵缺口勘误

Status: proposed
Class: architecture

## Decision

ADR-0023 §D3 实施前提与「实施注意事项」仍写「`plan_snapshot` 缺少 / 也不写 `nfs_path`，须先改 `_fetch_script_metadata` + `_build_plan_snapshot`」。

仓内事实相反：`plan_dispatcher_sync._fetch_script_metadata` 已 select `Script.nfs_path`；
`plan_dispatcher_core.build_plan_snapshot` 已写入每 step 的 `nfs_path`。C1（D1 fail-fast）
亦已 ✅。本刀只纠这两处过时前提，改为「已落地、D3 可直接消费」。

不碰 ADR-0021 / ADR-0033（并行 #2856 幽灵键勘误面）。

## Alternatives

- 捆 A2（external-tools「Phase 2/3 零启动」dated 注）→ 否决：弱于代码矛盾；另开刀。
- 顺手改 ownership X1 概念名 / ADR-0039 同幽灵 → 否决：禁令/避让面；0039 仍 Proposed。
- 只改注意事项、留 §D3 前提 → 否决：两处同假，留一半仍误导实施者。

## Verification

- `rg -n '缺少.*nfs_path|也不写.*nfs_path' docs/adr/ADR-0023-script-traceability.md` → 0
- `python3 scripts/run_gates.py check:quick`

## Revisit

- D3/C5 前端消费 `plan_snapshot.steps[*].nfs_path` 时，本勘误即为前置真值。
- external-tools Living「Phase 2/3 零启动」dated 排期注（A2）可后续独立小刀。
