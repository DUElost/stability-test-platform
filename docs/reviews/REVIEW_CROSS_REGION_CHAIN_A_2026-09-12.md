# 跨区收口 · 执行链（链 A）端到端逐跳对证

- 日期：2026-09-12。
- 基线：origin/main `d820e1a9f3a6d4c9dd9411cee395fc280226ac2a`（2026-09-12 17:55:40 +0800）。
  本文所有 `file:line` 均相对该基线。
- 定位：落实 [`PROJECT_REVIEW_PLAN.md`](./PROJECT_REVIEW_PLAN.md) §6「跨区收口」的**链 A（执行链）**部分。
  **这是静态对证 + 既有测试盘点，不是动态验证通过、不是缺陷修复完成、也不是生产验收结论**，
  更不代表全面审查已交付（链 B 未启动，见 §7）。
- 本轮只交付本文件；不修改业务代码、既有 ADR 与其他会话报告。发现的处置去向见 §5。

## 0. 结论

1. **链 A 六跳的接缝在代码上是闭合的**：每一跳都有明确的生产者、消费者、持久化权威与完成谓词，
   且「只有一个 claimer 胜出」这类关键不变量落在 PostgreSQL 约束上（偏唯一索引 + CAS + 行锁），
   而不是落在时序假设或 Redis 上。
2. **权威边界没有骑墙**：队列与租约的业务事实在 PostgreSQL（`plan_run.status`、`device_leases`），
   Redis 只做队列与瞬时跨进程通信；全仓未发现 Redis 承载执行链业务事实（§2 各跳第 3 项）。
3. **最弱的一环不是某一跳，而是「终态 → 页面」的收敛路径与验证面**：
   非 abort 的回收路径会终态化 PlanRun 却不推送 `plan_run_status`（§5 F-A2），
   且 `/dashboard` Socket.IO 推送至今没有成文的投递语义（§6 G-4）。
4. **六个必测场景里，重复回报与迟报到的单跳防御很扎实，缺的是跨跳组合与真实 Agent/Socket 参与**
   （§3、§6）。未覆盖部分已在 §6 逐条列出，不外推为「没问题」。

## 1. 范围与方法

- 范围：`Plan/Suite → 快照与派发 → 队列准入 / 租约与 claim → Agent 执行 → complete 终态 ACK → 聚合 → 实时通知与页面展示`。
- 每跳核对五要素：生产者 / 消费者 / 持久化权威 / 失败恢复 / 完成条件，并给出对应测试或复现命令。
- 方法：只读静态对证（`file:line`）+ 既有测试盘点（**本轮未运行测试套件**）。
  关键论断经二次复核，复核清单见 §4；未复核项在 §6 标注。
- 与既有报告的关系：R01–R15 各区报告仍是各自区域的证据载体；本文只做**接缝**对证，不重述区域结论，
  不重新定义协议。已跟踪缺陷直接引用既有单号（如 §5 F-A1 → #1552），不重复立单。

## 2. 逐跳对证

### 跳 1：Plan/Suite → 快照物化与派发

1. **生产者**：`prepare_plan_run`（`backend/services/plan_dispatcher_sync.py:467`）；
   MANUAL 入口 `backend/api/routes/plans.py:1215`，SCHEDULE `backend/api/routes/schedules.py:101`，
   CHAIN `backend/services/plan_chain_trigger.py:219,297`。
   快照构造 `build_plan_snapshot`（`backend/services/plan_dispatcher_core.py:499`），
   冻结 step 的 `script_name`/`script_version`/`params`/超时（`plan_dispatcher_core.py:519-550`）。
2. **消费者**：准入泵 `claim_queued_plan_runs`（`backend/services/admission_pump.py:123`）→
   `admission_transaction`（`admission_pump.py:617`）；后者用 `_build_lifecycle_from_snapshot(pr.plan_snapshot)`（`admission_pump.py:699`）物化 Job。
3. **持久化权威**：`plan_run.plan_snapshot`（JSONB，`backend/models/plan_run.py:47`）；
   目标清单权威是 `plan_run_target_device`（`backend/models/plan_run.py:183-217`），
   `run_context.dispatch_device_ids` 仅作兼容读取。
   **版本与步参在派发时冻结**；但脚本 sha256 与 Suite 内容在准入时**再次按现库校验**（`admission_pump.py:488-511`、`suite_binding.py:164-176`），内容变更以 `suite_content_changed` 显式失败——这是有意设计，不是漏冻结。
