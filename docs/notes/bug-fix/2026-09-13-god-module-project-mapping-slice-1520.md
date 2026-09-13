# God-module 垂直切片二：项目型号映射写链下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#1520 方针「按垂直切片、不整体重写」的第二刀：把 `projects.py` 的**型号映射写链**
（`map/preview` / `map/apply` / `rules/{model}` 删除）抽到
`backend/services/project_mapping.py`，连同被这三条路径复用的两个归一助手
（`blank_to_none` / `normalize_models`）。路由退化为「解析 → 调服务 → 序列化」。

| 服务函数 | 原路由内联逻辑 |
|---|---|
| `map_preview`（私有） | 归一匹配设备、型号级归属查表、冲突/未知型号判定、`will_assign` 统计 |
| `preview_project_mapping` | 取项目 + USER/归档门禁 + 预览（零写入） |
| `apply_project_mapping` | 冲突 409、SEED 让位、大小写变体收敛到设备事实原值、并发 IntegrityError→409、`apply_project_model` 审计、`emit_project_changed(assigned)` |
| `remove_project_mapping_rule` | 活跃行 404、删除、`remove_project_model` 审计、`emit_project_changed(rule_removed)` |

`projects.py` 865 → **680 行**（两刀累计 1003 → 680）；行为不变由既有
75 例 API 用例（**一行未改**）兜底。

## Alternatives

- **只搬 apply/remove、预览留在路由**：弃——`map_preview` 同时被三条路径消费，
  留下会让「冲突判定」有两个真源（apply 前的 409 与 preview 的 conflicts 必须同源）；
- **把 inventory 读链一并搬走**：弃（本刀）——读链（`_load_inventory` /
  `_aggregate_inventory` / `_inventory_summary` / `_model_to_projects`）与写链耦合度低、
  面更大，留第三刀（见 Revisit）；
- **服务层返回 primitives、路由装配 schema**：弃——仓内已有
  `services/report_service.py` import `api.schemas` 的先例，且预览结果本身是
  对外契约的一部分（`ProjectMapPreviewOut`），直接复用 schema 避免双份结构；
- **顺手改 `promote_seed_project`**：弃——它属「SEED 转正」另一条语义，不在
  map 写链范围（只改当前 Requirement 必需内容）。

## Verification

- **行为回归**：`TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/api/test_project_routes.py -q` → **75 passed**（用例零修改）；
- **新增服务层直测 9 例**（`backend/tests/services/test_project_mapping.py`）：
  归一助手（去重/strip/大写）、空型号 422、USER 冲突上报与 reassign 放行、
  未知型号、**SEED 让位 + 成员行写设备事实原值**、**大小写变体收敛**
  （`INFINIX_X1102D` → 设备事实 `Infinix_X1102D`）、无 reassign 的 409、
  remove 的 404 与审计 → **9 passed**；
- **反例实证**：注入「移除 SEED 让位分支」→ **4 failed**
  （1 服务直测 + 3 API 用例：`test_preview_and_apply_from_seed_and_null` /
  `test_user_conflict_skipped_unless_reassign` / `test_seed_cede_keeps_device_fact_case`）
  ——证明抽出的代码正是行为路径本身，且两层用例都能捕获；恢复后全绿；
- `python tools/dev/check_layering.py` → services 无 api.routes 反向依赖；
- `ruff` → All checks passed；`check:quick` → **7 gates OK**。

未做：inventory 读链未动（第三刀）；`promote_seed_project` 未动。

## Revisit

- **第三刀候选**：inventory 读链（`_load_inventory` / `_aggregate_inventory` /
  `_inventory_summary` / `_model_to_projects` / `_rule_values_for_project`）——
  搬完后 `projects.py` 有望降到 ~450 行（纯路由 + 响应装配）；
- **`_fill_summary` 的归属**：它同时被详情/列表/写路径复用（读聚合 + 响应装配混合），
  第三刀时需裁定「读聚合进服务、装配留路由」的切分线；
- **助手命名**：`blank_to_none` / `normalize_models` 从私有助手变为服务公开函数，
  若第三刀还有模块需要，可考虑收进 `services/project_common.py` 之类的共享小模块；
- **`promote_seed_project`**（SEED→USER 就地转换）仍内联在路由中，属独立业务线，
  未纳入本单；若继续拆应单独切片。
