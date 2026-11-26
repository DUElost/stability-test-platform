# ADR-0065：统一「收尾中（`finishing`）」过渡态模型与不可中断临界区有界保护

- 状态：Proposed v0.3（2026-11-26 v0.3：按首轮独立复核修订——补齐进入临界区前的有界同步许可仲裁与控制面持久化前置，修复 `D_atomic` 同量纲绝对截止、静默写入窗、存活/推进双通道核验、ADR-0043 首次时钟保留与 CAS 复读，补齐失租/强杀后的刷机设备完整性欠账与 `MAINTENANCE` 隔离闭环，限定 D5.1 整体成功口径与混合 Plan / `skipped` / `/complete` 载荷契约，补齐在途能力冻结、持久化 `force` 升级与重连 `ABORT_LOCAL` 协同，补全 D1 全量投影/预期掉 ADB/四面刷新与热更新/优雅停机边界，§6 收口采纳选项乙显式冻结 `atomic_group` 并覆盖引擎插入的 VBUS 刷后残留检查，逐句校正 ADR-0059 写作约定；v0.2：2026-11-26 吸收 Owner 裁定，合并为单一「收尾中（`finishing`）」集合并下设两类子模型、单台刷机流程按整体落终态；v0.1：2026-11-26 初稿）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-11-26
- 决策者：Owner
- 标签：执行协议, 中止语义, 刷机, 设备归还, 状态投影
- 归属域：n/a（Proposed 阶段不入语义归属表；转 Accepted 后登记至 `docs/design/2026-semantic-ownership.md`，与 `device-return`、`run-terminal-semantics` 协同划界）
- 关联：
  [执行协议契约](../design/07-execution-protocol.md)（§2–§3、§7、§9.2、§9.5）；
  [ADR-0043](./ADR-0043-abort-grace-subject-alignment.md)（中止宽限的请求主体同构）；
  [ADR-0048](./ADR-0048-execution-status-semantics-v2.md)（执行终态语义 v2）；
  [ADR-0058](./ADR-0058-planned-batch-execution.md)（批次交付流程 D7–D9）；
  [ADR-0061](./ADR-0061-device-return-contract.md)（设备归还契约与 B6 解除有界保护）；
  [ADR-0063](./ADR-0063-vbus-cycle-flash-without-adb.md)（VBUS 切电刷机 D5 第 3 条刷后残留检查）

---

## 1. 背景与证据

### 1.1 问题现场

1. **刷机烧写临界区被直接 `abort` 砍砖（2026-11-25 PlanRun `#1679` / `job=210319`）**：
   - `P525AAN` 批量刷机期间，操作者对运行中的 PlanRun 执行 `POST /api/v1/plan-runs/{id}/abort`。
   - 控制面立即向 Agent host 下发 `command=abort`，Agent `PipelineEngine.cancel()` 仅豁免 `phase == "teardown"`（`backend/agent/pipeline_engine.py:1237`），对 `phase == "init"` 正在执行 `spd_dump` 烧写分区的进程直接发送 `SIGTERM`（`rc=-15`）。
   - 目标设备（`AUXMNX6A****0239`）分区写入被腰斩，滞留于展锐下载态（`1782:4d00`）且 USB 端点死锁，软件复位（`spd_dump reset`、`usbreset`、端口重枚举）均报 `LIBUSB_ERROR_TIMEOUT`，只能现场强复位后离线手刷救砖。
2. **多专项共用「受保护收尾」诉求，但缺乏统一顶层模型与对外过渡态**：
   - 开关机、休眠、GPU、Monkey、MTBF 等专项在手动中止、手动退出、主体失败、barrier 超时或自然到点后，均需在 `teardown` 阶段执行设备解除与回读（或仅收取结果）。
   - B6（ADR-0061 v1.1 与 `执行协议契约（07-execution-protocol.md）§9.2`）已针对 `teardown` 解除落地 `EXECUTING_DISARM` 与 `disarm_guard` 有界保护，但此前只针对 A 类（后置解除）存在，既未把「收尾中」抽成对用户可见的一等过渡态，也未覆盖 B 类（刷机等 `init` 阶段的不可中断原子交付流程）。
   - 操作者点完中止或定时计划自然到点后，界面持续显示 `RUNNING`，无法区分「仍在跑主体测试 / 卡死未响应中止」与「正在安全收尾（刷完当前设备或等待设备解除归还）」。

### 1.2 问题本质：一个统一集合，两类本质不同的收尾成员

不在 B6 `disarm_guard` 之上给刷机打特例补丁，而把「**作业已离开普通可随时中断的主体运行期，正在执行受平台保护的收尾工作**」提升为一等概念——**`finishing`（收尾中）集合**，其下并列两类语义正交的子类型（`finishing_kind`），共用同一套有界保护与观测底座：