4. **失败恢复**：prepare 单事务，快照子表写失败整体回滚（`plan_dispatcher_sync.py:644-647`）；
   派发后卡在 PRECHECK 的由 `backend/scheduler/precheck_reaper.py:291` 重排（上限 `MAX_ADMISSION_REQUEUE_ATTEMPTS=3`，`:287`），耗尽转 FAILED（`:367-429`）。
5. **完成条件**：`plan_run` 落库且 `status='QUEUED'`、快照子表齐备（`plan_dispatcher_sync.py:1158-1162`）。
   注意：无 DB 约束保证「QUEUED 至少有 1 个目标行」；缺失在准入侧按致命处理（`admission_pump.py:657-659`）。

### 跳 2：派发 → 队列准入 / 租约与 claim（fencing）

**2a 队列准入**（QUEUED → PRECHECK → RUNNING）

1. **生产者**：跳 1 提交的 QUEUED PlanRun + `plan_run_target_device` / `plan_run_host`。
2. **消费者**：`pump_admission_tick`（`backend/services/admission_pump.py:188`，注册 `backend/scheduler/app_scheduler.py:319-326`）
   → SAQ `plan_admission_task`（`admission_pump.py:760`）→ `admission_transaction`。
3. **持久化权威**：`plan_run.status` + `plan_run.admission_attempt_id`（所有权令牌，`backend/models/plan_run.py:45,74`）。
   **队列权威在 PostgreSQL，不在 Redis**；`QueueReason` 亦落库。
4. **失败恢复**：可重试竞争 → 整事务回滚后由 `requeue_plan_run` 重写 QUEUED + 退避（`admission_pump.py:277-328`）；
   致命 → `fail_plan_run_admission`（`admission_pump.py:331-387`）；未知异常留 PRECHECK 交 reaper（`admission_pump.py:829-834`）。
5. **完成条件**：事务提交时 `plan_run.status == 'RUNNING'` 且每个目标设备恰有一行 `JobInstance(PENDING)`（转换 `admission_pump.py:728`；`total_job_count` `:719`）。

**2b 设备 claim 与租约**

1. **生产者**：物化出的 `JobInstance(status=PENDING)` 与已解析的 `pipeline_def`（`plan_dispatcher_sync.py:1034-1044`）。
2. **消费者**：`POST /jobs/claim`（`backend/api/routes/agent_api.py:636`）→ `_claim_jobs_for_host`（`agent_api.py:375`）
   → 状态机转 RUNNING（`:483`）+ `acquire_lease`（`:487`，`backend/services/lease_manager.py:34`），响应携带 `fencing_token`（`agent_api.py:712`）。
3. **持久化权威**：`device_leases` 的 `status='ACTIVE'` + `fencing_token` + `lease_generation`（`backend/models/device_lease.py:23,32,33,39`）；
   令牌形态 `f"{device_id}:{new_gen}"`（`lease_manager.py:132`）。租约行是占用的唯一真值。
4. **失败恢复**：过期 ACTIVE 租约不由 claim 自行回收，`device_lease_reconciler` 是唯一过期处理者（`lease_manager.py:104-108`）；
   Agent 侧 `_handle_lease_lost`（`backend/agent/lease_renewer.py:193-215`）；恢复接管轮换令牌（`agent_api.py:611-630`）。
5. **完成条件**：`job.status == 'RUNNING'` 且存在匹配的 ACTIVE 租约（提交 `agent_api.py:508-509`；校验谓词 `_get_valid_runtime_lease` `agent_api.py:519-556`）。

**「只有一个 claimer 胜出」落在哪里**（这是本跳最关键的接缝）：

- 设备维度：PostgreSQL 偏唯一索引 `uq_device_leases_active_per_device`（`device_lease.py:52-62`，`WHERE status='ACTIVE'`）——
  INSERT flush 触发 `IntegrityError` 即认输（`lease_manager.py:142-151`）。**这是最终裁决者**。
- Job 维度：偏唯一索引 `uq_job_active_per_device`（`backend/models/job.py:82-89`），物化冲突转 `DEVICE_BUSY` 重排（`admission_pump.py:707-713`）。
- 续租：**数据库级 CAS**，不是只比对 payload —— `_cas_renew_leases`（`agent_api.py:1375`）绑定
  `(job_id, fencing_token) IN (...)` + `host_id` + `agent_instance_id` + `status=ACTIVE` + `expires_at > now` + `Job.status==RUNNING`（`agent_api.py:1402-1412`）。
