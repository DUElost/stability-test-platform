# 跨区收口 · 日志链（链 B）端到端逐跳对证

- 日期：2026-09-13。
- 基线：origin/main `df4458ec0107f5c644339138c54d0f725191ef24`（2026-09-12 22:43:47 -0700）。
  本文所有 `file:line` 均相对该基线。
- 定位：落实 [`PROJECT_REVIEW_PLAN.md`](./PROJECT_REVIEW_PLAN.md) §6「跨区收口」的**链 B（日志链）**部分，
  承接[链 A 报告](./REVIEW_CROSS_REGION_CHAIN_A_2026-09-12.md) §7-1 的交接与 #1498。
  **这是静态对证 + 既有测试盘点，不是动态验证通过、不是缺陷修复完成、也不是生产验收结论。**
- 本轮只交付本文件与总纲 §5 回写；不修改业务代码、既有 ADR 与其他会话报告。

## 0. 结论

1. **链 B 六跳的接缝在代码上是闭合的**：`device_log_event`（PostgreSQL）状态机
   是贯穿跳 1–5 的持久化权威，Agent 侧 SQLite（signal outbox、DLE 注册 outbox）
   只做重放缓冲，中心存储目录（`devices/`、`dedup/`、`merge/`、`jira/`）由
   DB 行 + 完成标记共同索引；未发现日志链业务事实入 Redis。
2. **历史踩坑的修复都有测试背书**：#1071 完备性按 host×平台、#381/#1079
   `upload_mark` 水位线防假就绪、#1527 merge 全平台失败显式 raise、#1070
   extract 原子拷贝 + 完成标记、#1073 事件目录加 event_id 层、#1074 中心发布
   失败必抛、#1174 可提取态不降级、#1550 PULL_FAILED 派生汇点。
3. **最弱一环在链尾「清理后的可追溯性」**：retention 清轨（#1521 引入）只清
   `devices/` 与 `dedup/`，`jira/{run_id}/` 永不清——DB 行删除后 extract 产物
   成为无索引孤儿目录（§5 F-B1）；jira-drafts 列表先 SQL LIMIT 后 Python 过滤
   （§5 F-B2）。两处均为本轮新发现、未跟踪。
4. **实现规格（2026-08-20）与代码已漂移 9 处**（§2 各跳标注 + §5 F-B3），
   其中多数是 #287/#1073 等修复后规格未回写——R09-F09/#1050 已跟踪
   「规格描述已替代契约」，F-B3 清单可供其收口直接使用。
5. 五个必测场景中 ①③④ 有直接测试；② 关键失败分支有测试但「NFS root
   缺失的整链行为」无专项测试；⑤ 只有「行删除」测试、无「目录孤儿」断言
   ——这正是 F-B1 漏出的原因（§3 矩阵）。

## 1. 范围与方法

- 范围：`异常发现 → 本地落盘 → 事件上报 / 文件上传 → 扫描归并 → 归档 / 提取 → 报告 / JIRA / 页面消费`。
- 每跳核对五要素：生产者 / 消费者 / 持久化权威 / 失败恢复 / 完成条件，并给出对应测试或复现命令。
- 方法：只读静态对证（`file:line`）+ 既有测试盘点（**本轮未运行测试套件**）。
  证据双轨：**控制面侧**（`saq_tasks.py`、`dedup_scan.py`、`dedup_extract.py`、
  `device_log_event.py`、`agent_api.py`、`plan_runs.py`、`runs.py`、`dedup.py`、
  `cron_scheduler.py`、`log_observation.py`、`post_completion.py`、`upload_manager.py`）
  全部本会话亲读；**Agent 侧**（`aee/`、`event_uploader.py`、`local_disk_monitor.py`、
  `scan_runner.py`、`watcher/`）由探查取证，其中载荷性论断经本会话抽样亲核
  （清单见 §4）；未亲核的行号引用前请复核。
- 与既有报告的关系：R09（#1055）/ R10（#1086）台账仍是区域证据载体，本文只做
  **接缝**对证，不重述区域结论；已跟踪缺陷直接引用 issue 号，不重复立单。