```text
统一过渡态集合：finishing（收尾中）   [持久化 status 仍为 RUNNING，不改 DB 终态机与活跃索引]
  │
  ├── 共享底座（统一 FinishingGuard 契约）：
  │     • 进入前同步许可仲裁（防止脚本先操作硬件、后到达 abort 竞态）与控制面持久化前置
  │     • 同量纲绝对截止锚点（控制面服务端时钟冻结、重复 abort 不重置、时钟回拨持久失效）
  │     • 存活心跳与推进证据分离核验（按控制面接收时刻计算，兼容静默写分区窗口）
  │     • 统一 reaper / recycler 候选与最终行锁 CAS 复读入口（保留 ADR-0043 首次主体时钟）
  │     • 统一权威投影：ui_status="finishing" + finishing_kind + finishing_trigger + 剩余计数/截止时间
  │
  ├── 子类型 A：teardown_disarm（后置解除收尾）
  │     • 场景：开关机、休眠、GPU、Monkey、MTBF（手动中止、手动退出、主体失败、barrier 超时或自然到点进入 teardown）
  │     • 保护对象：设备足迹解除、开机回读与自然到点时的结果收取（解除资格按冻结版本与设备足迹确定）
  │     • 终态口径：沿用 ADR-0061——测试终态与解除结果正交（中止→ABORTED，自然到点→COMPLETED），解除失败转设备欠账
  │     • 与 B6 关系：自然收编现有 disarm_guard / EXECUTING_DISARM，保持线协议向后兼容
  │
  └── 子类型 B：atomic_commit（原子流程收尾）
        • 场景：展锐 BSL 刷机、MTK 刷机等不可中断硬件交付流程（收到普通 abort 时已获批进入临界区）
        • 保护对象：冻结在 plan_snapshot 中的当前单台设备 atomic_group 闭环（含引擎插入的 VBUS 刷后残留检查）
        • 终态口径：纯原子交付 Job 整机闭环全部实证成功→COMPLETED；任一步失败或保护窗超期→FAILED；
                   混合 Plan（含未执行 patrol）完成本机原子闭环后跳过 patrol→ABORTED；显式 force 强杀→ABORTED
        • 完整性义务：获批进入硬件临界区前持久化 flash_integrity 待核验义务；失租/超期/强杀未获验机证据一律转 MAINTENANCE 欠账隔离
        • 排队隔离：未获批进入临界区的作业（PENDING / return_check / 缓存拉取 / lock-wait）不进入本集合，立即中止为 ABORTED
```

### 1.3 最小持久化约束

`JobInstance.status` 与 `PlanRun.status` 被部分唯一索引 `uq_job_active_per_device`（`PENDING|RUNNING|UNKNOWN`）、claim 门禁、reaper、聚合器与热更新门控强依赖。因此本提案沿用 `执行协议契约（07-execution-protocol.md）§9.2` 的持久化边界：**不在数据库 `status` 枚举中新增状态值，持久化层维持 `RUNNING`，由控制面统一投影 `finishing` 集合及子类型明细。**

---

## 2. 决策

> 本节按 [ADR-0059 写作约定](../development/writing-conventions.md)书写。每条规则独立成句，标明执行者，且仅使用「必须 / 不得 / 建议 / 可以」表达规范强度。

### D1 统一「收尾中（`finishing`）」过渡态集合、双子类型与全量投影口径

1. 实施者不得在 `PlanRun.status` 或 `JobInstance.status` 数据库持久化枚举中新增过渡状态值。
2. 控制面必须将处于收尾中的活跃 Job 持久化状态保持为 `RUNNING`。
3. 控制面必须在 PlanRun 列表、PlanRun 详情、设备矩阵与 Job 详情响应中提供统一的权威过渡态投影字段 `ui_status="finishing"`（界面统一展示名：**`收尾中`**）。
4. 前端不得根据零散时间字段或执行日志自行推断 `finishing` 过渡态。
5. 对每一个投影为 `ui_status="finishing"` 的活跃 Job，控制面必须同时投影正交字段 `finishing_kind`（取值为 `teardown_disarm` 或 `atomic_commit`）与 `finishing_trigger`（取值为 `manual_abort`、`manual_exit`、`natural_end`、`body_failure` 或 `barrier_timeout`）。
6. 控制面必须按以下全量规则计算单 Job 级 `ui_status`：
   - 对处于 `PENDING` 的 Job，控制面必须投影 `ui_status="pending"`，且不得将其纳入 `finishing`；
   - 对处于 `UNKNOWN` 的 Job，控制面必须投影 `ui_status="unknown"`，且不得将其纳入 `finishing`；
   - 对处于 `RUNNING` 且已获批进入硬件临界区、但尚未收到普通 `abort` 的刷机 Job，控制面必须投影 `ui_status="running"`，不得提前投影为 `finishing`；
   - 对处于 `RUNNING` 且正在执行 `teardown` 解除排队（`WAITING_EXECUTION_SLOT`）、执行解除步骤（`EXECUTING_DISARM`）或仅收取结果的 `teardown` 阶段 Job，控制面必须投影 `ui_status="finishing"` 与 `finishing_kind="teardown_disarm"`；
   - 对处于 `RUNNING` 且已获批进入硬件临界区并在收到普通 `abort` 后正在完成 `atomic_group` 闭环的 Job，控制面必须投影 `ui_status="finishing"` 与 `finishing_kind="atomic_commit"`；
   - 对处于 `RUNNING` 且已获批进入硬件临界区（无论是否已收到 `abort`）的 Job，只要主机心跳与保护存活证据新鲜，控制面不得因刷机期间预期的 ADB 断连将其 `ui_status` 降级为 `"unknown"`。
