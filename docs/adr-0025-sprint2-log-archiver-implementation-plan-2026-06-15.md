# ADR-0025 Sprint 2 实现计划：Agent 日志归档调度器（LogArchiver + 磁盘溢出 + 去重集成规划）

> 日期：2026-06-15
> 关联：[ADR-0025](./adr/ADR-0025-phase4-architecture-alignment.md) D4、ADR-0018（Watcher/Puller/ArtifactUploader）、project-vision 第 7 步「日志回传导出」+ 原则 2「结果到报告/JIRA」
> 预计：核心 5-8 天；去重集成为后续独立子阶段（先规划）
> 方向：Option B（ADR D4 原样，每日扫描 + 归档 NFS + 溢出），去重集成既有工具 `stability_Start-Log-Scan`

---

## 1. 背景与勘察结论

### 1.1 目标

让真实专项产生的**完整运行日志**可被控制平面取回（支撑 JIRA 提单），并把 Agent 本地 1TB 盘在长跑无人值守下约束在安全水位——对应 vision 第 7 步与原则 2。

### 1.2 勘察结论（代码佐证，2026-06-15）

| 事实 | 证据 | 影响 |
|------|------|------|
| pipeline **已**在每 Job 终态把 run 日志 tar 成 `{log_dir}.tar.gz` | `pipeline_engine.py:499-561`，调用 `:1314` | 归档动作已存在，缺的是目的地与注册 |
| 该 tar 落 **agent 本地盘**，URI 为 `file://本地路径`，仅存进 RUN_COMPLETE 快照（非 JobArtifact） | `config.py:42` LOG_DIR 本地；`pipeline_engine.py:543`；`agent_api.py:850-853` | 控制面取不到运行日志包 |
| 控制面下载强制产物在 NFS 根下，否则 `ArtifactPathOutsideRootError` | `artifact_paths.py:57-68` | file:// 本地路径下载必失败 |
| puller crash 文件（AEE/VENDOR_AEE）走 NFS → JobArtifact → 可下载 | `agent_api.py:1402` `/jobs/{id}/artifacts` | 与运行日志包形成**不对称**：崩溃文件能拿、整包拿不到 |
| **无**任何代码把本地 `logs/runs`/tar.gz 复制到 NFS | grep 仅命中本地 tar | 运行日志包停留本地、最终丢失 |
| **无**本地 `logs/runs` 保留/清理策略 | grep 零命中 | 本地盘无界增长 |
| Agent 无 APScheduler，周期任务用 daemon 线程 + interval（如 OutboxDrainer） | `main.py:491-498` | 归档调度走 interval 线程，非 cron |

**两个真实 gap**：
- **G1 运行日志包不可达**（主缺口）：整包日志卡在 agent 本地盘，控制面取不回 → JIRA 提单缺料。
- **G2 本地盘无界增长**：长跑无人值守下 1TB 本地盘会被占满。

### 1.3 方向决策

用户选定 **Option B（ADR D4 原样）**：新建 Agent 侧 `log_archiver.py`（周期扫描 + 归档 NFS + 溢出）+ `local_disk_monitor.py`。**去重**参考既有成熟工具 `F:\automation-toolkit\python-tools\stability_Start-Log-Scan`，本 Sprint 仅规划集成路径，实际合入为后续独立子阶段（见 §5）。

> 与勘察的调和：pipeline 已逐 Job tar，LogArchiver **复用**该 tar（存在则搬运、不存在则补 tar），避免双重打包；并补齐「落 NFS + 注册 JobArtifact + 本地保留/溢出」三件缺的事。

---

## 2. NFS 布局与数据流

```
本地（agent，1TB）                          NFS（STP_WATCHER_NFS_BASE_DIR / STP_NFS_ROOT，14TB）
BASE_DIR/logs/runs/<job_id>/               {nfs_base}/archives/<YYYY-MM-DD>/<job_id>/
  init_<step>.log                            <job_id>.tar.gz          ← LogArchiver 搬运/打包
  patrol_<step>.log            ──归档──▶      manifest.json           ← 归档元数据(sha256/size/原始路径)
  teardown_<step>.log
BASE_DIR/logs/runs/<job_id>.tar.gz         （puller crash 文件已独立在 {nfs_base}/jobs/<job_id>/，不重复搬）
```

