# ADR-0026: 大规模化测试计划执行架构（PlanRun 准入队列 + 四层调度 + 控制面减负）

- 状态：Accepted
- 优先级：P0
- 目标里程碑：M5（待定）
- 日期：2026-07-15（初稿）；2026-07-16（评审收口修订、批准 Accepted）
- 决策者：平台研发组 / 架构组
- 标签：调度, 准入队列, 容量, 规模化, 状态机, 租约, 心跳, 聚合

> 本文已按 2026-07-15 评审意见收口（六项，见文末修订记录）。凡标注「待定 / 待压测确认」处，均需在灰度与压测后回填参数或收敛设计。文中严格区分三段式口径：「**P0 已实现**（已核实，配 `file:line`）」「**已知剩余缺口**」「**P1 目标**（本 ADR 提议，新表/新列标「拟」）」，请勿把目标段当作已实现事实。
>
> 规模目标：**60+ host、1000+ device 同时长稳测试**。ADR-0019 的压测阶梯只到 44 host（`docs/adr/ADR-0019-android-device-lease-and-capacity-scheduling.md:305`），本 ADR 面向下一量级。

---

## 背景

平台已在 ADR-0019（Device Lease + 容量 + fencing）、ADR-0020（Plan/PlanStep 一次性切换）、ADR-0021（派发门禁）、ADR-0022（patrol 心跳聚合）四份 ADR 上完成了「单 PlanRun 内」的执行闭环。但把目标从 44 host 拉到 60+ host / 1000+ device 后，暴露出四个**结构性缺口**，它们不是参数调优能解决的，而是模型层面的错配。

### 缺口①：跨 PlanRun 无排队机制 + 唯一索引成硬约束

**现状（已核实）**：派发在 `prepare_plan_run` 阶段就把 PlanRun 直接建成 `status="RUNNING"`（`backend/services/plan_dispatcher_sync.py:343,419`），随后 `complete_plan_run_dispatch` 一次性物化全部 `JobInstance`（`backend/services/plan_dispatcher_sync.py:455,610-630`）。没有「等待准入」这一态——要么派发成功进 RUNNING，要么在 prepare/complete 阶段因设备不可用直接 FAILED（`backend/services/plan_dispatcher_sync.py:513-539`）。

设备占用的唯一真值原本有两套口径且**不一致**：

- `uq_job_active_per_device`（`backend/models/job.py:73-83`）：partial unique index，`status IN ('PENDING','RUNNING','UNKNOWN')` 时同一 `device_id` 只能有一行。**PENDING 阶段就占位**。
- P0 修复前的 `_validate_dispatch_devices_sync` 与 claim 的忙设备过滤（`backend/api/routes/agent_api.py:367-381`）：都以 `DeviceLease.status == ACTIVE` 判忙。但 **PENDING job 尚未 claim，没有 lease**（lease 在 claim 时才 `acquire_lease`，`backend/api/routes/agent_api.py:425-432`）——这是 B4 悬挂的根因。

**B4 悬挂三段式口径**：

- **P0 已实现**：`_validate_dispatch_devices_sync` 已按 `uq_job_active_per_device` 的索引谓词补齐 active-job 检查（覆盖 PENDING/UNKNOWN 无 lease 场景，校验与物化共用同一真值口径，`backend/services/plan_dispatcher_sync.py:96-158`），冲突返回结构化 `reason=active_job`（附冲突 `job_id` / `job_status`，`plan_dispatcher_sync.py:148-158`，区别于 `active_lease`）；物化阶段的**最终竞态已补 `IntegrityError` 兜底**：唯一索引冲突 → 回滚本次全部 Job / WiFi 分配 → 结构化 FAILED（`reason=device_conflict_at_materialization`）——该兜底与本 ADR 评审收口同步落地。
- **已知剩余缺口**：竞争失败仍是 FAILED 终态（P0 旧语义）——没有跨 PlanRun 排队，60 host 并发争抢共享设备池时，用户看到的是「派发失败请重试」而非「排队等待」；重试靠人工。
- **P1 目标**：QUEUED 准入队列；竞争失败（含 `device_conflict_at_materialization`）改为回 QUEUED 自动重试（不变量④）。

### 缺口②：并行数是「Agent 自说自话」，控制面无权威

**现状（已核实）**：一个 host 同时跑几个 Job，完全由 Agent 主循环的 `available_slots` 决定：`available_slots = min(max_by_env, heartbeat_effective)`，其中 `max_by_env = max(0, max_concurrent_tasks - active_count)`（`backend/agent/main.py:944-946`），`max_concurrent_tasks` 取自环境变量 `MAX_CONCURRENT_TASKS`（默认 2，`backend/agent/main.py:437`）。执行并发的物理载体是 `ThreadPoolExecutor(max_workers=max_concurrent_tasks)`（`backend/agent/main.py:883-885`）。

问题在于**「线程池大小」= 「并发语义」**这一等式在长稳场景下失真：

- patrol 阶段绝大多数时间在 `sleep`（等下一周期），却仍占用一个线程池 worker 和一个「并发名额」。一台 host 上 20 台设备做 8 天 patrol，等于 20 个线程长期驻留但几乎不耗 CPU/ADB。
- 真正抢占资源的是**瞬时脚本执行 / ADB 调用**（monkey、截图、connect_wifi），它们才需要限流。用线程池大小同时表达「能同时长跑几台设备」和「能同时执行几个脚本」，两个语义被迫共用一个数字，无法独立调优。
- 控制面对「这台 host 到底在同时跑几台设备」没有权威记录——claim 端点按 `capacity` LIMIT 认领（`backend/api/routes/agent_api.py:405-412`），但认领后的实际并行度只活在 Agent 进程内存。

### 缺口③：控制面单进程载荷随规模平方级放大

**现状（已核实）**：控制面强制单实例（ADR-0002:32「生产 MVP 强制单实例后端运行」，ADR-0018 不变量 4「单进程约束」`docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md:202`，ADR-0019 不变量 2 `docs/adr/ADR-0019-android-device-lease-and-capacity-scheduling.md:273`）。以下热路径在 1000 device 下成为瓶颈：

- **终态聚合全量加载兄弟 job**：每个 Job 终态都触发 `PlanAggregator.on_job_terminal`，它 `SELECT * FROM job_instance WHERE plan_run_id = run.id`（`backend/services/aggregator.py:46-49`）加载**全部**兄弟 job 再重算。一个 200 设备的 PlanRun，200 次终态 = 200 × 200 = 4 万行加载；60 host 并发多个大 PlanRun 时是 O(N²)。
- **续租风暴（P0 已缓解）**：
  - *P0 已实现*：`POST /api/v1/agent/leases/extend-batch` 已上线（`backend/api/routes/agent_api.py:1306` `extend_leases_batch`），Agent `LeaseRenewer` 已改为一轮一批量请求（`backend/agent/lease_renewer.py:115-117,228-243`，分块默认 100）。1000 device 从「1000 个独立 HTTP 事务 / 60s」降为「每 host 每轮 1~N 个分块请求」。旧后端 404/405 自动回退单点端点（`lease_renewer.py:265-269`）。契约细节见 §5。
  - *已知剩余缺口*：批量端点镜像了单点端点的保活语义——续租成功仍顺带刷 `job.updated_at`（`agent_api.py:1421-1428`，单点为 `:1208`）兼作 RUNNING 保活，续租与保活仍耦合（见缺口④）；`execution_state` / `progress_marker` 只收不落库。
  - *P1 目标*：三信号拆列落库（不变量③），续租请求承载三份语义（§5）。