7. 当 PlanRun 下至少存在 1 个投影为 `ui_status="finishing"` 的活跃 Job，且不存在 `PENDING`、`UNKNOWN` 或普通可中断 `RUNNING`（非临界区 `init`、未中止的临界区 `init`、`patrol`、`backoff`）Job 时，控制面必须将该 PlanRun 投影为 `ui_status="finishing"`。
8. 当 PlanRun 被投影为 `ui_status="finishing"` 时，控制面必须按 `finishing_kind` 返回分组剩余计数（`atomic_commit` 剩余台数与 `teardown_disarm` 剩余台数）及最近保护截止时间。
9. 当 PlanRun 同时存在 `ui_status="finishing"` 的活跃 Job 与其他未终态活跃 Job（`PENDING`、`UNKNOWN` 或普通 `RUNNING`）时，控制面必须保持 PlanRun 级 `ui_status="running"`，并在明细计数与设备矩阵中逐 Job 展示 `finishing` 状态与子类型。
10. 控制面必须在 PlanRun 列表查询（`plan_run_catalog`）中支持按投影态筛选 `finishing`，同时在活跃 PlanRun 统计中继续按持久化 `RUNNING` 计入。
11. 前端设备矩阵 Minimap、设备明细表、PlanRun 列表与 Job 详情页必须统一消费控制面投影的 `ui_status`（不再仅由原始 `job_exec_status` 推导设备矩阵状态），并保持同一轮询/推送刷新口径。
12. 实施者必须保证 A 类与 B 类共用 `FinishingGuard` 围栏、预算与证据判定底座，同时 `teardown_disarm` 的解除资格必须继续按冻结脚本版本与已建立设备足迹判定（遵循 ADR-0061）。

### D2 普通中止分流、临界区仲裁与 `force=true` 紧急强杀

1. 收到不带 `force=true` 的 PlanRun 级或主机级 `POST /api/v1/plan-runs/{run_id}/abort` 时，控制面必须对命中主体内的 `PENDING` Job 立即置为 `ABORTED` 并释放本 Job 的 `JOB` 租约。
2. 收到不带 `force=true` 的普通中止时，对处于 `RUNNING` 且未获批进入不可中断临界区、也未进入 `teardown` 的 Job（包括 `return_check`、固件缓存拉取、主机刷机锁排队 `lock-wait`、`patrol` 巡航），Agent 必须立即中止当前步骤。
3. 上述步骤被立即中止后，若本 Job 按 ADR-0061 已建立专项设备足迹，Agent 必须将其转入 `teardown_disarm` 子类型执行解除。
4. 上述步骤被立即中止后，若本 Job 未建立任何专项设备足迹，Agent 必须直接上报终态 `ABORTED`。
5. 收到不带 `force=true` 的普通中止时，对处于 `RUNNING` 且已在仲裁点获批进入不可中断临界区（D3）的 Job，Agent 不得向当前进程树发送 `SIGTERM` 或 `SIGKILL`。
6. 对上述已获批进入临界区的 Job，Agent 必须将其转入 `atomic_commit` 子类型，继续顺序执行当前临界区步骤以及冻结在 `plan_snapshot` 内的本机 `atomic_group` 剩余步骤（含引擎在 VBUS 刷机后插入的 `return_check`），直至本机 `atomic_group` 结束、保护窗到期、确认失租或收到 `force=true`。
7. 控制面必须在 `POST /api/v1/plan-runs/{run_id}/abort`（含主机级作用域）上提供可选布尔参数 `force`（缺省 `false`）。
8. 控制面必须在 `PlanRun.run_context` 中按请求主体持久化 `force` 升级状态（run 级记于 `abort_requested`，主机级记于 `abort_requested_hosts[host_id]`）。
9. 控制面不得将已升级为 `force=true` 的主体中止强度降级回普通中止。
10. 控制面在筛选中止候选 Job 时必须同时纳入 `RUNNING` 与 `UNKNOWN` 状态的 Job，并在 SocketIO `abort` 载荷中显式携带 `force` 布尔字段。
11. 当操作者传入 `force=true` 时，Agent 必须跳过 `atomic_commit` 保护并立即向子进程树发送 `SIGTERM → SIGKILL`。
12. 当 `force=true` 中断处于 `teardown_disarm` 的 Job 时，Agent 必须将已开始但被中断的解除步骤记为 `incomplete`，将尚未开始的解除步骤保留为 `not_executed`，并以终止原因 `force_abort` 上报 Job 终态 `ABORTED`。
13. 当 `force=true` 中断处于 `teardown_disarm` 的 Job 时，控制面不得豁免 ADR-0061 设备归还欠账判定，必须按 `执行协议契约（07-execution-protocol.md）§9.5` 将本 Job 的 `JOB` 租约转为 `MAINTENANCE` 欠账租约。
14. 当 `force=true` 到达控制面时若目标 Job 已先期提交终态 `/complete`，控制面不得覆写已提交终态，必须在响应摘要中将该 Job 标记为 `already_terminal`。
15. 前端在发起 `force=true` 请求前必须展示二次确认弹窗，向操作者明确提示强杀刷机临界区可能导致设备滞留下载态并需人工复位及隔离恢复。

### D3 统一 `FinishingGuard` 底座、同步进入许可仲裁与冻结 `atomic_group`