数据流：
```
LogArchiver(interval 线程)
  扫描 logs/runs/<job_id>/  ← 仅"已完成且过 grace"的 job 目录
  → 复用/补打 tar.gz
  → 写 NFS archives/<date>/<job_id>/  (路径在 allowed root 下 → 控制面可解析)
  → POST /agent/jobs/{job_id}/artifacts (artifact_type=RUN_LOG_BUNDLE)  ← 经 ArtifactUploader 异步
  → local_db 标记 archived_at + nfs_uri
  → prune 本地 logs/runs/<job_id>/ 与本地 tar.gz
LocalDiskMonitor(interval 线程)
  本地盘使用率 ≥ 阈值 → 提前触发 LogArchiver 对最旧已完成 job 溢出 + prune
控制面
  GET /agent/{host_id}/archive-status   ← 查看归档进度/积压
  runs.py 下载端点                       ← 经 NFS JobArtifact 取回整包(已可达)
```

---

## 3. 工作项

### S2.1 — `backend/agent/log_archiver.py`（新建，核心）

`LogArchiver` 进程级单例 + daemon interval 线程（仿 `OutboxDrainer` 形态）：

| 能力 | 说明 |
|------|------|
| 扫描 | 遍历 `RUN_LOG_DIR`（`config.py:42`）下 `<job_id>/` 目录 |
| 完成判定 | job_id **不在** `local_db.get_active_jobs()` 活跃集合 **且** 目录 mtime 早于 `archive_grace_seconds`（默认 1800s）→ 安全归档（§6） |
| 打包 | 若 `<job_id>.tar.gz` 已由 pipeline 生成则复用；否则 `tarfile` 补打 |
| 落 NFS | 写 `{nfs_base}/archives/{date}/{job_id}/<job_id>.tar.gz` + `manifest.json`（sha256/size/源路径/job 元数据） |
| 注册 | 经 `ArtifactUploader.submit(...)` 异步 POST `/jobs/{job_id}/artifacts`（`artifact_type=RUN_LOG_BUNDLE`），幂等由后端 `(job_id, storage_uri)` 保证 |
| 标记 | `local_db.mark_job_archived(job_id, nfs_uri, sha256)` |
| 清理 | 注册成功 + 标记后 `shutil.rmtree(logs/runs/<job_id>)` + 删本地 tar.gz |
| 指标 | 经心跳上报 `archived_total / pending_archive / spilled_total / archive_failed`（Gauge/Counter，仿 outbox 积压上报） |

配置（env）：`STP_LOG_ARCHIVE_ENABLED`（默认 true，随 watcher 子系统）、`STP_LOG_ARCHIVE_INTERVAL_SECONDS`（默认 3600 hourly 扫描，仅处理过 grace 的目录 → 等效"每日"但延迟有界）、`STP_LOG_ARCHIVE_GRACE_SECONDS`（默认 1800）。

> **调度说明**：ADR D4 写"每日定时"，但 Agent 无 APScheduler。采用 hourly interval 线程只处理"已完成 + 过 grace"目录，归档延迟 ≤1h（优于严格每日，且即时性更利 JIRA）。如确需严格每日，加 `STP_LOG_ARCHIVE_DAILY_AT` 时刻门控。

### S2.2 — `backend/agent/local_disk_monitor.py`（新建）

| 能力 | 说明 |
|------|------|
| 监控 | interval 读本地盘使用率（复用 `system_monitor.get_disk_usage(str(BASE_DIR))`，注意按 BASE_DIR 所在盘而非 `/`） |
| 阈值 | `STP_LOCAL_DISK_SPILL_THRESHOLD`（默认 80%）超阈触发提前溢出 |
| 溢出 | 调 `LogArchiver.spill_oldest(target_free_pct)`：对最旧的已完成 job 强制归档 + prune，直到回落到 `STP_LOCAL_DISK_SPILL_TARGET`（默认 70%）|
| 边界 | 永不溢出活跃 job 目录；溢出仍走"归档→注册→prune"完整链，不裸删 |

