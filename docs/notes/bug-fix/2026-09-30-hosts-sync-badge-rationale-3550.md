# #3550 hosts 展开面板对齐判据解释（2026-09-30）

Status: implemented
Class: bug-fix

## Decision

09-30 部署 `cb15e823` 后现场实测：48/48 主机 `matched`，但展开面板三列修订分叉
（期望 `@cb15e823`、上报/热更新部署 `@6a2846a2`），只看前端的用户把它读成 Bug。
语义本身正确——ADR-0040 v1.1 判据是载荷摘要 digest，修订是溯源文本（期望修订取
发布根构建期 `VERSION`，每次部署必前进；上报/部署修订只在真正推送时更新，
digest 相等即 nothing-to-converge）。缺口在展示面：收起行徽章早有 digest 悬浮提示
（#2155 收口时加），**展开面板无任何解释**，两处不一致。

本单：`ExpandableHostTable.tsx` 展开面板「Agent 版本」卡内
1. 「对齐状态」行下补一行常显小字「对齐以「部署摘要」判定；修订文本仅作溯源，
   期望修订随每次合入前进」——判据说明放明处，不依赖悬停可发现性；
2. 徽章加**条件化** `title`：仅 `matched` 且期望修订 ≠ 上报修订时出现归因文案，
   正常形态不打扰（收起行既有提示不动）。

不改 Host schema / API 取数 / 三列取源语义——那是 ADR-0040 v1.1 的展示契约。

## Alternatives

- 只加悬浮长文：放弃。会疑惑「为什么不一致」的用户恰恰不知道该悬停哪里；
  且原生 title 的呈现行为 jsdom 测不了，长文案落悬浮等于不可测面变大。
- 把「期望修订」列换成期望 digest 对比：放弃。改的是 ADR 裁决过的列语义，
  且丢失 revision 溯源价值；「部署摘要」列已提供 digest 对拍的原料。
- 数据侧让期望修订只在载荷变化时前进：放弃。`VERSION` 是构建期全源 rev 记录，
  为展示便利改构建语义，方向错误。

## Verification

- `npx vitest run src/components/network/ExpandableHostTable.test.tsx`：37 passed
  （既有 35 + 新 2：分叉形态断言常显行与徽章 `title` 内容；一致形态断言无归因）。
  新用例按 testing.md §4 断言 DOM 属性（`getAttribute('title')`），不悬停；
  面板徽章用 `within('.bg-card')` 界定，防与收起行徽章重名混淆。
- `python scripts/run_gates.py check:quick`：结果见 PR。

## Revisit

- 若上线后仍有用户误读（告警单/issue 出现同类问法），下一步可把期望侧 digest
  短摘要也列进面板（两侧并排，判据可自证），本单不做是为了不引新术语。
- 收起行徽章 `title` 与面板归因文案是两处硬编码中文，若 #3487 同族的 i18n 化
  启动，应一并收进同一常量。
