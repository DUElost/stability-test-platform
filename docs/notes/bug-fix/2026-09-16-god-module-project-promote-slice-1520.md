# God-module 垂直切片四：SEED promote 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#1520 方针的第四刀：把 `projects.py` 的 **SEED→USER 就地转正**抽到
`backend/services/project_registry.py`（与 create / archive 同属登记簿写路径），
新增 `promote_seed_project_entry`：

| 门禁 / 副作用 | 行为 |
|---|---|
| 非 SEED / 未知 key | 404 `seed project not found` |
| `LEGACY` | 422（兜底标签不可转正） |
| `ARCHIVED` | 409 |
| 成功 | `source=USER` + 成员型号幂等补齐 + `promote_seed_project` 审计 + `emit_project_changed(..., "promoted")` |

路由退化为「调服务 → 装配 `ProjectSummaryOut`」；`_fill_summary` 仍不用于
promote（保持原装配口径：`summary_rows` 全量 + 不填 platforms）。

`projects.py` **518 → 455 行**（四刀累计 1003 → 455）。

## Alternatives

- **新建 `project_promote.py`**：弃——单函数、与 registry 生命周期同族，拆文件
  只增加 import 跳转；
- **顺手让 promote 改走 `_fill_summary`**：弃——本刀只搬业务，不改响应装配口径；
- **把型号发现 SQL 改写成「仅 Device 不 join 成员行」**：弃——行为不变优先，
  现有 join 口径已有 API 用例锚定。

## Verification

- **行为回归**：`backend/tests/api/test_project_routes.py` + 既有 registry 服务测
  （业务断言未改）→ 与新增直测合计 **92 passed**；
- **新增服务层直测 5 例**（`TestPromoteSeed`）：成功转正+审计、LEGACY 422、
  ARCHIVED 409、二次 404、未知 404；
- 循环依赖：`blank_to_none` 在 `promote_seed_project_entry` 内惰性导入
  （`project_mapping` 顶层依赖本模块）；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **`projects.py` 剩余 455 行**：主要是 list/detail 装配、`list_project_models`
  覆盖查询、customers 列表；下一刀若继续瘦身，候选是 detail 近期 run 装配或
  models 覆盖读路径（面仍小于 plan_runs / agent_api）；
- **#1520 主战场**：`plan_runs.py` / `agent_api.py` 仍是千行级——projects 四刀
  已证明「业务线垂直切片」可复用；下一主切片应切回那两处最大模块；
- Issue #1520 保持 OPEN（ledger），本 PR 只 `Refs #1520`。
