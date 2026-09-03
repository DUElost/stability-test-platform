# 平台全面只读健康审查报告（含 ADR-0033 在途文档核验）

- **状态**：Living
- **日期**：2026-09-03
- **审查基线**：HEAD `fdf0247c`；工作树在途改动（`docs/DOC-MAP.md`、`docs/adr/README.md` 登记 + ADR-0033 与配套设计文档）一并纳入核验对象
- **范围**：backend 控制面 / Agent（`backend/agent/`）/ frontend / docs·CI·流程 / 在途 ADR-0033 文档
- **性质**：全只读静态审查。五路并行审查（每路独立读代码）后，主线程对每路最高优先级断言**逐条抽样复核**（见 §2 复核记录）；未运行测试、未连库写操作、未修改任何被审文件
- **对应产出**：本文档登记于 DOC-MAP；代码与在途文档**均不代改**，修订项列于 §7/§8 供作者采纳

---

## 一、结论摘要

**整体结论：核心健康，无 S 级违约，但有 3 个 A 级实缺陷与一份合入前需修订的在途 ADR。**

1. **执行与状态核心质量高**：终态协议（/complete 幂等 + fencing + payload digest）、两层钟、EventUploader 过滤模型、OperationScheduler 并发原语、fencing 链（coordinator epoch / lease token）均与文档宣称一致且实现严谨——五路无一发现这些不变量被违反。
2. **3 个 A 级实缺陷**（§3/§5）：
   - **前端链尾「追加链尾」必然 422**：`PlanChainTailCreate.specialty_key` 后端必填，前端 payload 与 TS 类型两层都缺 → 每次调用 `Field required`；
   - **`/jobs/{id}/complete` 未知状态串静默落 FAILED**：损坏/漂移 payload 伪装成真实失败，审计无痕；
   - **watcher archive `readiness` 幻影字段**：后端全 git 史零产出，前端「最终归档」提示恒不弹，且 FAILED gate 与后端 ADR-0028 D2 语义矛盾。
3. **ADR-0033 文档（在途）**：问题定性准确、核心数字成立（F1：100 版本 / 59,406 行 .py / 27.43% 全复核通过），但存在 **3 个 H1 缺决策**（未引 ADR-0032、新 Tool Contract 与现态执行器衔接全悬空、Manifest×ADR-0020 双版本体系权威未定义）+ **现状描述失真**（30000 字符上限 #291 已删、`aeeexp` 平台代码零命中、`support_files_manifest` 语义漂移），**建议修订后再合入**（明细 §6）。
4. **文档-代码漂移密度上升是当前最大系统性问题**：ADR-0032（08-31）落地后 `aee/CLAUDE.md`、feasibility 评审的 #220 表述未跟上；#291（08-24）后 `AGENTS.md` merge 回退段过时；容量公式、migration id、main.py 锚点等多处漂移——多为「行为先改、常驻文档后补」的同步债（§7）。

---

## 二、方法与证据纪律

五路并行只读审查 + 复核：

| 路 | 范围 | 关键断言经主线程复核 |
|---|---|---|
| E docs/CI | docs 全树 630 条相对链接全量核验、workflow 与 AGENTS.md 逐项对照 | 5/5 属实（断链 3、migration id、workflow 旧名、PR 模板、CONTINUOUS 误导） |
| D ADR-0033 核验 | 两文档逐项 × 代码 × feasibility × issue（gh 只读） | 4/4 属实（F1 数字、#291 删除、ADR-0032 supersede、退役工具崩溃现场） |
| A backend 控制面 | api/services/tasks/scheduler/models/core + 近 2 周合入 | 3/3 属实（静默 FAILED、FK SET NULL、enqueue 先于 commit） |
| B Agent 侧 | `backend/agent/` 全目录约 1.5 万行 | 6/6 属实（#729 残留、shutdown 作用域、aee_ts 污染、零读取点、mtk 固定、PROGRESS 17/32） |
| C frontend | types.ts × backend schema 抽样 11 组、页面缺陷 | 3/3 属实（链尾 422、readiness 零产出、FAILED gate 矛盾） |

行号均对应当前工作树（HEAD `fdf0247c`）；未连库验证项在文中标注「未验证」。

---

## 三、backend 控制面

### 发现

