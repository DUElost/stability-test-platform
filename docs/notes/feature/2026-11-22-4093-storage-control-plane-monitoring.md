# #4093 分源存储保持控制面 CPU/内存/存储监控与 NFS 解耦

Status: implemented
Class: feature

关联：[#4093](https://github.com/DUElost/stability-test-platform/issues/4093)、
[#205](https://github.com/DUElost/stability-test-platform/issues/205)、
PR #4084、PR #4089。

## Decision

针对「15.4 作为中心存储主机接入 `node_exporter` + `STP_STORAGE_NODE_JOB`，同时控制平面（8.202）仍须保留自身 CPU、内存、本地存储容量与历史趋势」的需求，并在实施前消除 #4093 隐患，本单完成以下改造：

1. **修复 #4093（控制面 NFS 就绪态解耦）**：
   - `backend/services/file_server_monitor.py` 在 `remote_log_share=True` 时，独立计算 `control_nfs_service_ready` 并固定赋给 `control_plane.nfs.service_ready` 与 `storage_server.nfs.service_ready`（`control_plane_root` 导出场景），不再被 `storage_job` 的 `storage_threads == 0` 覆盖。
2. **保留控制面本地存储与历史趋势**：
   - `ControlPlanePanel` 新增可选字段 `disk: Optional[FileServerStorage] = None`，复用共享采集 helper `_collect_disk_metrics` 与 `_local_mount_info`（支持目录直挂与父挂载点回溯）采集 `STP_CONTROL_PLANE_STORAGE_ROOT` 的容量、inode 与可写状态。
   - `FileServerHistory` 新增可选字段 `control_plane: Optional[FileServerNodeHistory] = None`，通过 `_node_history_queries` 持续保留控制面 `control_job` 的 `cpu_usage_pct`、`memory_usage_pct`、`nfs_requests_per_second` 与控制面根 `capacity_usage_pct`（同源同路径直接复用 `history`，不重复查询 Prometheus）。
3. **前端 `/storage` 双视角与类型同步**：
   - 同步 `frontend/src/utils/api/types.ts`（`FileServerControlPlanePanel.disk`、`FileServerNodeHistory`、`FileServerOverview.history.control_plane`）及 `index.ts` 导出。
   - `FileServerPage.tsx` 在分源且存在 `history.control_plane` 时，在「资源趋势」区提供「控制平面 / 中心存储机」切换按钮（默认展示控制平面；切换至中心存储机且 `external_share_no_collector` 时明确标注 CPU/内存未接采集器），并在「控制平面」页签渲染「本地存储（控制面根）」容量进度与路径摘要。

## Alternatives

- **直接将控制面 CPU/内存混入单条 `history`**：会在 `STP_STORAGE_NODE_JOB` 接入后再次失去 `15.4` 或 `8.202` 其中一方的趋势视图——否决。
- **直接修改既有 `history.cpu_usage_pct` 口径为控制面**：破坏已有分源契约与测试预期——否决；新增 `history.control_plane` 可选字段保持向后兼容。

## Verification

- `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python scripts/run_pytest.py backend/tests/services/test_file_server_monitor.py backend/tests/api/test_stats.py tests/test_api_response_shape_contract.py -q`
  → **67 passed**
- `npm --prefix frontend run type-check` → **0 errors**
- `npx --prefix frontend eslint frontend/src/pages/storage/FileServerPage.tsx frontend/src/pages/storage/FileServerPage.test.tsx frontend/src/utils/api/types.ts frontend/src/utils/api/index.ts --max-warnings 0` → **0 warnings**
- `npm --prefix frontend test -- --run src/pages/storage/FileServerPage.test.tsx` → **10 passed**
- `.venv/bin/python scripts/run_gates.py check:quick` → **16 gates OK**

## Revisit

- PR 合入并部署后，在生产控制面与中心存储机完成 `15.4` `node_exporter` + Prometheus `storage-server` job + `env.backend` `STP_STORAGE_NODE_JOB` 配置，并复验 `/storage` 双视角趋势切换与双面板指标。
