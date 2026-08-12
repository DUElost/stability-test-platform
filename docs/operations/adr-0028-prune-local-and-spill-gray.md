# #217 — PRUNE_LOCAL 与 HddSpill 灰度备忘

> **最后更新**：2026-08-12  
> **Issue**：[#217](https://github.com/DUElost/stability-test-platform/issues/217)

## 不变量

| 项 | 约定 |
|----|------|
| `STP_EVENT_UPLOADER_PRUNE_LOCAL` | **默认 0**；**禁止**写入控制面 fleet 同步 / hot-update 下发 |
| 风险 | 上送 patch 成功后删本机目录；若 CIFS 事后不可读，本地已无副本 |
| 状态 | 实现为上送成功后 `REMOTE`→`PRUNED`（本地删）。控制面 extract/`count_pending` 把 `PRUNED` 视为已上送且可提取（`remote_path` 仍在 CIFS） |
| HddSpill | 只 enqueue `state=LOCAL`；路径走 EventUploader（`devices/{plan_run_id}/` 或 `unassigned/{id}/`）；SSD fallback 禁用 |

## 单机 prune 灰度

1. 选双盘 host（`STP_AEE_LOCAL_ROOT` 在 HDD，如 `172.21.8.143`）。
2. Agent `.env` 增加 `STP_EVENT_UPLOADER_PRUNE_LOCAL=1`（勿写控制面 `.env.backend`）。
3. `POST .../hosts/{id}/reload-config`（env 即时生效，不必重启）。
4. 跑一轮带 AEE 的 Plan；确认新事件最终 `state=PRUNED`、本机 `local_path` 目录已删、CIFS `remote_path` 仍在；extract 仍能打包。
5. 回滚：设回 `0` + `reload_config`。

## Spill 压测（人工降阈值）

生产盘往往远低于 95%。临时：

1. Agent `.env`：`STP_LOCAL_DISK_SPILL_THRESHOLD=5`（或低于当前 `df` 已用%）、`STP_LOCAL_DISK_MONITOR_INTERVAL_SECONDS=30`。
2. **重启** Agent（threshold 在 `configure()` 时固化，reload_config 不够）。
3. 确保该 host 有 `state=LOCAL` 的 DLE（可合成小目录 + ingest）；观察 `hdd_spill_enqueue_event_uploader`。
4. 恢复阈值（建议生产 95）并重启。
