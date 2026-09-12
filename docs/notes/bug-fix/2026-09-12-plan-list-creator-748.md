# Agent Note: Plan 编排列表恢复「创建者」信息（#748）

Status: implemented
Class: bug-fix
Issue: #748

## Decision

在 **Plan 单元格内**恢复创建者信息行（`plan.name` / `plan.description` 之后）：

```tsx
{plan.created_by && (
  <p className={cn('mt-0.5 truncate text-xs', TEXT.caption)}>创建者: {plan.created_by}</p>
)}
```

文案**逐字沿用**表格化前卡片态的渲染（`606b4350` 删除的行：
`{plan.created_by && <span>创建者: {plan.created_by}</span>}`），故用半角冒号与原实现一致。

## Alternatives

issue 给了两条路，且明确「若认为该信息低频，可接受现状并关闭——但需明确决策」：

- **新增「创建者」列**：不选。表格已是 6 列（Plan / 专项 / 步骤 / 失败阈值 / 更新 / 操作），
  `min-w-[720px]` 已需横向滚动；再插一列会挤压既有列宽，而收益与「副文本」完全相同。
- **接受现状并关闭**：不选。这是 `606b4350` 表格化引入的**信息回退**（卡片态有、表格态无），
  不是「本来没打算展示」；且数据链路完好（见下），恢复成本是 3 行 JSX。

## Verification

- **字段真伪先验证**（本会话第三次执行该纪律；`#826` 曾因 issue 前提未验证而差点写出有害改动）：
  `created_by` 确为真实链路 —— `backend/models/plan.py:62`（`Column(String(128))` DB 列）、
  `backend/api/routes/plans.py:538`（读出口 `created_by=plan.created_by`）、
  `:640` / `:797`（创建时写入 `current_user.username`）、前端 `types.ts:999` `Plan.created_by?: string | null`。
- **issue 验证口径**：`grep created_by frontend/src/pages/orchestration/PlanListPage.tsx` → 非空（2 处：新增行 + 注释）。
- `vitest run src/pages/orchestration/PlanListPage.test.tsx` → **5 passed**（4 旧 + 1 新）。
  新增用例同时锁定反向行为：无 `created_by` 的 Plan **不渲染占位行**（全表仅 1 处「创建者:」），
  避免「有字段就渲染空标签」的退化。
- `tsc --noEmit` → 干净；`check:quick` → 见 PR 描述（7 gates）。
- 前端全量 `vitest run` → 见 PR 描述。
- **未验证（诚实标注）**：窄屏下表头/列宽的实际观感（本次不新增列，故布局影响仅限 Plan 单元格
  内部多一行，且沿用 `truncate` + `TEXT.caption` 既有样式）。

## Revisit

- 若后续认为创建者属高频信息，可再评估提列为独立列（需同时调整 `min-w-[720px]` 与列宽分配）。
- 同类「卡片→表格」迁移可能还有别处丢信息：本单只按 issue 范围收口，未做全局比对。