- 准入所有权：`admission_attempt_id` CAS（`admission_pump.py:634-643` 等）。
- 未发现任何 Redis/Lua fencing —— Lua 仅用于 Socket.IO sid 注册（`backend/realtime/agent_sid_registry.py:68-79`），在执行链之外。

### 跳 3：claim → Agent 执行（Pipeline / JobSession）

1. **生产者**：claim 响应（`agent_api.py:635-718`；`JobOut` 含 `pipeline_def`/`fencing_token`/`watcher_policy` `:252-263,705-717`）。
2. **消费者**：`run_task_wrapper`（`backend/agent/job_runner.py:235-473`）→ `JobSession`（`backend/agent/job_session.py:101-260`）→ `PipelineEngine`（`backend/agent/pipeline_engine.py:754`）。
3. **持久化权威**：控制面 `JobInstance.status` / `pipeline_def`（`backend/models/job.py:26,28`）；
   Agent 本地 SQLite `active_job_registry`（`backend/agent/registry/local_db.py:131-137`，claim 时 `save_active_job`）。**本跳无 Redis 参与**。
4. **失败恢复**：claim 后本地登记失败 → `_rollback_failed_claim` 补偿回滚（`backend/agent/main.py:302-344`）；
   Agent 重启 → `recovery/sync` 对账（RESUME/UPLOAD_TERMINAL/ABORT_LOCAL，`main.py:405-556`）。claim 阶段无独立重试队列，靠服务端 PENDING 行 + 恢复对账。
5. **完成条件**：引擎侧 `termination_reason ∈ {"completed","timeout"}` 判成功（`pipeline_engine.py:1899`）；
   runner 侧映射 FINISHED/CANCELED/FAILED（`backend/agent/pipeline_runner.py:94-99`）；`run_task_wrapper` 无条件发 `/complete`（`job_runner.py:412-433`）。

绑定契约的强制点：claim 必填字段 `REQUIRED_CLAIM_FIELDS`（`backend/agent/watcher/contracts.py:51-58`），
JobSession fail-fast（`job_session.py:496-508`）；`fencing_token` 缺失直接 KeyError（`job_runner.py:251`）。
`script:<name>` 唯一解释点：`_resolve_action` 仅接受 `script:` 前缀（`pipeline_engine.py:1493-1498`），
版本按 `name::version` 解析且不符即 `ScriptVersionMismatch`（`backend/agent/registry/script_registry.py:134-145`）。

### 跳 4：执行 → complete 终态 ACK

1. **生产者**：`complete_job`（`backend/agent/api_client.py:216-267`）——**先本地落盘再 HTTP**（`enqueue_terminal` `:237`，POST `:243-247`）；
   独立重试生产者 `OutboxDrainThread._drain_once`（`backend/agent/outbox_drainer.py:125-218`）。
2. **消费者**：控制面 `POST /agent/jobs/{id}/complete`（`backend/api/routes/agent_api.py:954-1274`），`SELECT ... FOR UPDATE`（`:962-966`）。
3. **持久化权威**：控制面 `JobInstance.terminal_payload_digest`（幂等摘要，`backend/models/job.py:33`）+
   终态快照 `StepTrace(step_id="__job__", event_type="RUN_COMPLETE")` 以 `uq_step_trace_event_id` 唯一（`agent_api.py:1173-1187`、`job.py:119`）；
   Agent 侧 SQLite `job_terminal_outbox`（`local_db.py:82-91`，`job_id UNIQUE`）。**无 Redis**。
4. **失败恢复（两层）**：
   - 即时：`_post_with_retry` 指数退避，**409 立即抛出、不重试**（`api_client.py:128-140`）；
   - Outbox：HTTP 失败但本地已落盘 → 延迟补送（`api_client.py:248-267`），本地未落盘才 `TerminalReportLostError`。
   Drainer 语义（`outbox_drainer.py:175-212`，逐条已复核）：409 可 ACK 状态 → ACK 下台；409 冲突/未知 → 保留，达 10 次转死信；
   **404 → 直接 ACK 丢弃**（`:195-197`）；其它 4xx → 死信；5xx/网络异常 → 无限重试不设上限。
