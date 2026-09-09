# R04-F15 落地：移除型号规则后失效 project-models 缓存（#958）

Status: implemented
Class: bug-fix

## Decision

`ProjectDetailPage` 的型号归属列表（`projectKeys.modelsOf(projectKey)` =
`['project-models', key]`）在 `removeRuleMutation.onSuccess` 中缺失失效——
详情页在移除规则后仍显示旧归属型号（只 invalidate 了 detail /
inventoryModels / inventorySummary）。跨端 `useCrossClientSync` 的
PROJECT_CHANGED 分支也未含 `project-models` 前缀——另一浏览器/标签页
同步不到规则变更。

修复（两处）：

1. `removeRuleMutation.onSuccess` 补
   `invalidateQueries({ queryKey: projectKeys.modelsOf(projectKey) })`；
2. `useCrossClientSync` PROJECT_CHANGED 补 `['project-models']` 前缀失效
   （与该 key 组同前缀，一次覆盖所有项目）。

## Alternatives

- **移除成功后本地直接改写缓存（setQueryData 从列表剔除 M1）**——放弃：
  服务端是权威（移除伴随设备归属收敛），refetch 比本地镜像可靠且与
  既有 invalidate 模式一致；
- **为 useCrossClientSync 建 hook 级测试**——放弃：需 socket mock 全套
  基建（useSocketIO.test.ts 现有形态成本高），改动与既有 5 行同构，
  由页面级测试 + review 覆盖（Revisit）。

## Verification

- **反例实证**：回退 ProjectDetailPage 保留测试 → 新用例失败（移除后
  型号列表未刷新，M1 仍在）；修复版全绿；
- 新增用例（`ProjectDetailPage.test.tsx` +1）：移除规则后断言**更新后的
  内容**（型号列表消失），非只断言删除请求（验收 1）；
- `ProjectDetailPage.test.tsx` 全套 **14 passed**；
- `check:quick`（含 eslint/tsc）与 PR 门禁：见 PR 描述。

## Revisit

- 跨端分支（验收 2）无 hook 级测试：若未来 useSocketIO mock 基建成熟，
  可补「PROJECT_CHANGED 触发 ['project-models'] invalidate」用例；
- updateMutation（登记簿编辑）与 archive/rename 的 onSuccess 已含 detail/
  list 失效但同样不含 modelsOf——规则不变场景无碍；若未来编辑影响归属
  规则，需同本单补全。