- **[A] `backend/api/routes/agent_api.py:954-955`** — `/jobs/{id}/complete` 对未知状态串（`SUCCESS`/`DONE`/拼写错/`" completed "` 带空白）经 `_RUN_TO_JOB.get(raw, JobStatus.FAILED)` **静默落 FAILED**；仅映射到 RUNNING 才 400。Agent 契约漂移或损坏 payload 会被伪装成一次真实失败：终态快照、聚合计数、成功率与 dedup/report 链全按失败处理且审计无异常。建议：未知状态一律 400/409（与 RUNNING 分支同路径），响应携带 `requested_status`。
- **[A] `backend/scheduler/cron_scheduler.py:215-272` + `backend/models/job.py:180-182`** — 每小时保留清扫直删终态 PlanRun/Job，`job_log_signal.device_log_event_id` FK 为 `SET NULL`（模型正确，cron 内注释仍写 CASCADE——注释与模型漂移），被删 run 的 signal/event 行**无清理通道**、单调堆积（仅 `/log-signals/orphans` 可见）。且这正是 #729「幽灵 /complete 404」的生产来源（离线 Agent outbox 行跨保留窗口存活）。建议 cron 显式清理孤儿行并修注释。
- **[B] `backend/services/job_terminalization.py:117-125,134-142`** — `trigger_next_plan`/`enqueue_dedup_terminal_async` 在 `db.commit()` **之前**执行（Redis 不可回滚）：commit 失败时 run 回滚但 scan 键已占用，重试终态化可能被 SAQ 去重或对 RUNNING run 扫出部分产物。概率低、有自愈，「enqueue 在 commit 后」才是安全序（未验证实际故障记录）。
- **[B] `backend/services/plan_dispatcher.py:66-100`** — 异步 `preview_plan_dispatch` 组装 lifecycle 后未跑 `validate_pipeline_def`（同步真实派发路径 `plan_dispatcher_sync.py:485` 有），预览可能展示最终会被 400 拒绝的生命周期。
- **[B] `backend/api/routes/plan_runs.py:346-380`** — 列表仅按 `started_at.desc()`（QUEUED/PRECHECK 为 NULL）且无游标，翻页漂移。低频管理页，影响小。
- **[B] `backend/scheduler/device_lease_reconciler.py:322-336`** — abort reaper 时间戳 cast 失败回滚后用 ISO **字符串比较**兜底，混入 `Z`/`+00:00`/小数秒即序错乱。建议 fallback 用 Python 侧解析。
- **[B] `backend/services/log_observation.py:53-56`** — DLE 权威计数按 `COUNT(DISTINCT remote_path/local_path)` 后按 subtype 分类，`ANR ≥ 10` 等阈值实际按「文件路径数」判定，同路径多事件被低估（与 ADR-0028 口径是否一致未验证）。
- **[B] `backend/services/plan_run_aggregation.py:30-53`** — `aborted>0 → FAILED` 把「部分 ABORTED + 其余全 SUCCESS」也判 FAILED，人工单点 abort 场景偏严（未验证是否为有意口径）。
- **[C] `backend/realtime/socketio_redis.py`** — Redis pub/sub 瞬时消息（非业务存储）与「Redis 仅做 SAQ broker」措辞并存，建议 CLAUDE.md 显式豁免避免误判违约。

### 亮点
1. /complete 的「租约 token + terminal_payload_digest + 冲突审计（stale_job_completion_rejected / terminal_payload_conflict）」与 UNKNOWN→租约恢复→RUNNING→终态路径，是教科书级 fencing。
2. `plan_run_abort.py:240-271` 批量 ABORT 的 `synchronize_session="fetch"` 显式注释（改 auto 会让内存态恒真、legacy run 卡 RUNNING）——事故教训固化成注释的纪律。
3. `record_scan_archive_state` 用 `jsonb_set` 局部更新避免整行读改写竞态；`count_hosts_with_scan_artifacts` 三维收窄与 docstring 一一对应。
4. #729 修复（3efe86e3）正确：`e.response.status_code if e.response is not None else None` + 真实 `Response(status=404)` 回归锁；控制面全仓搜索无同类兄弟 bug（llm_client/file_server_monitor 均显式判 status）。

---

## 四、Agent 侧（`backend/agent/`）

### 发现

