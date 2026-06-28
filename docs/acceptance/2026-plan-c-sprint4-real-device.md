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
| 后端版本 | `c7a38c7`（终验时）；Agent 未热更新（`--no-hot-update`） |
| 后端地址 | `http://172.21.10.25:8000` |
| Agent Host ID | `auto-fdaf1d55e319` |
| Agent IP | `172.21.10.36` |
| 设备序列号 | `11914404BG100577`, `11914404BG102162`, `121512542H004524` |
| NFS/CIFS 挂载点 | `Y:\sonic_tinno` (控制面) / Agent 侧同路径挂载 |
| scan tool 路径 | `stability_Start-Log-Scan_20260615/start_log_scan.py`（15.4 CIFS） |
| 操作人 | Rin |
| Smoke 日期 / PlanRun | 2026-06-24 / **#41**（§八） |
| 管道终验日期 / PlanRun | **2026-06-27 / #52**（§九，签字依据） |

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

### AC-R-01：start_log_scan.py -m 0 全量扫描 + -dedup_org 去重

**前置**：scan tool 已部署；Agent 已 configure ScanRunner。

**说明**：代码实际执行两阶段：① `-m 0 -d {hdd}` 全量扫描 ② `-dedup_org {org.xls}` 去重。文档模板中 AC-S4-01 已更新为 `-m 0`。

**步骤**：
1. 触发 scan（终态自动 或 `POST /api/v1/plan-runs/{id}/dedup/scan`）
2. 查看 Agent 日志

**期望**：Agent log 出现 `scan_runner_start` 和 `dedup_runner_start`，两次 subprocess 退出码均为 0。

**实际结果**：

| 项 | 值 |
|----|-----|
| plan_run_id | **52** |
| Agent log 关键行 | `scan_runner_start` / `dedup_runner_start` → SUCCESS（`sprint4_real_device_verify.py` 自动判定） |
| subprocess 退出码 | **0** |
| 结果 | ☑ PASS  ☐ FAIL |

**证据**（日志片段）：
```
（粘贴 scan_runner_start / scan_runner_success 日志）
```

---

### AC-R-02：_org.xls + _dedup_org_*.xls 产出到 HDD

**步骤**：
1. scan 完成后，检查 HDD 目录

**期望**：`{hdd_root}/Result_*_org.xls` 和 `{hdd_root}/Result_*_org_dedup_org_*.xls` 均存在。

**实际结果**：

| 项 | 值 |
|----|-----|
| _org.xls 路径 | `Y:\sonic_tinno\dedup\52\auto-fdaf1d55e319_..._052222_org.xls` |
| 文件大小 | **1074688** bytes |
| mtime | `2026-06-27`（与 dedup_org 同批次） |
| 结果 | ☑ PASS  ☐ FAIL |

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
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（跳过，单测覆盖 AC-S4-02） |

---

## 二、Agent 上送 NFS（UploadManager）

### AC-R-04：scan 报告上送到 NFS dedup/

**步骤**：
1. scan 成功后，检查 NFS 目录

**期望**：`{nfs_root}/dedup/{plan_run_id}/{host_id}_Result_*_org.xls` 和 `{host_id}_Result_*_org_dedup_org_*.xls` 均存在。

**实际结果**：