5. **完成条件**：`target ∈ {COMPLETED, FAILED, ABORTED}` 且状态机转移合法（`agent_api.py:970-972,1143-1155`），
   随后 `release_lease` + `on_job_terminal` + 提交（`:1215-1244`）。
   幂等三重奏：同 digest → 200 `idempotent`；digest 冲突 → 409 `TERMINAL_PAYLOAD_CONFLICT`；历史令牌不符 → 409 `STALE_COMPLETION_TOKEN`（`:992-1095`）。

顺序保证的形式是**同线程代码顺序 + 上传端只读 `acked=0` 行**（`api_client.py:235-252`；本地事实不可替换，`local_db.py:614-647`）。
未发现跨进程 happens-before 的额外机制（§6 G-6）。

### 跳 5：complete → 聚合与终态收口

1. **生产者**：`POST /complete` 尾部 `PlanAggregator.on_job_terminal`（`agent_api.py:1242`，提交 `:1244`）；
   sync 入口供 recycler/abort 用（`backend/services/job_terminalization.py:187`）。
2. **消费者**：计数器自增（`job_terminalization.py:103`）+ 聚合（`backend/services/plan_run_aggregation.py:212`）。
3. **持久化权威**：`plan_run` 五计数器列 `total/terminal/completed/failed/aborted_job_count`（`backend/models/plan_run.py:82-86`），
   `PlanRunHost` 镜像（`plan_run.py:168-172`）；单 Job 事实权威仍是 `job_instance.status`。
   **O(1) 已复核为真**：`apply_plan_run_aggregation_from_counters` 只读 5 个计数列，不做兄弟 job 扫描（`plan_run_aggregation.py:219-226`），
   仅在 `total_job_count == 0` 时回落全量（`job_terminalization.py:177-184`）。ADR 依据 `docs/adr/ADR-0026-plan-execution-scaling.md:231-251`。
4. **失败恢复**：低频对账自愈 `counter_reconciler`（`backend/scheduler/counter_reconciler.py:38`，重算并重新聚合，间隔 300s `app_scheduler.py:53-56`）；
   幂等终态守卫 `_TERMINAL_PLAN_RUN_STATUSES`（`plan_run_aggregation.py:21-25`）+ `FOR NO KEY UPDATE` 串行化（`job_terminalization.py:137-143`）。
   **从不回报的超时路径**：recycler `recycle_once`（`backend/scheduler/recycler.py:804`），
   PENDING 判据 `created_at < now - DISPATCHED_TIMEOUT_SECONDS`（默认 120s，`:807,825-834`），
   RUNNING 未上报锚点**刻意不用 `updated_at`**（`recycler.py:145,903-923`）；
   abort ACK 超时由 `_reconcile_aborted_running_jobs` 转 UNKNOWN 且**保留 ACTIVE 租约**（`device_lease_reconciler.py:343-363,366-388`，grace 默认 60s `backend/core/job_timeout_config.py:76-79`）。
5. **完成条件**：`total > 0 且 terminal_job_count >= total_job_count`（`plan_run_aggregation.py:219-222`），
   状态判定 `_resolve_plan_run_status`（`:30-52`，abort 覆盖自然结果 `:47-51`）；收口写 `status/ended_at/result_summary`（`:180-192`）。

### 跳 6：聚合 → 实时通知与页面展示

1. **生产者**：**聚合本身不发 Socket.IO**，只调 `notify_plan_run_terminal` 与报告缓存刷新（`plan_run_aggregation.py:195-208`）。
   事件由路由/回收器在 commit 后发：`agent_api.py:1246-1253`、`recycler.py:474-492`、`plan_run_abort.py:447-519`。
   事件定义 `broadcast_run_job_update` / `broadcast_plan_run_status`（`backend/realtime/socketio_server.py:591-608`），
   事件名 `job_status` / `plan_run_status`，namespace `/dashboard`，room `plan_run:{run_id}`。
2. **消费者**：前端 `frontend/src/hooks/useSocketIO.ts:342-355`（订阅 rooms/events），
   `frontend/src/hooks/plan-run/usePlanRunDetailData.ts:105-137`（失效），通知铃铛 `frontend/src/components/ui/NotificationBell.tsx:49-54`。
