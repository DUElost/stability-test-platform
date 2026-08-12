# #217 — PRUNE_LOCAL 与 HddSpill 灰度备忘

> **最后更新**：2026-08-12  
> **Issue**：[#217](https://github.com/DUElost/stability-test-platform/issues/217)

## 不变量

| 项 | 约定 |
|----|------|
| `STP_EVENT_UPLOADER_PRUNE_LOCAL` | **默认 0**；**禁止**写入控制面 fleet 同步 / hot-update 下发 |
| Fleet 同步范围 | hot-update 只合并 `_FLEET_ENV_KEYS` + `STP_AGENT_*` 映射 + 安装布局键（`hot_update_env_overrides()`）。`PRUNE_LOCAL` **不在** allowlist，即使控制面 `.env` 误设也不会进入 payload |
| 风险 | 本机删成功后若 CIFS 事后不可读，本地已无副本 |
| 状态语义 | 上送成功后：先 `rmtree(local)`，**仅删除成功**再 patch `PRUNED`。`state=PRUNED` ⇒ 本地已不存在。删除失败则保持原状态（通常 `REMOTE`） |
| extract | 控制面把 `PRUNED` 与 `REMOTE`/`ARCHIVED` 一样视为可提取（`remote_path` 仍在 CIFS） |
| HddSpill | 只 enqueue `state=LOCAL`；路径走 EventUploader（`devices/{plan_run_id}/` 或 `unassigned/{id}/`）；SSD fallback 禁用 |

## Post-merge 验收清单（同一轮）

灰度 host 固定 **`172-21-8-143`**（双盘 `STP_AEE_LOCAL_ROOT=/mnt/hdd/aee_events`）。

1. **合入后** `git checkout main && pull`，**重启控制面** `stability-backend`（加载 `PRUNED` extractable 修复）。
2. **仅**在该 host Agent `.env` 设 `STP_EVENT_UPLOADER_PRUNE_LOCAL=1`（勿写 `.env.backend` / fleet）。
3. `POST /api/v1/plan-runs/hosts/172-21-8-143/reload-config`。
4. **Spill 注入**：临时 `STP_LOCAL_DISK_SPILL_THRESHOLD` 低于当前 `df` 已用% + 短 interval，**重启 Agent**；注入/确保一条 `LOCAL` DLE；确认日志 `hdd_spill_enqueue_event_uploader`，`remote_path` 为 `devices/{plan_run_id}/` 或 `unassigned/{id}/`；恢复阈值 95 并重启。
5. **Prune Plan**：跑一轮带 AEE 的 Plan（如 plan 7 / device 19）；确认新事件 `state=PRUNED`、本机 `local_path` 目录已删、CIFS `remote_path` 在、`jira/{plan_run_id}/` extract 齐全。
6. **非目标 host**：抽查另一台 `.env` **无** `PRUNE_LOCAL=1`（或显式 `0`）。
7. **回滚验证**（可选结束灰度）：该 host 设回 `STP_EVENT_UPLOADER_PRUNE_LOCAL=0` + `reload_config`；再跑一轮确认新事件停在 `REMOTE`/`ARCHIVED` 且本机目录仍在（直到其它策略清理）。

## 2026-08-12 已执行（合入前预跑）

见 [#217 评论](https://github.com/DUElost/stability-test-platform/issues/217#issuecomment-5265170834)：spill→`unassigned/…`；PlanRun **#203** `PRUNED`×10 + extract OK；host 143 当时保持 prune=1 观察。合入后按上面清单补做控制面 `main` 重启对齐。