| 项 | 值 |
|----|-----|
| NFS dedup 路径 | `Y:\sonic_tinno\dedup\52\` |
| 文件名 | `auto-fdaf1d55e319_..._org.xls` + `_org_dedup_org_20260627_052224.xls` |
| host_id 前缀正确 | ☑ 是  ☐ 否 |
| 结果 | ☑ PASS  ☐ FAIL |

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
| NFS devices 路径 | `Y:\sonic_tinno\devices\52\` |
| 事件目录数 | **233** |
| 目录名示例 | `2026_0603_063411_640_db.43.JE` |
| 结果 | ☑ PASS  ☐ FAIL |

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
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（跳过，单测 AC-S4-06 / P1-4） |

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
| plan_run_id | **52** |
| triggered 数 | **1**（单 Agent） |
| skipped 数 | **0** |
| ONLINE Agent 数 | **1** |
| 结果 | ☑ PASS  ☐ FAIL |

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
| SQL | `SELECT host_id, storage_uri FROM plan_run_artifact WHERE plan_run_id=52 AND artifact_type='scan_result_xls'` |
| 行数 | **≥2**（多次 scan 批次；终验见 dedup/52） |
| host_id 值 | `auto-fdaf1d55e319` |
| 结果 | ☑ PASS  ☐ FAIL |

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
| SQL | `SELECT storage_uri FROM plan_run_artifact WHERE plan_run_id=52 AND artifact_type='merge_result_xls'` |
| 行数 | **≥1** |
| storage_uri | `...\merge_result\Result_MergeFiles*.xls`（DB 路径可能为历史 merge；CIFS dedup/52 有 fresh 产物） |
| 结果 | ☑ PASS  ☐ FAIL |

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
| scan 前 | pending | 2026-06-27 |
| scan 后 | scanned | 2026-06-27 |
| merge 后 | **merged** | 2026-06-27 |
| 结果 | ☑ PASS  ☐ FAIL |

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
| PlanRun 终态 | **SUCCESS** |
| 自动 enqueue | ☑ 是  ☐ 否 |
| 后端 log | `saq_scan_start plan_run=52`（终态自动 + 脚本 `dedup/scan`） |
| 结果 | ☑ PASS  ☐ FAIL |

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
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（未在本轮真机单独测；API 已实现） |

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
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（跳过） |

---

### AC-R-14：增量 re-scan 限频（P1-3 回归，2026-06-27 更新）

**步骤**：
1. Plan 有 **RUNNING** PlanRun 且 `auto_archive_interval_seconds` 已设
2. 首次增量 scan 完成后，在 interval 内观察 sweep
3. interval 过后再观察是否 enqueue 下一次增量 scan
4. PlanRun **终态且已有 scan artifact** 后，确认 sweep **不再** enqueue

**期望**：
- RUNNING：interval 内不重复 enqueue `:inc` scan；interval 过后可再 enqueue
- 终态：仅首次 `is_final=True`；已有 `scan_result_xls` 后 sweep 永久 skip 该 run

**实际结果**：

| 项 | 值 |
|----|-----|
| 首次 scan 时间 | _________ |
| 第二次 sweep 时间 | _________ |
| 距上次 scan | _________ s |
| 是否 enqueue | ☐ 否（正确）  ☐ 是（错误） |
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（跳过） |

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
| merged | 已合并（API `scan_status=merged`） | — |
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（未 UI 目视；API 状态正确） |

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
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（未 UI 目视） |

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
| artifact_type 标注 | `scan_result_xls`, `merge_result_xls`（API `/dedup/status`） |
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（未 UI 目视；extract 经 API 已 PASS） |

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
| jira 路径 | `Y:\sonic_tinno\jira\52\` |
| 拷贝目录数 | **233**（含后续 extract 增量） |
| 目录名示例 | `2026_0603_063411_640_db.43.JE` |
| 结果 | ☑ PASS  ☐ FAIL |

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
| 结果 | ☐ PASS  ☐ FAIL  ☑ N/A（单 host） |

---

## 八、前置 Smoke 验证（2026-06-24）

> Sprint 4 scan/upload/merge 管道验证前的基础 smoke，确认 Agent + 设备 + 调度主链可通。

### 环境

| 项 | 值 |
|----|-----|
| Host ID | `auto-fdaf1d55e319` |
| Host IP | `172.21.10.36` |
| Host 状态 | ONLINE |
| 设备 1 | ID=99, serial=`11914404BG100577` |
| 设备 2 | ID=63, serial=`11914404BG102162` |
| 设备 3 | ID=62, serial=`121512542H004524` |
| Plan | ID=5, name=`smoke-plan-001` |
| PlanRun | ID=41 |
| 操作人 | `_________` |
| 验证日期 | 2026-06-24 |

### 主链 smoke 结果

| 项 | 值 | 结果 |
|----|-----|------|
| PlanRun 41 状态 | SUCCESS | ✅ PASS |
| Job 57 | COMPLETED | ✅ PASS |
| Job 58 | COMPLETED | ✅ PASS |
| Job 59 | COMPLETED | ✅ PASS |
| 设备最终状态 | 三台均 ONLINE, adb_state=device | ✅ PASS |
| active lease | 0（全部释放） | ✅ PASS |

### 异常记录

| 项 | 说明 |
|----|------|
| Plan #7 误用 | 无 `timeout_seconds`，持续 patrol；已 abort → PlanRun 40 = FAILED/ABORTED，lease 经 reconciler 释放 |

### Sprint 4 管道验证阻塞 — 已全部解除 ✅

| 项 | 状态 | 说明 |
|----|------|------|
| `POST /plan-runs/41/dedup/scan` | ✅ 已修复 | scan tool 配置完成 |
| Agent .env | ✅ 已修复 | 追加 `STP_DEDUP_SCAN_PYTHON/SCRIPT`、`STP_AEE_LOCAL_ROOT`、`STP_AEE_NFS_ROOT` |
| `start_log_scan.py` | ✅ 已确认 | 15.4 CIFS 上 `stability_Start-Log-Scan_20260615` 可用 |
| `plan_run_artifact` (Run 45) | ✅ 6 artifacts | 3 scan + 3 dedup |
| scan tool 依赖 | ✅ openpyxl, pymysql, xlrd, xlwt 已安装 |
| Reconciler HDD 写入 | ✅ `job_session.py` 改 `get_aee_local_root()` |
| scan 模式 | ✅ `-m 5` 改为 `-m 0` (AEE_TNE, 无需 DB) |

### 新增能力（验证期间补充）

| 项 | 说明 |
|----|------|
| scan → dedup 两阶段 | ScanRunner 新增 `run_dedup_org()`；`_scan_and_upload` 先 scan 再去重 |
| reconfigure 热更新 | `POST /hosts/{id}/reload-config` → SocketIO → `configure(force=True)` |
| HDD 溢出阈值 | 80% → 95%（仅接近满时才 spill 到 CIFS） |

---

## 九、Sprint 4 管道终验（PlanRun #52，2026-06-27）

> 一键脚本：`python backend/scripts/sprint4_real_device_verify.py --no-hot-update`  
> Jira extract：`python backend/scripts/jira_extract_run52.py`  
> Jira API smoke：`python backend/scripts/test_jira_api.py`

### 主链

| 项 | 值 | 结果 |
|----|-----|------|
| PlanRun | **#52** | ✅ |
| 终态 | **SUCCESS**（3 设备，~7 min） | ✅ |
| Jobs | j87 / j88 / j89 → **COMPLETED** | ✅ |
| 设备 | ID 99 / 62 / 63（10.36 三台） | ✅ |
| `scan_status` | **merged** | ✅ |
| dedup/52 | `_org.xls` 1.0 MB + `_dedup_org` 247 KB | ✅ |
| devices/52 | **233** 事件目录 | ✅ |
| jira/52 | **233** 事件目录 + 4 份 xls | ✅ |

### Jira 提单（Transsion，1 条 dry-run）

| 项 | 结果 |
|----|------|
| 本地 `jira_upload_list_dry_run_verify.py` | ✅ upload_list + create `--dry-run` |
| `POST /api/v1/jira/runs` upload_list | ✅ SUCCESS（修复：绝对路径 + 厂商 env） |
| `POST /api/v1/jira/runs` create dry-run | ✅ SUCCESS，`CREATE_NEW`，未写库 |
| Tinno | ⚠️ venv 缺 `xlrd`；非 Transsion 生产路径，记已知限制 |

### 已知限制（不阻塞 #30）

| 项 | 说明 |
|----|------|
| Agent 热更新 | `POST .../hot-update` 30s 超时；用 Ansible 或 `--no-hot-update` |
| merge_result DB 路径 | 可能指向历史 `merge_result/2026_06_25_*`；Run 52 以 CIFS `dedup/52` 为准 |
| 前端 UI 回归 | AC-R-15～17 未目视；API `scan_status` / artifacts 已正确 |

---

## 签字

| 项 | 值 |
|----|-----|
| 验证总结 | ☑ 全部 PASS（核心管道 + Transsion Jira dry-run）  ☐ 有 FAIL 项 |
| 操作人 | Rin |
| 签字日期 | 2026-06-27 |
| 备注 | 关 [#30](https://github.com/DUElost/stability-test-platform/issues/30)；Tinno 提单待 venv 补 `xlrd` |

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-06-24 | 初版真机联调验证记录模板 |
| 2026-06-27 | PlanRun #52 管道终验签字；§九 Jira dry-run；关 #30 |
