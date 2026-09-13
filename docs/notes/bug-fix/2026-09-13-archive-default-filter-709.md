# 归档项目默认过滤：列表 ACTIVE + 选择器排除 ARCHIVED（#709）

Status: implemented
Class: bug-fix

## Decision

#659 归档守卫落地后，归档项目仍进入默认列表与筛选/选择器。按 #709 实现：

- `api/projects.list(status?: 'ACTIVE'|'ARCHIVED')` 增加可选 `status`（不传＝全部，
  与后端 `/projects` 语义一致）；新增 `listActive()`（`?status=ACTIVE`）。
- **筛选/选择场景改用 `listActive()`**：`ProjectFilterSelect`、`AssignProjectDialog`、
  `usePlanEditForm`（Plan/PlanRun/结果/设备页下拉与派发选择器）——归档项目不再
  稀释下拉，也无法被选为新的归属目标。
- **项目登记簿页（ProjectsPage）**：新增「生命周期」chip 组（在用/已归档/全部），
  **默认 `ACTIVE`**；切到「已归档」作为复查/解档入口。列表仍一次拉全部、客户端
  过滤（避免额外请求，保持复查即时性）。

影响面：`frontend/src/utils/api/projects.ts`、`frontend/src/components/project/ProjectFilterSelect.tsx`、
`frontend/src/pages/devices/components/AssignProjectDialog.tsx`、
`frontend/src/pages/orchestration/usePlanEditForm.ts`、`frontend/src/pages/projects/ProjectsPage.tsx`。

## Alternatives

- 让 `list()` 默认 `ACTIVE`：会同时隐藏登记簿页的归档项，破坏复查/解档入口；
  故默认不变、新增显式 `listActive()` 供选择场景使用。
- 服务端过滤改为分状态多请求：增加请求数且「全部」复查需切换状态；客户端过滤
  在当前项目量级更简单可靠。
- 仅改登记簿列表、不改选择器：选择器仍可选中归档项目做归属，语义不完整。

## Verification

- `vitest run`（ProjectsPage / PlanListPage / PlanRunListPage 及全量）→ 87 files /
  460 tests passed。（vitest 报告的 19 个 “worker timeout” 为 worktree 软链
  node_modules 下的启动资源竞争，非用例失败。）
- `tsc --noEmit`、`eslint` 变更文件通过。

## Revisit

- 归档项目数量很少时该过滤价值有限（#709 原触发条件 >5）；已按建议前置实现，
  如运营反馈「登记簿默认想先看全部」，可把默认改回「全部」而保留 chip。
- 若后端未来支持分页，客户端过滤需改为服务端 `?status=` 分页查询。
