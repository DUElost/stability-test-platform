# ADR-XXXX（草案，编号待人工确定）: 大规模化测试计划执行架构（PlanRun 准入队列 + 四层调度 + 控制面减负）

- 状态：Draft / Proposed
- 优先级：P0
- 目标里程碑：M5（待定）
- 日期：2026-07-15
- 决策者：平台研发组 / 架构组
- 标签：调度, 准入队列, 容量, 规模化, 状态机, 租约, 心跳, 聚合

> 本文为 **草案**。凡标注「待定 / 待压测确认」处，均需在灰度与压测后回填参数或收敛设计。文中严格区分「现状（已核实，配 `file:line`）」与「目标（本 ADR 提议）」，请勿把目标段当作已实现事实。
>
> 规模目标：**60+ host、1000+ device 同时长稳测试**。ADR-0019 的压测阶梯只到 44 host（`docs/adr/ADR-0019-android-device-lease-and-capacity-scheduling.md:305`），本 ADR 面向下一量级。

---

## 背景

平台已在 ADR-0019（Device Lease + 容量 + fencing）、ADR-0020（Plan/PlanStep 一次性切换）、ADR-0021（派发门禁）、ADR-0022（patrol 心跳聚合）四份 ADR 上完成了「单 PlanRun 内」的执行闭环。但把目标从 44 host 拉到 60+ host / 1000+ device 后，暴露出四个**结构性缺口**，它们不是参数调优能解决的，而是模型层面的错配。

### 缺口①：跨 PlanRun 无排队机制 + 唯一索引成硬约束

**现状（已核实）**：派发在 `prepare_plan_run` 阶段就把 PlanRun 直接建成 `status="RUNNING"`（`backend/services/plan_dispatcher_sync.py:387-399`），随后 `complete_plan_run_dispatch` 一次性物化全部 `JobInstance`（`backend/services/plan_dispatcher_sync.py:582-600`）。没有「等待准入」这一态——要么派发成功进 RUNNING，要么在 prepare/complete 阶段因设备不可用直接 FAILED（`backend/services/plan_dispatcher_sync.py:479-508`）。

设备占用的唯一真值有两套口径且**不一致**：

- `uq_job_active_per_device`（`backend/models/job.py:73-83`）：partial unique index，`status IN ('PENDING','RUNNING','UNKNOWN')` 时同一 `device_id` 只能有一行。**PENDING 阶段就占位**。
- `_validate_dispatch_devices_sync`（`backend/services/plan_dispatcher_sync.py:52-134`）与 claim 的忙设备过滤（`backend/api/routes/agent_api.py:367-381`）：都以 `DeviceLease.status == ACTIVE` 判忙。但 **PENDING job 尚未 claim，没有 lease**（lease 在 claim 时才 `acquire_lease`，`backend/api/routes/agent_api.py:425-432`）。

**后果（B4 悬挂）**：两个 PlanRun 选中同一 device 时，第一个物化出 PENDING job（占唯一索引、无 lease），第二个在 `prepare` 校验时查 `DeviceLease.ACTIVE` 查不到 → 校验通过 → 在 `complete_plan_run_dispatch` 物化 Job 时 `db.flush()`（`backend/services/plan_dispatcher_sync.py:599`）撞 `uq_job_active_per_device`，抛 `IntegrityError`。该处**未捕获唯一索引冲突**，导致整批物化事务失败悬挂。当 60 host 并发排队同一批共享设备池时，这是高频路径而非边角。

### 缺口②：并行数是「Agent 自说自话」，控制面无权威

**现状（已核实）**：一个 host 同时跑几个 Job，完全由 Agent 主循环的 `available_slots` 决定：`available_slots = min(max_by_env, heartbeat_effective)`，其中 `max_by_env = max(0, max_concurrent_tasks - active_count)`（`backend/agent/main.py:944-946`），`max_concurrent_tasks` 取自环境变量 `MAX_CONCURRENT_TASKS`（默认 2，`backend/agent/main.py:437`）。执行并发的物理载体是 `ThreadPoolExecutor(max_workers=max_concurrent_tasks)`（`backend/agent/main.py:883-885`）。

问题在于**「线程池大小」= 「并发语义」**这一等式在长稳场景下失真：

