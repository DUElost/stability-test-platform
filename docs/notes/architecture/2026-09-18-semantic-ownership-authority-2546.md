# 语义归属索引草案落地（#2546）

Status: proposed
Class: architecture

## Decision

按 issue #2546 方案 A + 多稿 Accept-with-nits，落地 Ownership Authority **only**；并按 2026-09-18 用户三点修正修订：

1. **B/C 深嵌为主**：日志链与 Jira 核心能力进入平台代码边界；厂商/客户易变面仍用 Adapter 隔离；禁止不可维护的全量 `scripts/` 复制。A/D 仍为编排(+提权)深嵌 + 原厂/大包外置。
2. **索引覆盖现行 Accepted ADR**：§5.1 域覆盖矩阵 + 实填关键行 + TBD 占位与触碰增补纪律（非仅 0033/0020；亦非全量名词 Inventory）。
3. **评审采纳对照**：设计文 §6 列出已采纳 / 未采纳及理由。

文件：

- `docs/design/2026-semantic-ownership.md`（权威草案）
- `docs/DOC-MAP.md` / `docs/adr/README.md` 模板 `归属域`
- `ADR-0021` `script_meta` 措辞降级（nits）
- Project 副本与 `tool-ownership-and-structure.md` B/C 勘误

本 PR **不**实现 S15 代码；**不**改 ADR-0033 D1 正文 flash 行。

## Alternatives

- 方案 B 全量 Inventory → 不采纳（评审一致）。
- 方案 C 仅逐对 → 保留运行期路径，不替代 A。
- 「B/C 引擎一律外置」→ **用户否决**；改为深嵌 + 适配边界。
- 本 PR 同步 S15 / `LINK_TREES+=docs/adr` → 延后，避免表未稳先锁门禁。

## Verification

- 设计文无「以本文为准」；§3 B/C 为深嵌主路径；§5.1 覆盖多域 Accepted ADR。
- §6 采纳表与未采纳理由齐全。
- `python3 tools/dev/check_governance_surface.py`。
- CI on PR #2751。

## Revisit

- 合入后：S15 实现；0033 D1 写 flash；TBD 行触碰补锚；台账 X3 措辞收窄。
- `LINK_TREES += docs/adr` 另开治理单。
- Y1/Y2 关单时填 `audit-log` / specialty 相关行。