- **[B] `backend/agent/host_registry.py:74-75`** — **#729 同类残留（全仓唯一）**：`exc.response.status_code if exc.response else None`，requests.Response 4xx/5xx falsy → auto_register 失败日志丢 status/body。仅日志级（随后 raise），但应统一口径。
- **[B] `backend/agent/main.py:1228-1251`** — **shutdown 停止作用域错放**：ArtifactUploader/LocalDiskMonitor/LogArchiver 的 `stop()` 被包进 `if log_signal_drainer is not None:`（watcher 分支），而三者启动（591-617）在 watcher 门控之外——watcher 禁用时优雅关停跳过 stop，`local_db.close()` 后 daemon 线程再 tick 抛 `RuntimeError("LocalDB is closed")`；`EventUploader.stop()` 更是任何分支都未被调用。建议 stop 组与启动组同作用域。
- **[B] `backend/agent/main.py:1087-1094 + 452-480`** — 启动 recovery sync 仅启动一次 + 设备重连时触发，无周期兜底：Agent 重启时控制面不可达则本地 `active_job_registry` 悬空行无人 reconcile，LogArchiver 视其 active 永不归档 `logs/runs/{job_id}/`。终态 outbox 有 15s 周期兜底，active registry 没有对应物。
- **[B] `backend/agent/main.py:1208-1211 + operation_scheduler.py:304-315`** — 优雅关停把**排队等 permit** 的 job 直接 FAILED（`SchedulerShutdown` 唤醒全部 waiter → permit 获取失败 → init 失败），而非 ABORTED/移交。批量热更窗口内排队中的 patrol/init job 集体 FAILED（控制面 hot-update 是否有 idle 前置未验证）。
- **[B] `backend/agent/aee/unisoc_reconciler.py:189-198`** — **`aee_ts` 字段错位**：塞入 `meta.event_subtype`（字符串），MTK 版（reconciler.py:760-771）此处是设备时间戳原文（另有 `aee_ts_utc`）。UNISOC 链路该字段被污染，设备时钟漂移排查的载体失效。
- **[B] `backend/agent/event_uploader.py:508-543`** — UPLOAD_FAILED 无死信收敛：600s `_retry_failed_loop` 以 attempt=0 重入队，每次又 5 次全新尝试（attempt 不持久化），与模块注释「重试上限才真正生效」（471-473）相左；CIFS 长期故障时每 ~10 分钟烧一轮 5 次重试 + rmtree-vs-copy 抖动。
- **[B] `backend/agent/scripts/gpu_check/v1.0.4/gpu_check.py:44-55,181-236`** — **跨 run 状态残留**：`dead_streak`/seq 状态文件 `/tmp/gpu_check_{serial}.json` 以 serial 为键、job 起止不清零，新 run 首个 patrol 周期若 GPU 进程尚未拉起会继承上一 run 的 dead_streak 提前判死（grace=2 时多一次即触发）。建议 key 带 job_id/fencing 或 run 起始重置。
- **[B] #220 文档与代码漂移（同 D 路 F7）**：`STP_WATCHER_AEE_RECONCILE_PLATFORMS`（.env.example:198 / aee/CLAUDE.md:13 宣称的平台白名单）全代码库**零读取点**；UNISOC 设备在任意 job 中都会启动真实 `UnisocUniviewReconciler`（ADR-0032 已 supersede #220），且仓库内无 `uniview_watcher` 本地树生产者 → 该 reconciler 实际空转扫描（每 job 180s 线程）。QCOM 仍为真 stub。
- **[B→已更正]** `backend/agent/unisoc_scan_runner.py` 接线核查：scan-queue worker 每次 scan job **无条件串行追加调用** `UnisocScanRunner.instance().run_scan_and_upload(...)`（`scan_runner.py:182-190`），启动与 reload_config 均 `configure()`（`main.py:599,774`）——**展锐归档链（ADR-0032 D4c）并非挂起**，原「除测试外无生产调用方」表述有误，更正。遗留真实缺口：① 接线与常驻文档表述脱节（§7 #2；GitHub 已跟踪：#769/#754/#760）；② 控制面 scan_now 链上送仍固定 mtk 分区（`scan_runner.py:228,230`），双平台产物中心路径分区的完整闭环状态建议以 #754/#760 为准跟踪。
- **[C] `backend/agent/event_uploader.py:172-173,470-471`** — 注释残留已删 CONTINUOUS 双模式描述，与 :212「#287：CONTINUOUS 全量模型已删除」自相矛盾。
- **[C] `backend/agent/main.py:1116-1124 + capacity_reporter.py:49-69`** — 根 CLAUDE.md 容量公式已不适用：`MAX_CONCURRENT_TASKS` 在 agent 无读取点，现口径 = min(空闲健康设备数, 健康门, `STP_MAX_CLAIM_SLOTS` 默认 5)。文档需更新（含 test_capacity_unification.py 头注释）。
- **[C] `backend/agent/job_runner.py:268-271`** — JobSession `lock_register/lock_deregister` 传 no-op，锁生命周期实际全在 main 层/runner——注释声称「Phase 2 锁释放必定执行」与接线割裂（目前无实际 bug）。

### 协议核验通过（未列为发现）
终态协议（update_job 仅 RUNNING、complete 仅经 /complete、409/404 语义对齐）；两层钟（wall 缺省 300、stall 缺省关、0=不限须配 stall_seconds≥1）；action 仅 `script:<name>`；stdout JSON→step_trace→JobStatus 链路一致；subprocess 全 PIPE；路径约定与不变量一致；EventUploader 无 CONTINUOUS 逃生阀残留。

### #728/#729 修复抽查
- #729（3efe86e3）：diff 仅 outbox_drainer.py:115-116 一处 truthiness + 真实 Response 回归用例 + note；409 分支语义与后端响应结构匹配；watcher 侧 emitter 走通用 except 无同类问题。**唯一残留 = host_registry.py:74-75**。
- #728（cc12f9c2）：仅新增 `gpu_check/v1.0.4/` + 测试 + seed 迁移，未触碰 v1.0.0–v1.0.3（ADR-0020 合规）；判定顺序与 commit message 逐条一致；PROGRESS 走 stderr（_lib.py:131）符合打戳协议。

