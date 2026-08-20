# 前端 UI 审查 B12：危险控件着色统一为常驻 destructive

Status: implemented
Class: simplification

依据：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 11 位（B12）。
侦察重定性：B12 不是「无区分」，是**同一语义两种时机**——4 处危险色
仅 hover 出现、5 处常驻。与 A3 状态胶囊分裂同构。

## Decision

- **常驻胜出**：新增 `INTERACTIVE.iconDanger: 'text-destructive
  hover:bg-destructive/10'`（常驻红字，hover 只加底色反馈）；
  `PIPELINE_EDITOR.iconBtnDanger` 去 `hover:` 前缀改常驻（保留其边框
  风味）。理由：密集表格里逐个 hover 试探恰是误点成因；常驻红是全站
  5 处既成惯例（WifiPage / PlanListPage / NotificationsPage×2 /
  ExpandableHostTable / HostBulkActionBar）。
- **4 处 hover 位点迁移**：PlanCanvas IconBtn（经 token 一行，唯一
  tone="danger" 消费者）、SchedulesPage:306、UserTable:88、
  SelectionPresets（ghost Button 加 iconDanger）。三页的
  `hover:text-destructive` 手写串全部删除，收敛进 token。

## 二次确认核实（B12-b 零代码结案）

侦察表标记「无确认」的三处，沿调用链核实**确认全部在上游页面级**：

| 位点 | 确认位置 |
|---|---|
| UserTable onDelete | UsersPage:87 `确定要删除此用户吗？此操作无法撤销` |
| ExpandableHostTable onDelete | HostsPage:311 `确定删除主机「name」(ip)？` |
| HostBulkActionBar 批量删 | HostsPage:278 `确定删除选中的 N 台主机？` |

组件内没有 ≠ 缺失——与 StatCardSkeleton 同一类「只查一层」陷阱，
本轮先决核实后零改动结案。审查 §B12 的「无二次确认」指控不成立。

## Alternatives

- **反向统一为 hover**（否决）：与既成惯例相反，且违背 B12 的初衷
  （降低误点成本）。
- **给三处「补」确认**（否决）：核实后确认已存在，补了会变成双重弹窗。

## Verification

- 门禁：tsc / eslint / 全量 vitest / build 全绿。
- DOM 实测（`/tmp/ui-shot-rig/verify-b12.js`）：/schedules 与 /users
  删除按钮**静息态**（无 hover）计算色 = `rgb(239,68,68)`（--destructive），
  同行非危险按钮保持 muted `rgb(100,116,139)`——只染了危险位，没染整行。
  （原计划以 /wifi 常驻删除为对照，生产无资源池按钮不存在，改为与
  destructive 计算值直接比对 + 两页互证。）

## Revisit

- `destructiveMenu`（菜单项）与 `iconDanger`（图标按钮）两个 token
  并存是有意分工（有无背景/边框的基底不同），词表一致
  （text-destructive + hover bg）；若第三种基底出现再评估合并。
