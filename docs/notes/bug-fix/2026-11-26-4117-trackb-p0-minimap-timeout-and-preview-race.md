# Track B P0 修复：PlanRun 轻投影超时一致性（Q5）与跨页预览代次防护（Q3）

Status: implemented
Class: bug-fix
Related: #83, #496, #4117

## Decision

针对 Track B 独立复核中发现的两项 P0 阻塞问题：

1. **PlanRun 轻投影补齐超时输入并修复 `disarm_guard` 500（Q5）**：
   - 在 `backend/services/plan_run_devices.py` 的 `_load_lightweight_plan_run_rows` 中引入 `_LightweightTimeoutJobStub` 与 `_minimal_patrol_pipeline_def`：
     - 显式携带 `id=int(row.job_id)`，消除 `running_heartbeat_deadline` → `job_guard_deadline` → `guard_owner_valid` 读取 `job.id` 时的 `AttributeError` 500；
     - 补充查询 `last_patrol_heartbeat_at`、`current_patrol_step`、`updated_at`，并在 SQL 层仅提取 `jsonb_typeof(pipeline_def->'lifecycle'->'patrol')` 与 `pipeline_def->'lifecycle'->'patrol'->'interval_seconds'` 两个轻量标量，不拉取完整 `pipeline_def` 大 JSONB；
     - 使 Minimap、分页 `/devices`、旧全量 `/devices` 与单 Job 详情共用完全一致的 `running_heartbeat_deadline` / `running_heartbeat_timeout_seconds` 语义；
     - `build_plan_run_devices` 调用 `_load_lightweight_plan_run_rows(..., include_stuck=False)`，跳过非当页行不需要的 `stuck_job_ids` 预计算（当页行在 `_hydrate_device_matrix_items` 中按完整 `JobInstance` 精确计算），避免分页查询触发无谓的逐行护栏校验。
2. **设备页与主机页跨页预览增加代次 + 筛选作用域双校验（Q3）**：
   - 在 `frontend/src/pages/devices/DevicesPage.tsx` 与 `frontend/src/pages/hosts/HostsPage.tsx` 中引入单调递增的 `previewRequestSeqRef` 与当前筛选作用域引用 `activeFilterScopeKeyRef`，并在 `crossPagePreviewEntry` 状态上绑定产生时的 `scopeKey`；
   - 筛选条件变化、清空选择、切回当前页勾选、批量写操作结束或组件卸载时均递增代次并复位 loading，晚到的旧 `selection-preview` 响应（成功或失败）静默丢弃，不会复活旧集合、误弹提示或向写接口提交旧筛选 ID。

## Alternatives

- **Q5 直接在 `_load_lightweight_plan_run_rows` 查询完整 `JobInstance.pipeline_def` JSONB**：实现更短，但 1,000 Job 场景会把数百 KiB~数 MiB 的步骤定义全量拉回应用进程，违背 B2-3 窄投影设计初衷；改为 SQL 提取两个标量即可 100% 复现 `has_patrol_lifecycle` 与 `_patrol_interval_seconds`。
- **Q3 仅用 `AbortController` 取消请求**：`api.*.selectionPreview` 当前未透传 `signal`，且仅靠取消信号仍可能遇到回调在状态切换瞬间已入微任务队列的边界；采用单调代次 ref + `scopeKey` 绑定状态派生，既无 API 签名破坏，又从数据结构层面杜绝跨筛选误读。

## Verification

- 隔离 PostgreSQL 后端回归：`backend/tests/api/test_plan_run_b2_3_minimap_pagination_job_83.py`（新增 `disarm_guard` + 有效 `DeviceLease` 四端点 200 一致性测试，以及长周期巡检 `interval_seconds=600` 超时判定三端一致测试）与 `backend/tests/api/test_plan_run_stuck_alignment.py`、`backend/tests/services/test_plan_run_devices.py`。
- 前端竞态回归：`frontend/src/pages/devices/DevicesPage.test.tsx` 与 `frontend/src/pages/hosts/HostsPage.test.tsx`（均以挂起 Promise 模拟筛选切换后晚到旧预览回包，验证不复活旧集合且不提交旧 ID）。
- 仓库门禁：`scripts/run_gates.py check:quick`。

## Revisit

- 若后续 `running_heartbeat_timeout_seconds` 新增读取 `JobInstance` 或 `pipeline_def` 的其他字段，需同步扩展 `_LightweightTimeoutJobStub` 与 `_minimal_patrol_pipeline_def`（或在 `job_timeout_config.py` 中收口为共享只读协议类型）。
- 后续 Fix PR 2 将继续关闭 Q2（设备列表 `tags` 载荷上界）、Q4（主机列表挂载明细裁剪与动态排序翻页一致性）及 Q7（`#3514` 规模验收归档）。