### scripts 目录数值事实（供 ADR-0033 引用）
- 32 个脚本名 / **100 个版本目录** / .py+.sh ≈ 59,486 行（59,406 .py + 80 .sh）
- 高频版本：flash_firmware=15（v1.0.0–v1.3.10）、gpu_check=5（v1.0.0–v1.0.4）、powercycle_check=7、oobe_skip=2、sleep 组各 2
- **PROGRESS 打戳 17/32 脚本名**，高频最新版均已打戳；未打戳 15 个名：check_device、clean_env、connect_wifi、ensure_root、fill_storage、install_apk、monkey_check、monkey_launch、monkey_resource_push、monkey_teardown、monkey_test、push_resources、stop_aimonkey、aee_signal_trigger、noop——**在这些步骤上开 `stall_seconds` 前必须确认版本已打戳**。

### 亮点
1. EventUploader 自愈闭环（event_uploader.py:301-322）：「本地已删而远端仍在」显式建模，重打 REMOTE 信任远端校验和，不卡死 UPLOAD_PENDING。
2. pipeline_engine 子进程与时钟工程：非阻塞 select reader + stop 事件打断、`killpg` SIGTERM→SIGKILL 两段杀树、PROGRESS 只认 stderr 容忍前导空白、8MB 捕获上限——对孙进程持管道写端/活锁刷屏/输出打爆内存三类现场均有防御。
3. OperationScheduler 三分状态并发原语 + coordinator epoch fencing + lease token 再校验，fencing 链少见地严谨。

---

## 五、frontend

### 缺陷

- **[A] `frontend/src/pages/orchestration/usePlanEditForm.ts:326-344` + `utils/api/types.ts:1200-1209`** — 链尾追加 payload 不含 `specialty_key`（保存路径 :236-265 有而此处漏），后端 `PlanChainTailCreate.specialty_key: str` **必填**（`backend/api/routes/plans.py:143`）→ **每次「追加链尾」必然 422 Field required**，toast「追加失败」不落库（链尾原子接口回滚干净，无数据损坏）。TS 类型两层都缺字段，双双没拦住。
- **[B] `frontend/src/pages/execution/PlanRunDetailPage.tsx:122-139` + `types.ts:1831-1838`** — `archiveDataReady` 读 `archive.readiness?.ready ?? ready_for_extract`，后端 WatcherArchiveOut（`api/schemas/plan_run.py:354-361`）**全 git 史从不产出**这两个键（`git log -S` 零命中）→ `finalArchiveReady` 恒 false，「最终归档」提示永不弹出；且弹窗 gate `status==='FAILED'` 与后端 `trigger_extract` 对 FAILED 一律 409（ADR-0028 D2）语义矛盾——正确 gate 应是 `scan_status==='merged'` 且仅 SUCCESS/PARTIAL_SUCCESS。测试夹具 mock 了生产不存在的形状，盲区自洽。手动入口仍在 DedupReportCard，属功能退化非丢失。
- **[B] `frontend/src/components/ui/status-badge.tsx:56-77`** — DEVICE_UI 表无 ABORTED 键：`job_exec_status='aborted'`（后端可产出，routes/plan_runs.py:1771-1772）喂入后回退灰色「未知」，批量中止 PlanRun 时设备矩阵整列中止态显示「未知」；另 `unknown` 文案「已断开」偏连接语义。
- **[B] `frontend/src/pages/devices/DevicesPage.tsx:99` + `components/device/ExpandableDeviceTable.tsx:680`** — `current_task` 后端 DeviceOut **全仓零产出**（幻影字段），展开行「当前任务」对所有设备恒显「无任务」，BUSY 设备正在执行的任务无从显示；`schedulable`（types.ts:72）同为零产出，planExecuteReadiness.ts:173 的「后端权威准入」实际永远走 `status==='ONLINE'` 兜底。
- **[C] `types.ts:1903-1917`** — PlanRunAbortResult 六个幽灵键（pending_aborted_job_ids 等）与后端实际键（`abort_requested_jobs`/`released_leases`，services/plan_run_abort.py:192,195,474-475）漂移，注释自称「Legacy counters retained」系照抄旧实现；当前消费方只读 status 暂无影响。

### types.ts ↔ backend schema 漂移（抽样 11 组）
| 接口组 | 漂移 | 严重度 |
|---|---|---|
| Plan 编排（PlanCreate/Update/ChainTailCreate） | `specialty_key` 后端必填，TS 声明可选；ChainTailCreate 完全缺失（见 A 级） | **A** |
| GET /scripts ScriptEntry（routes/scripts.py:139 `capabilities`） | TS 缺 `capabilities: string[]`，前端无法按脚本能力提示停滞钟 | C |
| plan_snapshot（plan_dispatcher_core.py:478-530） | 缺 `barrier_timeout_seconds`/`barrier_max_wait_seconds`/step `stall_seconds`/`params` | C |
| POST abort 结果 | 6 幽灵键 vs 后端 2 实键 | B |
| DeviceOut（api/schemas/device.py:23-50） | `schedulable`/`current_task` 零产出（见 B 级） | B |
| HostOut | 仅 `ssh_auth_type`（host.py:41）未录入 | C |
| jira runs | 后端 #431 起返回 `jira_project_key` 未录入（无消费方，潜伏） | C |
| ai-assistant config/actions | `updated_at`/`requested_by` 后端可空、TS 非空 | C |
| 状态枚举（PlanRunStatus 六态/link_stats 三拆/log-events/platform_buckets） | **逐字段一致**（无 DEGRADED 误加、无 PARTIAL_SUCCESS 遗漏） | — |

