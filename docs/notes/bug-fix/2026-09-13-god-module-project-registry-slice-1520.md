# God-module 垂直切片：项目登记簿写路径下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

`backend/api/routes/projects.py`（1003 行、39 处 DB 操作、**引 0 个 service**）是 #1520
点名的三处 God-module 之一——业务规则、事务边界与 HTTP 对象同函数体。本切片延续
#1520 的「按垂直切片、不整体重写」方针，抽出**项目登记簿写路径**一条业务线：

新增 `backend/services/project_registry.py`（294 行），承载：

| 服务函数 | 原路由内联逻辑 |
|---|---|
| `create_project_entry` | SEED 保留名 422、大小写不敏感唯一 409、IntegrityError 并发兜底、审计、事件 |
| `update_project_facets` | USER 源门禁、归档不可改 409、逐字段 diff + **逐字段审计**、无变更不提交 |
| `rename_project_entry` | 双重门禁、新 key 保留名/唯一校验、from→to 审计、事件 |
| `archive_project_entry` / `unarchive_project_entry` | 状态机 409、审计、事件 |
| `get_project_or_404` / `require_user_project` / `require_active_project` | 被 8 处路由复用的取项目与门禁（下沉符号，路由改为 import） |

路由侧退化为「解析 → 调服务 → 序列化」：`projects.py` 1003 → **865 行**，
`_UPDATABLE_FIELDS` / 私有门禁函数 / 内联事务全部移出；`PUT` 路由仍保留
「`model_fields_set` 为空 → 422」这一步（请求形状判定），其余交给
`update_project_facets`。异常沿用 `HTTPException`（#1519 / #1520 首切片确立的
服务层先例）；`request` 只用于审计 IP 提取（可空）。

**行为不变是硬约束**：既有 75 例 API 级用例（`backend/tests/api/test_project_routes.py`）
**一行未改**照样全绿；另新增 `backend/tests/services/test_project_registry.py`
（12 例）补上切片前测不到的「服务边界」层——这正是 #1520 失败场景①
（业务规则无法脱离 FastAPI 单测）的反面证据。

## Alternatives

- **继续只修 bug 不拆**：弃——#1520 已明确 H 级结构债；本切片零行为变更、可用既有
  用例兜底，是拆分的低成本窗口；
- **一次性拆完 projects.py（含 inventory / map 两条链）**：弃——issue 明确
  「不建议一次性大重构」；inventory 聚合与 map preview/apply 逻辑耦合读路径，
  另行切片；
- **服务层返回领域异常、路由映射 HTTP**：弃（本单）——既有下沉切片（#1519
  `plan_run_queries`、首切片 `plan_run_manual`）已确立「服务层直接 `HTTPException`」
  先例；本单从众以免同一层两套错误风格。若将来服务要被非 HTTP 调用方复用，
  再统一引入领域异常（属独立决策）；
- **服务接收 Pydantic schema 对象**：弃——服务改吃 primitives + actor 身份，
  与既有切片一致，也让 `services/` 不依赖 `api.schemas`（分层更干净）。

## Verification

- **行为回归**：`TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/api/test_project_routes.py -q` → **75 passed**（用例零修改）；
- **新增服务层直测**（12 例，真实 PG testcontainer）：保留名 422、大小写变体 409、
  **无字段变更不提交且不写审计**、逐字段审计条数与顺序、归档只读 409、改名
  from→to 审计与 key 落地、SEED 不可改名 422、归档/解档往返审计、重复归档/对
  ACTIVE 解档 409 → **12 passed**；
- `python tools/dev/check_layering.py` → services 无 api.routes 反向依赖（新模块
  未破坏 #1519 分层门禁）；
- `python -m ruff check`（两文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`。

未做：inventory / map 两条链未动（仍留在 projects.py）；`_fill_summary` 等响应装配
按设计留在路由层。

## Revisit

- **剩余业务线**：`projects.py` 仍含 inventory 聚合（`_aggregate_inventory` /
  `_load_inventory` / `_inventory_summary`）与型号映射链（`map/preview` / `map/apply` /
  `remove-rule`）——下一刀的自然边界；拆完后再评估是否值得把 projects.py 压到
  「纯路由」形态；
- **重复的四道守卫**：`map preview/apply`、`remove-rule` 仍直接调用
  `require_user_project` / `require_active_project`（本次已改为 import 服务版），
  但其自身事务与审计仍在路由内——属下一刀范围；
- **actor 传递形态**：当前服务签名收 `actor_id` / `actor_username` 两个标量；
  若后续更多服务需要，可考虑引入轻量 `Actor` dataclass（`services/` 内定义），
  避免签名继续膨胀——本单不做以免牵动既有切片；
- **`#1520` 其它两模块**：`agent_api.py`（3239 行 / 64 DB 操作）与 `plan_runs.py`
  剩余部分仍在原状，按 issue 方针另开切片。