## 2. 逐跳对证

### 跳 1：异常发现（设备 → Agent 判定）

1. **生产者**：设备侧 MTK `db_history`（`backend/agent/aee/db_history.py:11-48`，逗号分隔
   ≥10 字段，vendor 行要求 `/data/vendor/aee_exp/db.` 前缀且非 bootreason）；
   UNISOC uniview 目录（`unisoc_reconciler.py:210-242` 先 `ls`+pull 同步设备事件）。
2. **消费者**：MTK `AeeDbHistoryReconciler`（`backend/agent/aee/reconciler.py:272`）主循环
   `_run`（`:478-537`）：baseline 180s / 新候选触发 burst 60s×5（`:334-348`）；
   连续 tick 异常 ≥10 自关（`:390-393,504-521`）并复位 watcher 抑制位（`:1078-1092`）。
   拉取判定 `processor.py:192-213`（行不在 processed/pending 才入队），pull 重试上限 10
   （`:62,287-303`）。平台路由 `get_collector_for_platform`（`collector.py:41-62`）按
   `device.platform`；reconciler 层选择 `_resolve_reconciler_class`（`job_session.py:300-313`）：
   UNISOC→`UnisocUniviewReconciler`、QCOM→不启动。watcher 侧信号由 `SignalEmitter`
   （`watcher/emitter.py:43-139`）写本地 outbox（幂等键 `job_id+seq_no`），
   `OutboxDrainer` 5s×50 条 POST `/agent/log-signals`（`:310`）。
3. **持久化权威**：「发现了什么」的权威在设备侧 `db_history`/uniview 本身；
   传递层权威是控制面 `job_log_signal`（PG）。Agent 内存 `pending_tasks`/
   `processed_lines` 是防重队列，不是事实源。**Redis 不参与**。
4. **失败恢复**：#1044 修复后 pull/verify 失败经 `_handle_pull_failed`
   （`reconciler.py:777-852`）上报且每条 pending 只报一次（`processor.py:394-415`）；
   重试耗尽记 processed 防 #829 复活（`:287-303`）；reconciler 自关后 inotifyd
   兜底恢复（`device_watcher.py:219,224-225`，reconciler 活跃期 AEE 抑制）。
5. **完成条件**：pull 成功 + strict verify → emit AEE signal（`reconciler.py:854-953`）
   并进入跳 2 的 DLE 注册；D2 hash 未变且无 pending 时跳过（`:554-571,589-597`；
   `:590` 保证 pending 未清不跳，#1044）。

**漂移**：spec §5.2 的 `PlatformCollector.detect()`「detect=False 跳过本轮」在实现中
**零调用点**（平台判定走 `detect_device_platform`，`job_session.py:343`）；
`TriggerInfo`（`collector.py:16-20`）定义后全仓未使用。

### 跳 2：本地落盘（设备 → Agent HDD/SSD → DLE LOCAL）

1. **生产者**：`processor.py:340-355` adb pull（可中断包装 `reconciler.py:230-265`，
   `rc==0 and exists()` 判成功）；host 级写盘 slot（`processor.py:316-317`）。
   目录布局 `{local_root}/{MMDD}/{serial}/{aee_type}/{ts}_{db}/`
   （`paths.py:215-222` + `processor.py:129-145,310-314`）；`get_aee_local_root`
   判定链 `STP_AEE_LOCAL_ROOT` → SSD fallback → `/mnt/hdd/aee_events`
   （`paths.py:90-123`）。
2. **消费者**：strict verify（`processor.py:316-338`，已存在目录先校验复用）。
3. **持久化权威**：Agent 本地文件 + 控制面 `device_log_event` 行（state=`LOCAL`，
   `local_path`）。DLE 注册 `DeviceLogEventClient.create_local_event`
   （`device_log_event_client.py:84-141`，POST `/agent/device-log-events`
   payload `state="LOCAL"`）：失败写 `dle_register_outbox`（`:66-82,134,140`），
   EventUploader 每 30s 顺带 drain（`event_uploader.py:647,651-667`），
   `_MAX_REGISTER_ATTEMPTS=10` 次转死信（`:185-199`）——**R09-F01/#1042 的
   持久补偿已落地**（对照测试 `test_dle_register_outbox_1042.py` 5 例）。
   UNISOC 对应 `unisoc_reconciler.py:282-342`（category=`UNIVIEW`）。
