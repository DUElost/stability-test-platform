# 执行记录：宽屏 + 设备量 / 通过率列

Status: preview
Class: feature

## Decision

- `PageContainer width="wide"`（与主机页同档），表格列加宽。
- `GET /plan-runs` 每项增加 `device_count`（当前页 `JobInstance` 批量计数；≈ 设备量）。
- 列表新增「设备」「通过率」（`result_summary.pass_rate`，无则 —）。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunListPage.test.tsx
JWT_SECRET_KEY=test-secret python -m pytest backend/tests/api/test_plan_runs_api.py -q --tb=line
npm run build && rm -rf dist-preview && mv dist dist-preview
```

## Revisit

若需「成功/失败 job 数」拆列，可复用 `result_summary`；host 数另聚合。