### S2.3 — `backend/agent/main.py`（集成）

在 watcher 子系统启用块（`main.py:475-505` 附近）内 configure + start：
```
LogArchiver.instance().configure(local_db, nfs_base_dir, artifact_uploader=ArtifactUploader.instance(), ...).start()
LocalDiskMonitor.instance().configure(local_db, archiver=LogArchiver.instance(), base_dir=BASE_DIR, ...).start()
```
shutdown 段（`main.py:853+` finally）加 `.stop(timeout=...)` 收尾，仿 `log_signal_drainer`。

### S2.4 — `backend/api/routes/agent_api.py`（新增只读端点）

`GET /agent/{host_id}/archive-status`：返回该 host 归档概览
```json
{ "pending_archive": int, "archived_total": int, "spilled_total": int,
  "last_archive_at": iso, "local_disk_pct": float, "failed": int }
```
鉴权复用 `_verify_agent` / 或用户态读（与现有 agent 端点一致）。数据源：心跳上报的归档指标 + （可选）DB 中 RUN_LOG_BUNDLE 类型 JobArtifact 计数。

### S2.5 — `backend/agent/registry/local_db.py`（归档标记）

新增 `job_archive` 表或在 active_job 旁加列：`job_id PK, archived_at, nfs_uri, sha256, spilled(bool)`。方法：`mark_job_archived(...)` / `is_job_archived(job_id)` / `list_unarchived_completed()` / `count_pending_archive()`。建表走幂等 ALTER（仿 `log_signal_outbox.dead_letter` 的 idempotent ALTER 模式）。

### S2.6 — JobArtifact 注册复用

- 复用 `/jobs/{job_id}/artifacts`（`agent_api.py:1402`）+ `ArtifactUploader`。
- **需扩白名单**：`_ARTIFACT_TYPE_WHITELIST`（`agent_api.py` 附近）加 `RUN_LOG_BUNDLE`，否则 422 拒绝。
- storage_uri 必须在 allowed root（`{nfs_base}/archives/...` 在 `STP_WATCHER_NFS_BASE_DIR` 下 → 通过 `resolve_local_artifact_path`）。

---

## 4. 「已完成 Job」判定（关键正确性）

归档/溢出只能作用于**确定不再写入**的 job 目录，否则会归档到一半的日志或与活跃 watcher/pipeline 竞争：

1. job_id **不在** `local_db.get_active_jobs()`（Agent 权威活跃集合）
2. **且** 目录 mtime 早于 `archive_grace_seconds`（覆盖 teardown 收尾 + outbox flush 窗口）
3. （可选增强）下次心跳响应可带 job 终态确认；首版用 1+2 已足够保守

溢出场景额外：按目录 mtime 升序选最旧，逐个归档直至回落目标水位。

---

## 5. 去重集成规划（后续独立子阶段，先规划不实现）

### 5.1 既有工具能力（`stability_Start-Log-Scan`，V2.0.9）

- 扫描 MTK AEE/TNE 日志，识别 13 类异常（JE/NE/ANR/KE/KE-API/EE combo·modem·scp/SWT/HWT/HANG_DETECT/OCP/HW reboot）
- **相似度去重**：`config_aee_tne.json` `aee_similar_ratio=90 / tne_similar_ratio=90`，`dedup_type` 支持 `shanghai`/`factory`
- 出 Excel：`*_org.xls`（原始）+ 去重后 `.xls`
- **离线去重**：`-dedup_org <org.xls> [-side shanghai|factory]`，无需重扫
- CLI **已内建平台集成参数**：`-pipeline <id>` / `-uuid` / `-nas` / `-start_time` / `-device_count` / `-project` / `-build` / `-end`（最后一次扫描）/ `-m 5`（Platform Monkey）
- 环境：Windows + Python 3.7/3.8 + xlwt/xlrd，PyInstaller 打包