4. **失败恢复**：pull/verify 失败/耗尽 → PULL_FAILED DLE
   （`reconciler.py:1050-1076` → `device_log_event_client.py:201-247`）。
   **不对称观察**：`create_pull_failed_event` 失败仅 log（`:237-247` 无 outbox），
   见 §5 F-B4。EventUploader 发现本地目录缺失且远端无副本也补 PULL_FAILED
   （`event_uploader.py:413-419`）。
5. **完成条件**：POST 200 且 `device_log_event.state='LOCAL'`。HddSpill 强制路径
   在水位 ≥80% 时以 `prune_after_upload=True` 入同一上传队列
   （`local_disk_monitor.py:222-249`）。

### 跳 3：事件上报 / 文件上传（Agent → 中心存储）

1. **生产者（控制面标记）**：`upload_task`（`backend/tasks/saq_tasks.py:552-622`）——
   `collect_upload_event_dir_names`（`dedup_extract.py:392-415`）从 scan xls Path 列
   解析事件目录名（`allowed_serials` 限定本 run 设备，#213 B2 废弃 signal union），
   LIKE 直写 `UPDATE device_log_event SET state='UPLOAD_PENDING'`
   （`saq_tasks.py:575-592`；`_MARKABLE_EVENT_STATES` `:48` 含在途态——
   回边 `UPLOAD_PENDING→UPLOADING` 等均在状态矩阵内，`agent_api.py:2286-2289`），
   写 `run_context.upload_mark` 轮次水位线（`:604-617`，#381）。
2. **消费者（Agent 唯一执行者）**：`EventUploader`（`event_uploader.py`）——
   `queue.Queue(512)` + `Semaphore(2)`（`:161-162`，#389 先拿 slot 再起线程）；
   30s 快速轮询 `GET /agent/device-log-events?host_id=…&state=UPLOAD_PENDING&limit=200`
   （`:608-649`；端点 `agent_api.py:2633-2654`，**只拉 UPLOAD_PENDING** `:115-126`，
   UPLOADING/UPLOAD_FAILED 交 600s 慢循环 `:669-720`——有意漂移，见 F-B3）；
   attempt 持久化（`:55-95`，#785）；上传 = patch UPLOADING（`:446`）→
   `UploadManager._copytree_safe`（`:448`）→ 自读回 sha256 比对、不一致删坏副本
   （`:449-460`，#1083）→ patch REMOTE + remote_path + checksum（`:462-464`）；
   重试 `min(300, 2^attempt)` ×5 → UPLOAD_FAILED（`:474-487`）；**REMOTE ack 成功
   才 prune**（`:465,536-606`；PRUNE 默认关，env `STP_EVENT_UPLOADER_PRUNE_LOCAL`
   按机灰度或 spill 强制）。
3. **持久化权威**：`device_log_event.state/checksum/remote_path`（PG）+ 中心目录
   `{nfs_root}/devices/{plan_run_id}/{event_id}/`（`event_uploader.py:385-394`，
   #1073 加 event_id 层——R10-F04 已修；无 plan_run 兜底 `devices/unassigned/{event_id}/`
   `:395`）。
4. **失败恢复**：三层——注册 outbox（跳 2）、recover/retry 双轮询（本跳）、
   attempt 持久化防重试上限失效。PULL_FAILED 对所有非终态源合法
   （#1550 派生汇点，`agent_api.py:2298-2312`）。
5. **完成条件**：`state ∈ _EXTRACTABLE_STATES = {REMOTE, ARCHIVED, PRUNED}`
   （`agent_api.py:2266-2268`）。