- **心跳减负（P0 已收口）**：权威设备心跳仍走 `/api/v1/heartbeat`；两端点返回 `heartbeat_interval_seconds`，Agent 钳位采纳；硬件字段按 `DEVICE_SNAPSHOT_INTERVAL` 降采样。轻量 `/api/v1/agent/heartbeat` 已带同契约，供后续双通道收敛。
- **实时日志（P2-2 已收口）**：`_MQStepLogger` → `StepTraceWriter.send_log` → `AgentSocketIOClient` 按批（`STP_LOG_BATCH_MAX_LINES` / `STP_LOG_BATCH_FLUSH_MS`）emit `step_log`；心跳 `backpressure.log_rate_limit` + control `backpressure` 命令闭环；`STP_STEP_LOG_STREAM=0` 可回退为 no-op。
- **SAQ producer/worker（P0 已拆）**：`init_saq_producer` 与 `start_saq_worker` 解耦；`STP_ENABLE_INPROCESS_SAQ=0` 时 producer 仍可 enqueue，外部 worker 同队列消费。

### 缺口④：三个独立存活信号被混用于单个 `job.updated_at`

**现状（已核实）**：判定一个 RUNNING job 是否「还活着」，实际上混用了同一列 `job.updated_at`：

- **租约存活**：Agent `extend_lock` 每 60s 刷新 lease `expires_at`，同时刷新 `job.updated_at`（`backend/api/routes/agent_api.py:1203-1208`；P0 批量端点镜像同一耦合，`agent_api.py:1421-1428`）。
- **执行器存活 / 业务进度**：recycler 的 RUNNING 超时判据是 `job.updated_at < now - running_heartbeat_timeout_seconds(job)`（`backend/scheduler/recycler.py:639,647-651`），窗口 900s（patrol 阶段 300s，`backend/core/job_timeout_config.py:47,54`）。
- **patrol 业务进度**：另有 `last_patrol_heartbeat_at`（`backend/models/job.py:47`）用于 patrol stall 检测（`backend/scheduler/recycler.py:665-690`）。

于是「lease 还在续（设备没丢）」被续租顺带写进 `updated_at`，掩盖了「执行器其实卡死、没有任何业务进度」。反过来，一个在等待执行名额（本 ADR 引入的 OperationScheduler permit）而合法阻塞的 job，也会因为 `updated_at` 不刷新而被误判超时。**存活证明的语义被单列承载，无法区分「设备在」「执行器在」「业务在推进」三件不同的事**。

---

## 决策

引入**四层职责分离**的执行架构，配套控制面可靠性改造。核心业务语义（与用户反复确认的共识）：**同一 PlanRun 的全部设备属于同一次长跑；`RUNNING` 是长生命周期状态，不是瞬时资源。** 任何把「长跑」拆成「分批完整 Job」或按「全局 RUNNING 上限」限流的方案都违背这一语义（见「备选方案与权衡」逐条驳回）。

### 1. 四层架构

| 层 | 职责 | 现状对照 | 目标 |
|----|------|----------|------|
| **① PlanRun Admission Queue** | 跨计划准入、优先级、FIFO、aging；决定「哪个 PlanRun 现在可以入场」 | 无此层，`prepare_plan_run` 直接建 RUNNING（`plan_dispatcher_sync.py:343,419`） | 新增 QUEUED 态 + queue pump；准入才物化 Job |
| **② PlanRunHost / HostRunCoordinator** | 「一个计划在一个 host 上」的设备集合、阶段（barrier / patrol wave）与恢复 | 无此层，Job 直接挂 PlanRun，host 维度只是 `JobInstance.host_id` 列 | 新增 `plan_run_host` + `plan_run_target_device` 表（prepare 时创建的不可变派发快照）+ per-host Coordinator 心跳 |
| **③ Host OperationScheduler** | 每 host **瞬时**脚本 / ADB 并发上限（默认 5），取代「线程池大小 = 并发语义」 | `ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TASKS)`（`agent/main.py:883`）同时表达长跑数与执行并发 | Agent 侧 permit 信号量；长跑设备数与瞬时执行并发解耦 |
| **④ Device Job + Lease/Fencing** | 设备级所有权、结果、审计、防重复执行 | 已实现（ADR-0019），`device_leases` + `uq_job_active_per_device` | **保留，不推翻** |

第四层是 ADR-0019 的既有成果,本 ADR 明确**保留** `device_leases` + fencing_token + `uq_job_active_per_device`,只在其上补齐前三层。

**部署位置与共享范围(评审收口,权威口径)**:

| 组件 | 部署位置 | 共享范围 |
|------|----------|----------|
| Queue pump | **控制面** APScheduler job(单进程内,ADR-0018 约束) | 全平台单实例 |
| PlanRunHost / plan_run_target_device | **控制面**持久化投影(PostgreSQL 表) | — |
| HostRunCoordinator | **Agent 侧**(每 host 进程一个) | 该 host 上全部 PlanRun 的协调 |
| Host OperationScheduler | **Agent 进程内每 host 单例** | **同一 host 上所有 PlanRun、所有 Job、所有 Coordinator 逻辑必须共享同一个 OperationScheduler 实例**——若每个 PlanRun 各建 Scheduler(5),两个 PlanRun 同 host 实际并发会变成 10,permit 上限即失效 |

OperationScheduler 的调度队列至少支持:**FIFO、公平性(per-device 防饿)、等待取消(job abort 时撤销排队请求)、shutdown 唤醒(进程退出时唤醒全部等待者,不留挂死线程)**。

### 2. 四条不可破坏的不变量

> 本节四条不变量是整套设计的护栏，任何实现细节冲突时以本节为准。

#### 不变量①：QUEUED 的 PlanRun 不创建 active Job；准入时全部目标 Job 原子物化

QUEUED 态的 PlanRun 只有 `plan_snapshot` + 目标设备清单，**不得**存在任何 `JobInstance` 行。目标设备清单在 prepare 阶段固化为 `plan_run_target_device` 关系行（拟，见数据模型章节）——QUEUED 不建 Job 之后，设备清单不能只靠 `run_context.dispatch_device_ids` JSON，必须可关系查询、可 join。准入（QUEUED→RUNNING）时，靠 `uq_job_active_per_device`（`backend/models/job.py:73-83`）**在物化时刻**占位——一次性 `INSERT` 全部目标 Job，让唯一索引成为原子准入的仲裁者，而**不预取 lease**（lease 仍在 Agent claim 时才 `acquire_lease`）。

这条不变量直接修复缺口①的 B4 根因：QUEUED 期间无 Job → 不占唯一索引 → 不会出现「PENDING 占索引但校验查 lease 查不到」的口径错位。物化改为「原子复查 + 竞争失败即**整个 admission 事务回滚**、回 QUEUED」（见 §4）。

#### 不变量②：RUNNING Job 等待 OperationScheduler permit 不属于失败或 stall

引入第三层后，RUNNING job 在 `WAITING_EXECUTION_SLOT`（等 permit）是**正常状态**。此时：

- 不得被 recycler 判为 RUNNING 超时（`backend/scheduler/recycler.py:625-663`）。
- 不得被判为 patrol stall（`backend/scheduler/recycler.py:665-690`）。
- 只产生 queue-latency 告警指标，不推进任何终态。

**关键规则**：step timeout 从**获得 permit 之后**开始计时；等待 permit 的时间只是排队延迟，绝不算脚本失败。

#### 不变量③：三个存活信号相互独立，不再复用单个 `job.updated_at`

明确拆分为三条正交信号（修复缺口④）：

| 信号 | 语义 | 现状载体 | 目标载体 |
|------|------|----------|----------|
| **租约存活** | 设备仍被本 host 合法占用 | `lease.renewed_at` + 顺带 `job.updated_at`（`agent_api.py:1203-1208`；批量版 `:1421-1428`） | `lease.renewed_at`（已有，`device_leases`），解除与 `updated_at` 的耦合 |
| **执行器存活** | Agent worker 进程 / 线程仍在跑本 job | 复用 `job.updated_at` | 新增 `last_execution_heartbeat_at`（拟） |
| **业务进度** | 脚本 / patrol 周期在实际推进 | `last_patrol_heartbeat_at`（`job.py:47`，仅 patrol） | `last_progress_at`（拟） + 沿用 `last_patrol_heartbeat_at` |

三信号可**共载于同一个批量续租请求**（传输一次、语义三份，见 §5；请求 schema 的 `execution_state` / `progress_marker` 字段已在 P0 前向兼容），但落库为三列、三套超时判据。

