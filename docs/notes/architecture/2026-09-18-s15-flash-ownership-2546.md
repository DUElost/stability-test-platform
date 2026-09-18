# S15 门禁 + ADR-0033 flash 补登记（#2546 follow-up）

Status: proposed
Class: architecture

## Decision

在 #2751 归属口径已钉的前提下落地两项合入后执行项：

1. **S15**：`tools/dev/check_governance_surface.py` 增加表内判据①②③（key 唯一；`path :: 定位` 命中恰 1；已写 `归属域` 则 key 须在表内）。附 `--self-test` 红绿样例。ownership 文件缺失则跳过。
2. **ADR-0033 v1.3**：D1 Tier3 典型工具增列刷机；「刷机补登记」句；头部 `归属域：semantic-ownership flash-tool`；同步 README / DOC-MAP / ownership `flash-tool` 锚。

不做 Phase 2 DedupMergeEngine、不做包存储。叠在 #2751 分支上开 draft PR（可叠合入）。

## Alternatives

- 等 #2751 合入后再开 S15 → 拉长空窗；改为文件缺失跳过以支持并行。
- 只改 ownership 表不 bump 0033 → 无法填 F-5 分类学空洞。
- 把 S15 做成散文扫描 → 评审已否决（X2 假阳性）。

## Verification

- `python3 tools/dev/check_governance_surface.py --self-test`
- `python3 tools/dev/check_governance_surface.py --check`（S1–S15）
- `rg -c '刷机\|flash' docs/adr/ADR-0033-tool-kit-ecosystem-integration.md` > 0

## Revisit

- #2751 合入后本 PR 变基 main；若 base 仍为 ownership 分支则 GitHub 自动可合并栈。
- `LINK_TREES += docs/adr` 仍另开。
- TBD ownership 行触碰补锚。