**信任边界（R09-R02/#1052 落地，全部亲核）**：`_verify_agent`（`agent_api.py:91`，
fleet 共享密钥）→ host 存在（`:2417-2419`）→ job-host 一致（`:2425-2429`）→
host/job/plan_run 三元组一致 400（`:2430-2443`）→ 身份字段不可变 403
（serial `:2494-2501`、job_id `:2502-2513`、plan_run_id `:2514-2525`）→
#1051 预分配 UUID 幂等重放（`:2453-2482`）→ #1174 可提取态不降级
（`:2526-2532`）→ 表外迁移 409 `DLE_INVALID_TRANSITION`（`:2550`）。
`remote_path` 经 `_validated_remote_path`（`:2348-2381`，含 #389 unassigned 回退）。

### 跳 4：扫描归并（scan_now → xls → merge）

**触发面**：终态自动 `should_trigger_dedup`（`dedup_scan.py:838-842`，
SUCCESS/PARTIAL_SUCCESS/FAILED）← 聚合与回收两路
（`job_terminalization.py:79-80,99-100`）；abort 路径 `plan_run_abort.py:488`；
RUNNING 增量轮由 cron（`cron_scheduler.py:469,513`，`is_final=False`）；
手动 API（`dedup.py:449,479`）。

1. **生产者（控制面下发）**：`scan_task`（`saq_tasks.py:364-549`）——
   水位线先于下发（`:379-380`）；Socket.IO `call_agent_control(host,"scan_now")`
   （`:389`，无 ack 记 `not_acked` 并落 archive `:397-401,503`）。
2. **消费者（Agent 扫描）**：`scan_runner` 单飞（`scan_runner.py:119-311`：
   enqueue 去重合并、启动窗口等待 configure、MTK/UNISOC 未配置跳过并告警）→
   `start_log_scan.py -m 0` 产 `_org.xls` → `run_dedup_org` 二次去重 →
   `UploadManager.upload_scan_report` → `{nfs}/dedup/{run}/[{platform}/]{host}_{file}`
   （`upload_manager.py:64-94`，platform 子目录 mtk/unisoc）。
3. **持久化权威**：`plan_run_artifact`（PG，`artifact_type='scan_result_xls'`）
   ← `run_scan_sync` 轮询注册（`dedup_scan.py:104-138`，扫 `dedup/{id}/`、
   `mtk/`、`unisoc/` 三目录 `:129`）。
4. **失败恢复 / 完备性**：轮询预算 #732（interval 10s、budget 300s+per-host、
   near-complete grace `saq_tasks.py:429-461`）+ 收尾重注册（`:465-475`）；
   完备性 `count_hosts_with_scan_artifacts`（`dedup_scan.py:141-208`）四维收窄：
   按 host 去重 / 本轮 triggered / since 水位 / `require_platforms`（#1071，
   `saq_tasks.py:440-443`）；零产物 ERROR、部分产物 WARNING 但仍链后继
   （`saq_tasks.py:484-496`）；`record_scan_archive_state` 用 `jsonb_set` 写
   `run_context.archive` 并在零产物时挂 `result_summary.scan_failed`
   （`dedup_scan.py:211-285`）。
5. **merge**：`merge_task`（`saq_tasks.py:758-879`）——`_run_sync_exclusive`
   进程内互斥（`:782-790`，#1123）；FAILED run skip（#697，`:797-811`）；
   **全平台失败 ERROR+raise（#1527，`:813-824`）**——SAQ 记失败并重试，
   不再静默 SUCCESS 空 report。`run_merge_sync`（`dedup_scan.py:288-431`）：
   轮次过滤 `_load_org_files_for_merge`（`:470-519`，`scan_round_id` 或
   created_at floor；拒绝无界加载 `:507-512`）；`-merge_files_list` 探测缓存
   （`:522-551`，不支持抛 RuntimeError 不回退，#291）；`-side factory/shanghai`
   （`:342-343`）；#1072 工具目录跨进程锁（`:350`）；exit≠0 与 stderr 判死
   （`:387-398`）；**#1074 中心发布失败必抛**（`:409-412`）→ 发布
   `dedup/{run}/merge/{platform}/`（`:768-809`）并登记 artifact（`:810+`）。

