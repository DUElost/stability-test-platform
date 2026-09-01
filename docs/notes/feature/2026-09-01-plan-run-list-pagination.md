# PlanRun 列表真分页

Status: accepted
Class: feature

## Decision

`GET /api/v1/plan-runs` 改为分页壳（破坏性：原 `data` 数组 → 对象）：

```json
{
  "items": [...],
  "total": 120,
  "skip": 0,
  "limit": 50,
  "stats": { "total": 120, "running": 3, "failed": 5 }
}
```

- `total`：当前 status/q/project 筛后总数（翻页）
- `stats`：同 project/plan 作用域，**不受** status/q 影响（KPI 点筛数字不跳）
- `status` 可重复（`QUEUED`+`PRECHECK` = 排队 Tab）
- `q`：Run ID / Plan 名 / 触发者

前端：`planRuns.listPage` + `PaginationBar`（20/50/100）；旧 `list()` 仍 unwrap 为 `items[]`。

## Alternatives

- 只抬 limit：库变大后又只能看最近 N 条。
- KPI 跟筛后 total：点「失败」后「总数」变失败数，语义糊。
- 全站改吃 PaginatedResponse：爆炸面大。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunListPage.test.tsx
npx tsc --noEmit
```

## Revisit

风险列；控制面部署前须同步本 API 变更（否则旧前端解析数组会挂）。
