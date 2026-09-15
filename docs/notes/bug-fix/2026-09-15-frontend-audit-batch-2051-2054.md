# 前端与通知链接审计批（#2051–#2054）：退役路径、缓存键、就绪门、协议相对 URL

Status: implemented
Class: bug-fix

本批四项同源（2026-09-13 窗口审计），均为「门禁/路径宣称存在但实际不可达或可绕过」。

## Decision

**#2051 全退役后开关消失 → ADR-0038 回收路径断头**
`HostsPage.tsx` 的空态分支（`tableData.length === 0`）早返回在「显示已退役」复选框之前，
而 `showRetired` 是 `include_retired=true` 的唯一来源 → 最后一台在用主机被退役后页面只剩
空态，开关不再渲染，UI 上再没有路径勾选它看到退役主机、进而解除退役。
**改**：把复选框抽成 `retiredToggle` 变量，空态分支同样渲染（含按开关区分的文案：
未勾选时说「所有主机都已退役，勾选可查看并解除退役」，勾选后说「还没有主机」）。

**#2052 `projectKeys.list()` 被两种 queryFn 共用 → 归档项目串数据**
项目页用 `list()`（全状态），选择器/指派弹窗用 `listActive()`（仅 ACTIVE），**同一个
`['projects']` 键**。React Query 按 key 建条目：先访问设备/计划页会把 ACTIVE-only 数据写进
该键 → 项目页「已归档」tab 渲染 0 张卡；反向则把归档项目灌回选择器（正是 #709 要消除的）。
**改**：`queryKeys.ts` 新增 `projectKeys.active() = ['projects','active']`，三处
`listActive()` 消费点（ProjectFilterSelect / AssignProjectDialog / usePlanEditForm）改用它；
`['projects']` 前缀失效仍覆盖两者，故既有 `invalidateQueries` 无需改动。
> 注：`usePlanEditForm` 原用自己的 `['projects-for-plan-editor']` 键（无冲突），本批一并
> 归口到 `active()`，避免同数据集多键。

**#2053 就绪度的退役门在唯一调用点不可达**
`planExecuteReadiness` 有 `host?.retired_at → 节点已退役` 分支，但 `PlanExecutePage` 的 hosts
查询是 `fetchHostList(0, 200)`（`include_retired` 默认 false）→ 后端把退役主机过滤掉 →
`hostMap` 查不到 → 退役分支与「节点离线」分支都不命中，设备被判 `ready: true`。
**改**：改用 ADR-0038 已有的 `hostKeys.retiredList()` + `fetchHostList(0, 200, true)`
（与该键的既有语义一致：含退役主机；前缀失效覆盖它）。

**#2054 `context.link` 放行协议相对 URL → 站内通知可把管理员带出站**
前端 `notificationTarget.ts` 与后端 `_resolve_alert_link` 都用 `startsWith("/")` 判定站内
路径，`//evil.example/x`（以及 `/\evil.example/x`）同样满足；前端把它当 `<Link to>` 渲染，
React Router 的 history 兜底会 `window.location.assign`，中键/Ctrl 点击直接走 href。
**改**：两端一起收紧为「单个前导斜杠」：`^/(?![/\\])`（Python 侧新增 `_INTERNAL_LINK_RE`
并补 `import re`——该模块此前没有 `re`）。

## Alternatives

- **#2052 只改 key 名不改消费点**：选择器仍读 `['projects']` 的全状态数据——不解决串数据。
- **#2052 让项目页改用 `active()`**：项目页按设计要展示归档项目（含「已归档」tab），不能收窄。
- **#2053 在页面侧另建一个含退役的 hosts 查询键**：与 ADR-0038 既有的 `retiredList()`
  重复，且会让主机页与执行页拿到两份退役列表缓存——改用既有键。
- **#2054 只改前端**：后端仍会把 `//host` 写进 `context.link`，任何其他消费方
  （未来的客户端/导出）都会继承同一漏洞——两端同改。

## Verification

- 前端全量：`npx vitest run` → **106 files / 810 tests passed**
- 受影响文件：HostsPage(21) / PlanExecutePage(55) / notificationTarget(6) /
  planExecuteReadiness(14) → **96 passed**
- 后端：`pytest backend/tests/services/test_notification_service.py backend/tests/api/test_notifications.py -q`
  → **61 passed**（含新增的 `_resolve_alert_link` 参数化 6 例）
- **反事实验证**（新增用例确实拦得住各自缺陷）：
  - #2051：还原 `HostsPage.tsx` → 新增的「列表为空时开关仍可见可点」**FAILED**（1 failed / 20 passed）
  - #2053：还原 `PlanExecutePage.tsx` → 新增的「就绪度以 include_retired=true 取主机」**FAILED**（1 failed / 54 passed）
  - #2054：正则语义逐例核验（`/hosts`、`/execution/plan-runs/3` 收；`//evil.example/x`、
    `/\evil.example/x`、`https://…`、`hosts` 拒）
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**

## Revisit

- **#2052 的同类面**：`queryKeys.ts` 里还有别的「同 key 不同 queryFn」风险点（如
  `hostKeys.list()` 被多个页面用不同参数调用）——本批只修已确认有数据污染的
  `projectKeys.list()`；若要机械化防复发，可在 queryKeys 注释里写明「一个 key 一个数据集」
  并考虑加一条源码级守卫，留给后续批次。
- **#2051 的空态文案**：勾选后仍为空时提示「还没有主机」，若将来出现「全退役」之外的
  空态来源（如租户过滤），文案需再分叉。