- patrol 阶段绝大多数时间在 `sleep`（等下一周期），却仍占用一个线程池 worker 和一个「并发名额」。一台 host 上 20 台设备做 8 天 patrol，等于 20 个线程长期驻留但几乎不耗 CPU/ADB。
- 真正抢占资源的是**瞬时脚本执行 / ADB 调用**（monkey、截图、connect_wifi），它们才需要限流。用线程池大小同时表达「能同时长跑几台设备」和「能同时执行几个脚本」，两个语义被迫共用一个数字，无法独立调优。
- 控制面对「这台 host 到底在同时跑几台设备」没有权威记录——claim 端点按 `capacity` LIMIT 认领（`backend/api/routes/agent_api.py:405-412`），但认领后的实际并行度只活在 Agent 进程内存。

### 缺口③：控制面单进程载荷随规模平方级放大

**现状（已核实）**：控制面强制单实例（ADR-0002:32「生产 MVP 强制单实例后端运行」，ADR-0018 不变量 4「单进程约束」`docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md:202`，ADR-0019 不变量 2 `docs/adr/ADR-0019-android-device-lease-and-capacity-scheduling.md:273`）。以下热路径在 1000 device 下成为瓶颈：

- **终态聚合全量加载兄弟 job**：每个 Job 终态都触发 `PlanAggregator.on_job_terminal`，它 `SELECT * FROM job_instance WHERE plan_run_id = run.id`（`backend/services/aggregator.py:46-49`）加载**全部**兄弟 job 再重算。一个 200 设备的 PlanRun，200 次终态 = 200 × 200 = 4 万行加载；60 host 并发多个大 PlanRun 时是 O(N²)。
- **续租风暴**：每个 active job 由 Agent `LeaseRenewer` 每 60s 单独 POST `/jobs/{id}/extend_lock`（`backend/agent/lease_renewer.py:43,171-198`），1000 device = 1000 个独立 HTTP 事务 / 60s。且每次 `extend_lock` 顺带 `job.updated_at = now`（`backend/api/routes/agent_api.py:1208`）兼作 RUNNING 保活——续租和保活耦合。
- **心跳走重路径**：Agent 心跳打 `/api/v1/heartbeat`（重路径），带 backpressure 的 `/api/v1/agent/heartbeat`（`backend/api/routes/agent_api.py:702`）实际无人调用；backpressure 链路未闭环（记忆核实项）。
- **实时日志空转**：`step_log` 每行 SocketIO 推送当前是休眠路径（`_MQStepLogger` → `StepTraceWriter.send_log` no-op），服务端在等不会来的事件；重新启用前必须先批量化 + 背压（记忆核实项）。
- **SAQ 生产端与 worker 耦合**：`_queue` 仅在 `start_saq_worker` 内初始化（`backend/tasks/saq_worker.py:99-118`），`STP_ENABLE_INPROCESS_SAQ=0` 会连生产端一起瘫痪，无法先拆出独立 worker（记忆核实项）。

### 缺口④：三个独立存活信号被混用于单个 `job.updated_at`

**现状（已核实）**：判定一个 RUNNING job 是否「还活着」，实际上混用了同一列 `job.updated_at`：

- **租约存活**：Agent `extend_lock` 每 60s 刷新 lease `expires_at`，同时刷新 `job.updated_at`（`backend/api/routes/agent_api.py:1203-1208`）。
- **执行器存活 / 业务进度**：recycler 的 RUNNING 超时判据是 `job.updated_at < now - running_heartbeat_timeout_seconds(job)`（`backend/scheduler/recycler.py:639,647-651`），窗口 900s（patrol 阶段 300s，`backend/core/job_timeout_config.py:47,54`）。
- **patrol 业务进度**：另有 `last_patrol_heartbeat_at`（`backend/models/job.py:47`）用于 patrol stall 检测（`backend/scheduler/recycler.py:665-690`）。

于是「lease 还在续（设备没丢）」被 `extend_lock` 顺带写进 `updated_at`，掩盖了「执行器其实卡死、没有任何业务进度」。反过来，一个在等待执行名额（本 ADR 引入的 OperationScheduler permit）而合法阻塞的 job，也会因为 `updated_at` 不刷新而被误判超时。**存活证明的语义被单列承载，无法区分「设备在」「执行器在」「业务在推进」三件不同的事**。

---

## 决策

引入**四层职责分离**的执行架构，配套控制面可靠性改造。核心业务语义（与用户反复确认的共识）：**同一 PlanRun 的全部设备属于同一次长跑；`RUNNING` 是长生命周期状态，不是瞬时资源。** 任何把「长跑」拆成「分批完整 Job」或按「全局 RUNNING 上限」限流的方案都违背这一语义（见「备选方案与权衡」逐条驳回）。