1. 实施者必须在 `backend/agent/contracts/` 中建立统一的 `FinishingGuard` 契约，以 `kind ∈ {"teardown_disarm", "atomic_commit"}` 区分两类收尾成员。
2. 实施者不得将 B 类保护逻辑以特例分支散落拼接在 `disarm_guard` 专用代码中。
3. 实施者必须将既有 B6（`执行协议契约（07-execution-protocol.md）§9.2`）的 `disarm_guard` 与 `EXECUTING_DISARM` 语义收编为 `FinishingGuard(kind="teardown_disarm")`。
4. 实施者必须在过渡期内保持既有 B6 字段与线协议向后兼容。
5. 具备不可中断硬件临界区的脚本必须在脚本包元数据（`capabilities.json`）中显式声明能力标记 `critical_section_guard`、最小临界区执行预算 `min_critical_section_seconds` 与默认临界区预算上限。
6. 控制面与 Agent 不得按脚本名硬编码猜测临界区能力。
7. 实施者必须在 `plan_snapshot` 中以显式 `atomic_group` 列表冻结单台设备的原子交付闭环成员步骤，并迁移专用刷机模板（如 `flash_unisoc_bsl`、`flash_firmware`）显式声明成员。
8. 控制面与 Agent 不得将同一 Plan `init` 阶段内未列入 `atomic_group` 的其余步骤默认吞并入原子闭环。
9. 对 VBUS 刷机作业，Agent 引擎必须将 ADR-0063 D5 第 3 条在首步刷机后插入的 `return_check` 显式纳入该 `atomic_group` 闭环。
10. 声明了 `critical_section_guard` 的脚本在执行首次不可中断硬件副作用之前（例如 `flash_firmware_unisoc_bsl` 持主机锁并准备发送 `adb reboot autodloader` 启动 `spd_dump` 之前），必须向 Agent 发起有界同步进入许可请求（`critical_section_enter_request`）并阻塞等待显式 `grant` 或 `deny` 回复。
11. 脚本不得仅凭单向 `stderr` 打点即开始执行不可中断硬件操作。
12. 若上述进入许可请求超时、控制通道断开或收到 `deny`，脚本必须立即按失败或中止退出，不得执行任何硬件写入或重启指令。
13. Agent 必须将 `critical_section_enter_request` 与普通 `abort`、`force=true` 及失租信号置于同一进程内互斥仲裁点并按以下规则仲裁：
    - 若仲裁点已收到普通 `abort`、`force=true` 或确认失租，Agent 必须向脚本回复 `deny`；
    - 若仲裁点尚未收到中止信号，Agent 必须先向控制面发起有界同步预登记请求，在控制面返回持久化成功前，Agent 不得向脚本回复 `grant`；
    - 若控制面在预登记时发现该主体已提交有效 `abort_requested`、本 Job 已失租或剩余预算不足 `min_critical_section_seconds`，控制面必须拒绝该预登记请求；
    - 收到控制面拒绝预登记或预登记超时后，Agent 必须向脚本回复 `deny`；
    - 仅在控制面预登记成功且仲裁点仍无中止信号时，Agent 可以将本 Job 原子置为 `critical_section_granted=true` 并向脚本回复 `grant`。
14. 一旦本 Job 置为 `critical_section_granted=true`，Agent 的步骤墙钟定时器与停滞 watchdog 在统一 `FinishingGuard` 截止 `D_atomic` 到达且存活心跳新鲜期间不得独立杀死该子进程树。
15. 脚本在硬件不可中断阶段完成后必须向 Agent 上报 `critical_section="exit"`。
16. 若本 Job 在硬件阶段期间已收到普通 `abort`，Agent 不得因 `critical_section="exit"` 提前撤销 `atomic_commit` 保护，必须继续保护至本机冻结 `atomic_group` 全部成员步骤执行完毕或 `D_atomic` 到期。
17. 脚本在获得 `grant` 之前（例如固件缓存拉取或 `lock-wait` 排队期间），Agent 不得将其纳入 `atomic_commit` 保护，收到普通 `abort` 时必须立即中止。

### D4 统一有界保护、同量纲截止、硬件完整性欠账与停机/热更新协同

1. 控制面与 Agent 必须对 `teardown_disarm` 与 `atomic_commit` 两类收尾强制实施有界保护窗，不得提供无上限等待。
2. 控制面 `abort reaper`（`device_lease_reconciler`）与 `recycler` 必须调用同一个 `FinishingGuard` 判定入口决定是否推迟 `UNKNOWN` 回收。
3. 控制面 `abort reaper` 与 `recycler` 必须在候选筛选阶段与最终行锁/CAS 事务提交阶段复读同一份持久化保护事实。
4. 首次冻结保护截止时间后，控制面与 Agent 不得因重复 `abort`、乱序回报、步骤重试或服务重启延长截止时间。
5. 控制面在 `plan_run_abort` 的全 Run 级与主机级两条写入路径中必须均保留首次 `at` 时间戳（补齐 ADR-0043 D2 在全 Run 重复中止上的现存偏差）。
6. 当全 Run 级与主机级中止时钟并存时，控制面必须取较早者作为有效截止基准，且必须完整覆盖主机级 late-claim Job。
7. 控制面计算 `teardown_disarm` 的截止时间时必须完全沿用 `执行协议契约（07-execution-protocol.md）§9.2`（`Q=7200s`、各解除步骤 `E_i`、绝对上限 `86400s`）。
8. 控制面必须按服务端时钟将 `atomic_commit` 的绝对保护截止时间 `D_atomic` 一次性冻结为以下三项同量纲绝对时刻的最小值，且不得引用仅用于 `patrol` 的 `lifecycle.timeout_seconds`：
   - `D_steps = T_arm + E_curr_rem + sum(E_i for subsequent steps i in atomic_group)`，其中 `T_arm` 为控制面服务端批准预登记时刻，`E_curr_rem` 为当前临界区步骤冻结 `timeout_seconds` 扣除获批前已耗时后的剩余预算（未配置步骤超时时取缺省值 `1800s`），后续各步取冻结 `timeout_seconds` 且重试不得重置；
   - `D_cap = T_arm + ATOMIC_HARD_CAP_SECONDS`（缺省 `3600s`）；
   - `D_run = T_job_start + PLAN_INIT_WALL_CAP`（`T_job_start` 为控制面记录的本 Job 启动时刻，`PLAN_INIT_WALL_CAP` 为冻结 `init` 各步超时之和加平台预留余量）。