#### 不变量④：准入竞争是可重试的调度结果 → 回 QUEUED；仅不可重试错误才 FAILED

设备 / WiFi 等资源在准入时被别的 PlanRun 抢占，是**调度结果**而非**故障**：

- **可重试 → 回 QUEUED** + `queue_reason`（`DEVICE_BUSY` / `RESOURCE_BUSY`）+ `next_admission_at`，由 queue pump 重试。包括：
  - 设备被其他 active Job 占用（复查发现或 INSERT 撞 `uq_job_active_per_device`）。**P0 过渡语义**是结构化 FAILED（`reason=device_conflict_at_materialization`，见缺口①）；P1 上线后统一改为回 QUEUED。
  - WiFi 池满——**现状** `_sync_allocate_devices` 抛 `AllocationError`（`backend/services/plan_dispatcher_sync.py:210,243`）后被 `complete_plan_run_dispatch` 转 **FAILED**（`backend/services/plan_dispatcher_sync.py:585-607`）。**目标**：归入可重试，回 QUEUED，不再 FAILED。
- **不可重试 → FAILED**：仅限数据不一致（设备被删 / host_id 变 NULL，`plan_dispatcher_sync.py:545-575`）、脚本契约错误（`missing_scripts`，`plan_dispatcher_sync.py:316,395`）、计划配置失效（snapshot 无 enabled step，`plan_dispatcher_sync.py:501`）。

### 3. execution_state 子状态与超时矩阵

RUNNING job 新增 `execution_state` 子状态列（拟），把「一个 RUNNING job 此刻在干什么」显式化，让超时判据能按子状态选择正确的时钟。

| execution_state | 含义 | 续 lease？ | RUNNING 超时判据 | patrol stall 判据 |
|-----------------|------|:----------:|------------------|-------------------|
| `WAITING_EXECUTION_SLOT` | 等 OperationScheduler permit | 是 | 用 **Coordinator 心跳** 判断（非执行心跳） | 不判断 |
| `EXECUTING_STEP` | 正在跑某个脚本 step | 是 | 用 **执行心跳**（`last_execution_heartbeat_at`）判断 | 按 **step timeout**（permit 后起算） |
| `PATROL_SLEEP` | patrol 周期间 sleep | 是 | 用 **Coordinator 心跳** 判断 | 按 **next patrol deadline**（`last_patrol_heartbeat_at` + interval×multiplier，沿用 `PATROL_STALL_MULTIPLIER`，`recycler.py:434`） |
| `WAITING_BARRIER` | 等同 host 内其他设备到达阶段屏障 | 是 | 用 **Coordinator 心跳** 判断 | 不判断 |

规则说明：

- **全部子状态都续 lease**——设备始终被合法占用，lease 存活与执行进度是两回事（不变量③）。
- `EXECUTING_STEP` 是唯一「执行器必须活跃」的态，用执行心跳判超时；其余三态执行器可以合法空闲，改用**较宽松的 Coordinator 心跳**（per-host 粒度，见 §5）判断整台 host 是否失联。
- **step timeout 只在 `EXECUTING_STEP` 计时，且从获得 permit 后开始**（不变量②）。`WAITING_EXECUTION_SLOT` 停留再久也只是 queue-latency，不是脚本超时。
- **恢复交互（关键）**：`execution_state` 必须进入 recovery-sync 的冻结 payload（`backend/api/routes/agent_api.py:151` 现有 `_build_recovery_job_payload`，调用点 `:2225,2282`），否则 `UNKNOWN→RUNNING` 恢复后会套错超时规则（例如把 `PATROL_SLEEP` 当 `EXECUTING_STEP` 判 step timeout 误杀）。

### 4. 准入原子提交流程（含 TOCTOU 处理与回滚边界）

准入 pump 把「慢操作」与「持锁短事务」严格分离，杜绝在持有设备行锁的事务里做外部 IO（长事务）：

```
① 慢操作在 admission transaction 之外先完成（PRECHECK 态）：
     - 脚本 sha256 SocketIO 校验（现有 _drive_dispatch_gate / precheck，plan_precheck.py + precheck/*)
     - host 在线检查
     - 写 precheck_started_at / admission_attempt_id（供 reaper stale recovery，见状态机章节）
   ↓
② 开启短 admission transaction：
     - 锁 PlanRun / PlanRunHost 行
     - 按 device_id 固定顺序（升序）对 plan_run_target_device 清单做最终资源复查：
       全部设备无 active Job（uq_job_active_per_device 口径）+ WiFi 池余量足够
   ↓
③ 同一事务内完成资源分配：WiFi 等资源分配（写分配行）
   ↓
④ 同一事务内一次性 bulk INSERT 全部 Job + 写 total_job_count 基线 +
   PlanRun PRECHECK→RUNNING → COMMIT
```

**回滚边界（评审收口）**：**不采用**「savepoint 只回滚 Job 物化」的设计——WiFi 等资源可能已在外层事务中分配，只回滚 Job 的 savepoint 会留下资源占用残留。规则：「最终资源复查 → WiFi 等资源分配 → 全部 Job bulk insert → PRECHECK→RUNNING」必须全部在**同一个短 admission transaction** 内完成；任一竞争失败（②复查不过 / ③`AllocationError` / ④INSERT 撞 `uq_job_active_per_device`）则**整个 admission transaction 回滚**——Job 与资源分配一起消失、无残留——随后**另开新事务**写回 QUEUED + `queue_reason`（`DEVICE_BUSY` / `RESOURCE_BUSY`）+ `next_admission_at`，由 queue pump 稍后重试（不变量④）。PlanRun 本身**保持 QUEUED**（不 FAILED）。

**硬约束**：

- **外部 SocketIO 脚本校验绝不能放在持有设备行锁的事务内**。现状 `_run_dispatch_gate_sync` 用 `asyncio.run(_drive_dispatch_gate(...))`（`backend/services/plan_dispatcher_sync.py:336-340`）驱动跨进程 RPC，一次校验可能数秒；若在设备行锁内执行，60 host 并发时锁等待会雪崩。慢操作必须在 ①，admission transaction 只覆盖 ②-④。
- queue pump 用 `FOR UPDATE SKIP LOCKED`（参照 claim 现有模式 `backend/api/routes/agent_api.py:411`）+ 幂等 `admission_token` + **单 pump 实例准入**（APScheduler 单进程内单 job，天然串行；符合 ADR-0018 单进程约束）。
- WiFi 池满（现状 `AllocationError` → FAILED，`plan_dispatcher_sync.py:585-607`）改为可重试回 QUEUED。

### 5. 批量续租接口契约（P0 已实现）

**P0 已实现**：`POST /api/v1/agent/leases/extend-batch`（`backend/api/routes/agent_api.py:1306` `extend_leases_batch`）已上线，Agent `LeaseRenewer` 已改为一轮一批量（`backend/agent/lease_renewer.py:115-117`，批量实现 `:228-305`），取代「每 job 每 60s 单独 POST `/jobs/{id}/extend_lock`」的旧路径（单点端点 `agent_api.py:1182-1211` 保留，作为回退）。

**请求**（已实现 schema，`agent_api.py:1232-1249`）：

```json
{
  "host_id": "host-12",
  "agent_instance_id": "uuid4",
  "leases": [
    {
      "job_id": 1001,
      "fencing_token": "29:42",
      "execution_state": "PATROL_SLEEP",
      "progress_marker": {"patrol_cycle_index": 137, "last_progress_at": "..."}
    }
  ]
}
```

**响应**：逐项返回，**结果按 item 隔离**——一个 item 失败不影响其他：

| item 结果 | 含义 | Agent 行为 |
|-----------|------|-----------|
| `renewed` | 续租成功 | 继续 |
| `stale_token` | fencing_token 不匹配 | 触发该 job 的 lease-lost 处置（`agent/lease_renewer.py:193-215` `_handle_lease_lost`，teardown 前重校验 token 防误杀并发 re-claim） |
| `job_not_running` | job 已非 RUNNING | 停该 job 循环 → recovery/sync |
| `lease_missing` | 无 ACTIVE lease 或已过期 | 同上 |

