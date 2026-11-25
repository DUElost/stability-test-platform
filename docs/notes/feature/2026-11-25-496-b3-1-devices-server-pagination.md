# Phase 2 Track B 阶段 B3-1：`DevicesPage` 切换服务端分页、独立 Facets 与跨页预览

Status: implemented
Class: feature
Related: #496, #4117, #4151, #4152, #4155

## Decision

按已定稿的 `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（v1.0，§3 / §6.1 / §6.2），将物理设备页（`DevicesPage`）从每 10 秒全量分页拉满切换为服务端真分页、独立三级 Facets 与显式跨页选择预览，保留 `PlanExecutePage` 与共享全量 helper（`fetchAllDevices` / `fetchAllDevicePages`）不变：

1. **单页有界查询与独立 Facets 缓存分离**：
   - `DevicesPage` 停止调用 `fetchAllDevicePages` 与 `fetchAllHosts`，改为按页请求 `api.devices.listPage({ projection: 'list', skip, limit, ... })`（默认 `limit=50`，支持 `50 / 100 / 200` 页容量切换，`keepPreviousData` 消除翻页闪烁）；
   - URL 参数映射遵循契约 §3.2 规则 3：`stale`→`include_stale`、`retired`→`include_retired`、`attention`→`status=need_attention`、`idle/testing/offline/error`→`ONLINE/BUSY/OFFLINE/ERROR`、`tagged`→`has_tags=true`；
   - 独立请求 `api.devices.facets`（缓存键 `deviceKeys.facets` 仅含三级作用域维度，不含页码、排序与 `q`），翻页、排序与输入搜索词均不触发重复 Facets 请求；仅当 Facets 成功返回且非 placeholder/加载态时才对失效下拉值做 URL 归一。
2. **排序入口收敛与差异记录（契约 §3.2 规则 5）**：
   - 服务端分页首版下线心跳遥测列（`状态`、`电量`、`温度`、`最后活跃`）与按主机显示名（`所属主机`）的前端排序入口，仅保留 `设备`（`serial`）的服务端稳定排序（循环 `serial asc` → `serial desc` → 默认 `(host_id NULLS LAST, id ASC)`），杜绝仅排序当前页或心跳遥测列跨页漂移。
3. **两条显式批量选择路径与超限保护（契约 §3.4）**：
   - 普通勾选仅作用于当前页，并在 `DeviceBulkActionBar` 显示分子/分母；
   - 点击「选择全部筛选结果」调用 `POST /api/v1/devices/selection-preview` 冻结匹配 `device_ids` 与影响摘要（`affected_models`、`devices_without_model_serials`、`retired_count`/`active_count`），写操作全部复用冻结 ID（含 `#4162` 快捷跳过 OOBE）；
   - 当命中超过单次写操作上限（500 台，`exceeds_limit=true`）时禁用全部写动作并明确提示收窄筛选，只读动作（复制序列号、导出 CSV）走用户主动触发的全集分页读取路径。
4. **详情深链与静态守卫更新（契约 §6.1）**：
   - `?drawer=<id>` 指向非当前页设备时，经 `api.devices.get(id)`（`deviceKeys.detail(id)`）独立加载并打开 `DeviceDetailDrawer`；
   - 按契约 §6.1 规则 4，仅更新 `tests/test_frontend_filter_workbench_guard.py` 中设备页的词法锚点为断言消费 `api.devices.facets` / `api.devices.listPage` 且无 `fetchAllDevicePages`，完整保留 `PlanExecutePage` readiness 守卫与全部 6 项治理守卫。

## Alternatives

- **在客户端保留遥测列当前页排序**：拒绝，会让用户误以为全量排序（违反契约 §3.2 规则 5）。
- **跨页全选后直接重跑筛选执行写接口**：拒绝，心跳实时变化可能静默扩大写操作范围；统一先预览冻结 ID 再提交显式 ID 写接口。

## Verification

- Vitest：`frontend/src/pages/devices/DevicesPage.test.tsx`、`frontend/src/components/device/ExpandableDeviceTable.test.tsx`、`frontend/src/components/device/DeviceBulkActionBar.test.tsx`、`frontend/src/components/device/DeviceDetailDrawer.test.tsx`
- 静态守卫：`pytest tests/test_frontend_filter_workbench_guard.py`
- 前端 `npm --prefix frontend run type-check` 与 `eslint`
- 仓级快门禁 `./scripts/project_python.sh scripts/run_gates.py check:quick`
- `stp-ui-review` 隔离栈双视口（`1440×900` / `1280×800`）× 双主题 Playwright 实机核验与截图审阅

## Revisit

- 阶段 `B3-2` 与 `B3-3` 将按同模式依次切换 `HostsPage` 与 `PlanRunDetailPage`；`PlanExecutePage` 选机池及 `fetchAllDevices` / `fetchAllDevicePages` 留待独立专题演进。