3. **持久化权威**：`plan_run.status`（`plan_run.py:45`）+ `result_summary`（`:60`）；**Socket 事件只是失效提示，不落库**。
   业务通知事实落 `NotificationLog`（`backend/services/notification_service.py:480-489`，ADR-0036）。
   Redis 用途仅 Socket.IO 跨进程 pub/sub（`backend/realtime/socketio_redis.py:46-59`）与 SAQ —— 与 `AGENTS.md` 硬不变量一致，**未发现业务事实入 Redis**。
4. **失败恢复**：Socket.IO 为 best-effort（`socketio_server.py:712-723` 无主循环时 warn 丢弃；`notification_service.py:734` 注释明示）；
   前端有界重连/回前台重连（`useSocketIO.ts:183-186`）+ **轮询兜底**（`planRunDetailUtils.ts:5-7,114-118`，10s/30s，终态停轮询 `usePlanRunDetailData.ts:134-136`）。
5. **完成条件**：后端 `plan_run.status ∈ {SUCCESS, PARTIAL_SUCCESS, FAILED}`（`plan_run_aggregation.py:21-25`），发送侧同一集合门控（`agent_api.py:1250-1252`）；前端 `isPlanRunTerminal` 后停轮询并断订阅。

## 3. 场景覆盖矩阵（既有测试盘点，本轮未运行）

| 场景 | 代表测试 | 断言所在跳 | 真实 PG / fake | E2E 或单跳 |
|---|---|---|---|---|
| 正常完成 | `backend/tests/integration/test_main_chain_happy_path.py::TestJobCompleteAggregationPath::test_complete_job_aggregates_plan_run_success` | 派发→准入→终态→聚合 | 真 PG；`gather_verify` 打桩 | 多跳（Agent 执行与 SocketIO 为模拟） |
| 正常完成（唯一跨到真实引擎） | `backend/tests/integration/test_dispatch_agent_pipeline_contract.py::test_dispatcher_pipeline_def_is_executable_by_agent_pipeline_engine` | 派发→准入→**真实 PipelineEngine** | 真 PG + 真引擎 | 多跳含 Agent 跳 |
| 正常完成（链式） | `backend/tests/integration/test_plan_chain_e2e.py::TestPlanChainDispatchE2E::test_parent_success_triggers_queued_child_plan_run` | 聚合→链触发 | 真 PG | 多跳（无真实 Agent/Socket） |
| abort | `backend/tests/api/test_plan_run_abort_api.py::TestPlanRunAbort::test_abort_running_job_releases_lease_only_after_agent_ack` | abort→ACK→聚合 | 真 PG | 多跳（ACK 直接调 `complete_job`，跳过 socket） |
| abort（竞态） | `backend/tests/services/test_plan_run_abort_aggregator_race.py`（9 例） | abort × 聚合双写 | 真 PG + 线程 | 多跳 |
| 超时 / 租约回收 | `backend/tests/scheduler/test_recycler.py::test_pending_timeout_fails_with_lease_release_attempt`、`::test_running_timeout_transitions_to_unknown`、`::test_running_timeout_cas_does_not_overwrite_concurrent_completion` | 超时→终态→聚合→通知 | 真 PG | 多跳 |
| 超时（租约维度） | `backend/tests/scheduler/test_device_lease_reconciler.py::test_reconciler_expired_lease_running_to_unknown`、`::test_reconciler_unknown_grace_releases_and_fails` | 过期→UNKNOWN→grace→FAILED | 真 PG | 多跳 |
| 超时（abort ACK） | `backend/tests/scheduler/test_abort_reaper.py::test_grace_expired_job_transitions_to_unknown`、`::test_active_lease_retained_after_abort_ack_timeout` | abort 回收 | 真 PG | 回收跳 |
| 断连重启 | `backend/tests/api/test_agent_dual_write.py::test_recovery_sync_same_boot_different_instance_resume`、`::test_recovery_sync_boot_id_mismatch_cleanup`、`::test_recovery_sync_unknown_within_grace_resumes`（同文件共 12+ 例） | 重连→租约→job 归属 | 真 PG | 多跳（无真实 socket） |
| 断连重启（sid 交错） | `backend/tests/realtime/test_agent_sid_registry.py::test_unregister_spares_newer_registration_after_reconnect` | 注册表 | Fake Redis | 单跳 |
| 重复回报 | `backend/tests/api/test_agent_dual_write.py::test_complete_job_idempotent_replay_same_token_returns_200`、`::test_postgresql_concurrent_same_terminal_payload_is_idempotent`、`::test_postgresql_concurrent_conflicting_terminal_payload_is_rejected` | complete 幂等/冲突 | 真 PG（含并发） | 单跳—多跳 |
| 重复回报（聚合侧） | `backend/tests/services/test_plan_run_aggregation_shared.py::test_aggregation_skipped_when_run_already_terminal` | 聚合终态守卫 | 单元 | 单跳 |
| 迟到回报 | `backend/tests/api/test_agent_dual_write.py::test_unknown_complete_recovers_to_running_before_completed`、`::test_complete_job_idempotent_replay_wrong_token_returns_409` | 迟到 complete | 真 PG | 多跳（grace 窗口内） |
| 迟到回报（回收竞态） | `backend/tests/scheduler/test_abort_reaper.py::test_complete_terminal_committed_between_scan_and_lock_is_not_overwritten`、`backend/tests/tasks/test_session_watchdog.py::test_complete_committed_before_watchdog_locks_is_not_overwritten` | 回收 × 迟到 | 真 PG | 竞态跳 |
| 迟到回报（产物） | `backend/tests/api/test_agent_api_artifacts.py::test_ingest_artifact_accepts_terminal_delayed_upload_with_historical_token` | 产物上传 | 真 PG | 单跳 |