### 1. 四层架构

| 层 | 职责 | 现状对照 | 目标 |
|----|------|----------|------|
| **① PlanRun Admission Queue** | 跨计划准入、优先级、FIFO、aging；决定「哪个 PlanRun 现在可以入场」 | 无此层，`prepare_plan_run` 直接建 RUNNING（`plan_dispatcher_sync.py:387`） | 新增 QUEUED 态 + queue pump；准入才物化 Job |
| **② PlanRunHost / HostRunCoordinator** | 「一个计划在一个 host 上」的设备集合、阶段（barrier / patrol wave）与恢复 | 无此层，Job 直接挂 PlanRun，host 维度只是 `JobInstance.host_id` 列 | 新增 `plan_run_host` 表 + per-host Coordinator 心跳 |
| **③ Host OperationScheduler** | 每 host **瞬时**脚本 / ADB 并发上限（默认 5），取代「线程池大小 = 并发语义」 | `ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TASKS)`（`agent/main.py:883`）同时表达长跑数与执行并发 | Agent 侧 permit 信号量；长跑设备数与瞬时执行并发解耦 |
| **④ Device Job + Lease/Fencing** | 设备级所有权、结果、审计、防重复执行 | 已实现（ADR-0019），`device_leases` + `uq_job_active_per_device` | **保留，不推翻** |

第四层是 ADR-0019 的既有成果，本 ADR 明确**保留** `device_leases` + fencing_token + `uq_job_active_per_device`，只在其上补齐前三层。

### 2. 四条不可破坏的不变量

> 本节四条不变量是整套设计的护栏，任何实现细节冲突时以本节为准。

#### 不变量①：QUEUED 的 PlanRun 不创建 active Job；准入时全部目标 Job 原子物化

QUEUED 态的 PlanRun 只有 `plan_snapshot` + 目标设备清单，**不得**存在任何 `JobInstance` 行。准入（QUEUED→RUNNING）时，靠 `uq_job_active_per_device`（`backend/models/job.py:73-83`）**在物化时刻**占位——一次性 `INSERT` 全部目标 Job，让唯一索引成为原子准入的仲裁者，而**不预取 lease**（lease 仍在 Agent claim 时才 `acquire_lease`）。

这条不变量直接修复缺口①的 B4 悬挂：QUEUED 期间无 Job → 不占唯一索引 → 不会出现「PENDING 占索引但校验查 lease 查不到」的口径错位。物化改为「原子复查 + 撞索引即回滚回 QUEUED」（见 §4）。

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
| **租约存活** | 设备仍被本 host 合法占用 | `lease.renewed_at` + 顺带 `job.updated_at`（`agent_api.py:1203-1208`） | `lease.renewed_at`（已有，`device_leases`），解除与 `updated_at` 的耦合 |
| **执行器存活** | Agent worker 进程 / 线程仍在跑本 job | 复用 `job.updated_at` | 新增 `last_execution_heartbeat_at`（拟） |
| **业务进度** | 脚本 / patrol 周期在实际推进 | `last_patrol_heartbeat_at`（`job.py:47`，仅 patrol） | `last_progress_at`（拟） + 沿用 `last_patrol_heartbeat_at` |

三信号可**共载于同一个批量续租请求**（传输一次、语义三份，见 §5），但落库为三列、三套超时判据。

#### 不变量④：准入竞争是可重试的调度结果 → 回 QUEUED；仅不可重试错误才 FAILED

设备 / WiFi 等资源在准入时被别的 PlanRun 抢占，是**调度结果**而非**故障**：

- **可重试 → 回 QUEUED** + `queue_reason`（`DEVICE_BUSY` / `RESOURCE_BUSY`）+ `next_admission_at`，由 queue pump 重试。包括：
  - 设备被其他 active Job 占用（撞 `uq_job_active_per_device`）。
  - WiFi 池满——**现状** `_sync_allocate_devices` 抛 `AllocationError`（`backend/services/plan_dispatcher_sync.py:180,213`）后被 `complete_plan_run_dispatch` 转 **FAILED**（`backend/services/plan_dispatcher_sync.py:555-577`）。**目标**：归入可重试，回 QUEUED，不再 FAILED。