漂移方向统计：状态机/枚举类全部精确；漂移集中在「后端先加、前端没跟上」（capabilities、snapshot 新键）与「后端已删/从未有、前端没清」（readiness、current_task、schedulable）两个方向。

### 近期合入 / IA 裁决抽查
- WATCHER_SIGNAL 2s debounce（usePlanRunDetailData.ts:132-137）与 4000ms waitFor 测试约定一致；小瑕疵：unmount 不清 pending timer（无害）。
- NAV-IA v1.3 裁决：admin 收进 UserMenu 已落地（AppShell.tsx:162-175 注释引用方案文档）；无僵尸路由。
- logo 替换干净（`stp-logo-master.png` 残留为死资产，无代码引用）；无 skip/todo/慢测（唯一 4000ms 等待与真实实现匹配）。

### 亮点
1. socket 生命周期范本：单例 + refcount + 30s idle 断连 + 登出强制断连（useSocketIO.ts:37-98）+ 401 唯一防抖 refresh 单次重放。
2. PlanStep 停滞钟往返防呆：无输入框仍透传 `stall_seconds`（types.ts:908-911 注释 + planEditUtils.ts:60-65 只写有值键），「整体替换行」模式下防静默清空。
3. TimeoutInput draft 机制、`cleanParams` 剥 'all' 哨兵、`unwrapApiResponse` 拒 null-data（client.ts:196-202）均可直接写进 review 检查单。

---

## 六、ADR-0033 + 设计文档核验（在途）

### 核验表（F1–F12）

| 项 | 文档主张 | 证据位置 | 判定 |
|---|---|---|---|
| F1 | 100 版本 / 近 6 万行 / 27.4% 后端代码 | 100 个 v 目录、59,406 .py 行 / 216,573 backend .py = **27.43%** | ✔（与 #735 口径一致） |
| F2 | 配置项膨胀至 189 个 | 两份 .env.example 活动键仅 29；代码 `os.environ/os.getenv` 去重读键实测 191 | ◐ 无出处无口径；建议注明统计方式或改「约 190 个读键」 |
| F3 | `-merge_files_list` / 30000 argv 上限 / 15 列 `aeeexp` / 能力探测 | 清单 + 探测 ✔（dedup_scan.py:458-520）；**30000 上限 #291（da0de5fd, 2026-08-24）已删除**（现只走清单，不支持即 RuntimeError）；`aeeexp` 全仓代码零命中（读 merge xls 是动态表头匹配，dedup_extract.py:44-56） | ✘ 两项现状描述失真（AGENTS.md:135-136 同款过时，见 §7） |
| F4 | Tier 3「APK 经 support_files_manifest 统一下发」 | 列真实存在（models/script.py:22 + migration k6l7m8n9o0p1），但现态语义 = **同一版本目录内非入口脚本文件的 sha 登记**（script_catalog.py:93-103，供 precheck 校验与 self-heal），APK 二进制进不了 manifest，无任何脚本用它分发 APK | ✘ 语义漂移——须明说「扩展现有列语义」而非断言现状 |
| F5 | merge_task 现态有厂商分支；目标态 DedupMergeEngine | `services/dedup/` 不存在 ✔（目标态）；现态 merge_task 只调 `run_merge_all_platforms_sync`（saq_tasks.py:504-591），厂商专用参数在 **service 层** dedup_scan.py（build_merge_argv :458-520、`-side` :303-304） | ◐ 层级写错：胶水在 service 不在 SAQ task |
| F6 | `jira_run` 表 | models/jira_run.py + migration a6b7c8d9e0f1 + `/api/v1/jira` 路由（dedup.py:37） | ✔ |
| F7 | 展锐「未来接入样板」× #220 约束 | **ADR-0032（08-31）已 supersede #220「仅 MTK」**（ADR-0032:104-108）：UNISOC Reconciler/Collector 已真实落地、`job_session.py:294-326` 按 platform 路由；QCOM 才是 stub | ✘ 两文档完全未引 ADR-0032，展锐链路当「待接入」与已落地事实矛盾（Phase 2 与 ADR-0032 高度重叠未交代） |
| F8 | 退出码 0/1/2 + summary.json 平台消费 | 现态：stdout 整份 JSON（pipeline_engine.py:141,445,1508）、returncode≠0 二元 FAILED（:1496-1499）、平台自造 **124=wall / 125=stall**（:86-88,1471-1485） | ✘ 6 个衔接点全悬空（谁解析 summary.json、1/2→Job 终态映射、124/125 地位、retry 按码区分、--check-env 挂哪环、存量 100 版本兼容路径）——见 H1-2 |
| F9 | Manifest+package store 与 ADR-0020 体系关系 | 既有体系完整（DB script 登记 + sha 快照 + 422 不可变 + 退役守卫 api/routes/scripts.py:264-273 + CI 门禁） | ✘ 未答：Manifest 是否进 DB、双体系并存期谁权威、manifest 变更流程、CI 门禁与退役守卫的 Manifest 对应物——见 H1-3 |
| F10 | 文档内部一致性 | ① ADR 退出码 0/1/2 vs design 加 124/137（137 归因「平台超时沙箱」不实，漏 125=stall）；② `--check-env`：ADR 未说判据 / design 输出 `{"ready":true}` JSON / §4.2 示例却 `sys.exit(0)` 无 JSON——三处互不一致；③ `secrets.jira_token` 明文 + step_trace 收割 stdout 无掩码条款；④ 示例 `platform="mtk"`×`MYOS16-Z2581`（实测 Z2581=UNISOC ums9230，aee/CLAUDE.md:23-24） | ◐/✘ ①③④ 实、②自相矛盾 |
| F11 | `{STP_AEE_NFS_ROOT}/tools/` 新布局段 | 权威子目录表（2026-storage-roles-and-aliases.md:18,111）无 `tools/` 段需补登记；DOC-MAP/adr-README 登记行格式 ✔；ADR 头「M7/M8」vs README「M7」、DOC-MAP「最后更新 2026-08-29」未刷新 | ◐ 部分 |
| F12 | 新概念撞车检索（DedupMergeEngine/tool_manifest/--check-env/summary.json/context.json/tools_cache） | 全代码零命中（仅无关近似 preflight_summary.json） | ✔ 无撞车 |