**P0 已实现的契约事实**：

- **单事务集合化校验，结果按 item 隔离**——不是全批原子（契约注释 `agent_api.py:1223-1227`）。一个 `stale_token` 不回滚整批，1000 device 里一个坏 item 不拖垮 999 个健康续租。
- **`RETURNING` 反映真实更新**：SELECT 预分类只是快照；CAS UPDATE 的 `RETURNING` 决定最终 `renewed` 集合，UPDATE 未命中的「本应可续」item 降级为 `lease_missing`，不虚报 `renewed`（`agent_api.py:1407,1433-1444`）。
- **最终 UPDATE 为数据库级 CAS**：`_cas_renew_leases`（`agent_api.py:1262-1304`）——tuple `(job_id, fencing_token)` 对绑定（row-value IN）+ `host_id` + `agent_instance_id`（非空时）归属校验 + `status=ACTIVE` + `expires_at > now` + `Job.status == RUNNING` join——本次评审后补齐，与本 ADR 收口同步落地（此前仅 `job_id` 集合 + ACTIVE + `expires_at > now` 守卫 UPDATE，token/归属只在前置 SELECT 校验，SELECT 与 UPDATE 之间的 token 轮转可被旧 Agent 覆写）。
- **分块**：Agent 按块发送（默认 100，`AGENT_LEASE_EXTEND_BATCH_CHUNK`，`lease_renewer.py:53,240-243`），避免单请求 body 过大 + 单事务过长。
- **旧后端自动回退**：批量端点返回 404/405 时 Agent 本进程永久回退单点 `extend_lock`（`lease_renewer.py:265-269`），Agent 与后端可独立升级。
- **三信号前向兼容**：`execution_state` / `progress_marker` 已进请求 schema，后端接受但忽略（`agent_api.py:1235-1242`）。

**已知剩余缺口**：续租成功仍镜像单点语义顺带刷 `job.updated_at` 兼作 RUNNING 保活（`agent_api.py:1421-1428`）——续租/保活耦合未解；`execution_state` / `progress_marker` 只收不落库。

**P1 目标**（不变量③）：三信号共载落库——`fencing_token`（租约存活）、请求到达即执行器存活证明（回填 `last_execution_heartbeat_at`）、`progress_marker`（业务进度，回填 `last_progress_at` / patrol 计数）。传输一次、语义三份、落库三列。`WAITING_*` / `PATROL_SLEEP` 子状态的存活由 per-host Coordinator 心跳兜底（§3 矩阵），不要求每设备高频执行心跳。

### 6. O(1) 终态聚合计数器 + 单一 terminalization 入口

**现状（已核实）**：`PlanAggregator.on_job_terminal` 每次终态 `SELECT` 全部兄弟 job 重算（`backend/services/aggregator.py:46-51`），O(N²)（缺口③）。且现有聚合语义**区分 FAILED 与 ABORTED**：`apply_plan_run_aggregation` 分别统计 `failed_only` 与 `aborted`（`backend/services/plan_run_aggregation.py:43-44`），并据此推导 SUCCESS / PARTIAL_SUCCESS / FAILED（`:58-66`）、写入 `result_summary` 的 `failed_only` / `aborted` 字段（`:83-85`）。

**目标**：在 `plan_run` / `plan_run_host` 上维护**原子计数器列**（拟），字段统一为五列——必须保持与现有聚合同等的区分度，否则终态判定无法由计数器直接推导：

- `total_job_count`：目标 Job 总数（admission transaction 物化时写入）
- `terminal_job_count`：已终态 Job 数
- `completed_job_count`：COMPLETED 数
- `failed_job_count`：FAILED 数（对应 `failed_only` 语义，不含 aborted）
- `aborted_job_count`：ABORTED 数

每个 Job 终态时：

- 在终态事务内 `UPDATE plan_run SET terminal_job_count = terminal_job_count + 1, ...`（单行原子自增，参照 patrol-heartbeat 现有 `func.greatest` / 原子 UPDATE 模式 `backend/api/routes/agent_api.py:1596`）。
- 当 `terminal_job_count == total_job_count` 时才触发一次 `apply_plan_run_aggregation` 做终态收敛 + chain trigger（`backend/services/aggregator.py:51-57`）。
- 聚合读取从「全表扫兄弟 job」降为「读五个计数器列」——O(1)。

**单一 terminalization 入口原则（评审收口）**：Job 的终态入口现有多处——`/jobs/{id}/complete`、abort reaper、recycler、session_watchdog、device_lease_reconciler——**全部终态入口必须经过同一个 terminalization 服务完成「置终态 + 计数器自增」**；任何入口绕开集中服务直接 UPDATE `status`，都会造成计数漂移（增了状态没增计数，或反之）。机制二选一：**集中服务（应用层）** vs **DB trigger**。本 ADR 选**集中服务 + 低频对账 sweep 自愈**（定期核对 `terminal_job_count == COUNT(*)`，漂移即修正并告警）：trigger 虽然天然全覆盖，但隐藏写放大、难以单测、且与现有 `JobStateMachine` 集中校验（`backend/services/state_machine.py`）的应用层治理路线相悖。服务签名、sweep 周期等实现细节留待落地。

保留 `FOR NO KEY UPDATE` 串行化锁（`backend/services/aggregator.py:36-42` 现有注释详述的死锁规避），只是把锁内的全量 SELECT 换成计数器读取。

---

## 备选方案与权衡

### 方案 A：Job 级设备排队，放开唯一索引到 RUNNING/UNKNOWN（驳回）

即把 `uq_job_active_per_device` 的 `postgresql_where` 从 `status IN ('PENDING','RUNNING','UNKNOWN')`（`backend/models/job.py:77-79`）收窄为只在 `RUNNING/UNKNOWN` 时唯一，允许多个 PlanRun 的 PENDING job 在同一设备上排队。

- **驳回理由**：PENDING job 排队意味着「多个不同 PlanRun 争抢同一台设备」，破坏「同一 PlanRun 全部设备属于同一次长跑」的语义边界——设备会在 A 计划和 B 计划之间被 Job 级调度器随意切换，无法保证一次长跑的设备集合稳定。且把准入决策下沉到 claim 层，跨计划优先级 / aging / FIFO 无处安放。本 ADR 改为**在 PlanRun 层排队**（QUEUED），设备唯一性仍由 PENDING 占位保证（不变量①）。

### 方案 B：`max_parallel_devices` 分批跑完整 Job（驳回）

即把一个 PlanRun 的 N 台设备拆成若干批，每批跑完整生命周期（init→patrol→teardown）再放下一批。

- **驳回理由**：长稳测试的语义是「N 台设备**同时**跑 8 天」，不是「N 台设备分批各跑 8 天」。分批会让同一 PlanRun 的设备处于不同的时间进度，patrol wave / barrier 无法对齐，failure_threshold（`backend/models/plan_run.py:44`）的「失败设备占比」失去同一时间基准。把长生命周期状态当成可分批调度的瞬时任务，是本 ADR 明确反对的。第三层 OperationScheduler 限制的是**瞬时脚本执行并发**，不是长跑设备数——两者正交。

### 方案 C：`GLOBAL_MAX_RUNNING_JOBS` 全局 RUNNING 上限（驳回）

即设一个全局「同时 RUNNING 的 Job 数」上限来限流。

- **驳回理由**：`RUNNING` 在本平台是**长生命周期状态**（一个 patrol job RUNNING 8 天），不是瞬时资源。用全局 RUNNING 上限限流，等价于「同时只能有 K 台设备在做长稳测试」，与 1000 device 同时长跑的目标直接冲突。真正需要限流的是**瞬时资源**（脚本执行、ADB 调用），已由第三层 OperationScheduler 按 host 粒度解决。把长生命周期状态当瞬时资源计数，是典型的模型错配。

### 方案 D：维持现状 + 局部修补（驳回）

