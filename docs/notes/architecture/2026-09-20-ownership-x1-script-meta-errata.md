# Ownership X1：幽灵 `script_meta` 概念名勘误

Status: proposed
Class: architecture

## Decision

#2856 已纠 ADR-0021/0033 机制句里的幽灵键，但 Living ownership 索引仍把 `plan_snapshot.script_meta` 写成「副本」载体（§1 X1；inventory `script-meta-freeze`）。

1. **X1 选定口径**：改为 `plan_snapshot` 步骤身份（`(script_name, script_version)` 等）= 派发冻结**副本面**；明示快照**无** `script_meta` 键。
2. **`script-meta-freeze` 行**：概念列同步为步骤身份冻结面；**保留** inventory key 名（避免 S15 锚漂移）。
3. **§8 nits 行**：回填 #2856 已去假键名。

不升 ADR-0039；不改 DOC-MAP 日志链 Draft 标签；不碰 #2869/#2876 文件。

## Alternatives

- 顺带 `DOC-MAP` `**Draft**` → `**Living Draft**`（device-log 标签同步）→ 本刀不做：弱于 Mode C 假键残留，且易被读成状态升格。
- 重命名 inventory key `script-meta-freeze` → 否决：无门禁强制改名收益，徒增锚漂移面。
- 改 ADR-0039 同类幽灵 → 禁令 / 文件避让（Accepted 前置未齐）。

## Verification

- `rg 'plan_snapshot\.script_meta' docs/design/2026-semantic-ownership.md` → 仅否定/勘误语境
- `python3 tools/dev/check_governance_surface.py --check`
- `python3 scripts/run_gates.py check:quick`

## Revisit

- ADR-0039 `plan_step.script_sha` 幽灵：待 #735 与 Accepted 前置齐
- DOC-MAP device-log 行 `**Draft**` vs 文件头 `Living Draft`：可选标签同步（非本刀）
- #2869 / #2876 合入后再扫同主题残留