### 5.2 集成定位：控制平面，而非 Linux Agent

**理由**：工具是 Windows 原生 + 重模块（modules/analyse/aee 解析器 + decompress 工具），而 Agent 在 Linux；且 vision 原则 2 的「结果到报告/JIRA」本就属控制平面职责（`runs.py` 已有 jira-draft）。在 Agent 内嵌该工具会引入跨平台移植负担，得不偿失。

### 5.3 集成缝（规划）

```
Agent LogArchiver 把整包日志归档到 NFS archives/<date>/<job_id>/   (Sprint 2 核心)
        │
        ▼
控制平面（Windows）后处理（后续子阶段）：
  [触发] PlanRun 终态自动 + PlanRun 详情页「重跑去重」手动按钮兜底
        │
  subprocess 调用 start_log_scan.py
    -d_abs <NFS archives 路径> -m 5 -p <place> -pipeline <plan_run_id> -uuid <run uuid> -end
    → 产出 Result_*_org.xls → -dedup_org ... -side shanghai → 去重后 .xls
        │
  [回填] 去重 .xls 存为可下载 JobArtifact + PlanRun 详情页挂链
        │
  [人工审核闸口] 运维下载 .xls 人工复核 —— 不自动提单
        │
  平台「.xls 上传接口」← 上传经审核的 .xls → 喂 stability_Jira-Automation 提单
        │
  （远期）待人工审核标准沉淀为可量化逻辑后，再接 scan→dedup→提单 全自动
```

### 5.4 集成决策（2026-06-15 确认）

**已定**：
- **触发点**：PlanRun 终态自动触发 + PlanRun 详情页「重跑去重」手动按钮兜底
- **回填形态**：去重 `.xls` 存为可下载 JobArtifact 并在 PlanRun 详情页挂链；**不自动提单**——设**人工审核闸口**：运维下载复核后，经平台「`.xls` 上传接口」喂 `stability_Jira-Automation` 提单。待人工审核标准沉淀为可量化代码逻辑后，再接 scan→dedup→提单全自动流程。

**已定稿（2026-06-16，见 `docs/adr-0025-dedup-integration-design-2026-06-16.md`）**：
- 调用形态：**subprocess + 工具自带解释器**（Py3.7/3.8 + xlwt/xlrd；非 import/非 exe，依赖隔离、工具零改动）
- `.xls` 上传接口：4 端点（dedup/scan · dedup/status · dedup/jira-upload · dedup/jira-result）；上传经复核 .xls → stage1 `--add-main-excel` → stage2 批量建单（默认 `dry_run=true` 第二道闸口）
- place/side 配置：**部署级 env**（STP_DEDUP_PLACE / STP_DEDUP_SCAN_TAG / STP_JIRA_VENDOR），不加 Host/Plan schema（单站点；升级路径已记录）
- 与 `runs.py` jira-draft 关系：**共存互补**（jira-draft=report_json 轻量草稿；去重链=MTK 原始日志重型批量提单），不替换

**待用户拍板的运营选择（默认值已给，不阻塞设计）**：首接厂商（transsion/tinno/moto）｜stage2 默认 dry_run=true｜终态自动触发范围（建议全局 env 开关默认开）

> 本 Sprint 不动该工具；仅保证归档产物（NFS 整包）是其可直接消费的输入。人工审核闸口是当前阶段的有意设计——全自动化是审核逻辑可量化后的远期目标。

### S2.7 — 清理 `_archive_logs` 的 file:// 死快照（已确认纳入）

现状：`pipeline_engine._archive_logs`（`:499-561`）在 Job 终态把本地 tar 的 `file://本地路径` 作为 artifact 塞进 RUN_COMPLETE 快照（`agent_api.py:850-853`）。该 URI 既不在 NFS 根下、控制面也物理读不到 → 是一条**永远下载失败的死产物**。

