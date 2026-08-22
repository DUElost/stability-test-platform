# B4 决议落地：承认原生 select，删除 Radix ui/select（issue #371）

Status: implemented
Class: simplification

## Decision

**全站下拉的唯一形态 = 原生 `<select>` + `FORM.select` / `FORM.selectSm`。**
`components/ui/select.tsx`（Radix）删除，规矩落在 FORM token 抬头注释。

- 迁移 Radix 消费者 **8 处**（审查时 2 处，#343 项目线 + #366 控件统一
  轮后涨到 8）：ProjectFilterSelect（共享，四页生效）、pagination-bar
  页大小、DeviceFilterBar 版本/型号 ×2、AuditLogPage 资源/操作 ×2、
  ProjectsPage 四个 facet、InventoryModelsTable 平台、MapModelsDialog
  与 AssignProjectDialog 项目选择。'all' 哨兵语义原样保留；空初值处
  用 `<option value="" disabled>` 承接原 placeholder。
- 测试交互从「点 trigger → 点 role=option」改为 `user.selectOptions`
  （原生语义，Devices/Projects 两文件三处）。

## 承认原生的理由（vs 统一 Radix）

- 原生在长列表（机型/项目名/审计资源）下滚动性能与键盘/读屏可访问性
  不劣于 Radix，且无 portal/浮层复杂度；
- 历史多数派（11:2 起步），统一 Radix 要迁 11+ 处且引入弹层成本；
  承认原生只迁当时少数派；
- 双轨的真实代价是「新页面随手挑一种」——#343 的四个新对话框全建在
  Radix 上，正是没有规矩的产物。删组件 + token 注释让第二 条线在
  类型层面不存在。

## Verification

- 门禁：tsc / eslint（12 改动文件 `--max-warnings 0`）/ 全量 vitest
  **602** / build 全绿。
- DOM 4/4（`/tmp/ui-shot-rig/verify-b4.js`）：audit 两原生下拉（10/7
  options，all 哨兵）切换触发重查；devices/projects/notifications/
  plan-execute 四个重度页 **零 combobox 残留**；devices 项目筛选为
  原生 select（value=all，9 options）。

## Revisit

- 若未来需要 Radix 级能力（可搜索/多选/分组），先回读本 note 的
  Alternatives 论证，再决定是重建组件还是引入 Combobox 专项组件——
  不要无声复活通用 select 双轨。
- audit 哨兵注释已随迁移改写（原注释描述的 Radix 空串问题不复存在，
  哨兵保留是因为空值语义歧义）。