- **驳回理由**：四个结构性缺口都是模型层面的，局部修补（例如 P0 已做的 active-job 校验 + `IntegrityError` 兜底）只能缓解 B4 悬挂的症状——竞争仍以 FAILED 终态收场、无排队重试——不解决「无跨计划排队」「并发语义错配」「单进程载荷平方放大」「三信号混用」。且持续修补会让 `plan_dispatcher_sync.py` / `recycler.py` / `aggregator.py` 进一步膨胀。P0 修复定位为 P1 队列上线前的**止血过渡**，不是终态方案。

---

## 数据模型变更

> 仅列出列名与用途，**不写 migration SQL**（迁移遵循 ADR-0008 Alembic-only，落地时单独出 migration）。以下均标「拟」。

### PlanRun 新增列（拟，`backend/models/plan_run.py`）

| 列 | 类型 | 用途 |
|----|------|------|
| `priority` | INT，默认 0 | 准入优先级；高优先级先出队 |
| `queue_reason` | VARCHAR | QUEUED 原因：`DEVICE_BUSY` / `RESOURCE_BUSY` / `PRIORITY_WAIT` / `PRECHECK_STALE` / NULL |
| `next_admission_at` | TIMESTAMP | 下次准入尝试时间（退避 + aging 用） |
| `admission_token` | VARCHAR | 幂等准入令牌，防 pump 重复物化 |
| `admission_attempt_id` | VARCHAR | 本次准入尝试标识；reaper 判定 stale PRECHECK 归属（状态机章节） |
| `precheck_started_at` | TIMESTAMP | 进入 PRECHECK 的时刻；reaper stale 判据（状态机章节） |
| `enqueued_at` | TIMESTAMP | 进入 QUEUED 的时刻；aging 计算基准 |
| `total_job_count` | INT，默认 0 | 目标 Job 总数（准入物化时写入）；`terminal == total` 触发聚合 |
| `terminal_job_count` | INT，默认 0 | O(1) 聚合计数器：已终态 Job 数 |
| `completed_job_count` | INT，默认 0 | 同上：COMPLETED 数 |
| `failed_job_count` | INT，默认 0 | 同上：FAILED 数（`failed_only` 语义，不含 aborted，对齐 `plan_run_aggregation.py:43`） |
| `aborted_job_count` | INT，默认 0 | 同上：ABORTED 数（对齐 `plan_run_aggregation.py:44`） |

### plan_run_target_device 新表（拟，评审收口新增）

QUEUED PlanRun 不创建 Job 后，「本次执行要跑哪些设备」不能只存在于 `run_context.dispatch_device_ids` JSON（现状写入 `backend/api/routes/plans.py:646`，消费 `backend/services/precheck/runner.py:342-343,388`）——JSON 无法做关系型 join、无法建索引、无法承载 1000 设备规模的集合查询。prepare 阶段固化为关系行：

| 列 | 类型 | 用途 |
|----|------|------|
| `id` | INT PK | |
| `plan_run_id` | INT FK → plan_run.id | |
| `plan_run_host_id` | INT FK → plan_run_host.id | 所属 host 分组行 |
| `device_id` | INT FK → device.id | 目标设备 |
| `host_id_snapshot` | VARCHAR | prepare 时刻设备所在 host 快照（设备后续换 Host 时用于审计对比） |
| `sort_order` | INT | 稳定展示顺序（准入复查按 `device_id` 升序加锁，与展示顺序解耦） |
| 唯一约束 | `(plan_run_id, device_id)` | 一次执行同一设备至多一行 |

**用途**：准入前「全就绪」查询（清单 × 设备/host 状态 join）、按 Host 分组（驱动 `plan_run_host`）、设备删除/换 Host 的审计（`host_id_snapshot` vs 现值）、1000 设备规模的集合查询、admission transaction 内的关系型 join 复查（§4 步骤②）。

**决策（评审收口）**：`plan_run_target_device` 与 `plan_run_host` 都在 **prepare 阶段创建**，作为**不可变派发快照**（与 `plan_snapshot` 同级语义：QUEUED 期间清单不随设备增删漂移）。`run_context.dispatch_device_ids` 保留为兼容读路径，权威口径迁移到本表。

### PlanRunHost 新表（拟）

| 列 | 类型 | 用途 |
|----|------|------|
| `id` | INT PK | |
| `plan_run_id` | INT FK → plan_run.id | |
| `host_id` | VARCHAR FK → host.id | |
| `device_count` | INT | 本 host 在本 PlanRun 的设备数 |
| `status` | VARCHAR | host 维度状态（拟枚举：`PENDING_ADMISSION` / `ADMITTED` / `RUNNING` / `TERMINAL`，落地时收敛）；与 `phase` 正交——status 表达准入/存续，phase 表达业务阶段 |
| `phase` | VARCHAR | host 内业务阶段：`INIT` / `PATROL` / `TEARDOWN` / `BARRIER_WAIT` |
| `admitted_at` | TIMESTAMP | 本 host 分组随 PlanRun 通过准入的时刻 |
| `coordinator_epoch` | INT | Coordinator 世代号；host 重启/接管时自增，防旧 Coordinator 迟到心跳复活状态 |
| `coordinator_heartbeat_at` | TIMESTAMP | per-host Coordinator 心跳（§3 矩阵的宽松兜底信号） |
| `admission_batch_size_snapshot` | INT | 准入时刻 OperationScheduler 并发上限的**审计快照**(仅记录,不参与运行时限流)。**双语义收口(评审)**:实际生效值 = Host/Agent 当前配置,**可热调**;热调作用于 host 全局 OperationScheduler,**不**为每个 PlanRun 建立独立限额;调小时不抢占已持有的 permit,只阻止新请求进入,直到 active 数降至新上限 |
| `last_error` / `queue_reason` | VARCHAR | host 维度最近一次失败 / 排队原因（准入复查失败时定位是哪台 host 的哪批设备） |
| `total_job_count` / `terminal_job_count` / `completed_job_count` / `failed_job_count` / `aborted_job_count` | INT | host 维度镜像 §6 五计数器（barrier / phase 推进判据） |
| 唯一约束 | `(plan_run_id, host_id)` | 一个 PlanRun 在一个 host 一行 |

**创建时机（评审收口）**：与 `plan_run_target_device` 同在 prepare 阶段创建、同为不可变派发快照；`admitted_at` / `coordinator_epoch` / `coordinator_heartbeat_at` / `admission_batch_size_snapshot` 等 Coordinator 相关字段在**准入（PRECHECK→RUNNING）后启用**，QUEUED 期间为空。

### JobInstance 新增列（拟，`backend/models/job.py`）

| 列 | 类型 | 用途 |
|----|------|------|
| `execution_state` | VARCHAR | 子状态：`WAITING_EXECUTION_SLOT` / `EXECUTING_STEP` / `PATROL_SLEEP` / `WAITING_BARRIER`（§3） |
| `last_execution_heartbeat_at` | TIMESTAMP | 执行器存活信号（不变量③）；`EXECUTING_STEP` 超时判据 |
| `last_progress_at` | TIMESTAMP | 业务进度信号（不变量③）；非 patrol step 的进度证明 |

> 注：`last_patrol_heartbeat_at` / `current_failure_streak` / `next_retry_at` / `manual_action` 已存在（`backend/models/job.py:47-52`，ADR-0022），复用不新增。

### 索引考量（落地时细化）

- QUEUED 出队：partial index `idx_plan_run_admission_queue` on `(priority DESC, enqueued_at ASC) WHERE status='QUEUED'`——对齐 pump ORDER BY；`next_admission_at` 仅作 WHERE 过滤。aging 表达式无法完整吃进 btree，QUEUED 基数小可接受残留 sort。
- `plan_run_target_device`：唯一 `(plan_run_id, device_id)`；辅助 `(device_id)`、`(plan_run_host_id)`、`(plan_run_id, sort_order)`（`idx_prtd_plan_run_sort`，admission 有序扫描）。
- `plan_run_host (plan_run_id, host_id)` 唯一索引 + `(host_id, phase)` 辅助索引。
- 设备矩阵：`idx_job_instance_plan_run_status` 左前缀服务 `plan_run_id` 过滤；端点以 Job⋈Device 单查询减往返，并用 `stability_plan_run_devices_query_duration_seconds` 观测 1000-device 耗时。