规模参考（grep 计数，未运行）：`test_agent_dual_write.py` 68、`backend/tests/scheduler/test_recycler.py` 24、
`backend/tests/api/test_plan_run_abort_api.py` 17、`test_plan_run_abort_aggregator_race.py` 9、
`test_device_lease_reconciler.py` 8、`test_abort_reaper.py` 6。

## 4. 本轮二次复核的论断

以下论断由本轮亲自复核（读源码/读测试原文），不是转述：

- 快照权威与列名（`plan_run.py:47`）、租约令牌形态（`lease_manager.py:132`）、偏唯一索引定义（`device_lease.py:52-62`）。
- 续租 CAS 的存在与位置（`agent_api.py:1375`）。
- Outbox 表结构与 404/409/4xx/5xx 四分支语义（`local_db.py:82-91`、`api_client.py:128-140`、`outbox_drainer.py:175-212`）。
- O(1) 聚合只读计数列（`plan_run_aggregation.py:219-226`）、计数器列（`plan_run.py:82-86`）。
- abort 的 `db.expire(pr, ["run_context"])` 位于聚合读取**之后**（`plan_run_abort.py:352/385` → `:415-416`），见 F-A1。
- 回收器 `has_broadcast` 三分支为 False 且对应路径确实调用了 `PlanAggregator.on_job_terminal`（`device_lease_reconciler.py:183-185,262-263,474-479,489-501`），见 F-A2。
- 文档/代码间隔漂移（`docs/design/06-realtime-and-background.md:46` vs `app_scheduler.py:31,215`），见 F-A3。
- 时间线 `aborted_job_count` 由 Python 重数（`plan_runs.py:1329`），见 F-A4。

## 5. 发现

| 编号 | 类型 | 严重度 | 位置 | 触发条件 | 影响 | 处置 |
|---|---|---|---|---|---|---|
| F-A1 | 缺陷（已跟踪） | P1 | `backend/services/plan_run_abort.py:352/385` vs `:415-416` | abort 一个尚有 RUNNING job 的 run | `expire` 排在聚合读之后，注解 `:415` 声称「让聚合读到库端值」与该时序不符。**本轮未构造出「abort 被判 SUCCESS」的反例**：PENDING 批量路径已按 RETURNING 行数自增 `aborted_job_count`（`:316-317`），RUNNING-only 的 run 在 abort 内不聚合（`has_active_jobs` `:375-379`），留待 Agent `/complete` 重读 | **已是 #1552**（open，P1）——本文不重复立单 |
| F-A2 | 缺陷（未跟踪） | P2 | `backend/scheduler/device_lease_reconciler.py:474-479`、`:489-501` | `expired_leases` / `stale_unknown` / `terminal_job_active_lease` 路径终态化 PlanRun（`:183-185`、`:262-263`） | 这些路径**不发** `plan_run_status`，浏览器只能靠轮询（10s/30s）发现终态；权威未失真（`plan_run.status` 仍正确），是**收敛延迟**而非丢事实 | 建议：要么补广播，要么把「靠轮询收敛」写成显式设计决定（现仅有 `# P1: 优先级最高` 的取舍注释） |
| F-A3 | 文档漂移（未跟踪） | P3 | `docs/design/06-realtime-and-background.md:46` vs `backend/scheduler/app_scheduler.py:31,215` | 阅读文档 | 文档写 Recycler `~15s`，代码默认 `RUN_RECYCLE_INTERVAL_SECONDS=30`；15s 实为 watchdog/reconciler（`app_scheduler.py:32-33`） | 建议一行修正 |
| F-A4 | 观察（未跟踪） | P3 | `backend/api/routes/plan_runs.py:1329` | 读取 timeline | `aborted_job_count` 由 Python 重数 jobs，不读 `plan_run.aborted_job_count` 计数列 → 与聚合权威**双源**。重数可能更准，但两源无一致性断言 | 建议：三选一（改读计数列 / 保留重数并写明理由 / 加等价性断言），本轮不裁决 |