**漂移**：spec §2.2 的 `remote_path` 布局无 event_id 层；spec §2.3 的
UPLOAD_FAILED 重扫「`updated_at < now()-600s`」条件在 Agent 侧 GET 无对应
参数（`event_uploader.py:683-692`，仅按 600s 节奏 + attempt 持久化等效）。

### 跳 5：归档 / 提取（jira bundle + 状态收口 + 清理）

1. **生产者**：`extract_task`（`saq_tasks.py:889-904`，`_run_sync_exclusive` 互斥
   `:898`）→ `run_extract_sync`（`dedup_extract.py:178-389`）。
2. **发现**：只走 `list_remote_paths_for_extract`（`device_log_event.py:116-124`；
   `_REMOTE_STATES = {REMOTE, ARCHIVED, PRUNED}` `:24-27`——**PRUNED 仍可提取，
   #1174**）；`associate_unassigned_events_to_plan_run`（`:127-146`，job 强路径 +
   serial/时间窗回退且防抢他 run 事件 #230）。
3. **拷贝语义**：#1070 原子化——staging 目录 + `.stp_extract_complete` 标记 +
   rename（`dedup_extract.py:133-175`），半成品清除重拷（`:286-295`）；#386 同名
   只把真落盘行标 ARCHIVED、其余保 REMOTE 待人工（`:274-308`）；merge xls 平台
   分区落点 `merge/{platform}/`（#766，`:27-36`）+ legacy 撞名后缀（`:39-54`）；
   完成后 `mark_events_archived` REMOTE→ARCHIVED（`device_log_event.py:193-214`，
   PRUNED 保持）并写 `run_context.extract` 汇总（`dedup_extract.py:369-382`）。
4. **清理 / 保留**：`run_retention_cleanup`（`cron_scheduler.py:261-400`，注册
   `app_scheduler.py:244-250`）——#936 引用闭包保留集（`:292-341`）；#1521
   **先文件后行**：`purge_run_storage_dirs` 只清 `devices/` 与 `dedup/`
   （`:241-258`，`for sub in ("devices", "dedup")`），purge 失败剔除出批下轮重试
   （`:344-357`）；#781/#798 signal/DLE 行显式删（`:382-395`）、console.log 随清
   （`:416`）。PRUNED 由 Agent 侧置位（`event_uploader.py:536-606`），
   控制面无 REMOTE→PRUNED 路径。
5. **持久化权威 / 完成条件**：`jira/{run_id}/` 目录 + DLE ARCHIVED/PRUNED +
   `run_context.{archive,upload_summary,extract}`；extract 返回 ≥0 即收口
   （-1 无 merge 产物、-2 无 NFS root，`dedup_extract.py:184-188`）。
   **F-B1：`jira/` 不在清轨内。**

### 跳 6：报告 / JIRA / 页面消费

1. **风险摘要**：`aggregate_risk_summary`（`log_observation.py:283`）——DLE 权威
   计数 + 未链接 signal 补充（`:60-107`，MOBILELOG signal-only `:57`）；
   S/A/B 判定 `_build_risk_summary`/`_classify_subtype`（`:109-139`）；
   **#1075 UNIVIEW 已纳入**（测试 `test_risk_summary_counts_uniview_dle_and_unlinked_signal`）。
   link stats 三桶 not_yet_archived / unlinkable / unlinked_fixable，
   `fixable_link_rate` 只看真故障桶（`:141-282`，#528）。
2. **链接修复**：`signal_link_reconciler.reconcile_signal_links_once`
   （`app_scheduler.py:342-345` 注册）；测试 `test_signal_link_reconciler.py`
   3 例（回填/幂等/watcher-summary 不再链接）。
3. **端点**：watcher-summary（`plan_runs.py:2088`，RUNNING 期）；终态
   `GET /plan-runs/{id}/log-events`（`plan_runs.py:2256-2299`，archive authority，
   state 过滤 + 分页）；orphan signals `GET /log-signals/orphans`（`logs.py:84`）。
