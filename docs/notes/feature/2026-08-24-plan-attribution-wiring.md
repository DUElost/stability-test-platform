# Plan 归属写入接线：create/update 接受 project/specialty + 字典 API（#405）

Status: implemented
Class: feature

## Decision

补上 ADR-0029 的写入断点（评审 #400 定位的两处之一）：

1. **`PlanCreate` / `PlanUpdate` 新增 `project_key` / `specialty_key`**（F2 口径，
   数字 id 只留 DB 外键）。`create_plan` 创建即写归属；`update_plan` 语义随
   `fields_set`——显式传 `null` = 清除，未提供不动另一维。
2. **字典 API `GET /api/v1/specialties`**（plans 路由，prefix `/api/v1`）：D6 专项
   下拉与列表分组的数据源。字典是静态种子（迁移灌入），**无写端点**——变更走迁移，
   避免为三个值建 CRUD 面。
3. **前端**：Plan 编辑页头部加「归属项目 / 专项」两个下拉（数据源 projects 列表 +
   specialties 字典）；保存时**只在变更后**把 key 放进 update payload（新建恒带）
   ——后端按 fields_set 记审计 changed，恒发会让每次无关保存都产生归属变更审计噪音；
   Plan 列表行显示 specialty 徽标。

## 放弃的备选

- **字典端点挂 `/api/v1/projects/specialties`**：拒绝。specialty 是 Plan 维度
  （D6），不是项目的子资源；plans 路由 prefix 恰是 `/api/v1`。
- **update 恒发归属字段**：见 3，审计语义优先。
- **列表一维分组（D6 完整形态）**：本次只做徽标展示不做分组控件——当前生产仅
  5 plan，分组的收益要等规模上来；编辑器/字典/展示已打通，「半死列」状态解除，
  分组留待真实需要。

## 如何验证

- 后端 `TestPlanAttribution` ×4：创建带归属、未知 key 404、update 变更/清除
  （fields_set 语义锁定）、字典端点；`test_plans_api.py` 50 例全绿。
- 前端 tsc 干净、ESLint 干净、orchestration+pipeline 96 例通过、全量 vitest
  604 例通过。
- 快照链路联动核验：Plan 归属变化后派发的新 Run 会经 #401 的 prepare 冻结
  （`plan.project_id` → `plan_run.project_id`），登记簿读路径自此对新数据生效。

## 边界与何时重议

- `append-chain-tail` 的 `PlanChainTailCreate` 未加归属字段（链尾追加沿用既有
  行为）；如需随创建归属，另起小 PR。
- 项目下拉只列 USER 项目（后端 `/projects` 默认口径）——给 SEED 项目建 Plan
  不支持也不应该（v2.4 产品面纠正）。
- D6 二维分组视图（项目 × 专项）待 Plan 数量增长后评估，触发条件同 ADR-0029 D8。