### 缺决策 / 矛盾 / 风险（按 REVIEW_ADR0031 分级）

- **H1-1 — 通篇未引用 ADR-0032（矛盾/重叠缺交代）**：ADR-0032 已裁定 supersede #220 并落地 UNISOC Reconciler/Collector/分区双 merge；被审文档把展锐链路当「样板打样」（#463 P2）、feasibility 与 aee/CLAUDE.md 仍是 supersede 前旧态。建议增「与 ADR-0032 关系」节（增量 = 契约 + 包管理重构；存量 = 已落地分区双 merge），并同步 aee/CLAUDE.md。
- **H1-2 — 新 Tool Contract 与现态执行器/状态机无衔接设计**：现态 stdout JSON→step_trace→终态 + 124/125 超时语义；文档未答 summary.json 消费方、退出码 1/2 → Job 终态映射、125=stall 地位、`retry` 是否按码 2 差异化、`--check-env` 挂 dispatcher/precheck/Agent 哪环（现态 precheck 是 ssh sha 校验，无此概念）、**存量 ~100 版本兼容路径**。建议 Phase 1 DoD 增「契约衔接设计（含双轨运行声明）」。
- **H1-3 — Manifest × ADR-0020 双版本体系权威与流程未定义**：Tier 3 仍须 `script:<name>` 进 PlanStep（架构不变量），包版本在 Manifest——升级 tar.gz 是否必须新建 script 版本（重蹈「每版本全量副本」）？manifest 变更流程、是否进 DB、CI 门禁/force_rebaseline/退役守卫（SCRIPT_STILL_REFERENCED 409）对应物各是什么？design §4.1 抽象基类 `run_merge(input_files, output_dir)` 还丢掉现态编排（round/waterline/_publish_merge_to_center，dedup_scan.py:292-388）——基类只应包 vendor CLI 调用。
- **H2-1 — 现状描述三类失真（F3/F4）**：30000 上限已删、`aeeexp` 属仓库外工具属性、support_files_manifest 是辅助脚本 sha 表。建议改准确表述（如「2026-08 已靠升级工具 + 强制 -merge_files_list 绕开 argv 上限」），避免 Accepted ADR 自带可被代码反驳的硬伤。
- **H2-2 — `--check-env` 三处协议互不一致**：建议裁定退出码 0/2 为准 + JSON 仅诊断载荷，示例代码真正实现契约（示例是 DoD 2 的测试靶子）。
- **H2-3 — secrets 泄露面未处理**：context.json 明文 secrets + step_trace 收割 stdout+stderr（pipeline_engine.py:1332-1360）→ 工具 echo 契约文件即永久落库/UI；且示例把 platform-only jira_token 放进 `execution_tier=device` 的 context，违反 D1 分层。需决策落盘权限/销毁/脱敏/按层分域。
- **H2-4 — 退役工具先修再依赖**：`backend/scripts/check_unreferenced_script_versions.py:50-51` 用同步 `create_engine` 吃 `resolve_database_url()` 的 `+asyncpg` URL（生产库即此形态）→ 崩溃现场与 #735 body 吻合；Phase 1 若依赖该工具须先修。「退役 47 个」与 #735 一致（47 活跃零引用 + 18 停用零引用 = 全表 65/101 零引用），措辞建议「零引用脚本」。
- **H2-5 — 勘误**：示例 device Z2581 属 UNISOC；ADR 头 M7/M8 vs README M7；DOC-MAP 最后更新未刷新；CLAUDE.md 决策表止于 0032；`tools/` 段补进存储布局表；DOC-MAP 未给 ADR-0031/0032 设「架构 ADR」单行却给 0033 设了（标准未说明）。

