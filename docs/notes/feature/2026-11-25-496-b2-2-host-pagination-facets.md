# Phase 2 Track B 阶段 B2-2：Agent host 增量筛选排序与独立 Facets

Status: implemented
Class: feature
Related: #496, #4117, #4151, #4152, #4155

## Decision

按已定稿的 `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（v1.0，§4 / §6.2），以向后兼容增量方式交付 Agent host 服务端分页筛选、全局排序、独立 10 键 Facets 与跨页批量选择预览（`B2-2`），并按 `add-api-endpoint` SOP 同步前端类型、API 客户端、Query Keys 与响应形状双向对拍，不切换既有 `HostsPage`：

1. **冻结 `GET /api/v1/hosts` 既有双形态并增量扩展**：
   - 未显式传 `skip` 与 `limit` 时继续返回至多 50 条裸数组，传任一参数返回 `{items, total, skip, limit}`（单页上限 `200`）；未传 `sort_by` 时维持原有 `Host.id ASC` 顺序，零破坏 `site_config`、部署 SOP、`install_agent.sh` 与既有 `fetchAllHosts`。
   - 新增增量参数：`status`（有效连通态 `ONLINE`/`DEGRADED`/`OFFLINE`）、`health_status`（`HEALTHY`/`DEGRADED`/`UNSCHEDULABLE`/`unhealthy`）、`sync_status`（`upgrade_pending`/`drift`/`matched`/`pending`/`unknown`）、`usb_anomaly`、`mount_anomaly`、`q`（空白分词 AND，覆盖 `id`/主机名/IP/健康原因代码与中文标签/Agent 版本）、`sort_by`（白名单 `{default, priority, id, name, devices, cpu, ram, disk, heartbeat}`，非法值 `422`）、`sort_dir`。
   - `sync_status=upgrade_pending` 与计数严格复用同一谓词（`ONLINE && agent_installed && drift`），不放宽为原始 `drift`；USB 异常沿用 3/2/1/0 优先级及退役/`emptied_at` 豁免规则；挂载空数据不计为挂载故障；分页切片前执行全局优先级/显式排序并以 `Host.id ASC` 为尾键。
2. **独立只读 `GET /api/v1/hosts/facets`**：
   - 按 `include_retired` 基础全集返回 10 个聚合计数字段：`all`、`online`、`degraded`、`offline`、`usb_anomaly`、`upgrade_pending`、`health_anomaly`、`mount_anomaly`、`aligned_agent_count`、`trackable_agent_count`（`unknown`/`pending` 保留在 `trackable_agent_count` 分母中），不受搜索词与翻页影响。
3. **跨页选择预览 `POST /api/v1/hosts/selection-preview`**：
   - 只读冻结当前筛选条件下全部匹配 `host_ids` 及安装/重装/热更新/刷机前置/退役计数摘要，为 `B3-2` 显式区分当前页勾选与跨页确认提供后端单一事实源。
4. **前端类型与契约对拍同步**：
   - 在 `frontend/src/utils/api/types.ts`、`hosts.ts`（新增 `listPage` / `facets` / `selectionPreview`，保留 `list` / `fetchHostList` / `fetchAllHosts` / `coerceHostList`）、`queryKeys.ts`（`hostKeys.page` / `hostKeys.facets`，严禁把分页包络写入 `['hosts']`）同步声明，并在 `tests/test_api_response_shape_contract.py` 登记 `HostFacetsOut ↔ HostFacets` 与 `HostSelectionPreviewOut ↔ HostSelectionPreview` 双向对拍。

## Alternatives

- **随 `GET /api/v1/hosts` 返回内嵌 `summary` 字段**：拒绝，破坏旧分页包络与裸数组兼容，且翻页/搜索都会重复计算全局计数；采用独立 `GET /api/v1/hosts/facets`（契约 §4 规则 2）。
- **在路由层直接拼接 JSON 文本 SQL 筛选 `agent_code_sync_status`**：拒绝，`agent_code_sync_status` 依赖运行期 `desired_artifact_digest` 与 `build_host_version_view` 推导；在目标规模 150 台主机下由纯只读 `host_inventory_query` 统一投影后筛选与聚合，确保列表与 Facets 100% 同口径且 PostgreSQL/SQLite 双端一致。

## Verification

- 定向 pytest：`backend/tests/api/test_hosts_b2_2_pagination_facets_496.py`、`backend/tests/api/test_hosts.py`、`backend/tests/api/test_inventory_readonly_b1_3325.py`、`tests/test_api_response_shape_contract.py`
- 前端 `npm --prefix frontend run type-check`
- 仓级快门禁 `./scripts/project_python.sh scripts/run_gates.py check:quick`

## Revisit

- 阶段 `B3-2` 切换 `HostsPage` 消费 `hosts.listPage`、`hosts.facets` 与 `hosts.selectionPreview` 时，一并核验双视口/双主题与跨页安装确认流；
- 若远期单站点 Agent host 数量远超 1,000 台，可再评估将派生同步态与 USB 优先级物化为表列下推 SQL。
