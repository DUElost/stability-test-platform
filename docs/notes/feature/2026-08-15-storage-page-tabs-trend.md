# 共享存储健康页分页布局与 7 天趋势

Status: implemented
Class: feature

## Decision

`/storage` 前端全宽（`PageContainer fullBleed`），节点详情拆为
「控制平面 / 中心存储机」两个页签（`role=tab` 组件态，非路由），
每个页签保留该节点完整监控字段：

- 控制平面：主机负载（CPU/内存/Load1m/核心数/总内存/运行时间/
  磁盘读写/网络收发）+ 客户端挂载（路径/源/状态/写权限）+ Prometheus。
- 中心存储机：容量四卡（可用容量/存储使用率/NFS 请求/Agent 挂载）+
  NFS 服务（状态/Export/服务线程/RPC 错误/Stale handle）+
  存储机主机负载 + Prometheus。

趋势图恢复三线（存储容量 + 控制面 CPU + 内存），新增 6H/24H/7D
范围切换：后端 `hours` 上限 24 → 168，采样步长约 72 点
（6h→5m、24h→20m、168h→140m）。Prometheus 保留时长设为 7 天
（`--storage.tsdb.retention.time=7d`）。

Agent 挂载 + 设备日志盘合并表保持全宽全局可见。后端契约不变
（仅 `hours` 上限放宽）。

## Alternatives

- 单页全字段堆叠：信息密度过高，页签按角色拆分更聚焦。
- 路由级页签（URL 参数）：当前无深链需求，组件态即可。
- 每个页签各配趋势图：需为存储机新增 CPU/内存 range 序列，
  同源过渡期与控制面重复，暂缓。

## Verification

- frontend：`npm run type-check` / `npm run lint` / vitest
  （含范围切换与页签切换用例）/ `npm run build`
- backend：`test_stats.py::TestFileServerOverview`、
  `test_file_server_monitor.py`（13 用例）
- Prometheus：`/api/v1/status/flags` → `storage.tsdb.retention.time=1w`

## Revisit

- 中心存储迁离 8.202 后（#205）：容量/NFS 请求趋势切存储机口径；
  如需存储机自身 CPU/内存趋势，为历史结构增加 storage 序列。
- 7 天保留若数据量增长过快，考虑 `retention.size` 兜底。
