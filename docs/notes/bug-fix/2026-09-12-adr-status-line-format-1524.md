# Agent Note: ADR 头部状态行归一 + S12 盲区收口（#1524）

Status: implemented
Class: bug-fix
Issue: #1524

## Decision

四处改动，核心是把「两篇 ADR 事实上不受 S12 约束」这件事**变成可见**：

1. **ADR-0035**：`- **状态**：` → `- 状态：`（键位粗体是**执行契约文档**的形态，
   S13 的 `parse_contract_status_line` 正是按 `- **状态**` 取行；ADR 不适用）。
2. **ADR-0022**：把元数据表里的 `| Status | Accepted |` 行改为标准状态行
   `- 状态：Accepted`（该表是全库唯一的表格形态状态声明）。
3. **ADR-0035 版本记录块归一**：原为整段散文，v1.1/v1.2 写在**行中**；S12 的
   `parse_adr_record_tip` 按 #1058 口径只认**行首**版本 token，故其「末项」被读成
   `v1.0`。归一为一行一版（`**vX.Y（…）**；`）后末项正确读作 `v1.2`。
4. **S12 增棘轮**：新增纯函数 `check_adr_status_line_present`，把「头部状态行在场且
   可解析」提升为 S12 显式前提——缺行/不可解析即报，**不再静默跳过**。同步治理面
   覆盖图（S12 行描述 + 修订记录）。

**根因（实测，非推断）**：S12 取行用 `l.strip().startswith("- 状态")`，而
`- **状态**：` 与表格形态都取不到 → `status_line=None` → `header_status=None` →
`check_adr_surface_sync` 的「不在场不约束」使该 ADR 退出**全部**索引一致性校验，
且不留任何信号。即：这不是「格式不好看」，而是**这两篇 ADR 当时确实不受索引门禁
约束**（`grep -L "^- 状态：" docs/adr/ADR-*.md` 只命中这两篇）。

**盲区打开后立刻暴露第二个既有问题**：修好状态行后首次 `--check` 即报
`S12 ADR-0035: 头部状态行 v1.2 ≠ 版本记录块末项 v1.0`——被静默跳过期间，该 ADR
的版本记录块一直是非机器可读形态却无人知晓。这正是本 issue 主张「必须收口」的实证，
故第 3 项改动**不是顺手修的**：不收口就无法让门禁转绿。

## Alternatives

- **让门禁容忍键位粗体（issue 第 3 项「容忍两种键形」的一半）**：否决。键位粗体是
  契约文档的规范形态（S13 依赖它），ADR 的规范形态是 `- 状态：`；宽容等于为同类漂移
  重新开一条隐形通道——本 issue 的成因正是「形态差异 → 取行失败 → 静默无覆盖」。
- **保留 ADR-0022 表格 Status 行、另加一条标准状态行**：否决。同一事实两处声明，而
  表格那处不受任何校验 → 正是「静默漂移」的温床；故把声明**移动**到权威位而非复制。
- **把 ADR-0022 整张元数据表改写成现代键位形态（`- 日期：` / `- 决策者：` / `- 关联：`）**：
  本次不做。`Reviewers` / `Successors` 在现代键位里没有对应键，改写需先定键名集合，
  属另一件事（记 Revisit）。
- **只修状态行、不管版本记录块**：不可行。门禁会红，PR 无法合入（见上「盲区打开后…」）。

## Verification

- `python tools/dev/check_governance_surface.py --check` → **绿**（S1–S13、S5x 全绿）。
- **端到端红样例（接线自证，不只测纯函数）**：临时抽掉 ADR-0022 状态行后重跑
  `--check`，实测报 `[BLOCK] S12 ADR-0022: 缺头部状态行（…会静默退出全部索引一致性
  校验…）`；恢复后复跑转绿。
- `python tools/dev/check_governance_surface.py --self-test` → 14 条规则红/绿双向通过
  （新增 4 例：规范状态行不报 / 键位粗体取不到行→报 / 表格形态→报 / 在场但不可解析→报；
  另加 `parse_adr_status_line("- **状态**：Accepted") == (None, None)` 钉住根因）。
- `python scripts/run_gates.py check:quick`（ruff / eslint / tsc / knip / compileall /
  gov-surface / ai-work）。
- 派生面一致性（改后新纳入校验，逐面核对）：ADR-0035 头部 v1.2 ↔ 版本记录末项 v1.2 ↔
  adr/README 主表 status=Accepted + 摘要 v1.2 ↔ DOC-MAP 末 token v1.2（M7 未列，跳过）；
  ADR-0022 头部 Accepted ↔ 主表 Accepted（无规范位版本，版本面跳过）。

## Revisit

- **ADR-0022 的遗留表格**（Date/Authors/Reviewers/Successors/Related）未归一为现代键位
  形态；需先定 `Reviewers` / `Successors` 的键名，与 #1523（ADR 编号冲突 + 索引计数）
  同批或另行立项。
- **issue 第 3 项的剩余部分**（「ADR 状态机门禁」：校验每篇 ADR 有合法状态且状态在允许
  集合内）未建。issue 原文写「**若排期**」，即该门禁尚未立项；S12 只管**索引一致性**。
  本单只收口「不可解析即静默跳过」这一盲区（新棘轮已覆盖「在场且可解析」谓词），
  不新建门禁——若排期，应在允许集合与 fallback 语义上单独裁决。
