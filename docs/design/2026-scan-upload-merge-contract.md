# Scan / Upload / Merge 跨进程契约

本文记录控制面与 Agent 之间无法从单个模块推导的设备日志归档契约。总体时序见
[`06-realtime-and-background.md`](./06-realtime-and-background.md) §9，存储角色见
[`2026-storage-roles-and-aliases.md`](./2026-storage-roles-and-aliases.md)。

## 控制面 merge

`backend/services/dedup_scan.py:run_merge_sync` 读取中心存储 `dedup/`：

1. `scan_tool_supports_merge_files_list()` 运行一次 `start_log_scan.py -h` 并缓存结果；
2. 支持时写同轮临时清单，使用 `-merge_files_list {listfile}`；
3. 工具过旧、脚本缺失或探测失败时抛 `RuntimeError`，不回退到 `-merge_files`；
4. `STP_DEDUP_SCAN_TAG` 含 `factory` 时传 `-side factory`，否则传
   `-side shanghai`。

## SAQ 链和完备性

```text
scan_task → upload_task → merge_task → extract_task
```

`upload_task` 只把 scan xls 引用的 LOCAL 事件标为 `UPLOAD_PENDING`。Agent
EventUploader 是唯一复制执行者，轮询后 copytree 到中心存储，并负责重试、校验、
PRUNE 和 HDD spill force。

`scan_task` 在下发 `scan_now` 前记录 `since` 水位线，随后按**可配置预算**轮询等待
（`STP_SCAN_POLL_INTERVAL` 默认 10s、`STP_SCAN_POLL_MAX_WAIT` 默认 300s，另可按 host 数叠加
`STP_SCAN_POLL_PER_HOST_SECONDS`；近齐时给一次 near-complete grace，`STP_SCAN_POLL_GRACE_*`；
#732）。等待超时仍会 enqueue 后继，避免单台慢 host 把部分报告变成零报告。

完备性由 `dedup_scan.scan_completeness(run_id, expected, since=...)` 判断，其中
`expected` 为 `{host_id: {平台分区}}`，由
`plan_run_scan_scope.load_expected_scan_platforms` 按**各 host 在本 PlanRun 中的设备
平台构成**派生：

- 完备性单位是 **(host, platform) 对**：纯 MTK host 只被要求 `mtk`、纯 UNISOC host
  只被要求 `unisoc`、混平台 host 才要求两者都到（ADR-0032 B1「MTK/UNISOC **分区
  各自**完备性判定」）；
- `hosts_with_artifacts` 是 host 级口径（`run_context.archive` 与前端「host 完成度」），
  按 host 去重、不按产物文件数；`units_*` 只用于轮询屏障；
- 只统计本轮 `expected` 内的 host（=本轮 triggered）；
- 只统计 `since` 之后登记的产物；
- 无采集/扫描实现的平台（如 QCOM）不产生期望——它们无法产出 scan 产物，不让 PlanRun 空等。

零产物记录 `saq_scan_no_artifacts`（ERROR），部分产物记录
`saq_scan_partial_artifacts`（WARNING）。两者都写
`PlanRun.run_context.archive` 的 `hosts_triggered`、`hosts_with_artifacts` 和
`scan_artifacts_registered`。

## Fleet env 与热刷新

控制面 scan 工具只读 `STP_BACKEND_DEDUP_SCAN_*`。Agent 的
`STP_DEDUP_SCAN_*` 与 `STP_UNISOC_*` 分别由控制面
`STP_AGENT_DEDUP_SCAN_*`、`STP_AGENT_UNISOC_*` 映射下发。

Agent `STP_NFS_ROOT` 由 `STP_AEE_NFS_ROOT` 镜像；不得下发控制面本机
`STP_NFS_ROOT`。`_FLEET_ENV_KEYS` 只包含两侧同值的键。热更新先合并 Agent `.env`
再重启，并回报 `AGENT_PATH_ENV_KEYS` 缺失项；热更新返回前不要并发触发
`reload_config`。

`POST /api/v1/plan-runs/hosts/{host_id}/reload-config` 通过 SocketIO 让 Agent 重读
安装目录 `.env`。Agent 侧刷新的组件见 `backend/agent/AGENTS.md`。

## 风险与链接健康

风险摘要由 `backend/services/log_observation.py:aggregate_risk_summary` 计算：
DeviceLogEvent 是权威计数，未链接 `job_log_signal` 只作补充。

| 等级 | 条件 |
|---|---|
| S | SWT、Fatal NE、Fatal JE、HWT、Kernel/KE、HW Reboot、HANG 任一非零 |
| A | ANR ≥ 10、JE ≥ 3、NE ≥ 2 或 Java ≥ 3 |
| B | 其他非零 |

`GET /plan-runs/{id}/watcher-summary` 的 `archive.link_stats` 将信号分成 linked、
unlinked_fixable、not_yet_archived。链接故障只看
`fixable_link_rate = linked / (linked + unlinked_fixable)`；分母为零时是 1.0。
`fixable_link_rate < 1.0` 或 `unlinked_fixable > 0` 时检查
`signal_link_reconcile_done` 和 `backend/scheduler/signal_link_reconciler.py`。
`not_yet_archived` 高表示归档及时性问题，不是链接故障。

终态事件使用 `GET /plan-runs/{id}/log-events`；RUNNING 仍使用 watcher-summary 和
`job_log_signal`。

## 中心存储路径

| 对象 | 路径 |
|---|---|
| JobArtifact | `{root}/jobs/{job_id}/` |
| 事件目录 | `{root}/devices/{plan_run_id}/` 或 `{root}/devices/unassigned/{event_id}/` |
| scan / merge | `{root}/dedup/{run_id}/`，merge 发布到 `merge/` 子目录 |
| extract | `{root}/jira/{run_id}/` |

中心存储根只配置 `STP_AEE_NFS_ROOT`。`STP_AEE_LOCAL_ROOT` 是按机 L1 路径，不由
hot-update 覆盖。`job_id IS NULL` 的 orphan signal 不进入 PlanRun watcher-summary；
管理员从 `GET /api/v1/log-signals/orphans` 查询。