改动：
- 保留 `_archive_logs` **生成本地 tar** 的能力（LogArchiver 复用之，§S2.1）
- **停止**把 file:// storage_uri 作为 artifact 上报进完成快照（移除 `pipeline_runner.py:74` 的 `"artifact": result.artifact` 死 URI 透传，或置空 storage_uri）
- 权威可下载产物改为 LogArchiver 注册的 `RUN_LOG_BUNDLE` JobArtifact（NFS uri）
- 测试：完成快照不再含不可达 file:// URI；下载链路只认 NFS JobArtifact

---

## 6. 测试计划

| 用例 | 文件 | 内容 |
|------|------|------|
| 归档 happy path | `backend/agent/tests/test_log_archiver.py`（新建） | 造已完成 job 目录 → 归档到临时 NFS + 注册 artifact（mock uploader）+ 标记 + prune 本地 |
| 跳过活跃 job | 同上 | job_id 在 active 集合 / 未过 grace → 不归档 |
| 复用已有 tar | 同上 | `<job_id>.tar.gz` 存在时复用不重打 |
| 注册幂等 | 同上 | 重复归档同 job → 后端 `(job_id,storage_uri)` 幂等，不重复 |
| 磁盘溢出 | `test_local_disk_monitor.py`（新建） | mock 磁盘使用率超阈 → 触发 spill_oldest → 最旧 job 归档+prune 直至回落 |
| 溢出不碰活跃 | 同上 | 活跃 job 永不被溢出 |
| archive-status 端点 | `backend/tests/api/` | 返回结构 + 鉴权 |
| 白名单 | `backend/tests/api/` | RUN_LOG_BUNDLE 入白名单后注册成功，非法类型 422 |
| 回归 | — | `python -m pytest backend/agent/tests/` + 后端全过 |

---

## 7. 风险与开放问题

| 风险/问题 | 缓解 |
|-----------|------|
| 归档到一半的活跃 job 日志 | active 集合 + grace 双重判定（§4）；溢出永不碰活跃 |
| NFS 写阻塞/不可用拖住归档线程 | 归档在独立 daemon 线程；NFS 失败仅记错+留本地待下轮重试，不删本地（先注册成功才 prune） |
| prune 误删未成功上传的日志 | 严格顺序：写 NFS → 注册 artifact 成功 → 标记 → 才 prune；任一失败保留本地 |
| 与现有 `_archive_logs` file:// 快照重复 | LogArchiver 复用其 tar；file:// 死快照清理已确认纳入（§S2.7） |
| Agent 无 cron，"每日"语义 | hourly interval + grace 近似，延迟有界（§S2.1 调度说明） |
| 去重工具 Windows/重依赖 | 定位控制平面，不入 Linux Agent（§5.2） |
| 本地盘按哪个挂载点算使用率 | `get_disk_usage(BASE_DIR 所在盘)`，非 `/`（S2.2 已注明） |

**待用户/后续确认**：
1. ~~是否清理 `_archive_logs` 的 file:// 本地快照~~ —— **已确认纳入**（§S2.7）
2. 去重子阶段触发点与回填形态（§5.4）—— **已确认**：终态自动+手动重跑；Excel 作产物 + 人工审核闸口 + 平台 .xls 上传接口喂 Jira-Automation（不自动提单）

---

## 8. 关键代码索引

| 位置 | 作用 |
|------|------|
| `backend/agent/config.py:41-50` | LOG_DIR / RUN_LOG_DIR / get_run_log_dir |
| `backend/agent/pipeline_engine.py:499-561,1314` | 既有 `_archive_logs`（复用对象） |
| `backend/agent/artifact_uploader.py` | fire-and-forget 上报（注册复用） |
| `backend/api/routes/agent_api.py:1402` | `/jobs/{id}/artifacts` 端点 + 白名单 |
| `backend/core/artifact_paths.py:57-68` | NFS 根校验（归档路径必须满足） |
| `backend/agent/system_monitor.py:71` | get_disk_usage（磁盘监控复用） |
| `backend/agent/main.py:475-505,853+` | watcher 子系统 configure/shutdown 集成点 |
| `F:\automation-toolkit\python-tools\stability_Start-Log-Scan\start_log_scan.py` | 去重工具入口（§5 集成对象） |
