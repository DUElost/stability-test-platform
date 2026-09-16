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

### merge 产物表头契约（#2256）

本仓库**按列名**消费工具产出的 merge 报告，不写死列序号——工具已实证会做列名归一
（UNISOC 输入输出为 MTK 形态：`ExpType` 带尾随空格、`DeviceCount` → `DeviceId`，
见 ADR-0032 §B3 spike 的抽样比对）。按名依赖共两处：

| 消费点 | 依赖列 | 取不到时 |
|---|---|---|
| `dedup_scan._rewrite_merge_report_paths_to_center` | `Path` | 该 xls 不重写中心路径，记 WARNING `merge_report_path_column_missing`（带实际表头） |
| `dedup_extract._event_dir_names_from_xls` | `Path`、`Detail`（可选） | 返回空集，记 WARNING `dedup_xls_path_column_missing`（带实际表头） |

两处都容忍大小写、首尾空白与列位置移动。**改名、删列、以及 `str.strip()` 不剥的
不可见字符（如 BOM `U+FEFF`）会命中 WARNING**——「表头漂移」与「本轮确实无数据」不得
只能靠人工比对区分。工具升级或新增平台时，**输出表头比对**是验收项（ADR-0032 §Revisit）。

已登记的两处**不阻断**面（知道就好，不要当成新故障）：

- `_rewrite_merge_report_paths_to_center` 的整段失败（读/写 xls 异常）只记
  `merge_report_rewrite_*_failed`，**不阻断发布**——中心副本的 Path 列仍是 Agent 本机
  路径（链接不可达），发布与登记照常；
- 列整体缺失时该 xls 被跳过（见上表），其余 xls 继续处理。

### 本机中转清理的引用判据（#2281）

控制面本机 `{工具目录}/merge_result/{ts}/` 是工具唯一可写位置：发布到中心并登记后
立即删除（I-13 方案 A），失败残留由 `sweep_stale_local_merge_outputs` 兜底。
兜底清理要求**三条同时成立**，缺一即保留：

1. 中心已配置（`resolve_shared_storage_root()` 可解析）；
2. **没有** `plan_run_artifact` 行指着该目录（`local_merge_artifact_refs`）——
   中心配置**之前**登记的 run 其 artifact 指向本机目录（`_publish_merge_to_center`
   返回 `None` 时的回退分支），对它们是「唯一副本」而非中转；
3. 目录形态像工具产物（含 `Result_MergeFiles*.xls`）且 mtime 超过
   `_MERGE_LOCAL_RETENTION_HOURS`（24h）。

第 2 条的查询失败时**跳过本轮清理**（留残留优于毁交付物）；被保留与被删除的目录都在
日志里留名（`merge_local_sweep_kept_referenced` / `merge_local_stale_swept`）。

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
- **对口径同时是下游展示口径**（#2271）：轮询屏障与 `run_context.archive` / 前端
  阶段判定都看 `units_satisfied / units_expected`——只有屏障用对口径、展示用 host 级
  数字时，「host 期望 2 平台、只交付 1 平台」会在前端显示 ok（假绿）；
- `hosts_with_artifacts` 保留为 host 级计数，但语义是「**在期望平台内**有产物的
  host 数」（交非期望平台产物不算完成），按 host 去重、不按产物文件数；
- `hosts_expected` 是「有期望平台映射的 host 数」——`hosts_triggered` 是它的**超集**
  （无平台映射的 host 不进 `expected`），展示面拿 triggered 当分母会永远追不上
  （#2271 的永久 warn）；
- 只统计本轮 `expected` 内的 host（=本轮 triggered）；
- 只统计 `since` 之后登记的产物；
- 无采集/扫描实现的平台（如 QCOM）不产生期望——它们无法产出 scan 产物，不让 PlanRun 空等。

零产物记录 `saq_scan_no_artifacts`（ERROR），部分产物记录
`saq_scan_partial_artifacts`（WARNING）。两者都写
`PlanRun.run_context.archive` 的 `hosts_triggered`、`hosts_expected`、
`hosts_with_artifacts`、`units_satisfied`、`units_expected` 与
`scan_artifacts_registered`；`result_summary.scan_failed` **每轮显式重写**
（true/false 都写：#2271 之前只写 true，一次零产物轮次后即使补齐也永久显示
「扫描未产生任何报表」），判据是**对口径**的「该交的 (host, 平台) 一个都没交」。

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
| scan / merge | `{root}/dedup/{run_id}/`；merge 发布到 `merge/`，按平台执行时落 `merge/{platform}/` |
| extract | `{root}/jira/{run_id}/` |

`merge/{platform}/` 是**平台分区**形态：下游 `dedup_extract._merge_uri_is_platform_partitioned`
按 `PlanRunArtifact.storage_uri` 的**路径形状**判定平台，并据此决定 jira bundle 的落点
（#766）。发布路径与登记路径必须同形——登记落 flat 而产物在分区目录时，下游会静默
合并到错误位置。

中心存储根只配置 `STP_AEE_NFS_ROOT`。`STP_AEE_LOCAL_ROOT` 是按机 L1 路径，不由
hot-update 覆盖。`job_id IS NULL` 的 orphan signal 不进入 PlanRun watcher-summary；
管理员从 `GET /api/v1/log-signals/orphans` 查询。
