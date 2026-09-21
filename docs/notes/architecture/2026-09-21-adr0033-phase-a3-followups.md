# ADR-0033 Phase A3 follow-ups：DLE zip + extract_bundle + PlanRun Jira 历史

Status: implemented
Class: architecture

## Decision

落地 #3013 剩余三条 follow-up（叠在 Phase A3 最小切片之上）：

1. `GET /api/v1/plan-runs/{id}/log-events/{event_id}/download` —— DLE
   `remote_path` 文件直出 / 目录 zip；仅 `REMOTE`/`ARCHIVED`；`PRUNED` → 409。
2. `run_extract_sync` 成功后幂等登记 `PlanRunArtifact(artifact_type=extract_bundle)`；
   既有 PlanRunArtifact 下载面对目录走 zip（与 DLE 共用 `artifact_zip`）。
3. `JiraRunHistory` 支持 `planRunId`，挂到 `PlanRunDetailPage`（DedupReportCard 下方）。

不新建 ToolRun；不碰 Package Store / Phase B–C；不改 ADR 索引（避免与
#3005/#3015 冲突）。

## Alternatives

| 选项 | 为何不选 |
|---|---|
| extract 时预写 `jira/{id}.zip` 文件 | 双份磁盘；目录才是 extract 事实，下载侧 zip 更贴 |
| DLE 路由塞进 `plan_runs.py` 薄壳 | 上帝文件封顶；与 A3 artifact 下载一样挂 `scan_router` |
| 独立 Jira 卡片组件 | `JiraRunHistory` 已可复用；加 `planRunId` 最小 |

## Verification

- `python3 -m pytest backend/tests/services/test_device_log_event_download.py backend/tests/services/test_plan_run_artifact_download.py backend/tests/services/test_dedup_extract.py -q --noconftest`（extract 需 DB fixture，走完整 conftest）
- `npm --prefix frontend test -- LogEventsCard.test.tsx JiraRunHistory.test.tsx DedupReportCard.test.tsx`
- `python3 scripts/run_gates.py check:quick`

## Revisit

- DLE zip 体积上限 / 流式 zip（当前 temp file + BackgroundTask 清理）
- Package Store 仅在 §5.4 触发时启动