### 与 TOOLKIT_FEASIBILITY_2026-08-26 一致性
继承且无误：脚本膨胀数字、Jira 管道现状、`support_files_manifest` 字段存在（评审 G14 说「放置手工」，ADR 升级为「必须经其统一下发」——语义被拔高，见 F4）。对不上：① 评审裁定「P1 必开 ADR 重议 #220」——ADR-0033 只覆盖 P2 形态，P1 的归属至今无文档明说（ADR-0032 实际承接但两边都未声明），需补边界声明；② 评审依赖序（G1 上传 API 先于方向 5 等）未在 gantt 体现（GPU/开关机模板打样 09-10 早于其依赖解锁）；③ 评审 G18 dry-run 策略未进设计（不冲突，遗漏）；④ 评审引 job_session.py:292-326 的 #220 白名单描述已被 ADR-0032 落地作废（引用过时）。

---

## 七、文档 ↔ 代码漂移汇总（跨域）

| # | 文档位置 | 代码事实 | 判定 |
|---|---|---|---|
| 1 | `AGENTS.md` scan/upload/merge 节「不支持才回退 -merge_files + 30000 字符上限」 | #291（2026-08-24）已删回退路径，现只走 `-merge_files_list`，不支持即 RuntimeError（dedup_scan.py:496-508） | **误导**（与 ADR-0033 F3 同源） |
| 2 | `backend/agent/aee/CLAUDE.md:13,16-19`「白名单 STP_WATCHER_AEE_RECONCILE_PLATFORMS、UNISOC detect→False」 | 全代码零读取点；UNISOC 已真实接入（ADR-0032 supersede #220） | **误导** |
| 3 | `docs/design/2026-adr-0025-log-flow-sequence.md:10,145,151,261,279`（自称产品终版） | `CONTINUOUS=1` 逃生阀 #287 已删（event_uploader.py:212） | **误导**（指引实现者扑空） |
| 4 | `CLAUDE.md:33` migration `q2r3s4t5u6v7w8` | 实际 `q2r3s4t5u6v7`（versions/q2r3s4t5u6v7_drop_host_max_concurrent_jobs.py） | **误导** |
| 5 | 根 `CLAUDE.md` 容量公式 `min(MAX_CONCURRENT_TASKS-active, effective_slots)` + test_capacity_unification.py 头注释 | agent 无 MAX_CONCURRENT_TASKS 读取点；现口径含 STP_MAX_CLAIM_SLOTS 默认 5 | 过时 |
| 6 | `CLAUDE.md:17` main.py:207 锚点；`.cursor/rules/00-project-context.mdc` 181-182 | `socketio.ASGIApp` 实际 main.py:257 | 漂移（低） |
| 7 | `docs/design/01-execution-pipeline.md:160-163` env 表无前缀 `STP_DEDUP_SCAN_*` | #295 已角色键分离，旧键仅兼容回落 + WARNING | 陈旧 |
| 8 | `.github/workflows/pr-update-branch.yml:5` `PR Agent (DeepSeek)` | 08-30 改名 `PR Agent (advisory review)`，该触发腿失效 | **失配** |
| 9 | `.github/pull_request_template.md:23`「security concerns 会阻断合入」 | 08-30 定案：全异步顾问、不阻断、走单独 issue | **失配**（误导每个 PR 贡献者） |
| 10 | `docs/notes/` 三个 08-31 文件（architecture/2026-08-31-unisoc-toolkit-73-463-alignment.md:6、feature/2026-08-31-toolkit-android-tools-g15-alignment.md:6、feature/2026-08-31-ai-assistant-t0-deep-read-pr-a.md:8） | `../adr/`/`../design/` 相对层级少一层 → 404 | **断链**（同型缺陷 08-26 治理工具抓到过又复发，L0 未覆盖 docs/notes/） |
| 11 | `docs/DOC-MAP.md:3`「最后更新 2026-08-29」 | 内容已含 09-01/09-03 条目 | 轻微 |
| 12 | `backend/agent/event_uploader.py:172-173,470-471` 注释 | CONTINUOUS 双模式描述与 :212 自相矛盾 | 代码注释残留 |

---

## 八、分级发现总表（S/A/B 合并去重）

