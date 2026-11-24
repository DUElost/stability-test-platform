# 列表服务端分页、只读投影与 Facet 聚合契约（草案 v0.1）

> **最后更新**：2026-11-24  
> **状态**：Draft v0.1（承接 [#4117](https://github.com/DUElost/stability-test-platform/issues/4117) D5 Phase 2、[#496](https://github.com/DUElost/stability-test-platform/issues/496)、[#3325](https://github.com/DUElost/stability-test-platform/issues/3325)、[#83](https://github.com/DUElost/stability-test-platform/issues/83)；待评审定稿后启动后端与前端联调）  
> **代码基线**：`origin/main@09eefb17f`  
> **关联文档**：[`2026-11-23-frontend-ui-governance-contract.md`](./2026-11-23-frontend-ui-governance-contract.md)、[`writing-conventions.md`](../development/writing-conventions.md)（ADR-0059）

本文的规则句按 ADR-0059（`docs/development/writing-conventions.md` §3）书写，只使用「必须 / 不得 / 建议 / 可以」并写明执行者。本文约束控制面面向 **150 Agent host / 3,750 物理设备 / 单 PlanRun 1,000 设备** 规模的三个高频列表域（物理设备、Agent host、PlanRun 设备矩阵）的前后端查询契约、只读投影边界与向后兼容迁移顺序。

---

## 1. 背景与待解瓶颈（非规则）

当前实现存在三项已核实的规模瓶颈：

1. **`GET /api/v1/devices`（`#496`、`#3325`）**：单页上限 `_DEVICE_LIST_MAX_LIMIT = 1200`；前端 `fetchAllDevicePages` 每 10 秒分页拉满全集并计算下拉候选项；后端 `backend/api/routes/devices.py` 在 GET 路径逐行调用 `_ensure_host_online_for_device` 懒查 `device.host`，并在状态翻转时同步 `db.commit()`，在 3,750 台规模及多查看者场景下造成读放大与写锁竞争。
2. **`GET /api/v1/hosts`（`#496`、`#3325`）**：后端 `backend/api/routes/hosts.py` 在 GET 路径调用 `_ensure_host_status_up_to_date` 同步写库，心跳抖动期列表读请求会触发写冲突。
3. **`GET /api/v1/plan-runs/{run_id}/devices`（`#83`）**：端点无分页参数，单次返回全量设备且每条 `DeviceMatrixItem` 序列化约 30 个字段；但前端 `DeviceOverview` 的 Minimap 视图要求全集方块矩阵，不能简单截断为单页。

---

## 2. 读端点纯只读不变量（解 `#3325`）

以下各条均为要求：

1. 后端实施者必须在 `GET /api/v1/devices` 与 `GET /api/v1/hosts` 中移除同步状态回写与 `db.commit()`，使列表读端点保持严格纯只读事务。
2. 后端实施者必须将 Agent host 心跳超时离线与关联设备离线级联收敛至后台周期对账任务，不得依赖用户打开列表页触发状态落库。
3. 当 Agent host 已超过心跳超时阈值但后台对账尚未落库时，后端实施者可以在只读响应序列化层按时间戳派生展示态（例如将展示状态投影为离线），但不得修改数据库行。
4. 后端实施者必须在列表查询中采用批量联表或显式预加载（例如 `joinedload` / 单次 `IN` 查询），不得在结果遍历循环中逐行懒加载 `device.host`。

---

## 3. `GET /api/v1/devices` 服务端分页与 Facets 契约（解 `#496`）

### 3.1 列表查询参数与排序

1. 后端实施者必须保留既有 `skip`、`limit`、`project_key`、`unassigned` 查询参数的向后兼容，并扩展以下可选查询参数：
   - `q`：按设备串号、型号、`product`、系统版本不区分大小写子串匹配；
   - `status`：支持单状态（`idle` / `testing` / `offline` / `error`）及聚合视图哨兵 `need_attention`（等价于离线或错误）；
   - `host_id`、`model`、`product`、`version`、`tag`、`has_tags`（布尔）、`show_stale`（布尔）、`show_retired`（布尔）；
   - `sort_by`（白名单：`serial` / `status` / `battery_level` / `temperature` / `host_id` / `last_seen`）与 `sort_dir`（`asc` / `desc`）。
2. 当 `sort_by` 不在白名单内时，后端实施者必须返回 `422` 校验错误，不得拼接动态 SQL 字段名。
3. 前端实施者在服务端分页上线后，必须将 `DevicesPage` 切换为单页拉取（默认 `limit=50`，页容量选项 `50 / 100 / 200`），不得继续每 10 秒轮询调用 `fetchAllDevicePages` 拉取全量设备。

### 3.2 独立 Facet 聚合端点

1. 后端实施者必须新增只读端点 `GET /api/v1/devices/facets`，接收与 `GET /api/v1/devices` 相同的筛选参数（不含 `skip`、`limit`、`sort_by`、`sort_dir`）。
2. `GET /api/v1/devices/facets` 的响应体必须包含两部分：
   - `status_counts`：在当前非状态维度筛选作用域下，`all`、`idle`、`testing`、`offline`、`error`、`need_attention`、`tagged` 的精确计数；
   - `options`：按当前状态与标签作用域收敛后的去重候选项列表（`hosts`、`models`、`products`、`versions`、`tags`），与 `tests/test_frontend_filter_workbench_guard.py` 既有的作用域收口语义一致。
3. 前端实施者仅可以在筛选条件变更、设备写操作成功失效缓存或低频后台刷新时请求 `GET /api/v1/devices/facets`，不得在单纯翻页或切换列排序时重复请求 Facets。

### 3.3 跨页批量选择边界

1. 服务端分页切换首版中，前端实施者必须将批量勾选作用域明确限定为当前页已选 `device_ids[]`，并在 `DeviceBulkActionBar` 中明确标注「当前页已选 N 台」，不得隐式在后台循环拉取全量设备拼装全选集合。
2. 未来若业务确需跨页按筛选条件批量操作，后端实施者与前端实施者必须另行扩展显式 `selection_mode: "filter_query"` 与确认上限，不得复用裸 `device_ids[]` 隐式表达全量。

---

## 4. `GET /api/v1/hosts` 服务端分页与计数契约（解 `#496`）

1. 后端实施者必须在 `GET /api/v1/hosts` 保留向后兼容，并支持可选参数 `q`（主机名 / IP / Agent 版本）、`health_status`、`show_retired`、`sort_by`、`sort_dir`、`skip`、`limit`。
2. 后端实施者必须提供轻量汇总计数（随列表响应的 `summary` 字段或独立 `GET /api/v1/hosts/facets`），覆盖状态条所需的 `all`、`online`、`degraded`、`offline`、`usb_anomaly`、`upgrade_pending`、`health_anomaly`、`mount_anomaly` 及 `aligned_agent_count`。
3. 针对 150 Agent host 规模，前端实施者建议默认页容量为 `50` 或 `100`；Agent host 的脚本在位明细、完整挂载列表等重字段仍按 `2026-11-23-frontend-ui-governance-contract.md` §5.1 在右侧 `HostDetailDrawer` 打开时按需查询，后端实施者不得在列表接口中重新塞回全量脚本明细。

---

## 5. `GET /api/v1/plan-runs/{run_id}/devices` 双视图分离契约（解 `#83`，保护 Minimap）

> **核心不变量**：`PlanRunDetailPage` 中的 Minimap 设备矩阵必须始终呈现该 PlanRun 的全量设备状态，任何分页优化不得将 Minimap 截断为单页。

### 5.1 轻量全集 Minimap 视图

1. 后端实施者必须提供不分页的轻量全集投影端点 `GET /api/v1/plan-runs/{run_id}/devices/minimap`（或同义参数 `?view=minimap`，定稿时二选一，不得双权威并存）。
2. 轻量全集投影中每条设备记录仅可以包含 Minimap 渲染与筛选计数所需的最小字段集合：`job_id`、`device_id`、`device_serial`、`host_id`、`ui_status`、`job_exec_status`、`device_link_status`、`log_signal_count`，并同响应返回全集口径 `facets`（`total`、`by_status`、`by_link_status`、`by_host`）。
3. 后端实施者不得在 Minimap 轻量投影中序列化 `disarm_results`、长错误栈或完整时间线明细。

### 5.2 表格真分页视图与单机详情按需加载

1. 后端实施者必须为 `GET /api/v1/plan-runs/{run_id}/devices` 扩展可选参数 `limit`、`offset`（或 `skip`）、`q`（按设备串号过滤）、`sort_by`、`sort_dir`；缺省未传 `limit` 时暂时维持既有全量行为，待前端切完后再设默认分页上限。
2. 前端实施者在表格视图下必须按需请求当前页（默认 `limit=50`），在 Minimap 视图下必须请求 §5.1 的轻量全集投影；点击 Minimap 方块或表格行打开 `DeviceDetailDrawer` 时，前端实施者必须按 `job_id` 按需拉取单机完整详情（复用或扩展 `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}`）。

---

## 6. 迁移顺序与验收门槛

### 6.1 分步向后兼容顺序

1. **阶段 B1（后端纯只读化，先解 `#3325`）**：后端实施者先移除 `GET /devices` 与 `GET /hosts` 的同步写库与 N+1 懒加载，验证查询耗时与零 `COMMIT` 回归测试。
2. **阶段 B2（后端扩展分页与投影端点）**：后端实施者按 §3–§5 新增可选查询参数、`facets` 与 Minimap 轻量投影端点，同步更新 `frontend/src/utils/api/types.ts`（遵循 `add-api-endpoint` SOP），此时旧前端路径保持完全兼容。
3. **阶段 B3（前端切换消费与规模验收）**：前端实施者依次将 `DevicesPage`、`HostsPage`、`PlanRunDetailPage` 切换至服务端真分页与 Minimap 双视图；`PlanExecutePage` 设备选机池的批量拉取策略同步评估是否复用服务端筛选。

### 6.2 规模验收门槛

进入生产放量前，实施者与复核者必须共同核验以下四项指标：

1. 后端回归测试证明 `GET /api/v1/devices` 与 `GET /api/v1/hosts` 在存在超时主机时产生 **0 次** SQL `UPDATE` / `COMMIT`。
2. 3,750 设备模拟夹具下，`GET /api/v1/devices?limit=50` 单次响应体不超过 `100 KB`、p95 延迟低于 `200 ms`。
3. 单 PlanRun 1,000 设备模拟夹具下，Minimap 轻量投影响应体不超过 `100 KB`，且 `PlanRunDetailPage` Minimap 完整渲染全部 1,000 个方块。
4. `DevicesPage` 与 `HostsPage` 切换服务端分页后，`stp-ui-review` 实机核验双视口（`1440×900` / `1280×800`）与 `tests/test_frontend_filter_workbench_guard.py` 全部通过。