4. **JIRA 双轨**：job 级草稿 `post_completion.py:95-99`
   （compose_run_report → build_jira_draft → `job_instance.jira_draft_json`；
   `post_processed_at` 在草稿 try/except **之后**置位 `:114`——草稿失败仍置位，
   是 F-B2 的触发源）；列表 `GET /runs/jira-drafts`（`runs.py:217-257`，
   **F-B2**）；批量提单为手动 vendor 工具链（`dedup.py:192+`，JiraRun 持久化
   `:332-368`；端点参数层测试 `test_dedup_jira_endpoints.py` 8 例）。
5. **前端**：`frontend/src/components/plan-run/LogEventsCard.tsx`（终态事件表）、
   `frontend/src/hooks/plan-run/planRunDetailUtils.ts`（轮询兜底，链 A §跳 6 同款
   机制）、`PlanRunDetailPage`。
6. **持久化权威**：`report_json` / `jira_draft_json`（PG JobInstance）+ `JiraRun`
   表；风险摘要纯派生不落库（watcher-summary 实时算）。

## 3. 场景覆盖矩阵（既有测试盘点，本轮未运行）

| 必测场景（总纲 §6.2） | 已覆盖证据 | 缺口 |
|---|---|---|
| ① 补采重试 | DLE 注册 outbox `test_dle_register_outbox_1042.py`（5）；UPLOAD_FAILED 重扫/耗尽跳过 `test_event_uploader.py::test_retry_failed_skips_exhausted_upload_failed`、recover 集合 `::test_recover_states_only_upload_pending`；#1044 `test_aee_reconciler.py::test_hash_unchanged_still_processes_when_pending_remaining`；SAQ retries=2 + round key 幂等；#1070 半成品重拷 `test_dedup_extract.py` | PULL_FAILED→重采→REMOTE 的**跨跳组合**无直接测试 |
| ② 存储不可用 | #1074 `test_dedup_scan_merge.py::test_run_merge_sync_raises_on_center_publish_oserror`；#1083 校验失败删坏副本（`test_event_uploader.py`）；stderr 判死 `::test_run_merge_sync_raises_when_subprocess_stderr_has_error` | NFS root 缺失整链行为（scan 返回 `""`、extract 返回 `-2`）无专项测试；CIFS 写满 → UPLOAD_FAILED → spill 再入队的循环无测试 |
| ③ 上传 / merge 时序 | #381 `test_saq_tasks.py::test_wait_for_upload_mark_returns_when_round_matches`；#1079 `::test_merge_task_mark_timeout_sets_ready_false_despite_pending_zero`；轮次过滤 `backend/agent/tests/test_saq_scan_pipeline.py::test_scan_task_ignores_same_hosts_previous_round_artifacts`、`::test_scan_task_ignores_stale_artifacts_of_untriggered_hosts` | 660s pending 预算耗尽的 best-effort extract 分支（`saq_merge_extract_best_effort`）无直接测试 |
| ④ 跨平台隔离 | #1071 `test_dedup_scan_merge.py`（require_platforms）；collector 路由 `test_platform_collector.py`（8）；`test_unisoc_reconciler.py`（5）、`test_unisoc_scan_runner.py`、`test_aee_scan_scope.py` | `-side factory/shanghai` 选择无显式断言（仅 argv 形态测试） |
| ⑤ 清理后的可追溯性 | PRUNED 仍可提取 `test_device_log_event_prune_extract.py::test_pruned_events_count_as_upload_complete_and_list_for_extract`；retention 行删除 + NFS purge + 失败延期 `test_retention_cleanup.py`（10 例，含 `test_nfs_run_dirs_purged_with_db_row`、`test_purge_failure_defers_db_row_for_retry`） | **F-B1 本体**：`jira/`（及 `jobs/` 产物目录）孤儿无代码无测试；删行后 `local_path` 悬空引用无可观测断言 |

规模参考（grep 计数，未运行）：`test_saq_tasks.py` 34、`test_dedup_scan_merge.py` 33、
`test_aee_reconciler.py` 36、`test_event_uploader.py` 23、`test_retention_cleanup.py` 10、
`test_log_observation.py` 11。