- **不可重试 → FAILED**：仅限数据不一致（设备被删 / host_id 变 NULL，`plan_dispatcher_sync.py:515-543`）、脚本契约错误（`missing_scripts`，`plan_dispatcher_sync.py:358-366`）、计划配置失效（snapshot 无 enabled step，`plan_dispatcher_sync.py:470-471`）。

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
- **恢复交互（关键）**：`execution_state` 必须进入 recovery-sync 的冻结 payload（`backend/api/routes/agent_api.py:1988-1993,2045-2050` 现有 `_build_recovery_job_payload`），否则 `UNKNOWN→RUNNING` 恢复后会套错超时规则（例如把 `PATROL_SLEEP` 当 `EXECUTING_STEP` 判 step timeout 误杀）。

### 4. 准入原子提交流程（含 TOCTOU 处理）

准入 pump 把「慢操作」与「持锁短事务」严格分离，杜绝在持有设备行锁的事务里做外部 IO（长事务）：

```
① 慢操作在持锁事务之外先完成：
     - 脚本 sha256 SocketIO 校验（现有 _drive_dispatch_gate / precheck，plan_precheck.py + precheck/*)
     - host 在线检查
   ↓
② 进入短 DB 事务：
     - 锁 PlanRun / PlanRunHost 行
     - 按 device_id 固定顺序（升序）检查 active-job 唯一约束 + WiFi 池余量
   ↓
③ 原子复查全部设备 / 资源：
     - 全部设备无 active Job（不撞 uq_job_active_per_device）
     - WiFi 池余量足够
   ↓
④ 一次性 INSERT 全部 Job + 写 admission_token → PlanRun QUEUED→RUNNING（经 PRECHECK）
```

**TOCTOU 处理**：若 ③ 的原子复查发现设备 / WiFi 被占（或 ④ INSERT 撞 `uq_job_active_per_device`）：

- 回滚本次物化（savepoint 回滚，不污染外层事务，参照 `acquire_lease` 现有 savepoint 模式 `backend/services/lease_manager.py:88-96`）。
- PlanRun **保持 QUEUED**（不 FAILED）。
- 写 `queue_reason`（`DEVICE_BUSY` / `RESOURCE_BUSY`）+ `next_admission_at`。
- queue pump 稍后重试（不变量④）。

**硬约束**：

- **外部 SocketIO 脚本校验绝不能放在持有设备行锁的事务内**。现状 `_run_dispatch_gate_sync` 用 `asyncio.run(_drive_dispatch_gate(...))`（`backend/services/plan_dispatcher_sync.py:306-310`）驱动跨进程 RPC，一次校验可能数秒；若在设备行锁内执行，60 host 并发时锁等待会雪崩。慢操作必须在 ①、锁只在 ②-④。
- queue pump 用 `FOR UPDATE SKIP LOCKED`（参照 claim 现有模式 `backend/api/routes/agent_api.py:411`）+ 幂等 `admission_token` + **单 pump 实例准入**（APScheduler 单进程内单 job，天然串行；符合 ADR-0018 单进程约束）。
- WiFi 池满（现状 `_sync_allocate_devices` 抛 `AllocationError` → FAILED，`plan_dispatcher_sync.py:551-577`）改为可重试回 QUEUED。

### 5. 批量续租接口契约

新增端点（拟）：`POST /api/v1/agent/leases/extend-batch`，取代现状「每 job 每 60s 单独 POST `/jobs/{id}/extend_lock`」（`backend/agent/lease_renewer.py:171-198`，`backend/api/routes/agent_api.py:1182-1211`）。

**请求**：

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
| `stale_token` | fencing_token 不匹配 | 触发该 job 的 `_on_lease_lost`（`agent/lease_renewer.py:220-221`） |
| `job_not_running` | job 已非 RUNNING | 停该 job 循环 → recovery/sync |
| `lease_missing` | 无 ACTIVE lease | 同上 |

**契约要点**：

- **单事务集合化校验，但结果按 item 隔离**——不是全批原子。现状 `extend_lease` 单 job 成功/失败（`backend/services/lease_manager.py:160`）；批量版必须避免「一个 stale_token 回滚整批」，否则 1000 device 里一个坏 item 拖垮 999 个健康续租。
- **三信号共载于此请求**（不变量③）：`fencing_token`（租约存活）、隐含的请求到达即执行器存活证明（回填 `last_execution_heartbeat_at`）、`progress_marker`（业务进度，回填 `last_progress_at` / patrol 计数）。传输一次、语义三份、落库三列。
- **大 host 分块 50-100**：一台 host 上百设备时，Agent 按 50-100 一块发送，避免单请求 body 过大 + 单事务过长。
- **Coordinator 心跳建议 per-host 粒度**：`WAITING_*` / `PATROL_SLEEP` 子状态的存活由 per-host Coordinator 心跳兜底（§3 矩阵），不要求每设备高频执行心跳。