9. Agent 在 `atomic_commit` 期间必须每 ≤15s 上报受控存活心跳（监控子进程与宿主健康）。
10. 控制面必须按控制面服务端接收时刻分别核验存活与推进两类新鲜度证据：
    - 若超过 `60s` 未收到存活心跳，控制面不得继续推迟回收；
    - 为兼容 `spd_dump` 静默写分区期间工具无输出超过 `60s` 的正常工况，在存活心跳持续新鲜前提下，控制面可以按独立的 `silent_write_progress_window_seconds`（缺省 `300s`，且受 `D_atomic` 封顶）核验推进新鲜度；
    - 若超过 `silent_write_progress_window_seconds` 仍无推进证据，控制面不得继续推迟回收。
11. 当 host 心跳有效、本 Job 的 `JOB` 租约有效、`fencing_token` 与 `boot_id` 匹配、设备占用归属匹配、存活与推进证据均在有效窗内这五项条件中任一项不满足时，控制面不得推迟回收。
12. 若控制面检测到服务器时钟回拨，控制面必须向该 Job 的保护记录持久化写入单向失效标记 `guard_invalidated_reason="clock_regressed"` 并立即停用保护。
13. 对已写入 `guard_invalidated_reason="clock_regressed"` 的 Job，控制面不得在后续轮询中恢复推迟回收。
14. 控制面在批准 `critical_section_enter` 预登记前，必须在数据库中持久化一条与 `(device_id, job_id)` 绑定的待核验硬件完整性义务（`obligation_kind="flash_integrity"`），纳入统一的设备归还欠账机制。
15. 当已登记 `flash_integrity` 义务的 Job 通过合法 `/complete` 上报完整 `atomic_group` 实证成功（含刷后开机回读与版本校验通过）时，控制面必须在同一终态事务内关闭该 `flash_integrity` 义务并正常释放本 Job 的 `JOB` 租约。
16. 当已登记 `flash_integrity` 义务的 Job 发生 `extend_lock` 或 `/complete` 返回 `409` 失租、`UNKNOWN` 到期落 `FAILED`、保护窗超期、临界区步骤失败或被 `force=true` 强杀时，控制面必须在统一终态租约事务（`handle_job_lease_release_sync`）内保留未关闭的 `flash_integrity` 义务并将本 Job 的 `JOB` 租约转为 `MAINTENANCE` 欠账租约。
17. 控制面不得在未经授权的设备开机与固件一致性回读探针清偿、或未经 Owner/管理员人工放行的情况下解除 `flash_integrity` 欠账租约。
18. 当 Owner 或管理员人工放行 `flash_integrity` 欠账租约时，控制面必须将该设备的归还状态置为 `unverified`，不得置为 `returned`。
19. 终态租约转换完成后，控制面对携带旧 `fencing_token` 的迟到 `/complete` 必须返回 `409` 且不得解除 `MAINTENANCE` 欠账租约。
20. 控制面对已提交终态的幂等重放不得改变设备欠账状态。
21. 当任一保护截止到达、确认失租（`409`）或收到 `force=true` 时，Agent 必须立即停止收尾并上报终态。
22. 在上述保护终止条件命中后，控制面必须恢复按现行无保护规则执行回收。
23. 控制面热更新门控（`host_upgrade_gate`）必须继续将 `RUNNING` 与 `UNKNOWN` 均视为活跃，并保留默认 `45s` 排空超时后拒绝升级的安全边界。
24. 控制面热更新门控在因活跃作业拒绝升级时，必须在响应中补充按主体汇总的 `finishing_kind` 原因与重试等待提示。
25. Agent 优雅停机（`graceful_shutdown`）在收到 `SIGTERM/SIGINT` 时，必须对在途 `atomic_commit` 作业提供与现有 `teardown_disarm` 共用的有界停机结算窗，且不得突破 systemd `TimeoutStopSec`。
26. 若停机结算窗到期时 `atomic_commit` 仍未完成，Agent 必须在关闭 LocalDB 前向本地 outbox 持久化未验证中断记录，确保控制面在重连或超时后按 D4 第 16 条隔离该设备。

