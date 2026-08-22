# 部署后 triage 清单核实与修复（P0–P3 20 项）

Status: implemented
Class: bug-fix

来源：用户提交的 20 项部署后 triage 清单（P0×2 / P1×7 / P2×7 / P3×4），
逐项对码核实后处置。基线 = origin/main `64e6cc5`（#343 合入后）。

## Decision（代码修复 9 项）

- **P0-1** Dashboard 五处 `onRetry={() => void 0}` → 接各 query 的
  `refetch`（activity/trend/hostFailure/planSuccess/passRateTrend）。
- **P1-3** 「运行中」语义色全站统一 **warning 琥珀**：status-badge 的
  job / job-result / plan-run 三处 RUNNING info→warning；
  `colors.ts` `ENTITY_STATUS_COLORS.execution.running` primary→warning
  （#356 线已把矩阵瓦片/Hero/KPI 定为 warning，本次收尾残余）。
  同步更新 #317 时代的 info 断言（×2 用例）。
- **P1-4** 阻塞瓦片斜纹 `BLOCKED_STRIPE` warning→destructive：红底琥珀纹
  在暗色主题下易读成两种状态，纹与底同走 destructive。Minimap 的
  foreground 中性纹不涉语义冲突，不动。
- **P1-5** Schedules 错误页头「重试」Plus→RefreshCw（与「新建」不再同图标）。
- **P1-6** XTerminal 下载成功反馈：`toast.success('日志已下载：<name>')`。
- **P1-7** PlanRun 导出浮层 z：菜单 z-20→z-30、遮罩 z-10→z-20——
  不再与 ExecuteCommandBar sticky z-20 同层歧义；移动端侧栏 z-50
  仍在其上（导航遮内容属预期）。
- **P2-14** Audit 筛选 Radix `SelectItem value=""` → 哨兵 `'all'`
  （state 初值/发送逻辑同步），规避空串受控异常。
- **P2-15** watcher 停用确认加 `variant: 'destructive'`（与批量安装
  确认一致）；测试断言同步。
- **P3-19** types.ts 注释 WatcherSummaryCard（已删组件）→ PlanRun 详情顶栏。

## 核实后不成立 / 已过时（3 项）

- **P2-11**：`handleInstall` **已传** `variant:'destructive'`（清单所引
  行号与现状不符，或已被后续提交修掉）。
- **P2-12**：PageHeader 页内 fallback「死分支」**在测试中可达**——
  5 个测试无 HeaderSlotProvider 渲染页面、依赖该分支显示标题；删除
  导致 5 红后回滚。应用内确实恒走槽位，但该分支是测试 harness 的
  依赖面，保留（#367 那条清理项据此打回）。
- **P0-2**：PR #343 **已 MERGED**（本文写作时合入 `64e6cc5`），
  「合入前须 rebase」已过时。

## P3-20 迁移链：一次误诊与回滚（留档，比清单本身更重要）

清单说 #343 部署需 alembic upgrade；核实时本地树（落后一个 merge）
缺 `t6u7` 迁移文件 → `alembic current` 报「Can't locate revision」→
误诊为「生产库版本孤儿」，用 SQL 把 `alembic_version` 从 t6u7 改到
s6t7。随后 worktree 检出发现 origin/main 已前进（并行会话刚合入
#343，带回 t6u7 且就是 head）——**生产库原值本来就是对的**，误诊
根因是诊断用了落后的本地树。已 UPDATE 回 t6u7，`current = head`
在真树上成立；stamp 往返净效果为零，schema 全程未动。
**教训**：对生产库做任何写操作前，先 `git fetch` 并在**最新**树上
验证「代码 head vs DB version」；本机常有并行会话推进 main，
本地检出的新旧不代表远端真相。

## 延后（4 项，已立 issue）

P2-10（Notifications 手写 MODAL 迁 Radix Dialog）、P2-13（Dashboard
图表区结构余项）、P3-17（STATUS_BG_COLORS/STATUS_CHIP 双源 +
LiveConsole/LoginPage legacy）、P3-18（矩阵瓦片 32px 触达值）。

## Verification

- 门禁：tsc / eslint（12 改动文件 `--max-warnings 0`）/ 全量 vitest
  **601** / build 全绿。
- DOM 实测 4/4（`/tmp/ui-shot-rig/verify-triage.js`）：Dashboard 拦截
  stats API 前 2 次 500（retry:1）→ 5 个错误块各带重试；点重试后
  图表恢复；/results 注入 RUNNING → 徽章底色 `rgb(245,159,10)`
  （主题 --warning），非蓝非红。

## Revisit

- RUNNING=warning 统一后，若未来有人主张「运行中=蓝」的行业惯例
  （GitLab running 为蓝），重议时以 #356 的先例为准：本产品已把
  warning 留给「进行中需关注」，partial/degraded 同色族。
- Radix Select 哨兵模式（'all'）只迁了 audit 页；其余用原生 select
  的页面不受影响（B4 规矩未定，见 issue #371）。