| 级别 | 条目 | 位置 |
|---|---|---|
| **A** | 链尾追加缺 specialty_key → 每次 422 | usePlanEditForm.ts:326-344 + types.ts:1200-1209 × plans.py:143 |
| **A** | /complete 未知状态串静默 FAILED | agent_api.py:954-955 |
| **A** | watcher archive readiness 幻影字段吞掉「最终归档」提示（+ FAILED gate 与后端 409 矛盾） | PlanRunDetailPage.tsx:122-139 × plan_run.py:354-361 |
| B | 保留清扫孤儿 job_log_signal 无清理通道（注释 CASCADE vs 模型 SET NULL） | cron_scheduler.py:215-272 × job.py:180-182 |
| B | 终态化 enqueue 先于 db.commit() | job_terminalization.py:117-142 |
| B | preview 派发未跑 validate_pipeline_def | plan_dispatcher.py:66-100 |
| B | abort reaper ISO 字符串比较兜底 | device_lease_reconciler.py:322-336 |
| B | DLE 计数按路径去重低估同路径多事件 | log_observation.py:53-56（未验证口径） |
| B | aborted>0 → FAILED 聚合偏严 | plan_run_aggregation.py:30-53（未验证有意口径） |
| B | #729 同类残留（日志级） | host_registry.py:74-75 |
| B | shutdown stop 组错放 watcher 分支 + EventUploader.stop 未调用 | main.py:1228-1251 |
| B | recovery sync 无周期兜底 → 悬空 active registry 不归档 | main.py:1087-1094 |
| B | 优雅关停把排队 job 变 FAILED | main.py:1208-1211 + operation_scheduler.py:304-315 |
| B | UNISOC `aee_ts` 塞 event_subtype | unisoc_reconciler.py:189-198 |
| B | UPLOAD_FAILED 无死信收敛、attempt 不持久化 | event_uploader.py:508-543 |
| B | gpu_check 跨 run dead_streak 残留 | gpu_check/v1.0.4/gpu_check.py:44-55,181-236 |
| B | #220 平台门禁文档零读取点、UNISOC 链路文档与接线脱节（接线实锤：scan_runner.py:182-190；已跟踪 #769/#754/#760） | aee/CLAUDE.md × job_session.py:291-313 |
| B | ~~unisoc_scan_runner 无生产调用方~~（已更正：接线实锤 scan_runner.py:182-190；scan 上送固定 mtk 分区） | unisoc_scan_runner.py × scan_runner.py:228-230 |
| B | DEVICE_UI 无 ABORTED 徽章 → 中止态显示「未知」 | status-badge.tsx:56-77 |
| B | current_task/schedulable 零产出 → 设备展开行恒「无任务」 | DevicesPage.tsx:99 + device.py:23-50 |
| B | abort 结果 6 幽灵键 vs 2 实键 | types.ts:1903-1917 × plan_run_abort.py:469-476 |
| B/C | 列表无游标翻页漂移 / PlanRunAbortResult 注释照抄旧实现 | plan_runs.py:346-380 / types.ts:1903 |
| C | socketio_redis 与「Redis 仅 broker」措辞 | realtime/socketio_redis.py |
| C | 容量公式文档过时、CONTINUOUS 注释残留、JobSession 锁注释割裂 | 见 §4 |

ADR-0033 文档侧：H1-1/H1-2/H1-3（§6）为合入前必答；H2-1 至 H2-5 建议合入前修订。

---

## 九、落地顺序建议（DoD）

1. **（改代码）** 修 3 个 A 级：链尾 payload + TS 类型补 `specialty_key`（并加一条 UI 冒烟断言）；/complete 未知状态 400/409 + 回归用例；watcher-summary 前端改读真实键或删幻影分支（连同 FAILED gate 修正）。
2. **（改代码）** host_registry.py:74-75 与 #729 统一口径（一行）；main.py shutdown stop 组与启动组同作用域 + EventUploader.stop。
3. **（改代码）** 保留清扫补孤儿 job_log_signal 清理通道；job_terminalization enqueue 移到 commit 后。
4. **（在途文档）** ADR-0033 合入前按 §6 H1-1–H1-3/H2-1–H2-5 修订（补 ADR-0032 关系节、契约衔接设计、双体系权威裁定、现状描述改准确、--check-env 裁定、secrets 条款），并将 `tools/` 段补进存储布局表、同步 CLAUDE.md 决策表与 DOC-MAP/adr-README 口径。
5. **（文档漂移）** §7 表 1-4、8、9 为真误导/失配，随近期 PR 顺手修；10 三条断链补 `../../`。
6. **（专项）** #728 Agent Note 补记；`check_unreferenced_script_versions` 的 sync-engine/asyncpg URL 问题随 #735 修复并加 smoke。

---

## 十、未验证项（留给后续/连库确认）

- 孤儿 job_log_signal 的实际堆积速率与生产存量（只读 SELECT 即可量化）；
- `_resolve_plan_run_status` 的 aborted 口径与 log_observation 路径数口径是否为有意设计（对照 ADR-0028/历史 issue）；
- hot-update 是否有「排队 job 清空」前置（§4 优雅关停 FAILED 的影响面）；
- 历史 DB 中是否有前端兜底「未知」徽章对应的存量枚举值。

---

## 十一、既有防御亮点（跨域）

1. 治理工具已证明能抓到真实缺陷（L0 08-26 抓到同型断链；DOC-MAP 36 链接 / reviews 25 文件双向登记簿维持「唯一入口」）。
2. CI 叙述诚实度实测零偏差：6 项 required checks + enforce_admins + strict 与 AGENTS.md 逐字一致。
3. 前端 socket 生命周期、后端 jsonb_set 并发写、Agent 子进程杀树与 fencing 链——三层各自把「最容易烂的并发/生命周期角落」做出了可审计的防御。