### D5 按子类型区分终态口径、混合 Plan 边界与在途版本交错兼容

1. 控制面与 Agent 必须按收尾子类型本质分别判定 Job 终态，不得混用两类口径：
   - 对 `teardown_disarm`（A 类），控制面与 Agent 必须保持 ADR-0061 契约：由主体测试的 `termination_reason` 决定 Job 终态（手动中止落 `ABORTED`，自然到点落 `COMPLETED`），且不得因解除成败改写该测试终态；
   - 对 `teardown_disarm`（A 类）中的解除失败，控制面必须通过 `disarm_results` 转为设备欠账；
   - 对 `atomic_commit`（B 类）且属于纯原子交付的 Job（冻结计划在当前 `atomic_group` 之后不存在未执行的必需非原子阶段，例如无 `patrol`），若收到普通 `abort` 后 `atomic_group` 内全部必需步骤均产生真实完成证据并执行成功，Agent 必须上报 Job 终态 `COMPLETED` 并在载荷中标记 `completed_during_abort_drain=true` 与 `atomic_group_completed=true`；
   - 在上述纯原子交付判定中，若必需步骤仅返回 `skipped=true` 且该步骤契约未显式声明 `skip_satisfies_delivery` 与目标版本核验证据，Agent 不得将其计作原子交付完成；
   - 对 `atomic_commit`（B 类）且属于混合 Plan 的 Job（冻结计划在 `atomic_group` 之后仍包含 `patrol` 或其他必需非原子阶段），若收到普通 `abort` 后本机 `atomic_group` 实证成功，Agent 必须跳过 `patrol`（按既有引擎契约推进或到达 barrier 以免阻塞同伴），并在存在设备足迹时按 ADR-0061 转交 `teardown_disarm`；
   - 对上述混合 Plan Job，Agent 必须最终上报 Job 终态 `ABORTED`（不得上报 `COMPLETED`），并在载荷中标记 `atomic_group_completed=true` 以供控制面关闭 `flash_integrity` 义务；
   - 对任意 `atomic_commit`（B 类）Job，若 `atomic_group` 内任一步骤失败或保护窗超期，Agent 必须上报 Job 终态 `FAILED`；
   - 对任意 `atomic_commit`（B 类）Job，若被 `force=true` 强杀或未获批进入临界区即被中止，Agent 必须上报 Job 终态 `ABORTED`。
2. Agent 必须将 `completed_during_abort_drain`、`atomic_group_completed`、`finishing_kind` 与 `abort_mode` 写入 `/complete` 请求载荷并持久化至 Agent LocalDB outbox。
3. 控制面必须在终态提交事务中消费上述字段并写入 Job 终态摘要。
4. 控制面必须保持 ADR-0048 父 PlanRun 聚合口径：只要 PlanRun 存在有效 `abort_requested`（或 `aborted > 0`），全部 Job 落终态后控制面必须将父 PlanRun 聚合为 `FAILED`。
5. 对因上述规则聚合为 `FAILED` 的父 PlanRun，控制面不得触发 `next_plan_id` 续链。
6. 控制面必须将纯原子交付 Job 在 drain 中以 `COMPLETED` 落终态的设备独立计入成功数，便于运维区分已交付与未交付设备。
7. 控制面与 Agent 在滚动升级与版本交错期间必须遵守以下在途能力冻结与降级规则：
   - **启动期能力准入**：在 Job claim 或启动时刻，控制面与 Agent 必须按冻结脚本包 `capabilities.json`、Agent 运行时能力与控制面 API/reaper 能力的交集协商并持久化该 Job 的 `negotiated_capabilities`（含 `finishing_guard_v2`、`atomic_commit`、`force_abort`）；
   - **在途能力冻结**：对已启动的在途 Job，控制面与 Agent 必须在其整个生命周期内锁定上述已协商能力集；
   - **未协商场景**：当 Job 启动时任一方未声明 `atomic_commit` 能力（脚本包无 `critical_section_guard`、旧版 Agent 或旧版控制面），控制面与 Agent 不得启用 `atomic_commit` 保护，必须退化为现行无临界区保护的 `abort` 行为且不得伪造保护窗；
   - **心跳回复缺字段场景**：滚动发布期间若新 Agent 收到旧控制面实例不含 `finishing_guard_v2` 字段的心跳回复，对新启动的 Job，Agent 不得协商启用 `atomic_commit` 并必须记录 `atomic_guard_protocol_unavailable`；
   - **心跳回复缺字段时的在途 Job**：对启动前已冻结 `atomic_commit` 能力的在途 Job，Agent 必须继续按已冻结能力并依赖控制面预登记接口响应决策，若预登记接口不可用则必须返回 `deny` 安全退化；
   - **在途降级与回滚约束**：运维人员与发布流程在存在活跃 `atomic_commit` Job 期间不得将控制面 API 或 reaper 回滚至不支持 `finishing_guard_v2` 的旧版本，必须先排空活跃 `atomic_commit` Job 再执行回滚；
   - **重连恢复与 `force` 同步**：Agent 重连执行 `agent_recovery`（含 `ABORT_LOCAL` 路径）或控制面重试下发中止时，控制面必须从持久化主体记录读取同一 `force` 强度并同步下发；
   - **旧版无法兑现 `force=true` 的显式反馈**：当操作者对锁定旧版能力（无 `force_abort`）的 Agent 主机下发 `force=true` 时，控制面不得静默宣称已强杀，必须在响应中显式返回 `force_unsupported_hosts` 并在下发普通兼容中止的同时提示风险。