## 4. 本轮二次复核的论断

以下由本会话亲自读源码核验（非转述）：

- SAQ 四段链全程：`scan_task`（`saq_tasks.py:364-549`）、`upload_task`（`:552-622`）、
  `merge_task`（`:758-879`）、`extract_task`（`:889-904`）与 `SAQ_FUNCTIONS` 注册（`:983-995`）。
- 完备性四维收窄与 #1071（`dedup_scan.py:141-208`）、`record_scan_archive_state`
  jsonb_set + `scan_failed` 标记（`:211-285`）。
- merge 全链：#1527 raise（`saq_tasks.py:813-824`）、#697 skip（`:797-811`）、
  `_load_org_files_for_merge` 轮次过滤与拒绝无界（`dedup_scan.py:470-519`）、
  #1072 锁（`:350`）、#1074 发布失败必抛（`:409-412`）。
- extract 全链：#1070 标记 + 原子 staging（`dedup_extract.py:133-175,286-295`）、
  #386 同名保 REMOTE（`:274-308`）、#766 分区落点（`:27-36`）、
  `list_remote_paths_for_extract` 三态（`device_log_event.py:24-27,116-124`）。
- DLE 信任边界全部闸门（`agent_api.py:91,2266-2268,2280-2312,2348-2381,2404-2560`）
  与 Agent 恢复端点（`:2633-2675`）。
- retention：`purge_run_storage_dirs` 只清两目录（`cron_scheduler.py:241-258`）、
  DLE/signal 显式删（`:382-395`）、#936 保留集（`:292-341`）。
- jira-drafts 列表先 LIMIT 后过滤（`runs.py:240-256`）及 `post_processed_at`
  在草稿失败后仍置位（`post_completion.py:95-99,114`）。
- `upload_scan_report` 平台子目录布局（`upload_manager.py:64-94`）。

Agent 侧抽样亲核（其余 Agent 侧行号来自探查代理，引用前请复核）：
`event_uploader.py:44-51`（常量）与 `:115-126`（recover 只拉 UPLOAD_PENDING）、
`reconciler.py:478-490`（tick 节奏）、`local_disk_monitor.py:35-39,156-158`
（单轮 20 / SSD 禁用）、`device_log_event_client.py:237-247`（PULL_FAILED
无 outbox）、`agent_api.py:2266-2268`（`_EXTRACTABLE_STATES`）。

## 5. 发现

