# B2-G8：三处空态把查询失败读成「确实没有」——失败提示 + 重试（#3496）

Status: implemented
Class: bug-fix

关联：批次 B2 规划 [#3497](https://github.com/DUElost/stability-test-platform/issues/3497)（§1 F1 / §3 G8 行）、
缺陷单 [#3496](https://github.com/DUElost/stability-test-platform/issues/3496)、
同族判据 #1195（查询失败不得展示空态）、
参照实现 [`DedupReportCard.tsx`](../../../frontend/src/components/plan-run/DedupReportCard.tsx) 的 `statusError` 分支。

## Decision

按 #3497 的 F1 形态（查询失败被读成确定事实）修三处：`useQuery` 的 `isError` 显式处理，
失败时显示「加载失败 + 重试」，**成功且为空时原空态文案不变**；失败分支置于 loading 之后、空态之前
（与参照实现同序），「重试」调用各自 `refetch()`：

- `frontend/src/components/schedule/DeviceMultiSelect.tsx`：`devicesQ.isError` →
  「设备列表加载失败，暂无法判断可选设备。」；不再回落「暂无设备」/「无匹配设备」。
- `frontend/src/pages/assistant/components/LogPanel.tsx`：`logQ.isError` →
  「执行日志加载失败，暂无法判断输出。」；不再显示「暂无输出」/「暂无输出，等待执行…」。
- `frontend/src/pages/scripts/ScriptManagementPage.tsx`（`UsageSection`）：`usageQ.isError` →
  「使用统计加载失败，暂无法判断近 30 天使用记录。」；不再显示「近 30 天无 Plan 使用记录」。

范围仅前端展示面（#3497 §0：不改后端 API / schema / 查询），不阻断任何操作。

## Alternatives

- **抽全站通用「查询失败」组件 / hook**：#3497 §6 明确列为不做；本批逐点按参照实现修，避免顺手重构。
- **失败时禁用选择 / 提交**：§6 排除——本批只修真值面，不加操作阻断。
- **把失败折成哨兵值塞进 `data` 再统一渲染**：会把「失败」与「空」继续混在同一值域，
  正是本形态的成因；且偏离参照实现。

## Verification

- `cd frontend && npx vitest run src/components/schedule/DeviceMultiSelect.test.tsx src/pages/assistant/components/LogPanel.test.tsx src/pages/scripts/ScriptManagementPage.test.tsx`
  → **3 files / 17 tests passed**。新增 4 条：三处「失败 → 出现失败提示且无原空态文案」+
  LogPanel「成功且为空 → 原文案」（DeviceMultiSelect / ScriptManagementPage 的成功空态由既有用例覆盖）。
- **变异自证**：临时把三处 `isError` 判定改为 `false`（等价去掉修复）→ `3 failed / 14 passed`，
  失败恰为每文件 1 条新增失败路径用例，两条「成功且为空」用例保持绿；`git checkout` 恢复后重跑 17 passed。
- `cd frontend && npm run lint -- --max-warnings 0` → OK
- `cd frontend && npm run type-check` → OK
- `cd frontend && npm run knip` → OK
- `python scripts/run_gates.py check:quick` → **OK（16 gates）**
- `python -m pytest tests/ -q` → **1933 passed / 18 skipped**（本机以 `.venv/bin/python` 执行，656s；与改动前基线一致，无增量失败）

## Revisit

- 生效方式：随控制面部署（前端换包），整批一次（#3497 §5）；本单元无脚本发包、无模板 pin、无重指。
- PR 用 `Refs` 不关单；L2/L3 核对（正常路径 / 浏览器阻断请求抽查失败态）按 #3497 §5 在部署后执行。
- 同族仍登记在 §8 的 F1 证据项（`NotificationBell`、`AnomalyDashboard` 等）不在本批承诺内；
  若现场确证误导，另立单。