---

## 3. 备选方案与权衡

| 备选方案 | 做法 | 未采纳理由 |
|---|---|---|
| **方案 1：拆分为两个对外过渡态名称（`中止中` + `收尾中`）** | 人工中止显示「中止中」，自然到点显示「收尾中」。 | Owner 裁定合并为单一顶层概念 **`收尾中`（`finishing`）**，通过子类型（`teardown_disarm` / `atomic_commit`）与触发源字段表达差异，避免界面与 API 状态词表碎片化。 |
| **方案 2：在 `disarm_guard` 上直接加 `if is_flash` 特例补丁** | 保持 `disarm_guard` 只面向 B6，把刷机硬塞进解除逻辑里。 | A 类（后置解除，终态正交、失败转欠账）与 B 类（原子交付闭环，含刷前完整性义务与整体成败口径）语义本质不同，硬塞会导致代码和契约持续腐化；抽成统一 `FinishingGuard` 集合 + 两类子模型才能一劳永逸。 |
| **方案 3：仅靠单向 `stderr` `critical_section="enter"` 打点进入临界区** | 脚本打印标记后立即执行 `adb reboot autodloader` / `spd_dump`。 | 存在明确竞态窗口：读线程尚未消费 `enter` 时 `cancel()` 仍可强杀进程，且控制面尚未落盘保护与完整性欠账义务；改为进入前有界同步许可仲裁 + 控制面预登记持久化，彻底闭合竞态。 |
| **方案 4：默认吞并同 Plan `init` 阶段全部剩余步骤作为原子尾段** | 不显式声明 `atomic_group`，收到 `enter` 后默认放行所有剩余 `init` 步骤。 | 容易误把第二段刷机或无关的专项足迹建立步骤卷入中止后放行范围，且无法覆盖不在 `PlanStep` 列表里的引擎插入 VBUS 刷后 `return_check`；改为显式冻结 `atomic_group` 可控且无歧义。 |
| **方案 5：临界区烧写成功后跳过 `oobe` 并把 Job 标 `ABORTED`** | 烧写完立刻跳后续步骤并强制标 `ABORTED`。 | 会留下「已刷机但未过 OOBE」的半成品设备，且运维无法从 Job 终态直接看出该机已完成交付；Owner 裁定单台纯刷机流程按整体执行并按整体成败落 `COMPLETED` / `FAILED`。 |
| **方案 6：在 DB `status` 枚举新增 `FINISHING` 持久化状态** | 改数据库状态机与部分唯一索引。 | 触及 `uq_job_active_per_device`、claim、reaper、聚合器、热更新全链路，迁移与回归风险远高于权威投影方案。 |

---

## 4. 影响范围

- **共享契约层（`backend/agent/contracts/`）**：新增统一 `finishing_guard` 契约模块（定义 `FinishingKind`、`FinishingTrigger`、同量纲截止计算、存活/静默推进分离判定、`flash_integrity` 义务契约与能力协商常量，向后兼容既有 `device_return` / `disarm_guard` 载荷）。
- **Agent 侧**：
  - `backend/agent/pipeline_engine.py`：实现进入临界区的同步许可仲裁点、控制面预登记调用、watchdog/步骤墙钟与 `D_atomic` 协同、冻结 `atomic_group`（含 VBUS 引擎插入 `return_check`）执行、混合 Plan `atomic_commit → teardown_disarm` 交接、`force=true` 强杀与 `force_abort` 终止原因；
  - `backend/agent/api_client.py` 与 LocalDB outbox：扩展 `/complete` 载荷（`completed_during_abort_drain`、`atomic_group_completed`、`finishing_kind`、`abort_mode`）与持久化 outbox；
  - `backend/agent/graceful_shutdown.py`：纳入在途 `atomic_commit` 的有界停机结算与 outbox 未验证中断落盘；
  - 刷机脚本族（`flash_firmware_unisoc_bsl`、`flash_firmware`）：新版本声明 `critical_section_guard`、`min_critical_section_seconds` 并接入同步进入许可握手与 `critical_section="exit"`。
- **控制面侧**：
  - `backend/services/plan_run_abort.py`：新增 `force` 参数、按主体持久化单调 `force` 状态、修正全 Run 级重复中止保留首次 `at`（ADR-0043 D2）、将 `UNKNOWN` 纳入中止候选、SocketIO 下发携带 `force` 并汇报 `already_terminal` / `force_unsupported_hosts`；
  - `backend/services/agent_recovery.py`：重连 `ABORT_LOCAL` 路径透传持久化主体 `force` 强度；
  - `backend/services/device_return.py` 与 `backend/services/agent_completion.py`：新增预登记端点（冻结 `D_atomic` + 写入 `flash_integrity` 义务）、终态统一租约事务核销或转 `MAINTENANCE` 欠账；
  - `backend/scheduler/device_lease_reconciler.py` 与 `backend/scheduler/recycler.py`：收敛到统一 `FinishingGuard` 判定入口，实行候选与行锁 CAS 双次复读及 `clock_regressed` 持久失效；
  - `backend/services/plan_run_devices.py`、`backend/services/plan_run_catalog.py` 与 `backend/services/host_upgrade_gate.py`：实现全量 `ui_status` 投影（含刷机预期掉 ADB 不误降 `unknown`）、列表筛选计数与热更新拒绝提示；
  - `backend/models/job_instance.py` 与 `plan_snapshot`：扩展 `finishing_guard` / `negotiated_capabilities` / `atomic_group` 冻结结构。