| 编号 | 类型 | 严重度 | 位置 | 触发条件 | 影响 | 处置 |
|---|---|---|---|---|---|---|
| F-B1 | 缺陷（未跟踪） | P2 | `backend/scheduler/cron_scheduler.py:245`（`for sub in ("devices", "dedup")`）vs `:390-395`（DLE 行删除） | retention 到期删除 PlanRun | `jira/{run_id}/` 永不清理：DLE 行删除后 extract 产物（报告唯一持久副本）成为无索引孤儿目录，中心存储单调膨胀且与 #1521「先文件后行、行是目录唯一索引」的自愈语义相悖 | 建议：`purge_run_storage_dirs` 纳入 `"jira"`（同先文件后行语义）；`jobs/{job_id}/` 产物目录同型缺口（归 R10 域），建议同批核对 |
| F-B2 | 缺陷（未跟踪） | P3 | `backend/api/routes/runs.py:240-256` | 最近 `limit` 条 `post_processed_at` 窗口内存在无草稿 job（草稿生成失败时 `post_processed_at` 仍置位，`post_completion.py:96-99,114`） | 草稿列表返回条数 < limit 甚至为空（更早的真实草稿被窗口挡住）；现测试 `test_newest_first_and_limit_respected` 未混入无草稿 job 故未拦截 | 建议：过滤下推 SQL。注意 `jira_draft_json IS NOT NULL` 不够（JSONB `none_as_null=False` 落 JSON `null`，`:232-236` 自述），需 `!= 'null'::jsonb` 类谓词或物化布尔列 |
| F-B3 | 文档漂移（部分已跟踪） | P3 | `docs/design/2026-device-log-event-implementation-spec.md` 各节 vs 代码 | 阅读 spec | 9 处漂移：①CONTINUOUS 逃生阀已删（#287）spec §2.5/2.6 仍写；②`_recover_states` 只拉 UPLOAD_PENDING（`event_uploader.py:115-126`）vs §2.4；③remote_path 布局多 event_id 层（#1073）vs §2.2；④UPLOADING patch 时序后移且用 `_copytree_safe` vs §2.2；⑤UPLOAD_FAILED 重扫条件 vs §2.3；⑥`detect()` 零调用 vs §5.2；⑦`TriggerInfo` 空挂 vs §5.1；⑧spec 状态枚举漏 `UPLOAD_PENDING`；⑨spill 阈值类默认 95%（`local_disk_monitor.py:44`）vs main 注入 80%（`main.py:823`）双口径 | R09-F09/**#1050** 已跟踪「规格描述已替代契约」——本清单供其收口直接使用，不重复立单；⑨ 建议单源化 |
| F-B4 | 观察（未跟踪） | P3 | `backend/agent/aee/device_log_event_client.py:237-247` | PULL_FAILED 注册 POST 失败 | 无 outbox 重放（与 `create_local_event` 的 `dle_register_outbox` `:134-140` 不对称）：行未创建过时该次「发现但拉不动」事实只剩 Agent 日志与 signal，风险摘要少一条 PULL_FAILED 计数 | 缓解已存在：EventUploader missing-local 兜底再补 PULL_FAILED（`event_uploader.py:413-419`）仅当行已存在。建议复用同一注册 outbox |

**已核非缺陷接缝**：`_MARKABLE_EVENT_STATES` 含在途态的控制面回边标记与状态矩阵
一致（`agent_api.py:2286-2289`），in-flight 去重 `_active_ids` 兜底防双传。
**不变量复核**：日志链业务事实全在 PG（`device_log_event`、`plan_run_artifact`、
`job_log_signal`、`report_json`/`jira_draft_json`）与中心/本地文件系统；Agent SQLite
仅重放缓冲；Redis 不承载——符合 AGENTS.md 硬不变量。

## 6. 证据缺口与未覆盖（独立保留，不外推为「没问题」）

- **G-B1** 无真实 NFS + `start_log_scan.py` 工具的隔离 E2E：scan/merge 测试全部
  假 runner / monkeypatch 口径（`test_saq_scan_pipeline.py`、`test_dedup_scan_merge.py`）。
- **G-B2** CIFS 写满 / 慢的整链行为（spill → 上传失败 → 重试 → spill 再入队）无测试。
- **G-B3** 批量提单 vendor 工具链（`dedup.py` subprocess 段）只有端点参数层测试
  （401/422/503/409/500），无执行链测试。
- **G-B4** 删行后 `local_path`/`remote_path` 悬空引用无可观测断言（retention 测试
  只断言行删除与 devices/dedup 目录清理）。
- **G-B5** UNISOC 端到端真机验收仍欠（维持 R09 台账口径，spec §5.4 表）。

G-B1～G-B5 均为搜索未命中的负向结论，本轮未逐条重复检索验证，使用前应复检。

## 7. 后续与交接

1. 链 A（PR #1600）+ 链 B（本 PR）交付后，总纲 §6「两条端到端链路」对证完成；
   总纲 §5 回写随本 PR 一并落（链 A 当轮未回写，本次补登两链）。
2. F-B1/F-B2/F-B4 为未跟踪新发现，按纪律由批次收窗统一裁决立单（处置前先查重）；
   F-B3 供 #1050 收口引用。
3. 本报告与链 A 报告共同构成 #1498 验收判据 1/3/4 的交付；判据 2（五类场景）
   见 §3 矩阵，含显式缺口列。
4. 本文仍是接缝对证，不是完成宣告：审查完成、缺陷修复完成、动态验证通过、
   生产验收通过四项互不替代。
