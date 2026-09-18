# God-module 垂直切片：projects 读侧 catalog 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

`plan_runs` / stall 测试被 #1805 / #2577 占窗；本刀切回 `projects.py` 残余读侧：

抽到 `backend/services/project_catalog.py`：

| 服务函数 | 原路由逻辑 |
|---|---|
| `fill_project_summary` | `_fill_summary`（写路径成功后复用） |
| `fill_promoted_summary` | promote 历史口径（不填 platforms） |
| `list_project_summaries` | `GET /projects` 过滤 + 装配 |
| `list_customer_entries` | `GET /customers` |
| `list_project_model_coverage` | `GET /{key}/models` |
| `build_project_detail` | `GET /{key}` 详情 |

路由退化为 Depends + `ok(...)`。`projects.py` **455 → 360**。

## Alternatives

- **顺手统一 promote 走 `fill_project_summary`**：弃——历史口径故意不填
  platforms，本刀零行为变更；
- **继续啃 plan_runs re-export**：弃——#1805 占窗 `plan_runs.py`；
- **只搬 detail**：弃——list/models/customers 同属读侧装配，一并收口更短路径。

## Verification

- 服务直测：`test_project_catalog`（8 例）；
- API 回归：`test_project_routes` 合计 **81 passed**；
- `ruff` + `check:quick`。

## Revisit

- `projects.py` 已基本薄壳（写路径本来就在 registry/mapping）；
- #1520 主战场残余：`plan_runs` re-export/注释、`agent_api` 鉴权/re-export；
- Issue #1520 保持 OPEN；`Refs #1520`。