---

## 状态机变更

### PlanRun 状态机（`backend/services/state_machine.py:47-60` `PLAN_RUN_VALID_TRANSITIONS`）

**现状（已核实）**：`PlanRunStatus` 只有 `RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED`（`backend/models/enums.py:13-18`）；合法迁移为 `RUNNING → {SUCCESS, PARTIAL_SUCCESS, FAILED}` + 有意的 `FAILED → RUNNING`（人工重试，`backend/services/state_machine.py:53-55`，`backend/services/precheck/runner.py:317` `retry_plan_run_dispatch`）。

**目标**：新增 `QUEUED` / `PRECHECK` 两态，扩展迁移表：

```
QUEUED   → PRECHECK           # pump 选中，进入慢操作（脚本校验 / host 检查）；写 precheck_started_at + admission_attempt_id
QUEUED   → FAILED             # abort，或排队阶段即可判定的不可重试错误（不变量④的不可重试类）
PRECHECK → RUNNING            # admission transaction 提交成功，正式入场
PRECHECK → QUEUED             # a) 原子复查 / 物化竞争失败回队（不变量④）
                              # b) reaper 将超时 PRECHECK 恢复回队（stale recovery，见下）
PRECHECK → FAILED             # 不可重试错误（脚本契约 / 数据不一致 / 配置失效），以及 PRECHECK 期间取消 / abort
RUNNING  → {SUCCESS, PARTIAL_SUCCESS, FAILED}   # 保留现状
FAILED   → QUEUED             # 人工重试改走准入队列（评审收口，替代现状 FAILED → RUNNING，见下）
```

- **无 `QUEUED → QUEUED` 自环（评审收口）**：准入竞争失败写回 QUEUED 只更新排队字段（`queue_reason` / `next_admission_at`），状态本身未变，**不构成状态机迁移**，无需自环。（从 PRECHECK 竞争失败返回走 `PRECHECK → QUEUED`。）
- **`FAILED → RUNNING` 改为 `FAILED → QUEUED`（评审收口）**：现状人工重试 `retry_plan_run_dispatch`（`backend/services/precheck/runner.py:317`）把 FAILED 的 PlanRun 直接重置回 RUNNING 再重走 dispatch gate。QUEUED/PRECHECK 上线后若保留该迁移，人工重试将**绕过新准入流程**（不排队、不做 admission transaction 最终复查、不受优先级/aging 约束）。目标：人工重试改为置回 **QUEUED**（沿用 `dispatch_device_ids` 校验与幂等防重逻辑），由 pump 统一走 `QUEUED → PRECHECK → RUNNING`；`PLAN_RUN_VALID_TRANSITIONS` 中 `FAILED → RUNNING` 移除、改注册 `FAILED → QUEUED`。
- **PRECHECK stale recovery（评审收口）**：PRECHECK 是「pump 已选中、慢操作进行中」的中间态——期间 pump / SAQ / Backend 崩溃会把 PlanRun 永久卡在 PRECHECK。新增 `precheck_started_at` / `admission_attempt_id` 两列（数据模型章节），由 reaper——参照现有 `reconcile_stale_precheck_runs`（`backend/scheduler/precheck_reaper.py:133`，stale 阈值模式 `PRECHECK_QUEUE_STALE_SECONDS` / `PRECHECK_ACTIVE_STALE_SECONDS`，`precheck_reaper.py:31-33`）——将 `precheck_started_at` 超时、且 `admission_attempt_id` 无活跃 SAQ job 归属的 PRECHECK 行**恢复回 QUEUED**（`queue_reason=PRECHECK_STALE`）。与现状 reaper 对 stale run 直接 `_fail_plan_run`（`precheck_reaper.py:93`）不同：恢复优先，达到重排上限（沿用 `MAX_PRECHECK_REENQUEUE_ATTEMPTS` 思路，`precheck_reaper.py:51`）才 `PRECHECK → FAILED`。
- `PlanRunStateMachine.transition`（`backend/services/state_machine.py:62-80`）需接受新态；`prepare_plan_run` 改为建 `QUEUED`（现状建 `RUNNING`，`backend/services/plan_dispatcher_sync.py:343,419`）。
- `DEGRADED` 仍仅历史可读、不再生产（与 CLAUDE.md 状态机约定一致）。

### Job 状态机（`backend/services/state_machine.py:12-22` `VALID_TRANSITIONS`）

**不新增顶层状态**。`execution_state` 是 RUNNING 的**子状态列**，不是 `JobStatus` 枚举成员——`PENDING → RUNNING → COMPLETED/FAILED/ABORTED/UNKNOWN` 及 `UNKNOWN → RUNNING/FAILED`（`backend/services/state_machine.py:12-22`）全部保留不变。execution_state 只在 `status == RUNNING` 期间有意义，终态时置 NULL。

---

## 与现有子系统的交互矩阵

| 子系统 | 现状锚点 | 本 ADR 需要的改动 |
|--------|----------|-------------------|
| **queue pump（新）** | 无 | 新增 APScheduler IntervalTrigger job（单进程内，符合 ADR-0018）；`FOR UPDATE SKIP LOCKED` 选 QUEUED、驱动慢操作、admission transaction 物化；单实例串行准入 |
| **dispatcher** | `prepare_plan_run` 建 RUNNING（`plan_dispatcher_sync.py:343,419`）、`complete_plan_run_dispatch` 物化（`:455`）；P0 已补 active-job 校验（`reason=active_job`，`:96-158`）与物化 `IntegrityError` 兜底（结构化 FAILED，`reason=device_conflict_at_materialization`，与本 ADR 收口同步落地） | `prepare` 改建 QUEUED + 固化 `plan_run_target_device` / `plan_run_host` 快照；物化逻辑迁入 pump 的 admission transaction，竞争失败整体回滚回 QUEUED（P0 的 FAILED 兜底改回队）；WiFi `AllocationError`（`:585-607`）改可重试回队 |
| **人工重试** | `retry_plan_run_dispatch` 置 FAILED→RUNNING 重走 dispatch gate（`precheck/runner.py:317`，`state_machine.py:53-55`） | 改置 FAILED→QUEUED 入队，统一走准入流程（状态机章节） |
| **precheck reaper** | `reconcile_stale_precheck_runs` 对 stale run `_fail_plan_run`（`precheck_reaper.py:133,93`） | 超时 PRECHECK 恢复回 QUEUED（`queue_reason=PRECHECK_STALE`），达重排上限才 FAILED（状态机章节） |
| **claim** | `_claim_jobs_for_host`（`agent_api.py:319`）按 lease 过滤忙设备 | 基本保留；claim 只认领已 RUNNING PlanRun 的 PENDING job（现有 `PlanRun.status=='RUNNING'` 过滤 `agent_api.py:401` 天然兼容） |
| **recycler** | `recycle_once`（`recycler.py:567`）；RUNNING 超时看 `updated_at`（`:639`）；patrol stall（`:665`） | 按 `execution_state` 选时钟（§3 矩阵）；`WAITING_EXECUTION_SLOT` 不判超时/ stall（不变量②）；RUNNING 超时改看 `last_execution_heartbeat_at` 而非 `updated_at`（不变量③）；终态置换走 terminalization 服务（§6） |
| **session_watchdog** | host 心跳超时 → job UNKNOWN（`session_watchdog.py:36-73`）；UNKNOWN grace → FAILED（`:76-99`） | 保留；host 失联仍把该 host 全部 RUNNING job 转 UNKNOWN；可改用 `plan_run_host.coordinator_heartbeat_at` 做更精细的 host 存活判断；终态走 terminalization 服务（§6） |
| **device_lease_reconciler** | `_reconcile_expired_leases`（`device_lease_reconciler.py:64`）、abort reaper（`:300`）、stale UNKNOWN（`:189`） | 保留两段式 lease 过期；lease 存活判据解耦出 `updated_at`（不变量③）后，reconciler 仍以 `lease.expires_at` 为准（`:75`），不受影响；终态走 terminalization 服务（§6） |
| **abort** | `abort_plan_run`（`plan_runs.py:363`）写 `run_context.abort_requested`；reaper 消费（`device_lease_reconciler.py:300-386`） | QUEUED 的 PlanRun abort 直接 QUEUED→FAILED（无 Job 需回收）；PRECHECK 期间 abort 走 PRECHECK→FAILED；RUNNING 的走现有 abort reaper 路径 |
| **aggregator** | 曾全量加载兄弟 job | **P2-1 已改**：委托 `job_terminalization`；`total_job_count>0` 时 O(1) 读五计数器；否则 fallback 全量扫 |
| **dispatch gate / precheck** | `_drive_dispatch_gate`（`plan_precheck.py` → `precheck/*`）跨进程脚本校验 | 移到 pump 的「慢操作」阶段（§4 步骤①），**严禁**在 admission transaction 内执行 |
| **LeaseRenewer（Agent）** | **P0 已批量**：一轮一批量 `extend-batch`（`lease_renewer.py:115-117,228-305`），404/405 自动回退单点（`:265-269`） | P1：三信号落库（§5）；`execution_state` / `progress_marker` 从「接受但忽略」转为回填三列 |
| **SAQ** | `_queue` 与 worker 曾耦合于 `start_saq_worker` | **P0 已拆**：`init_saq_producer` / `start_saq_worker`；`STP_ENABLE_INPROCESS_SAQ=0` 时 producer 仍可 enqueue |

