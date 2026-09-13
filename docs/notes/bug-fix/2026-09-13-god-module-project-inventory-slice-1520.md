# God-module 垂直切片三：项目读侧聚合下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#1520 方针的第三刀：把 `projects.py` 的**读侧聚合**抽到
`backend/services/project_inventory.py`——汇总聚合（设备数/在途 run/平台派生/
成员型号）+ inventory 型号级聚合与摘要，共 8 个函数：

| 服务函数 | 原路由内联逻辑 |
|---|---|
| `platforms_map` | 项目平台从成员型号 ⋈ 设备派生（distinct，UNKNOWN 哨兵剔除） |
| `summary_rows_for` / `summary_rows` | 设备数（成员型号派生）+ 在途 run 数（RUNNING/QUEUED/PRECHECK） |
| `aggregate_inventory` | 型号级聚合：mapped/unmapped、平台集合、排序 |
| `rule_values_for_project` | 项目活跃成员型号 → `match_models` 兼容列表 |
| `model_to_projects` | 活跃成员行全量预取（model → {project_key}，USER 口径） |
| `load_inventory` / `inventory_summary` | 全量聚合入口与摘要计数（含严格未映射口径） |

`projects.py` **680 → 518 行**（三刀累计 1003 → 518）；`_fill_summary` 按既定
切分线留在路由（响应装配），仅改为调用服务函数。

## Alternatives

- **`_fill_summary` 一并下沉**：弃——它是「读聚合 + 响应装配」混合体，装配属路由；
  本刀只搬聚合（与第三刀前 Note 里写明的切分线一致）；
- **只搬 inventory、不搬 summary 聚合**：弃——`summary_rows_for` 与 inventory 共用
  「成员型号 ⋈ 设备」派生口径，分开会让同一口径散在两处；
- **把 `USER_SOURCE` 常量也搬进本模块**：弃——复用 `project_registry.USER_SOURCE`
  （第二刀已建的真源），避免第四个副本；
- **顺手改 inventory 的查询形态（如改成一条 SQL）**：弃——本刀只做移动，行为不变。

## Verification

- **行为回归**：`backend/tests/api/test_project_routes.py`（75 例，**一行未改**）+
  `test_project_mapping.py`（9 例）→ **84 passed**；
- **新增服务层直测 6 例**（`backend/tests/services/test_project_inventory.py`）：
  型号级 mapped/unmapped 计数与平台集合、按设备数排序、项目设备数 + 在途 run 计数
  （含 `summary_rows` 全量口径）、平台派生（空入参返回 `{}`）、成员型号只取活跃、
  inventory 摘要的「USER 映射 vs SEED/未映射」拆分（total 3 / mapped 1 / unassigned 2）
  → **6 passed**；
- **反例实证**：移除 `summary_rows_for` 的在途 run 状态过滤 → **2 failed**
  （1 服务直测 + 1 API 用例 `test_aggregates_device_and_running_run_counts`）；
  恢复后全绿；
- `ruff` → All checks passed（顺带修掉遮蔽：路由内局部变量 `platforms_map` 改名
  `platforms_by_project`，避免与导入同名）；`check:quick` → **7 gates OK**。

未做：`_fill_summary` / `promote_seed_project` 仍在路由（装配与独立业务线）。

## Revisit

- **`projects.py` 剩余 518 行**：主要是路由定义 + 响应装配（`_fill_summary`、
  `_host_to...` 类装配、schema 映射）与 `promote_seed_project`；若要继续瘦身，
  下一刀候选是「SEED→USER 转正」单条业务线（面小、独立）；
- **`aggregate_inventory` 的入参形态**：当前收 `list[(model, platform)]`（便于
  纯函数测试）；若将来调用方改为流式/分页，需要保持派生口径单一真源；
- **口径文档化**：本轮三刀把「项目/型号/设备归属」的读口径集中到了两个服务模块，
  若 `docs/design` 有统计口径页，可考虑把「USER 成员 = 映射、SEED 不算」的规则
  上收为文档条目（当前分散在代码注释）。