- **前端侧**：PlanRun 列表（含 `finishing` 筛选）、详情页、设备矩阵 Minimap（改消费权威 `ui_status`）、设备表与 Job 详情统一展示「`收尾中`」徽标、子类型明细与倒计时，以及「强制中止」二次确认弹窗。
- **兼容性**：严格向后兼容（D3 第 2 条、D5 第 4 条），不破坏现行 B6 归还链路与旧版 Agent。

---

## 5. 落地路径（按 ADR-0058 批次交付）

本提案跨控制面保护/欠账事务、Agent 执行引擎/停机结算、共享契约、刷机模板与在途版本交错兼容，明确命中 **ADR-0058 D7「多单元协调激活 + 数据/硬件安全」强判据**，转 `Accepted` 后按 ADR-0058 批次拆分并遵守 D8–D9（合入不等于生效，激活前全量复核通过）：

1. **阶段 1（ADR 定稿与语义登记）**：独立复核通过且 Owner 确认 §6 后转 `Accepted`，同步更新 `docs/design/07-execution-protocol.md` 与 `docs/design/2026-semantic-ownership.md`。
2. **阶段 2（统一底座 + A 类投影先行）**：落地 `backend/agent/contracts/` 统一 `FinishingGuard`、补齐 ADR-0043 全 Run 首次时钟保留、收编 B6 `disarm_guard` 为 `teardown_disarm`，上线控制面全量 `ui_status="finishing"` 投影与前端四面统一展示（开关机 / Monkey / GPU / 休眠 / MTBF 立即获得「收尾中」可视性）。
3. **阶段 3（B 类 `atomic_commit` + 预登记完整性欠账 + `force=true` 逃生阀 + 刷机接入）**：先发控制面（API + 预登记 + 欠账事务 + reaper/recycler + recovery）与 Agent（仲裁点 + watchdog + outbox + 优雅停机），确认机队版本齐备后迁移刷机模板 `atomic_group` 并发布支持 `critical_section_guard` 握手的 `flash_firmware_unisoc_bsl` / `flash_firmware` 新版本。

---

## 6. 裁决记录与待 Owner 确认项

### 已裁决（2026-11-26，Owner）
1. **过渡态名称与统一集合模型**：合并为单一 **`收尾中`（`finishing`）**，作为一等集合概念纳入模型，下设 `teardown_disarm`（A 类）与 `atomic_commit`（B 类）两个正交子类型，统一抽成 `FinishingGuard` 而不在 B6 上打补丁。
2. **单台刷机流程整体语义与终态口径**：将单台纯刷机流程视为原子整体；获批进入临界区后收到普通 `abort` 时跑完本机闭环，实证成功标 `COMPLETED`，任一步失败或超期标 `FAILED`（混合 Plan 存在未执行 `patrol` 时跳过 `patrol` 并标 `ABORTED`）。

### 本稿建议收口项（待 Owner 在转 `Accepted` 时最终拍板）
1. **采纳显式冻结 `atomic_group`（原选项乙，已写入 D3 第 4 条）**：将 `atomic_group` 写入冻结 `plan_snapshot`，专用刷机模板按已核对成员迁移，并显式纳入引擎插入的 VBUS 刷后 `return_check`，避免默认吞并全部剩余 `init` 导致误跑第二段刷机或无关足迹建立步骤。
2. **按 ADR-0058 批次拆分交付（已写入 §5）**：独立复核通过并由 Owner 裁定转 `Accepted` 后，按 ADR-0058 建立批次载体 issue 推进实施与协调激活。

---

## 7. 关联实现与文档

- `backend/agent/pipeline_engine.py`（`PipelineEngine.cancel`、进程读线程与 watchdog、VBUS 刷后 `return_check`、`INIT→PATROL`、跳过分支）
- `backend/agent/api_client.py`（`/complete` 载荷）与 `backend/agent/graceful_shutdown.py`（停机结算与 outbox）
- `backend/services/plan_run_abort.py`（`abort_plan_run`、主体时钟写入、候选筛选与 SocketIO 下发）
- `backend/services/agent_recovery.py`（重连恢复 `ABORT_LOCAL`）
- `backend/services/agent_completion.py` 与 `backend/services/device_return.py`（终态租约事务、欠账判定与归还契约）
- `backend/scheduler/device_lease_reconciler.py` 与 `backend/scheduler/recycler.py`（`UNKNOWN` 回收与行锁复读）
- `backend/services/plan_run_devices.py`、`backend/services/plan_run_catalog.py`、`backend/services/host_upgrade_gate.py`
- `docs/design/07-execution-protocol.md`（§3、§9.2、§9.5、B6 兼容契约）
- `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（列表分页与 Minimap 契约）
- `docs/adr/ADR-0061-device-return-contract.md` 与 `docs/adr/ADR-0063-vbus-cycle-flash-without-adb.md`
