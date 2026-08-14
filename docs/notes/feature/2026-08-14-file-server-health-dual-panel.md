# 共享存储健康页双栏（控制面 + 存储机，过渡期同源）

Status: implemented
Class: feature

## Decision

`/storage`（`GET /api/v1/stats/file-server`）从单机视角拆成两个面板，
是 #205 的过渡期形态：中心存储迁离 8.202 之前，两栏同源显示
「控制面 debian13 (8.202)」与「存储机 8.202（与控制面同机）」。

- 左栏 `control_plane`：控制面主机负载（CPU/内存/load/磁盘 IO/网卡）
  与对 `STP_AEE_NFS_ROOT` 的客户端挂载状态。
- 右栏 `storage_server`：盘容量/inode、服务端 NFS（export/线程/RPC/stale）、
  存储机自身负载，以及 Agent 挂载合规；`same_source` 为真时标注「与控制面同机」。
- 数据源按 Prometheus job 分栏：左栏用 `STP_CONTROL_PLANE_NODE_JOB`
  （回落 `STP_FILE_SERVER_NODE_JOB` / `file-server`）；右栏用
  `STP_STORAGE_NODE_JOB`。右栏未配 job 且 `STP_AEE_SHARE_ADDRESS` 已设时，
  不回退刮控制面，而是报 `STORAGE_METRICS_UNAVAILABLE` —— 防止「只改地址、
  不刮新机」的假分源（#205 明令禁止）。
- 容量/历史曲线仍取自控制面客户端挂载近似（#205 认可的近似口径），
  切盘后才有必要改成刮存储机本机文件系统。

契约变化：`FileServerOverview` 顶层 `server/storage/system/nfs/monitoring`
折叠进两个面板，前端 `FileServerPage` 与 `types.ts` 同步重写。

## Alternatives

- 保持单机视角、切盘时只改 `STP_FILE_SERVER_ADDRESS`：被 #205 否决，
  会丢掉控制面负载或误报导出端。
- 现在就为「未来 15.4」加独立的 `storage-server` Prometheus job：
  目标机还不存在，属于无效提前量。
- 新增第二个路由/页面：被产品约定否决，健康页永远挂控制面单页双栏。

## Verification

- `backend/tests/services/test_file_server_monitor.py`（同源、分源、
  分源未配 job 三个用例）
- `backend/tests/api/test_stats.py::TestFileServerOverview`
- `frontend/src/pages/storage/FileServerPage.test.tsx`
- `python scripts/run_gates.py check:pr` 8 门禁全绿

## Revisit

中心存储迁离 8.202 时（#205 启动）：设 `STP_AEE_SHARE_ADDRESS` +
`STP_STORAGE_NODE_JOB`，新存储机装 node exporter、Prometheus 加
`storage-server` target；届时把容量/历史查询切到存储机本机文件系统，
并考虑把左栏 job 改名为 `control-plane`。
