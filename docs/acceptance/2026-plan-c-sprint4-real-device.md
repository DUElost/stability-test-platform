# 真机联调验证记录：方案 C Sprint 4

- **PRD**：[2026-plan-c-storage-and-archive.md](../prd/2026-plan-c-storage-and-archive.md)
- **设计**：[2026-plan-c-storage-and-access.md](../design/2026-plan-c-storage-and-access.md)
- **管道时序**：[06-realtime-and-background.md §9](../design/06-realtime-and-background.md)
- **验收矩阵**：[2026-plan-c-sprint4.md](./2026-plan-c-sprint4.md)
- **跟踪**：[GitHub #30](https://github.com/DUElost/stability-test-platform/issues/30)

> 本文档为 Sprint 4 真机联调验证记录模板。每项检查点需填写：实际结果、证据（命令输出/截图/日志片段）、操作人、时间。
> 验证全部通过后，作为 #30 真机签字依据，勾选验收矩阵发版勾选最后一项。

---

## 环境信息

| 项 | 值 |
|----|-----|
| 后端版本 | `git rev-parse HEAD` = _________ |
| Agent 版本 | `git rev-parse HEAD` = _________ |
| 后端地址 | `http://_________:8000` |
| Agent Host ID | `_________` |
| Agent IP | `_________` |
| 设备序列号 | `_________` |
| NFS/CIFS 挂载点 | `_________` |
| scan tool 路径 | `STP_DEDUP_SCAN_SCRIPT=_________` |
| 操作人 | `_________` |
| 验证日期 | `_________` |

### 环境变量确认

```bash
# 后端
echo $STP_AEE_NFS_ROOT        # NFS 根目录
echo $STP_DEDUP_SCAN_PYTHON   # scan tool 解释器
echo $STP_DEDUP_SCAN_SCRIPT   # scan tool 脚本
echo $STP_DEDUP_AUTO_SCAN     # 终态自动触发开关

# Agent
echo $STP_AEE_LOCAL_ROOT      # Agent HDD 根目录
echo $API_URL                 # 后端 API 地址
echo $MAX_CONCURRENT_TASKS    # Agent 并发上限
echo $STP_WATCHER_ENABLED     # Watcher 开关
```

---

## 一、Agent 本地 scan（ScanRunner）

### AC-R-01：start_log_scan.py -dedup_org 执行

**前置**：scan tool 已部署；Agent 已 configure ScanRunner。

**步骤**：
1. 触发 scan（终态自动 或 `POST /api/v1/plan-runs/{id}/dedup/scan`）
2. 查看 Agent 日志

**期望**：Agent log 出现 `scan_runner_start plan_run=X host=Y final=True argv=[...]`，subprocess 退出码 0。

**实际结果**：

| 项 | 值 |
|----|-----|
| plan_run_id | _________ |
| Agent log 关键行 | `_________` |
| subprocess 退出码 | _________ |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**（日志片段）：
```
（粘贴 scan_runner_start / scan_runner_success 日志）
```

---

### AC-R-02：_org.xls 产出到 HDD

**步骤**：
1. scan 完成后，检查 HDD 目录

**期望**：`{hdd_root}/**/Result_*_org.xls` 存在，mtime 为本次 scan 时间。

**实际结果**：

| 项 | 值 |
|----|-----|
| _org.xls 路径 | `_________` |
| 文件大小 | _________ bytes |
| mtime | `_________` |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```bash
ls -la {hdd_root}/**/Result_*_org.xls
stat {org_xls_path}
```
```
（粘贴命令输出）
```

---

### AC-R-03：无 fresh 时返回 None（P1-5 回归）

**步骤**：
1. 不执行 scan tool（或模拟失败）
2. 触发 `scan_now`
3. 检查 Agent 日志

**期望**：Agent log 出现 `scan_runner_no_fresh_org_xls`，不上送文件。

**实际结果**：

| 项 | 值 |
|----|-----|
| Agent log 关键行 | `_________` |
| 是否上送 | ☐ 否（正确）  ☐ 是（错误） |
| 结果 | ☐ PASS  ☐ FAIL  ☐ N/A（跳过） |

---

## 二、Agent 上送 NFS（UploadManager）

### AC-R-04：scan 报告上送到 NFS dedup/

**步骤**：
1. scan 成功后，检查 NFS 目录

**期望**：`{nfs_root}/dedup/{plan_run_id}/{host_id}_Result_*_org.xls` 存在。

**实际结果**：

| 项 | 值 |
|----|-----|
| NFS dedup 路径 | `_________` |
| 文件名 | `_________` |
| host_id 前缀正确 | ☐ 是  ☐ 否 |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```bash
ls -la {nfs_root}/dedup/{plan_run_id}/
```
```
（粘贴命令输出）
```

---

### AC-R-05：事件目录上送到 NFS devices/

**步骤**：
1. upload_events 触发后，检查 NFS 目录

**期望**：`{nfs_root}/devices/{plan_run_id}/` 下有时间戳前缀目录（`YYYY-MM-DD_HH-MM-SS_*`）。

**实际结果**：

| 项 | 值 |
|----|-----|
| NFS devices 路径 | `_________` |
| 事件目录数 | _________ |
| 目录名示例 | `_________` |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```bash
ls -la {nfs_root}/devices/{plan_run_id}/
```
```
（粘贴命令输出）
```

---

### AC-R-06：自动发现只选时间戳目录（P1-4 回归）

**步骤**：
1. 在 HDD source_root 下创建非时间戳目录（如 `test_dir`）
2. 触发 upload_events（空 event_dir_names）
3. 检查 NFS devices/ 下是否有非时间戳目录

**期望**：NFS `devices/` 下只有 `YYYY-MM-DD_HH-MM-SS_*` 目录，无 `test_dir`。

**实际结果**：

| 项 | 值 |
|----|-----|
| 非时间戳目录是否被排除 | ☐ 是（正确）  ☐ 否（错误） |
| 结果 | ☐ PASS  ☐ FAIL  ☐ N/A（跳过） |

---

## 三、控制面管道（SAQ scan→upload→merge）

### AC-R-07：scan_task emit scan_now 到 ONLINE Agent

**步骤**：
1. PlanRun 终态触发或手动触发 scan
2. 查看后端日志

**期望**：后端 log `saq_scan_dispatched plan_run=X triggered=Y skipped=Z`，triggered 数 = ONLINE Agent 数。

**实际结果**：

| 项 | 值 |
|----|-----|
| plan_run_id | _________ |
| triggered 数 | _________ |
| skipped 数 | _________ |
| ONLINE Agent 数 | _________ |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**（后端日志）：
```
（粘贴 saq_scan_dispatched 日志）
```

---

### AC-R-08：NFS poll 注册 DB（PlanRunArtifact）

**步骤**：
1. scan_task poll NFS 完成后，查询 DB

**期望**：`plan_run_artifact` 表有 `scan_result_xls` 行，行数 = host 数。

**实际结果**：

| 项 | 值 |
|----|-----|
| SQL | `SELECT host_id, storage_uri FROM plan_run_artifact WHERE plan_run_id=X AND artifact_type='scan_result_xls'` |
| 行数 | _________ |
| host_id 值 | `_________` |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```
（粘贴 SQL 查询结果）
```

---

### AC-R-09：merge_task 产出 merge xls

**步骤**：
1. scan_task 完成后自动 enqueue merge_task
2. merge 完成后查询 DB

**期望**：`plan_run_artifact` 表有 `merge_result_xls` 行。

**实际结果**：

| 项 | 值 |
|----|-----|
| SQL | `SELECT storage_uri FROM plan_run_artifact WHERE plan_run_id=X AND artifact_type='merge_result_xls'` |
| 行数 | _________ |
| storage_uri | `_________` |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```
（粘贴 SQL 查询结果 + 后端 merge_started/merge_artifacts_registered 日志）
```

---

### AC-R-10：scan_status 状态流转

**步骤**：
1. 分别在 scan 前、scan 后、merge 后调用 API

**期望**：`scan_status` 从 `pending` → `scanned` → `merged`。

**实际结果**：

| 阶段 | scan_status | 时间 |
|------|-------------|------|
| scan 前 | _________ | _________ |
| scan 后 | _________ | _________ |
| merge 后 | _________ | _________ |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```bash
curl -s -H "Authorization: Bearer {token}" http://{backend}:8000/api/v1/plan-runs/{id}/watcher-summary | jq '.data.archive.scan_status'
```
```
（粘贴三次 API 响应）
```

---

## 四、五触发场景

### AC-R-11：场景①终态自动

**步骤**：
1. 跑一个 PlanRun 到 SUCCESS（或 PARTIAL_SUCCESS）
2. 观察后端日志

**期望**：自动 enqueue scan_task，无需手动触发。后端 log `saq_scan_start plan_run=X final=True`。

**实际结果**：

| 项 | 值 |
|----|-----|
| PlanRun 终态 | _________ |
| 自动 enqueue | ☐ 是  ☐ 否 |
| 后端 log | `_________` |
| 结果 | ☐ PASS  ☐ FAIL |

---

### AC-R-12：场景④手动归档

**步骤**：
1. `POST /api/v1/plan-runs/{id}/archive`
2. 检查后端是否同时 emit `archive_now` + `scan_now`

**期望**：后端同时下发 `archive_now` 和 `scan_now` 到 Agent。

**实际结果**：

| 项 | 值 |
|----|-----|
| API 响应 | `_________` |
| archive_now emit | ☐ 是  ☐ 否 |
| scan_now emit | ☐ 是  ☐ 否 |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```bash
curl -s -X POST -H "Authorization: Bearer {token}" http://{backend}:8000/api/v1/plan-runs/{id}/archive
```
```
（粘贴 API 响应 + 后端日志）
```

---

### AC-R-13：场景⑤自动归档间隔

**步骤**：
1. 设置 `Plan.auto_archive_interval_seconds = 60`
2. PlanRun 终态后等待 60s+
3. 检查 `auto_archive_sweep` 是否 enqueue

**期望**：后端 log `auto_archive_sweep triggered=1`。

**实际结果**：

| 项 | 值 |
|----|-----|
| auto_archive_interval_seconds | _________ |
| sweep 间隔（env） | _________ |
| 首次 sweep enqueue | ☐ 是  ☐ 否 |
| is_final（首次） | ☐ True  ☐ False |
| 结果 | ☐ PASS  ☐ FAIL |

---

### AC-R-14：增量 re-scan 限频（P1-3 回归）

**步骤**：
1. 首次 scan 完成后，等待第二次 sweep（在 interval 内）
2. 检查是否重复 enqueue

**期望**：interval 内不重复 enqueue `:inc` scan。后端 log 无 `auto_archive_sweep triggered=N`（N > 0 的情况在 interval 内不再出现）。

**实际结果**：

| 项 | 值 |
|----|-----|
| 首次 scan 时间 | _________ |
| 第二次 sweep 时间 | _________ |
| 距上次 scan | _________ s |
| 是否 enqueue | ☐ 否（正确）  ☐ 是（错误） |
| 结果 | ☐ PASS  ☐ FAIL  ☐ N/A（跳过） |

---

## 五、前端展示

### AC-R-15：WatcherSummaryCard scan 状态 chip

**步骤**：
1. 打开 `/execution/runs/{id}` 详情页
2. 观察 WatcherSummaryCard 底部 scan 状态

**期望**：显示 `待扫描` → `已扫描` → `已合并` chip。

**实际结果**：

| 阶段 | 显示文本 | 颜色 |
|------|---------|------|
| pending | _________ | _________ |
| scanned | _________ | _________ |
| merged | _________ | _________ |
| 结果 | ☐ PASS  ☐ FAIL |

---

### AC-R-16：归档进度展示（#18 回归）

**步骤**：
1. 详情页观察 WatcherSummaryCard 存储运维区

**期望**：显示 `N 归档 · M 归档中 · K 归档失败`。

**实际结果**：

| 项 | 值 |
|----|-----|
| archived_jobs | _________ |
| pending_jobs | _________ |
| failed_jobs | _________ |
| 结果 | ☐ PASS  ☐ FAIL |

---

### AC-R-17：DedupReportCard 操作 + artifact_type 标注（#17 回归）

**步骤**：
1. 详情页观察 DedupReportCard
2. 点击 scan / merge / extract 按钮

**期望**：按钮可点击；产物列表含 `[artifact_type]` 标注（如 `[aee_crash]`、`[scan_result_xls]`）。

**实际结果**：

| 项 | 值 |
|----|-----|
| scan 按钮可点 | ☐ 是  ☐ 否 |
| merge 按钮可点 | ☐ 是  ☐ 否 |
| extract 按钮可点 | ☐ 是  ☐ 否 |
| artifact_type 标注 | `_________` |
| 结果 | ☐ PASS  ☐ FAIL |

---

## 六、extract 提单

### AC-R-18：extract 从 devices/ 拷贝到 jira/

**步骤**：
1. merge 完成后，`POST /api/v1/plan-runs/{id}/dedup/extract`
2. 检查 NFS jira 目录

**期望**：`{nfs_root}/jira/{plan_run_id}/` 下有从 `devices/` 拷贝的事件目录。

**实际结果**：

| 项 | 值 |
|----|-----|
| jira 路径 | `_________` |
| 拷贝目录数 | _________ |
| 目录名示例 | `_________` |
| 结果 | ☐ PASS  ☐ FAIL |

**证据**：
```bash
ls -la {nfs_root}/jira/{plan_run_id}/
```
```
（粘贴命令输出）
```

---

## 七、多 host 场景（如有 2+ Agent）

### AC-R-19：scan_task 多 host poll 等待（P1-1 回归）

**步骤**：
1. 2+ ONLINE Agent 的 PlanRun 终态
2. 观察后端日志 poll 循环

**期望**：后端 log `saq_scan_poll plan_run=X elapsed=Ys registered=N/M`，等待 N >= M 或超时。

**实际结果**：

| 项 | 值 |
|----|-----|
| ONLINE Agent 数 | _________ |
| registered 最终值 | _________ |
| 等待时长 | _________ s |
| 后端 log | `_________` |
| 结果 | ☐ PASS  ☐ FAIL  ☐ N/A（单 host） |

---

## 签字

| 项 | 值 |
|----|-----|
| 验证总结 | ☐ 全部 PASS  ☐ 有 FAIL 项（列出：_________） |
| 操作人 | `_________` |
| 签字日期 | `_________` |
| 备注 | `_________` |

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-06-24 | 初版真机联调验证记录模板 |
