# Phase 2 Track B 阶段 B2-3：PlanRun 双视图分离、表格真分页与单 Job 详情

Status: implemented
Class: feature
Related: #83, #4117, #4151, #4152, #4155, #4159

## Decision

按已定稿的 `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（v1.0，§5 / §6.2 / §7.3），在保持既有 `GET /api/v1/plan-runs/{run_id}/devices` 完全向后兼容的前提下，落地 PlanRun 设备矩阵的双视图分离与单 Job 详情端点（`B2-3`），并同步前端类型、API 客户端、Query Keys 与响应形状双向对拍（不切换旧版 `PlanRunDetailPage`）：

1. **独立轻量全集 Minimap（`GET /api/v1/plan-runs/{run_id}/devices/minimap`）**：
   - 新增独立端点（不引入同义参数 `?view=minimap`），直接以窄列联表投影构造全量设备格子，每条严格仅含 7 个命名字段（`job_id`、`device_id`、`device_serial`、`host_id`、`job_exec_status`、`device_link_status`、`log_signal_count`），删除旧回退字段 `ui_status`、不加载 `disarm_results` / `DeviceLease` / 长错误栈；
   - 同响应返回全集 `facets`（`total` / `by_status` / `by_link_status` / `by_host`）、按主机去重的显示名映射 `hosts`（包含退役主机及无主机桶 `unassigned`）与全局 `stuck_job_ids`；
   - 在 40 host × 25 device = 1,000 Job 声明夹具下实测原始 UTF-8 JSON 严格 `≤ 384 KiB`（另行记录 gzip 体积）。
2. **表格真分页与聚合 KPI 并集筛选（`GET /api/v1/plan-runs/{run_id}/devices`）**：
   - 未传 `skip` / `limit` 时保留全量返回与既有 `total`（PlanRun 全集数）；
   - 新增 `skip`、`limit`、`q`（按设备串号大小写不敏感子串过滤）、`sort_by`（白名单，非法值 `422`）、`sort_dir` 及 `filtered_total` 字段（绝不覆盖 `total`）；
   - 补齐两张聚合 KPI 并集筛选：`status=lost_or_backoff` / `status=unknown,backoff` 等价于 `unknown OR backoff`；`link_status=abnormal` / `not_online` 等价于 `device_link_status != online`；
   - 改为先窄投影计算全局 facets、过滤、排序并切片，再仅对当前页的 `job_id` 加载 `DeviceLease` / `disarm_results` 构造重 `DeviceMatrixItem`。
3. **单 Job 详情与延迟加载子查询**：
   - 新增独立路由模块 `backend/api/routes/plan_run_devices_v2.py`（保持 `plan_runs.py` 不越过 507 行文件上限）与服务 `backend/services/plan_run_job_detail.py`：
     - `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}`：严格校验 `(run_id, job_id)` 归属（错配 `404`），返回单机完整详情（含 `capabilities`、`disarm_results`、`step_trace_count`、`artifact_count`）；
     - `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}/timeline`：有上限阶段时间线子查询（截断长错误栈、不含 `output` 全文）；
     - `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}/watcher-signals`：有上限 Watcher 异常明细子查询（支持 `category` 过滤、不含 `first_lines` 原文）。
4. **前端类型与双向对拍同步**：
   - 同步更新 `frontend/src/utils/api/types.ts`、`planRuns.ts`、`queryKeys.ts`（`devicesMinimap` / `devicesPage` / `jobDetail` / `jobTimeline` / `jobWatcherSignals`），并在 `tests/test_api_response_shape_contract.py` 登记全部 8 个新响应模型及新路由文件 `plan_run_devices_v2.py`。

## Alternatives

- **在 `GET /plan-runs/{run_id}/devices` 上增加 `?view=minimap` 参数**：拒绝，会制造单一端点的双权威载荷并违背契约 §5.1 规则 1。
- **把新路由直接堆进 `backend/api/routes/plan_runs.py`**：拒绝，`plan_runs.py` 已在 503/507 行上限边缘；拆出 `plan_run_devices_v2.py` 既遵守文件规模门禁又清晰隔离 B2-3 新增面。
- **在 `GET /jobs/{job_id}` 响应中直接内嵌完整 `StepTrace.output` 与 `JobLogSignal.first_lines`**：拒绝，存在单 Job 数万行日志拖垮抽屉的风险；按契约 §5.3 规则 3 拆为按需延迟加载的有上限子查询。

## Verification

- 定向 pytest：`backend/tests/api/test_plan_run_b2_3_minimap_pagination_job_83.py`（含 1,000 Job Minimap 载荷字节门禁）、既有 PlanRun 设备/聚合/形态测试、`tests/test_api_response_shape_contract.py`
- 前端 `npm --prefix frontend run type-check`
- 仓级快门禁 `./scripts/project_python.sh scripts/run_gates.py check:quick`

## Revisit

- 阶段 `B3-3` 切换 `PlanRunDetailPage` 与 `DeviceDetailDrawer` 消费 `getDevicesMinimap`、分页 `getDevices` 及 `getJobDetail` / `getJobTimeline` / `getJobWatcherSignals` 时，验证 1,000 格渲染、邻居预取上限（≤2）与抽屉缓存按 `(run_id, job_id)` 隔离。