---

## 落地顺序

对齐 P0→P3 分层推进。P0 是「不改架构也必须先做」的止血 + 前置，P1 才引入新层。

### P0：止血与前置（不引入新层，可独立上线）

**已完成（截至本修订，初版随 `d33d936` / `bb294e1` 入库，评审修复随 `64608a9` 入库，见修订记录）**：

1. **批量续租 `extend-batch`**（§5）——单事务集合化、结果按 item 隔离、`RETURNING` 反映真实更新、分块、Agent 批量化 + 404/405 自动回退；缓解缺口③的续租风暴。含评审修复（`64608a9`）：**最终 UPDATE 升级为数据库级 CAS**（`_cas_renew_leases`：tuple `(job_id, fencing_token)` 绑定 + `host_id` / `agent_instance_id` 归属 + `Job.status==RUNNING` join + keepalive RUNNING guard）。
2. **B4 悬挂修复**——`_validate_dispatch_devices_sync` 按 `uq_job_active_per_device` 口径补 active-job 检查（`reason=active_job`）；物化最终竞态补 `IntegrityError` 兜底（`64608a9`：回滚全部本次 Job/WiFi 分配 → 结构化 FAILED，`reason=device_conflict_at_materialization`）。P0 语义为 FAILED，P1 队列上线后改回 QUEUED（不变量④）。
3. **三信号前向兼容**——`execution_state` / `progress_marker` 已进批量续租请求 schema（接受但忽略），P1 落库。

**已完成（续）**：

4. **SAQ producer/worker 拆分**——`init_saq_producer` / `start_saq_worker` 解耦；`STP_ENABLE_INPROCESS_SAQ=0` 时 producer 仍可 enqueue，admission pump 以 producer ready 为准。
5. **心跳减负**——权威设备心跳仍走 `/api/v1/heartbeat`（设备落库唯一写路径）；两端点均返回 `heartbeat_interval_seconds`，Agent 钳位采纳；硬件字段按 `DEVICE_SNAPSHOT_INTERVAL` 降采样。轻量 `/api/v1/agent/heartbeat` 已带同契约（host-only），供后续双通道收敛。

**已完成（续·指标）**：

6. **真实指标**——✅ `stability_admission_queue_latency_seconds` / `stability_admission_queue_depth`；`stability_lease_extend_batch_*`；`stability_plan_run_aggregation_duration_seconds{path}`；`stability_plan_run_devices_query_duration_seconds`；`stability_host_operation_slots_*` / `waiters`（Agent heartbeat `extra.operations` → 控制面 Gauge）。

### P1：准入队列 + OperationScheduler（核心两层）

1. **PlanRun Admission Queue**——✅ QUEUED/PRECHECK 态 + queue pump + admission transaction 原子物化（§2 不变量①④、§4）；`prepare_plan_run` 改建 QUEUED；`FAILED→QUEUED` 人工重试 + PRECHECK stale recovery reaper（状态机章节）；`device_conflict_at_materialization` 从 FAILED 改回 QUEUED。
2. **PlanRunHost / plan_run_target_device / HostRunCoordinator**——✅ prepare 阶段固化两张快照表 + per-host Coordinator 心跳与 `coordinator_epoch`（§3、数据模型）。
3. **Host OperationScheduler**——✅ Agent 侧 per-host 单例 permit 信号量（默认 5，待压测确认），解耦长跑设备数与瞬时执行并发（缺口②、不变量②）；`admission_batch_size_snapshot` 仅审计。含 pending-handoff cancel / abort 顺序收口。
4. **execution_state 矩阵**——✅ 子状态列 + 按态选时钟的 recycler 改造（§3）；三信号拆列落库（不变量③）。INIT→PATROL barrier 已接线（`STP_PHASE_BARRIER_ENABLED`）。

### P2：控制面规模优化

1. **O(1) 聚合计数器 + terminalization 服务**（§6）——✅ `job_terminalization` 单一终态入口 + `plan_run`/`plan_run_host` 五计数器自增 + `apply_plan_run_aggregation_from_counters`；`counter_reconcile` 低频对账 sweep。
2. **实时日志批量化 + 背压**——✅ Agent 批 flush + `log_rate_limit` 背压；控制面 `on_step_log` 兼容单行/批量；`append_log_lines` 批量落盘。
3. **索引与查询优化**——✅ 出队 partial index 改为 `(priority DESC, enqueued_at ASC)` 对齐 pump ORDER BY；`idx_prtd_plan_run_sort`；设备矩阵单 JOIN（job+device+host+lease）+ `stability_plan_run_devices_query_duration_seconds`（1000-device 压测复核用）。

### P3：水平扩展（远期，需突破单进程约束）

1. **准入 pump 的 leader election**——多控制面实例时保证单 pump 准入（现状单进程天然满足，ADR-0018:202）。
2. **SocketIO Redis adapter / Centrifugo**——ADR-0018 已为 1000+ 设备预留（`docs/adr/ADR-0018-...:176,136`）。
3. **控制面多实例**——需先解除 ADR-0002/0018 单进程约束，涉及全部后台调度 job 的 leader 选举，属独立大改，本 ADR 只标方向不展开。

---

## 风险与回滚

| 风险 | 缓解 | 回滚 |
|------|------|------|
| QUEUED 引入后，历史 MANUAL 派发路径行为变化（用户期望「点了就跑」） | pump 间隔足够短（待定，建议 ≤5s）；空闲资源时 QUEUED→RUNNING 近乎瞬时 | 保留 `prepare_plan_run` 直建 RUNNING 的旧路径为 feature flag，可关掉队列 |
| OperationScheduler permit 上限设错，patrol 唤醒风暴打满 permit 导致 step 排队 | permit 默认 5 起步（待压测确认）；queue-latency 告警先于误杀 | permit 上限可热调（作用于 host 全局 Scheduler，调小不抢占已持有 permit，仅阻新进；`admission_batch_size_snapshot` 只是审计快照不受影响）；极端情况退回「线程池大小 = 并发」的旧语义 |
| 批量续租逐项隔离实现有 bug，坏 item 拖累整批 | 单事务集合校验 + item 级结果隔离 + CAS UPDATE + 契约测试覆盖坏 item 混入（P0 已落地并有测试） | 回退单 job `extend_lock`（`agent_api.py:1182` 保留不删；Agent 404/405 自动回退已实现，`lease_renewer.py:265-269`） |
| O(1) 计数器与实际 Job 数漂移（终态入口绕过计数自增） | **单一 terminalization 服务**收口全部终态入口（§6）+ 低频对账 sweep 自愈（核对 `terminal_job_count == COUNT(*)`）+ 终态事务内原子自增 + `FOR NO KEY UPDATE` 串行（`aggregator.py:36`） | 回退全量 SELECT 聚合（`aggregator.py:46-49` 保留） |
| PRECHECK 中间态卡死（pump/SAQ/Backend 崩溃） | `precheck_started_at` + `admission_attempt_id` + reaper stale recovery 回 QUEUED（状态机章节） | reaper 关闭时人工 UPDATE 回 QUEUED（低频运维操作） |
| execution_state 未进 recovery payload，恢复后套错超时 | §3 硬性要求纳入冻结 payload；测试覆盖 UNKNOWN→RUNNING 后各子状态超时判据 | execution_state 缺省回退到 `updated_at` 时钟（退化为现状） |
| 三信号拆列迁移期间新旧判据并存不一致 | 迁移期双写 `updated_at` + 新列；recycler 先读新列、缺失 fallback 旧列 | 保留 `updated_at` 判据，先不删 |