### 6. O(1) 终态聚合计数器

**现状（已核实）**：`PlanAggregator.on_job_terminal` 每次终态 `SELECT` 全部兄弟 job 重算（`backend/services/aggregator.py:46-51`），O(N²)（缺口③）。

**目标**：在 `plan_run` / `plan_run_host` 上维护**原子计数器列**（拟：`terminal_job_count` / `success_job_count` / `failed_job_count` / `total_job_count`）。每个 Job 终态时：

- 在终态事务内 `UPDATE plan_run SET terminal_job_count = terminal_job_count + 1, ...`（单行原子自增，参照 patrol-heartbeat 现有 `func.greatest` / `+delta` 原子 UPDATE 模式 `backend/api/routes/agent_api.py:1358-1398`）。
- 当 `terminal_job_count == total_job_count` 时才触发一次 `apply_plan_run_aggregation` 做终态收敛 + chain trigger（`backend/services/aggregator.py:51-57`）。
- 聚合读取从「全表扫兄弟 job」降为「读四个计数器列」——O(1)。

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

- **驳回理由**：四个结构性缺口都是模型层面的，局部修补（例如只给 `complete_plan_run_dispatch` 加唯一索引 catch）只能缓解 B4 悬挂的症状，不解决「无跨计划排队」「并发语义错配」「单进程载荷平方放大」「三信号混用」。且修补会让 `plan_dispatcher_sync.py` / `recycler.py` / `aggregator.py` 进一步膨胀。

---

## 数据模型变更

> 仅列出列名与用途，**不写 migration SQL**（迁移遵循 ADR-0008 Alembic-only，落地时单独出 migration）。

### PlanRun 新增列（`backend/models/plan_run.py`）

| 列 | 类型 | 用途 |
|----|------|------|
| `priority` | INT，默认 0 | 准入优先级；高优先级先出队 |
| `queue_reason` | VARCHAR | QUEUED 原因：`DEVICE_BUSY` / `RESOURCE_BUSY` / `PRIORITY_WAIT` / NULL |
| `next_admission_at` | TIMESTAMP | 下次准入尝试时间（退避 + aging 用） |
| `admission_token` | VARCHAR | 幂等准入令牌，防 pump 重复物化 |
| `enqueued_at` | TIMESTAMP | 进入 QUEUED 的时刻；aging 计算基准 |
| `terminal_job_count` | INT，默认 0 | O(1) 聚合计数器：已终态 Job 数 |
| `success_job_count` | INT，默认 0 | 同上：成功 Job 数 |
| `failed_job_count` | INT，默认 0 | 同上：失败 Job 数 |
| `total_job_count` | INT，默认 0 | 目标 Job 总数（物化时写入）；`terminal == total` 触发聚合 |

### PlanRunHost 新表（拟）

| 列 | 类型 | 用途 |
|----|------|------|
| `id` | INT PK | |
| `plan_run_id` | INT FK → plan_run.id | |
| `host_id` | VARCHAR FK → host.id | |
| `device_count` | INT | 本 host 在本 PlanRun 的设备数 |
| `phase` | VARCHAR | host 内阶段：`INIT` / `PATROL` / `TEARDOWN` / `BARRIER_WAIT` |
| `coordinator_heartbeat_at` | TIMESTAMP | per-host Coordinator 心跳（§3 矩阵的宽松兜底信号） |
| `terminal_job_count` / `total_job_count` | INT | host 维度的 O(1) 计数器 |
| 唯一约束 | `(plan_run_id, host_id)` | 一个 PlanRun 在一个 host 一行 |

### JobInstance 新增列（`backend/models/job.py`）

| 列 | 类型 | 用途 |
|----|------|------|
| `execution_state` | VARCHAR | 子状态：`WAITING_EXECUTION_SLOT` / `EXECUTING_STEP` / `PATROL_SLEEP` / `WAITING_BARRIER`（§3） |
| `last_execution_heartbeat_at` | TIMESTAMP | 执行器存活信号（不变量③）；`EXECUTING_STEP` 超时判据 |
| `last_progress_at` | TIMESTAMP | 业务进度信号（不变量③）；非 patrol step 的进度证明 |

> 注：`last_patrol_heartbeat_at` / `current_failure_streak` / `next_retry_at` / `manual_action` 已存在（`backend/models/job.py:47-52`，ADR-0022），复用不新增。

### 索引考量（落地时细化）

