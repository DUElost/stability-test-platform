# Agent Note: U5-4 `PlanEditPage` 四栏减负与右侧步骤属性抽屉化（#4117）

Status: implemented
Class: feature

## Decision

- 解决生产实测反馈的 `/orchestration/plans/:id` 四栏并排挤压问题（`1280px` 视口下画布仅 ~476px、右侧编辑栏仅 `320px`）：
  1. **右侧步骤属性栏抽屉化（对齐 D2）**：移除常驻 `320px` 右侧列；点击画布中任意步骤时弹出宽幅右侧标准 `Sheet`（`size="lg"`，轻度背景遮罩 `bg-foreground/15 backdrop-blur-[1px]` 保留画布上下文隐约可见），内嵌 `PlanStepInspector` 并提供跨阶段 `< 上一步 / 下一步 >` 切步导航（`Init → Patrol → Teardown`）与关闭按钮，支持连续编辑多个步骤而无需反复开关抽屉。
  2. **左侧 `PlanChainPanel` 按需折叠**：当当前 Plan 不存在上下游链式关联（单 Plan）时默认折叠为 `44px` 紧凑竖栏（保留一键展开与追加下游 Plan 入口）；存在多节点 Plan 链时自动展开，且支持手动收起/展开。
  3. **壳层契约补齐**：`PlanEditPage` 的无效 ID、加载态与报错态统一由 `PageContainer width="content"` 包裹，对齐 `2026-11-23-frontend-ui-governance-contract.md` §3.1。

## Alternatives

- **保持常驻 320px 侧栏或仅做非模态定宽面板**：320px 无法舒适承载脚本参数表单与 ADR-0060 生效值投影；非模态覆盖浮层缺少标准 Radix 焦点陷阱与 Esc 焦点归位。采用轻遮罩宽幅 `Sheet` + 顶栏 `< 上一步 / 下一步 >` 兼顾了画布宽度、表单舒展度、连续编排效率与全站 D2 交互一致性。

## Verification

- `npx vitest run src/pages/orchestration/PlanEditPage.test.tsx src/components/pipeline/PlanStepInspector.test.tsx` 全绿（含单 Plan 默认折叠/展开、多节点链自动展开、点击步骤弹出抽屉与 `< 上一步 / 下一步 >` 切换及关闭用例）。
- `.venv/bin/python scripts/run_pytest.py tests/test_frontend_filter_workbench_guard.py -q` 全绿。
- `.venv/bin/python scripts/run_gates.py check:quick` 16/16 全绿。

## Revisit

- 待 Track A 全部合入后，在 `stp-ui-review` 对 `/orchestration/plans/:id` 做 `1440×900` 与 `1280×800` × 亮/暗双主题实机截图验收并回填 `#4117`。
