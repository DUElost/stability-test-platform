# 通知页三个页签的失败态真值化（#3199）

Status: implemented
Class: bug-fix

日期：2026-09-26 ｜ 归属：前端 / 告警面 ｜ 类型：bug-fix

## Decision

`NotificationsPage` 的三个页签（通知记录 / 通知渠道 / 告警规则）此前**整文件没有一处读
`isError`**，失败被折叠成"成功且为空"：记录页签显示 `暂无通知记录` 并把计数渲染成
`共 0 条通知`，渠道/规则页签显示 `暂无通知渠道 / 暂无告警规则`。

采纳的做法：

1. 三处查询各解构 `isError` 与 `refetch`，在 `isLoading` 与"空结果"之间插入**错误分支**，
   沿用仓内既有形状 `InlineError`（`components/ui/error-state.tsx:47-65`，传 `onRetry`
   才渲染「重试」按钮）——与 `DedupReportCard.tsx:469-478` 同形；
2. 计数行在 error 时渲染 `通知条数未知（加载失败）` 而**不是** `共 0 条通知`。
   理由：`total = logsQ.data?.total ?? 0` 会把故障变成一个**肯定性断言**（数字 0），
   对告警平台等于把"看不到告警"说成"没有告警"；`—`/「未知」不是零；
3. 文案显式带"暂无法判断…"，把不确定性交还给用户，而不是留一个看起来正常的界面。

判据不是新造的：#1195（R12-F09）已裁决「**查询失败不得展示空态——那是成功空结果的语义**」，
并已在 `DedupReportCard` 与 `NotificationBell.tsx:137` 落地。本单是同一判据在**页面侧**的补齐。

## Alternatives

- **只加 toast 不加内联态**：否。toast 会消失，留下一个"看起来没有告警"的页面仍然在撒谎。
- **把空态文案改成"暂无或加载失败"这种含糊措辞**：否。合起来说等于两种事实都说，
  用户无法据此决定要不要重试；失败必须是可识别、可行动的。
- **顺手给 `hasLogs`（`:81`）加 `isSuccess` 守卫**（我在 #3199 正文里曾把它列为第 4 步）：
  **已撤回**。复核后发现失败态原本是 `data=undefined → 0 → hasLogs=false`，加守卫后仍是
  `false`——**行为完全不变**，属把"表述更清楚"当成"修复"。按 AGENTS.md「只改当前 Requirement
  必需内容」不保留 no-op diff；落地页签在故障时回落 `channels` 是可接受的中性默认，且该页签
  现在也会显式报失败。**这是对本人上一轮方案的一次自我纠正。**

## Verification

- `vitest run src/pages/notifications/NotificationsPage.test.tsx` → **41 passed**
  （36 既有 + 5 新增，**无回归**）。新增用例覆盖：记录页签失败（含"不出现暂无/不出现共 0 条"
  的反断言 + 「未知」文案）、**点重试确实恢复列表**、渠道失败、规则失败、
  以及一条**反向对照**（成功且为空仍显示空态，防止把 error 分支写宽）。
- `tsc --noEmit`（含 `tsconfig.node.json`）退出码 0；`eslint` 两个改动文件退出码 0。
- `run_gates.py check:quick` 17 个门禁全过。
- 测试编写中踩到并修正的一处：`setData()` 内部会同时把 `listChannels/listRules` mock 成成功，
  失败注入必须放在它**之后**，否则被覆盖（第一版就是这么红的——红在我的测试，不在产品代码）。
- **未做**：真实浏览器复验。dev 隔离栈 `stp-dev` 落后 main 数百提交，且第二套同码栈 `stp-dev2`
  已按承诺回收；本单改的是 DOM 语义态，jsdom 可断言（AGENTS.md 的 jsdom 边界针对几何/命中/
  autofill，不覆盖此处），故不阻塞。

## Revisit

- 同族仍有两处未落地：**#3226**（会话探活失败被路由守卫当成未登录，正在另一张 PR 里做）与
  **#3090**（主机页脚本在位汇总失败被折叠成 `null`）。若后续再出现"把 `?? []`/`?? 0` 当兜底"的
  新页面，考虑加一条静态守卫：禁止 `useQuery` 结果在未读 `isError` 的情况下被 `?? []`/`?? 0`
  直接喂给 `EmptyState`——本仓已有同类 AST 登记制先例（`tests/test_admin_only_read_surface_register.py`、
  #2536 棘轮）。
- 若将来引入 react-query 全局错误边界或统一"加载失败"呈现组件，本文件里三处 `InlineError`
  应收敛到那一层，避免第四份形状出现。