- QUEUED 出队需要 `(status, priority DESC, next_admission_at, enqueued_at)` 复合索引支撑 pump 的 `FOR UPDATE SKIP LOCKED` 扫描。
- `plan_run_host (plan_run_id, host_id)` 唯一索引 + `(host_id, phase)` 辅助索引。

---

## 状态机变更

### PlanRun 状态机（`backend/services/state_machine.py:47-59` `PLAN_RUN_VALID_TRANSITIONS`）

**现状（已核实）**：`PlanRunStatus` 只有 `RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED`（`backend/models/enums.py:13-18`）；合法迁移为 `RUNNING → {SUCCESS, PARTIAL_SUCCESS, FAILED}` + 有意的 `FAILED → RUNNING`（人工重试，`backend/services/state_machine.py:53-55`，`precheck/runner.py::retry_plan_run_dispatch`）。

**目标**：新增 `QUEUED` / `PRECHECK` 两态，扩展迁移表：

```
QUEUED   → PRECHECK           # pump 选中，进入慢操作（脚本校验 / host 检查）
QUEUED   → QUEUED             # 自环：准入竞争失败（DEVICE_BUSY/RESOURCE_BUSY），回队重试（不变量④）
PRECHECK → RUNNING            # 原子物化成功，正式入场
PRECHECK → QUEUED             # 慢操作后原子复查发现资源被占 → 回队（不变量④）
PRECHECK → FAILED             # 不可重试错误（脚本契约 / 数据不一致 / 配置失效，不变量④）
RUNNING  → {SUCCESS, PARTIAL_SUCCESS, FAILED}   # 保留现状
FAILED   → RUNNING            # 保留现状（人工重试）
```

- `QUEUED → QUEUED` 自环通过更新 `queue_reason` + `next_admission_at` 表达，不是 no-op。
- `PlanRunStateMachine.transition`（`backend/services/state_machine.py:62-79`）需接受新态；`prepare_plan_run` 改为建 `QUEUED`（现状建 `RUNNING`，`backend/services/plan_dispatcher_sync.py:390`）。
- `DEGRADED` 仍仅历史可读、不再生产（与 CLAUDE.md 状态机约定一致）。

### Job 状态机（`backend/services/state_machine.py:12-22` `VALID_TRANSITIONS`）

**不新增顶层状态**。`execution_state` 是 RUNNING 的**子状态列**，不是 `JobStatus` 枚举成员——`PENDING → RUNNING → COMPLETED/FAILED/ABORTED/UNKNOWN` 及 `UNKNOWN → RUNNING/FAILED`（`backend/services/state_machine.py:12-22`）全部保留不变。execution_state 只在 `status == RUNNING` 期间有意义，终态时置 NULL。

---

## 与现有子系统的交互矩阵

