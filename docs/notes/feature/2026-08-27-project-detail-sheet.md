# 2026-08-27 — 项目抽屉式详情（方向 1 首个实践）

对应设计评审批次 B；落在批次 A（PR：登记簿页改版）之上。目的：把
GitHub Projects issue pane 的「渐进披露」方式引入项目列表——高频小修
（查看归属、改 JIRA 键、看在跑数）不再整页跳转。

## 决定了什么

- **新组件 `components/ui/sheet.tsx`**：shadcn Sheet 形态的右侧滑出层，
  直接基于已在依赖中的 `@radix-ui/react-dialog` 实现——零新增 npm 包。
- **`ProjectDetailSheet`**：点击项目卡开启；内部 `api.projects.get`
  拉完整详情（key 随 sheetKey 变化，关闭态不发请求）；内容为三段：
  KPI 行（设备/在跑/Plan，在跑>0 数值转 success 色）、facet 徽标 +
  SEED 标记、JIRA 项目键块（admin 显示「编辑」按钮，内嵌复用
  EditProjectDialog 形成抽屉内修正通道）。底部「打开完整详情页」
  保留全页跳转路径。
- **页面行为变化**：卡片 click/Enter 从 navigate 改为 setSheetKey；
  ProjectDetailPage 原有入口不动，两条路径并存。
- 抽屉内编辑成功后失效 list/detail/sheet 三处缓存，保证返回列表即见新值。

## 放弃的备选

| 备选 | 为什么放弃 |
|------|-----------|
| 抽屉内嵌完整设备/Plan 列表 | 引入两个额外查询让抽屉变重；KPI 数字已来自 detail 接口，深入浏览交给全页 |
| 在 Sheet 里复制一个单字段 JIRA 表单 | 与 EditProjectDialog 双真源；直接复用该对话框叠层更省且语义一致 |
| 给 InventoryModelsTable 也配抽屉 | 型号行的高频动作是「映射」，已有对话框覆盖；观察使用率再议 |

## 如何验证

- ProjectsPage.test.tsx 新增两例 + 重写一例：卡片点击开抽屉并断言抽屉数据
  来自 get 拉取、「打开完整详情页」触发 navigate；admin 打开预填的
  EditProjectDialog（OLD→输入框值断言）。全仓 vitest 613 passed。
- tsc / eslint 绿。线上验收并入批次收尾统一重部。

## 何时重议

- 若操作者反馈抽屉常用于深度排查：升级为「抽屉分页签 + 设备/Plan 精简表」；
- 其它列表页（脚本管理/计划）若确认同类高频小修需求，可将 Sheet 模式推广
  （先做 /projects 使用回访）。