**不变量复核**（三条 AGENTS.md 硬不变量在执行链上的强制点，均已定位到代码）：
`lifecycle` 唯一顶层（`backend/core/pipeline_validator.py:67-105`，运行期再拒 `pipeline_engine.py:907-919`）；
`action` 唯一格式 `^script:.+$`（`backend/schemas/pipeline_schema.json:46`、`backend/api/routes/action_templates.py:23`、`pipeline_engine.py:1493-1498`）；
`default_params` 不可原地改（`backend/api/routes/scripts.py:481-487`）。

## 6. 证据缺口与未覆盖（独立保留，不外推为「没问题」）

- **G-1 迟到回报落在 reclaim 已 finalize 之后**：未找到直接测试（现有最接近的是 grace 窗口内的 `test_unknown_complete_recovers_to_running_before_completed`）。
- **G-2 abort 全链 E2E**：现有测试用直接调用 `complete_job(ABORTED)` 模拟 Agent ACK，**没有**「真实 Agent 经 socket 收到 control → 中止 → ACK → 释租 → 聚合」的单条端到端。
- **G-3 实时通知与页面展示**：无测试断言聚合终态后 `/dashboard` room 实际收到 `plan_run_status`；前端展示无后端 E2E。
- **G-4 投递语义未成文**：`/dashboard` Socket.IO 的「best-effort」只在代码注释里（`notification_service.py:734`、`schedule_emit` 行为），ADR-0036 与 `docs/design/06-realtime-and-background.md` 均未覆盖；`docs/notes/process/2026-09-11-failure-mode-resilience.md:48` 已记录该空白。
- **G-5 控制面进程重启 mid-run**：未找到重启后把在跑 job/run 重新纳管的测试（现有只有被动回收）。
- **G-6 跨进程 happens-before**：`enqueue_terminal` 先于 POST 只有代码顺序，未见事务/fsync 级断言。
- **G-7 同设备并发 claim**：未找到「两 Agent 抢同一设备、恰好一个胜」的直接测试；现有覆盖是索引级（`test_device_leases_unique.py:23`）与顺序级。
- **G-8 快照不可变性**：未找到断言 `plan_snapshot` 内容不受后续 Plan/PlanStep 编辑影响的专项回归测试（不可变性由代码路径与 docstring 支撑）。
- **G-9 计数器等价性**：除 `counter_reconciler` 外，未见「O(1) 计数 = 重数」的等价性断言。

G-1～G-9 均为**搜索未命中的负向结论**，本轮未逐条重复检索验证，使用前应复检。

## 7. 后续与交接

1. **链 B（日志链）尚未启动**：按总纲「链 A 收口后启动」，本文只完成链 A。链 B 范围与必测场景见 §6 与 #1498。
2. **本文是接缝对证，不是完成宣告**：审查完成、缺陷修复完成、动态验证通过、生产验收通过四项互不替代；
   本轮的验证形式是只读静态对证 + 既有测试盘点，**未运行测试套件、未做隔离环境动态复现**。
3. **待办去向**：F-A1 引用既有 #1552；F-A2/F-A3/F-A4 为本文新观察，尚未立单，建议由批次收窗时统一裁决
   （处置前先按仓库纪律核对 open issue 列表，避免重复立单）。