**分阶段压测**：沿用 ADR-0019 的阶梯思路（`docs/adr/ADR-0019-...:305` 的 3→10→44），扩展为 **44→60→100 host** 灰度，每档验证续租成功率、准入延迟、聚合耗时、误杀率后再进下一档。

---

## 关联 ADR

- **扩展 ADR-0019**（Device Lease + 容量）：保留 `device_leases` + fencing_token + `uq_job_active_per_device`（第四层）；把「Agent 自说自话的 available_slots」上收为控制面三层调度。
- **扩展 ADR-0020**（Plan/PlanStep）：PlanRun 从「派发即 RUNNING」演进为「QUEUED→PRECHECK→RUNNING」，`plan_snapshot` 语义不变；目标设备清单同步快照化（`plan_run_target_device`）。
- **兼容 ADR-0021**（派发门禁）：脚本 sha256 校验 / 热更新软禁保留，移入 pump 慢操作阶段，严禁进 admission transaction。
- **扩展 ADR-0022**（patrol 心跳）：复用 `last_patrol_heartbeat_at` / `current_failure_streak` / `manual_action`；`PATROL_SLEEP` 子状态的 stall 判据沿用 `PATROL_STALL_MULTIPLIER`。
- **受约束于 ADR-0002 / ADR-0018**（单进程）：**queue pump 是控制面 APScheduler 单进程内 job**；**HostRunCoordinator 与 OperationScheduler 在 Agent 侧**（每 host 进程内单例，见 §1 部署位置表），不受控制面单进程约束影响；水平扩展（P3）留待专门 ADR 解除单进程约束。
- **推进 ADR-0011**（可观测性）：queue-latency、续租成功率、per-host 并发度、聚合耗时为新增指标族提供落地场景。
- **迁移遵循 ADR-0008**（Alembic-only）：本文数据模型变更落地时单独出 migration，不在此写 SQL。

---

## 待定清单（需压测 / 评审回填）

- pump 扫描间隔、`next_admission_at` 退避曲线、aging 提权阈值——**待定**。
- OperationScheduler `max_concurrent_operations` 默认值（暂定 5）——**待压测确认**。
- 批量续租分块大小（P0 现值 100，env 可调）与续租间隔——**待压测确认**。
- 三信号各自的超时窗口（执行心跳 / Coordinator 心跳 / 业务进度）——**待定**，需与现有 900s/300s（`job_timeout_config.py:47,54`）对齐后灰度下调。
- `PlanRunHost` 是否需要独立的 barrier 协调状态机（vs. 纯计数器判 barrier 到达）——**已接线脚手架**：Agent `pipeline_engine` INIT→PATROL 调用 `arrive`/`wait`（`STP_PHASE_BARRIER_ENABLED`，默认开）；是否升级为独立状态机仍可评审。
- `PlanRunHost.status` 枚举值收敛——**待落地细化**。
- terminalization 服务签名与对账 sweep 周期——**已落地**（`on_job_terminal` / `on_job_terminal_sync`；sweep 默认 300s，env 可调）；压测后再调周期。
- 目标里程碑编号——**待人工确定**（ADR 编号已定为 0026）。

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-15 | 初稿（`DRAFT-plan-execution-scaling.md`），随 `d33d936` 入库 |
| 2026-07-15 | 按评审意见收口，更名 `ADR-0026-plan-execution-scaling.md`。六项：① 新增 `plan_run_target_device` 目标设备关系表（数据模型章节）；② `PlanRunHost` 补 `status` / `admitted_at` / `coordinator_epoch` / `execution_batch_size` / `last_error` / `queue_reason`，明确与目标设备清单同在 prepare 创建为不可变派发快照（数据模型章节）；③ 状态机修正——补 `QUEUED→FAILED`、`PRECHECK→FAILED`（含取消），`FAILED→RUNNING` 改 `FAILED→QUEUED`，新增 `precheck_started_at` / `admission_attempt_id` + reaper stale recovery，删除 `QUEUED→QUEUED` 自环（状态机变更章节）；④ 准入回滚边界改为单一短 admission transaction 整体回滚，弃「savepoint 只回滚 Job 物化」表述（§4）；⑤ 计数器补全为 total/terminal/completed/failed/aborted 五列 + 单一 terminalization 服务原则（推荐集中服务 + 低频对账 sweep，§6）；⑥ 现状描述改「P0 已实现 / 已知剩余缺口 / P1 目标」三段式，消除「批量续租拟新增」「派发校验只查 lease」与已落地代码的矛盾（背景、§5、落地顺序 P0） |

**提交历史备注（溯源，不改写历史，仅登记边界）**：批量续租 / B4 派发校验 / 本 ADR 初稿实际随 `d33d936`（**混合提交**，含无关的前端 host 列表缓存修复等改动）入库；`bb294e1` 为 `progress_marker` 类型修正（str → 结构化 object）与批量续租双端测试补充；`6030f5a` 删除无引用的 async dispatch device validator（`backend/services/plan_dispatcher.py`），避免与 sync 侧 active-job 校验漂移；**`64608a9` 为 P0 评审修复独立提交**（批量续租最终所有权 CAS `_cas_renew_leases` + B4 物化 `IntegrityError` 兜底 + 全部配套测试），CAS/B4「已实现」记录以此为准。

| 日期 | 变更（续） |
|------|------|
| 2026-07-16 | 评审终审通过，状态 Proposed → **Accepted**。两项语义收口：① OperationScheduler 部署位置与共享范围定权威口径（§1 部署位置表）——queue pump 在控制面 APScheduler、HostRunCoordinator 与 OperationScheduler 在 Agent 侧、**同一 host 全部 PlanRun/Job/Coordinator 共享单例 Scheduler**，调度队列须支持 FIFO/公平/等待取消/shutdown 唤醒；修正「关联 ADR」中误写控制面侧的表述。② `execution_batch_size` 更名 `admission_batch_size_snapshot` 并收口双语义——实际生效值为 Host/Agent 当前配置可热调（作用于 host 全局、调小不抢占已持有 permit），快照列仅审计,消除与风险表「热调」的矛盾。P0 修复 commit 溯源回填 `64608a9` |
| 2026-07-20 | P1 Step 5b 正确性收口 + barrier 接线；P0 SAQ producer/worker 拆分 + 心跳 backpressure/降采样；P2-1 `job_terminalization` + O(1) 计数器 + `counter_reconcile` sweep。落地顺序章节同步勾选。 |
| 2026-07-20 | P2-2：`step_log` 批量化 + `log_rate_limit` 背压；恢复 `_MQStepLogger`→SocketIO 通路（`STP_STEP_LOG_STREAM` 可关）。 |
| 2026-07-20 | P2-3 + P0 指标：出队索引 retune + `idx_prtd_plan_run_sort`；设备矩阵单 JOIN + 耗时 Histogram；admission/extend-batch/aggregation/host-slots Prometheus 埋点。 |
