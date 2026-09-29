# 主机页脚本在位真值面（#3090）：汇总失败不再整条消失 + 项/台单位 + 全 n_a 空态

Status: implemented
Class: bug-fix

## Decision

按批次方案 #3497 §3 G1 行（形态级修复，参照实现 = DedupReportCard 的 `statusError` 分支
/#1195「查询失败不得展示空态」）实施，三处改动全部落在 #3090 判据面上：

1. **F1（失败 ≠ 无缺口）**：`HostsPage` 把 `scriptPresenceQ.isError` / `refetch` 以新 prop
   `scriptPresenceSummaryError` / `onRetryScriptPresenceSummary` 传入
   `ExpandableHostTable`。汇总查询失败时汇总条位置渲染「脚本在位汇总加载失败，暂无法判断
   缺口与账本陈旧」+ 重试按钮（`script-presence-summary-error`），不再把失败折叠成与
   「未提供汇总」同值 `null` 而整条消失；错误分支优先于数据分支（React Query 失败后
   `data` 仍保留上次成功值，若同时渲染旧汇总条会与失败提示并排出现真值矛盾）。
   逐台区块同判：汇总失败时原 `?.stale === true` 会把失败折叠成「不陈旧」，改为渲染
   「fleet 汇总加载失败，账本是否陈旧未知」（`host-script-presence-stale-unknown-*`），
   不再静默按 fresh 读。
2. **F4（单位）**：汇总条「未知 N 台」「维护窗 N 台」改「项」——`counts.*` 由
   `presence_counts_by_host` 按 `(host_id, state)` 逐行累加，是 host × name@version 的
   项数，只有 `hosts_total` / `hosts_with_gap` 是台数；与「缺口 N 台（N 项）」并排时
   不得把项当台对账。既有测试写死的错误单位一并改正。
3. **F4（空态因果）**：逐台空态区分两种——`counts.n_a > 0`（items 有意剔除 n_a，
   全 n_a = 已 sweep 且可达集与目标版本不相交）显示「本轮已 sweep，全部 N 项均
   『不适用』」，不再提示「重新核验」（点了也无条目）；仅 `n_a === 0` 的真空轮次保留
   「尚未跑过 sweep，可点『重新核验』」原文案。

本批禁止面均未触碰：无后端改动、无通用失败组件抽取（逐点按参照实现）、失败不阻断
任何操作（仅提示）、无样式/命名整理。

## Alternatives

- **失败时在页面级用 `ErrorState` 整页报错**：拒绝——主机列表本身可用，脚本在位只是
  页内一个区块，整页报错会放大故障面；参照实现（DedupReportCard）也是区块内行级提示。
- **「未知/维护窗」改展示 host 口径字段**（#3090 建议方向 2 的另一选项）：需要后端补
  host 去重计数字段，属后端改动，被 #3497 §0/§6 禁止；故取「改单位」侧。
- **重试入口走 `invalidateQueries`**（与页面既有 ErrorState 一致）：改用查询自身的
  `refetch()`，与参照实现 `void refetchStatus()` 同构，且不依赖 queryClient 注入。

## Verification

- `npx vitest run src/components/network/ExpandableHostTable.test.tsx
  src/pages/hosts/HostsPage.test.tsx`：59 passed（新增组件级反例 4 + 页面接线 1；
  组件级含「未 sweep 保留原文案」阴性对照）。
- 变异自证：四修复点分别改坏（错误分支 `false &&` 短路 ×2、单位写回「台」、n_a 判据
  `false &&` 短路）→ 恰好 4 个覆盖测试转红、阴性对照与其余 31 例保持绿；恢复后全绿。
- 通用门禁（#3497 §4）：`npm run lint -- --max-warnings 0` ✅ / `npm run type-check` ✅ /
  `npm run knip` ✅ / `python scripts/run_gates.py check:quick`（16 gates）✅ /
  `python -m pytest tests/ -q`（1933 passed, 18 skipped，cgroup MemoryMax=6G 硬顶）✅。

## Revisit

- #3497 §8 登记：`NotificationBell unreadQ` 等同族 F1 点位与本修复同构，方案已裁定
  只登记不修；若后续单点收口，可直接复用本 prop 形态。
- 汇总失败且 `data` 残留上次成功值时，「缺口/未知」明细不再可见（错误分支优先）——
  若运维反馈「失败时想看旧账本」，可改为失败提示 + 灰化旧值并排，属展示策略调整，
  当前按方案「不出现汇总条」执行。