| 子系统 | 现状锚点 | 本 ADR 需要的改动 |
|--------|----------|-------------------|
| **queue pump（新）** | 无 | 新增 APScheduler IntervalTrigger job（单进程内，符合 ADR-0018）；`FOR UPDATE SKIP LOCKED` 选 QUEUED、驱动慢操作、原子物化；单实例串行准入 |
| **dispatcher** | `prepare_plan_run` 建 RUNNING（`plan_dispatcher_sync.py:387`）、`complete_plan_run_dispatch` 物化（`:425`） | `prepare` 改建 QUEUED；物化逻辑迁入 pump，撞 `uq_job_active_per_device` 时回 QUEUED 而非悬挂/FAILED；WiFi `AllocationError`（`:555`）改可重试回队 |
| **claim** | `_claim_jobs_for_host`（`agent_api.py:319`）按 lease 过滤忙设备 | 基本保留；claim 只认领已 RUNNING PlanRun 的 PENDING job（现有 `PlanRun.status=='RUNNING'` 过滤 `agent_api.py:401` 天然兼容） |
| **recycler** | `recycle_once`（`recycler.py:567`）；RUNNING 超时看 `updated_at`（`:639`）；patrol stall（`:665`） | 按 `execution_state` 选时钟（§3 矩阵）；`WAITING_EXECUTION_SLOT` 不判超时/ stall（不变量②）；RUNNING 超时改看 `last_execution_heartbeat_at` 而非 `updated_at`（不变量③） |
| **session_watchdog** | host 心跳超时 → job UNKNOWN（`session_watchdog.py:36-73`）；UNKNOWN grace → FAILED（`:76-99`） | 保留；host 失联仍把该 host 全部 RUNNING job 转 UNKNOWN；可改用 `plan_run_host.coordinator_heartbeat_at` 做更精细的 host 存活判断 |
| **device_lease_reconciler** | `_reconcile_expired_leases`（`device_lease_reconciler.py:64`）、abort reaper（`:300`）、stale UNKNOWN（`:189`） | 保留两段式 lease 过期；lease 存活判据解耦出 `updated_at`（不变量③）后，reconciler 仍以 `lease.expires_at` 为准（`:75`），不受影响 |
| **abort** | `abort_plan_run`（`plan_runs.py:363`）写 `run_context.abort_requested`；reaper 消费（`device_lease_reconciler.py:300-386`） | QUEUED 的 PlanRun abort 直接 QUEUED→FAILED（无 Job 需回收）；RUNNING 的走现有 abort reaper 路径 |
| **aggregator** | `on_job_terminal` 全量加载兄弟 job（`aggregator.py:46-49`） | 改 O(1) 计数器（§6）；`terminal == total` 才触发 `apply_plan_run_aggregation` + `trigger_next_plan`（`:52-53`） |
| **dispatch gate / precheck** | `_drive_dispatch_gate`（`plan_precheck.py` → `precheck/*`）跨进程脚本校验 | 移到 pump 的「慢操作」阶段（§4 步骤①），**严禁**在设备行锁事务内执行 |
| **LeaseRenewer（Agent）** | 每 job 每 60s 单独 `extend_lock`（`lease_renewer.py:171`） | 改批量 `extend-batch`（§5），逐项结果隔离；三信号共载 |
| **SAQ** | `_queue` 与 worker 耦合于 `start_saq_worker`（`saq_worker.py:99`） | P0 先拆 producer/worker（`init_saq_producer` / `run_saq_worker`），为 pump 入队和后续水平扩展铺路 |

---

## 落地顺序

对齐 P0→P3 分层推进。P0 是「不改架构也必须先做」的止血 + 前置，P1 才引入新层。

### P0：止血与前置（不引入新层，可独立上线）

1. **批量续租** `extend-batch`（§5）——直接缓解缺口③的续租风暴，逐项结果隔离。
2. **B4 悬挂修复**——`complete_plan_run_dispatch` 物化 Job 时捕获 `uq_job_active_per_device` 冲突（`plan_dispatcher_sync.py:588-599`），不再悬挂；作为 P1 QUEUED 回队的过渡实现。
3. **SAQ producer/worker 拆分**——`init_saq_producer` / `run_saq_worker` 解耦（`saq_worker.py:99-118`），使 `STP_ENABLE_INPROCESS_SAQ=0` 不再瘫痪生产端。
4. **心跳减负**——Agent 心跳收敛到带 backpressure 的 `/api/v1/agent/heartbeat`（`agent_api.py:702`），闭环 backpressure；增量/降采样。
5. **真实指标**——补齐 queue-latency、续租成功率、聚合耗时、per-host 并发度等 Prometheus 指标（对齐 ADR-0011），为后续压测提供判据。

### P1：准入队列 + OperationScheduler（核心两层）

1. **PlanRun Admission Queue**——QUEUED/PRECHECK 态 + queue pump + 原子物化（§2 不变量①④、§4）。`prepare_plan_run` 改建 QUEUED。
2. **PlanRunHost / HostRunCoordinator**——新表 + per-host Coordinator 心跳（§3、数据模型）。
3. **Host OperationScheduler**——Agent 侧 permit 信号量（默认 5，待压测确认），解耦长跑设备数与瞬时执行并发（缺口②、不变量②）。
4. **execution_state 矩阵**——子状态列 + 按态选时钟的 recycler 改造（§3）；三信号拆列（不变量③）。

### P2：控制面规模优化

1. **O(1) 聚合计数器**（§6）——`plan_run` / `plan_run_host` 计数器列。
2. **实时日志批量化 + 背压**——重启 `step_log` 通路前必须先做（记忆核实项），否则空转变风暴。
3. **索引与查询优化**——QUEUED 出队复合索引、设备矩阵聚合端点（对齐 ADR-0022 C5a₂）在 1000 device 下的执行计划复核。

### P3：水平扩展（远期，需突破单进程约束）

1. **准入 pump 的 leader election**——多控制面实例时保证单 pump 准入（现状单进程天然满足，ADR-0018:202）。
2. **SocketIO Redis adapter / Centrifugo**——ADR-0018 已为 1000+ 设备预留（`docs/adr/ADR-0018-...:176,136`）。
3. **控制面多实例**——需先解除 ADR-0002/0018 单进程约束，涉及全部后台调度 job 的 leader 选举，属独立大改，本 ADR 只标方向不展开。

