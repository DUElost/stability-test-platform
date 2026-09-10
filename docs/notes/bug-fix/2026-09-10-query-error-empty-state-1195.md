# R12-F09 落地：查询失败与空结果分离（#1195）

Status: implemented
Class: bug-fix

## Decision

两处调用点把服务异常表达为空业务事实：#359 同类修过其它页面，这两个
是剩余缺口：

- `DedupReportCard`：去重状态请求失败后 `artifacts.length === 0` 落
  「暂无去重产物，点击扫描」空态 CTA——失败被误示为「扫描没产出」，
  甚至引导用户重复扫描；
- `NotificationBell`：`logsQ.data?.items ?? []` 使加载中/失败都显示
  「暂无通知」。

修复：两组件按 **loading / error / 成功空结果** 三分支渲染；error 态
明确文案 + 重试按钮（`statusQ.refetch` / `logsQ.refetch`）。成功空结果
文案不变（真实「暂无」语义保留）。

## Alternatives

- **error 时只隐藏空态（不显示任何内容）**——放弃：静默空白与「加载中」
  难区分，且失去重试入口；参考 #955 页面横幅模式给出错误态 + 重试；
- **抽共享的「查询态渲染」helper**——放弃：两处结构不同（卡片内联行 vs
  下拉列表居中块），抽象收益低；模式注释互指。

## Verification

- **反例实证**：回退两组件保留测试 → 3 用例失败（失败路径显示空态、
  pending 显示「暂无通知」）；修复版全绿；
- 新增/扩展用例：
  - `DedupReportCard.test.tsx` +1：失败 → 显示「去重状态加载失败」+ 重试，
    不出现「暂无去重产物」；
  - `NotificationBell.test.tsx` 新建 3 例：pending → 「加载通知…」/ error →
    「通知加载失败」+ 重试 / 成功空 → 「暂无通知」（回归）；
- 两文件 **6 passed**；
- `check:quick`（含 eslint/tsc）与 PR 门禁：见 PR 描述。

## Revisit

- R12 批其它「错误态 vs 空态」调用点（#1194/#1196 等）在本单范围外；
  本单收 #1195 列出的两个点；
- NotificationBell 的 `useSocketIO` 在测试中整体 mock——通知到达实时刷新
  路径不在本单测试面（已有 socket 层测试覆盖）。
