# 共享存储健康页：拆根后双目标与 CIFS 适配（#205）

Status: implemented
Class: feature

## Decision

B8 G0-b 把日志根切到外部 CIFS（`/mnt/stp-log` → 15.4）后，健康页仍用日志根查
控制面本机 `/var/lib/nfs/etab`，并按 `used_pct ≥ 90%` 报容量 critical，造成
`NFS_EXPORT_MISSING` / 「NFS 服务：异常」/ `CAPACITY_CRITICAL` 误报。本单最小收敛：

1. **NFS 导出对准控制面根**：`filesystem ∈ {cifs,smb,smb3}` 或分源且路径不同于
   `resolve_control_plane_storage_root()` 时，etab 检查与 nfsd 就绪判定改打控制面根
   （如 `/mnt/stp-aee`），响应里用 `nfs.scope=control_plane_root` + `export_path`
   标明；日志根健康只看 `mounted && backend_write_access` 与 Agent 挂载统计。
2. **外部 NAS 无采集器降级**：已设 `STP_AEE_SHARE_ADDRESS` 且未设
   `STP_STORAGE_NODE_JOB` 时，右栏 `unmonitored_reason=external_share_no_collector`，
   展示「未配置采集器（外部共享存储）」，**不**发 `STORAGE_METRICS_UNAVAILABLE`；
   配了非法 job 名仍告警。容量趋势仍可从控制面客户端挂载点取。
3. **容量阈值对齐 G0-c**：新增
   `STP_SHARED_LOG_CAPACITY_{WARNING,CRITICAL}_AVAIL_GIB`；未设时盘 `<2 TiB`
   回退百分比 80/90，盘 `≥2 TiB` 自动 800/300 GiB，避免 13 TB 盘剩余 >1.2 TiB
   时常驻 `CAPACITY_CRITICAL`。
4. **前端**：左栏在切盘后展示「控制面根 NFS 导出」；右栏展示中心存储地址与
   日志根容量，无采集器时降级文案；`types.ts` 向后兼容可选字段。

## Alternatives

- 把整页改成只盯 15.4：被 #205 否决，会丢掉控制面负载与本机导出监控。
- 分源未配 job 继续常驻 `STORAGE_METRICS_UNAVAILABLE`：现场 15.4 无
  node_exporter 是预期态，常驻 warning 与「服务故障」不可区分。
- 只改百分比阈值（如 95%）：大盘上百分比口径仍与 G0-c 绝对可用字节不一致。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_file_server_monitor.py backend/tests/api/test_stats.py -q`（48 passed）
- `frontend`: `tsc --noEmit`、`eslint`（touched）、`vitest run src/pages/storage/FileServerPage.test.tsx`（9 passed）
- `./scripts/project_python.sh tools/dev/env_inventory.py --check`
- `./scripts/project_python.sh scripts/run_gates.py check:quick`（16 gates）
- `stp-ui-review`：`up --sha cd028109f` → `/storage` 控制平面 / 中心存储机截图（`/tmp/ui-review-probe-205/`）→ `down`

## Revisit

- 站点 `env.backend` 上线窗口补 `STP_AEE_SHARE_ADDRESS=172.21.15.4`（及可选
  容量绝对阈值）；15.4 若日后接入 node_exporter，再设 `STP_STORAGE_NODE_JOB`。
- 若产品要把右栏 NFS 指标改刮存储机而非展示控制面根摘要，需 15.4 侧协议与
  scrape target 就绪后再收紧。
