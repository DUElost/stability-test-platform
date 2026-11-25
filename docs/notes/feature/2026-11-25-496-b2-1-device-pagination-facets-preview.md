# Phase 2 Track B 阶段 B2-1：物理设备有界分页、独立 Facets 与跨页选择预览

Status: implemented
Class: feature
Related: #496, #4117, #4151, #4152

## Decision

按已定稿的 `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（v1.0，§3 / §6.2 / §7），以增量向后兼容方式落地物理设备域后端查询与跨页预览能力（`B2-1`），并按 `add-api-endpoint` SOP 同步前端 API 类型、客户端与 Query Keys，不切换既有 `DevicesPage`：

1. **有界列表 DTO 与旧形态冻结**：
   - `GET /api/v1/devices` 冻结原有无分页参数返回裸 `List[DeviceOut]`、带 `skip`/`limit` 返回 `PaginatedResponse` 的双形态契约；
   - 新增增量参数 `q`（字面转义 `%`/`_`/`\`，覆盖串号、展示型号、展示 Product 含别名及「未上报」、`build_display_id`、主机展示名、受数组类型保护的 `EXISTS` 标签子串）、`host_id`（`unassigned` 映射到 `host_id IS NULL`）、`model`、`version`、`has_tags`（三态，历史非数组按空处理）、`sort_by`（白名单 `{serial, host_id}`，非法值 `422`，固定 `NULLS LAST` + `id ASC`）、`sort_dir`、`projection`（`full` / `list`）；
   - `projection=list` 返回有界 `DeviceListItemOut`（省略无界 `extra`，随行透传预关联 `host_name`），50 条载荷稳定控制在 100 KiB 门禁以内；
   - `status` 兼容既有大写状态及未知字面值透传，同时支持 `idle`/`testing`/`offline`/`error` 与 `need_attention`（有效 `OFFLINE OR ERROR`）。
2. **独立只读三级 Facets（`GET /api/v1/devices/facets`）**：
   - 在单次 `LEFT OUTER JOIN Host` + `EXISTS`/`NOT EXISTS` 项目归属的一台设备一行模型上，按作用域 B（状态与标签计数）、作用域 H（主机候选，不被已选 `host_id` 锁死）、作用域 D（有效 `host_id` 下的型号/原始 Product/版本候选，不受 `q` 或同级下拉锁死）聚合；首版不输出前端未消费的 `options.tags`。
3. **跨页批量选择预览与写上限守卫**：
   - 新增只读 `POST /api/v1/devices/selection-preview`，按当前筛选条件冻结确定序 `device_ids` 并返回 `affected_models`、`devices_without_model_serials`、`retired_count`、`active_count`、`stale_count`；写类动作上限 `500`，超限时置 `exceeds_limit=True` 并返回空 `device_ids` 杜绝静默部分执行；
   - 在 `POST /devices/bulk-project`、`POST /devices/bulk-swipe-trail`、`POST /devices/retire` 增加同源上限校验，超限时返回 `422`（`BULK_LIMIT_EXCEEDED`）。
4. **前端类型与契约对拍同步**：
   - 同步更新 `frontend/src/utils/api/types.ts`、`devices.ts`（新增 `listPage`/`facets`/`selectionPreview`，保留 `list`/`fetchAllDevicePages`/`fetchAllDevices`）、`queryKeys.ts`（`page`/`facets`/`detail`），并在 `tests/test_api_response_shape_contract.py` 登记全部 6 个新响应模型的双向对拍。

## Alternatives

- **在 `GET /api/v1/devices` 响应中直接内嵌 Facets**：拒绝，单纯翻页或排序都会重复触发聚合查询，且破坏旧分页包络形状；独立 `GET /api/v1/devices/facets` 可按筛选键独立缓存。
- **使用 `LEFT JOIN` 展开 `ProjectModel` 或 `jsonb_array_elements_text` 进主 `FROM`**：拒绝，存在重复计数与分页行膨胀风险；采用 `EXISTS`/`NOT EXISTS` 与 `CASE` 数组类型保护确保严格一台设备一行且兼容历史非数组脏行。
- **跨页批量写动作服务端重跑筛选**：拒绝，筛选结果随心跳实时漂移，可能静默扩大写影响范围；由预览接口冻结 `device_ids` 并在写接口复用显式 ID 与上限/权限重验。

## Verification

- 目标 pytest（新增 B2-1 套件 + 既有设备/B1 只读/退役/滑动留痕 + API 响应形状双向对拍契约）
- 前端 `npm --prefix frontend run type-check`
- 仓级快门禁 `./scripts/project_python.sh scripts/run_gates.py check:quick`

## Revisit

- 阶段 `B3-1` 切换 `DevicesPage` 消费 `devices.listPage`、`devices.facets` 与 `devices.selectionPreview` 时，一并更新 `tests/test_frontend_filter_workbench_guard.py` 锚点并保留 `PlanExecutePage` 选机池不变；
- 生产放量前在隔离 PostgreSQL 环境按 `#3514` 执行 150 host × 3,750 device 的 `EXPLAIN (ANALYZE, BUFFERS)` 与并发 p95 基准，若发现明确瓶颈再另开 PR 评估针对性索引。