---

## 风险与回滚

| 风险 | 缓解 | 回滚 |
|------|------|------|
| QUEUED 引入后，历史 MANUAL 派发路径行为变化（用户期望「点了就跑」） | pump 间隔足够短（待定，建议 ≤5s）；空闲资源时 QUEUED→RUNNING 近乎瞬时 | 保留 `prepare_plan_run` 直建 RUNNING 的旧路径为 feature flag，可关掉队列 |
| OperationScheduler permit 上限设错，patrol 唤醒风暴打满 permit 导致 step 排队 | permit 默认 5 起步（待压测确认）；queue-latency 告警先于误杀 | permit 上限可热调（env）；退回「线程池大小 = 并发」的旧语义 |
| 批量续租逐项隔离实现有 bug，坏 item 拖累整批 | 单事务集合校验 + item 级 try 隔离 + 契约测试覆盖坏 item 混入 | 回退单 job `extend_lock`（`agent_api.py:1182` 保留不删） |
| O(1) 计数器与实际 Job 数漂移（并发自增丢失） | 计数器自增在终态事务内 + `FOR NO KEY UPDATE` 串行（`aggregator.py:36`）；定期对账校验 `terminal_job_count == COUNT(*)` | 回退全量 SELECT 聚合（`aggregator.py:46-49` 保留） |
| execution_state 未进 recovery payload，恢复后套错超时 | §3 硬性要求纳入冻结 payload；测试覆盖 UNKNOWN→RUNNING 后各子状态超时判据 | execution_state 缺省回退到 `updated_at` 时钟（退化为现状） |
| 三信号拆列迁移期间新旧判据并存不一致 | 迁移期双写 `updated_at` + 新列；recycler 先读新列、缺失 fallback 旧列 | 保留 `updated_at` 判据，先不删 |

**分阶段压测**：沿用 ADR-0019 的阶梯思路（`docs/adr/ADR-0019-...:305` 的 3→10→44），扩展为 **44→60→100 host** 灰度，每档验证续租成功率、准入延迟、聚合耗时、误杀率后再进下一档。

---

## 关联 ADR

- **扩展 ADR-0019**（Device Lease + 容量）：保留 `device_leases` + fencing_token + `uq_job_active_per_device`（第四层）；把「Agent 自说自话的 available_slots」上收为控制面三层调度。
- **扩展 ADR-0020**（Plan/PlanStep）：PlanRun 从「派发即 RUNNING」演进为「QUEUED→PRECHECK→RUNNING」，`plan_snapshot` 语义不变。
- **兼容 ADR-0021**（派发门禁）：脚本 sha256 校验 / 热更新软禁保留，移入 pump 慢操作阶段，严禁进设备行锁事务。
- **扩展 ADR-0022**（patrol 心跳）：复用 `last_patrol_heartbeat_at` / `current_failure_streak` / `manual_action`；`PATROL_SLEEP` 子状态的 stall 判据沿用 `PATROL_STALL_MULTIPLIER`。
- **受约束于 ADR-0002 / ADR-0018**（单进程）：queue pump / Coordinator / OperationScheduler 控制面侧均为 APScheduler 单进程内 job；水平扩展（P3）留待专门 ADR 解除单进程约束。
- **推进 ADR-0011**（可观测性）：queue-latency、续租成功率、per-host 并发度、聚合耗时为新增指标族提供落地场景。
- **迁移遵循 ADR-0008**（Alembic-only）：本文数据模型变更落地时单独出 migration，不在此写 SQL。

---

## 待定清单（需压测 / 评审回填）

- pump 扫描间隔、`next_admission_at` 退避曲线、aging 提权阈值——**待定**。
- OperationScheduler `max_concurrent_operations` 默认值（暂定 5）——**待压测确认**。
- 批量续租分块大小（暂定 50-100）与续租间隔——**待压测确认**。
- 三信号各自的超时窗口（执行心跳 / Coordinator 心跳 / 业务进度）——**待定**，需与现有 900s/300s（`job_timeout_config.py:47,54`）对齐后灰度下调。
- `PlanRunHost` 是否需要独立的 barrier 协调状态机（vs. 纯计数器判 barrier 到达）——**待评审**。
- 目标里程碑编号与本 ADR 正式编号——**待人工确定**。

---

*草案 2026-07-15*
